"""Seeded synthetic ledger stress. This module never opens the application account."""

import json
import os
import random
from collections import defaultdict, deque
from decimal import Decimal as D

import pytest

from backend.app.execution.configuration import initialize
from backend.app.execution.models import PaperConfig, PaperOrder, RiskLimits
from backend.app.execution.paper_broker import IdempotencyConflict, PaperBroker
from backend.app.indicators.engine import IndicatorEngine
from backend.app.indicators.features import Features
from backend.app.markets.orderbook import KalshiBook
from backend.app.research.analytics import analytics
from backend.app.research.clock import EventClock
from backend.app.research.recorder import Recorder
from backend.app.research.replay import replay
from backend.app.state import State
from backend.app.storage.database import Database
from backend.app.strategy.engine import StrategyEngine

ZERO = D(0)
EPSILON = D("1e-20")
ASSETS = ("BTC", "ETH", "SOL")


def audit(broker):
    """Reconstruct cash, basis, inventory, fees, cycles and depth from journal rows."""
    db = broker.db
    precision = D(broker.config.balance_precision)
    fills = db.fills(-1)[::-1]
    settlements = db.many("SELECT data FROM settlements")
    orders = {row["id"]: row for row in db.many("SELECT data FROM orders")}
    events = [(row["time"], 0, row["id"], row) for row in fills]
    events.extend((row["time"], 1, i, row) for i, row in enumerate(settlements))
    cash = D(db.account()["starting_balance"])
    pnl = fees = ZERO
    inventory = defaultdict(lambda: dict(quantity=ZERO, basis=ZERO))
    filled = defaultdict(lambda: ZERO)
    debt = defaultdict(lambda: ZERO)
    entry_orders = defaultdict(set)
    round_trips = defaultdict(int)
    for _, kind, _, row in sorted(events, key=lambda item: item[:3]):
        if kind:
            payout = ZERO
            for (ticker, outcome), position in inventory.items():
                if ticker != row["ticker"] or position["quantity"] == 0:
                    continue
                paid = position["quantity"] if outcome == row["result"] else ZERO
                pnl += paid - position["basis"]
                payout += paid
                position.update(quantity=ZERO, basis=ZERO)
                round_trips[ticker] += 1
            assert payout == D(row["payout"])
            cash += payout
            continue
        spec = orders[row["order_id"]]["spec"]
        qty = D(row["quantity"])
        price = D(row["price"])
        fee = D(row["net_fee"])
        assert qty > 0 and qty % D(".01") == 0 and fee >= 0
        assert (
            price <= D(spec["limit_price"])
            if row["action"] == "buy"
            else price >= D(spec["limit_price"])
        )
        position = inventory[(row["ticker"], row["outcome"])]
        gross = qty * price
        revenue = -gross if row["action"] == "buy" else gross
        assert (revenue - fee) % precision == 0
        if row["action"] == "buy":
            cash -= gross + fee
            position["quantity"] += qty
            position["basis"] += gross + fee
            entry_orders[row["ticker"]].add(row["order_id"])
        else:
            assert qty <= position["quantity"]
            basis = position["basis"] * qty / position["quantity"]
            expected_pnl = gross - fee - basis
            assert abs(expected_pnl - D(row["realized_pnl"])) <= EPSILON
            pnl += expected_pnl
            cash += gross - fee
            position["quantity"] -= qty
            position["basis"] -= basis
            if position["quantity"] == 0:
                round_trips[row["ticker"]] += 1
        fees += fee
        filled[row["order_id"]] += qty
        if not row["maker"]:
            side = "ask" if (row["outcome"] == "yes") == (row["action"] == "buy") else "bid"
            canonical = D(row["raw_price"]) if row["outcome"] == "yes" else 1 - D(row["raw_price"])
            debt[(row["ticker"], side, canonical)] += qty
    account = db.account()
    assert cash == D(account["cash"]) and cash >= 0
    assert cash % D(account["balance_precision"]) == 0
    assert fees == D(account["fees"])
    assert abs(pnl - D(account["realized_pnl"])) <= EPSILON
    positions = {(p["ticker"], p["outcome"]): p for p in db.positions()}
    for key, p in inventory.items():
        assert p["quantity"] == D(positions[key]["quantity"]) and p["quantity"] >= 0
        assert abs(p["basis"] - D(positions[key]["cost_basis"])) <= EPSILON
    assert (
        abs(
            cash
            + sum((p["basis"] for p in inventory.values()), ZERO)
            - D(account["starting_balance"])
            - pnl
        )
        <= EPSILON
    )
    for key, qty in debt.items():
        assert db.debt(*key) == qty
    for ticker, ids in entry_orders.items():
        assert db.round(ticker)["entries"] == len(ids)
        assert db.round(ticker)["round_trips"] == round_trips[ticker]
    for order_id, row in orders.items():
        assert filled[order_id] == D(row["filled_quantity"]) <= D(row["spec"]["quantity"])
        if row["spec"]["action"] == "buy":
            assert D(row["spent"]) <= D(row["spec"]["max_cost"])
    active = db.active()
    reserved = sum(
        (D(o["spec"]["max_cost"]) - D(o["spent"]) for o in active if o["spec"]["action"] == "buy"),
        ZERO,
    )
    assert ZERO <= reserved <= cash
    for key, p in inventory.items():
        pending = sum(
            (
                D(o["spec"]["quantity"]) - D(o["filled_quantity"])
                for o in active
                if o["spec"]["action"] == "sell"
                and (o["spec"]["ticker"], o["spec"]["outcome"]) == key
            ),
            ZERO,
        )
        assert pending <= p["quantity"]
    return dict(
        fills=len(fills),
        maker_fills=sum(f["maker"] for f in fills),
        cash=str(cash),
        fees=str(fees),
        realized_pnl=str(pnl),
        settlements=len(settlements),
    )


@pytest.mark.parametrize("precision", ["0.01", "0.0001"])
@pytest.mark.parametrize("seed", [7, 41, 173])
def test_seeded_paper_journal_stress(tmp_path, precision, seed):
    steps = int(os.environ.get("KALSHI_STRESS_STEPS", "180"))
    assert 60 <= steps <= 5000
    audit_interval = int(os.environ.get("KALSHI_STRESS_AUDIT_INTERVAL", "1"))
    assert 1 <= audit_interval <= steps
    rng = random.Random(seed)
    fee_type = {
        7: "quadratic",
        41: "quadratic_with_maker_fees",
        173: "quadratic_with_combo_maker_fees",
    }[seed]
    clock = EventClock(1000.0)
    state = State(clock=clock)
    db = Database(tmp_path / "stress.sqlite3")
    config = PaperConfig(
        balance_precision=precision,
        latency_ms=10,
        slippage_dollars="0",
        depth_participation="1",
        queue_multiplier="1",
        queue_buffer_contracts="0",
    )
    limits = RiskLimits(
        max_entries_per_round=100000,
        max_round_trips_per_round=100000,
        max_daily_drawdown=1000000,
        max_consecutive_losses=1000000,
        max_loss_per_trade=200,
        max_loss_per_round=500,
    )
    broker = PaperBroker(db, state, config, limits, clock)
    initialize(
        db, dict(execution=config.model_dump(mode="json"), risk=limits.model_dump(mode="json"))
    )
    features = Features(state)
    indicators = IndicatorEngine(state, features, db)
    features.advanced = indicators
    engine = StrategyEngine(broker, features)
    path = tmp_path / "stress.jsonl.gz"
    recorder = Recorder(path, state, broker, engine, indicators)
    state.record_event = recorder.event
    state.observers.extend([features.observe, indicators.observe, recorder.observe])
    trade_buffers = {asset: deque(maxlen=100) for asset in ASSETS}
    specs = []
    round_id = 0

    def cycle():
        state.in_cycle = True
        error = None
        try:
            with db.transaction():
                broker.mark_account(clock())
            engine.tick()
            if db.active():
                broker.process()
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            state.in_cycle = False
            recorder.event("cycle", dict(error=error, mark_account=True))

    def book(asset):
        p = D(rng.randrange(5, 91)) / 100
        ask = p + D(rng.randrange(1, 6)) / 100
        snapshot = KalshiBook()
        snapshot.snapshot([[p, str(rng.randrange(1, 31))]], [[1 - ask, str(rng.randrange(1, 31))]])
        state.assets[asset]["kalshi"]["book"] = snapshot.view()
        state.health(f"kalshi:{asset}:book", "live", exchange_ts=clock())

    def refresh():
        for asset in ASSETS:
            market = state.assets[asset]["market"]
            market.update(
                fees_verified_at=clock(), fees_valid_until=clock() + 60, current_index="80000"
            )
            for name in (
                f"kalshi:{asset}:reference",
                *[f"kraken:{asset}:{channel}" for channel in ("book", "ticker", "trade")],
            ):
                state.health(name, "live", exchange_ts=clock())

    def start_round():
        nonlocal round_id
        round_id += 1
        for asset in ASSETS:
            state.assets[asset]["market"] = dict(
                asset=asset,
                ticker=f"SYNTHETIC-{asset}-{round_id}",
                event_ticker=f"SYNTHETIC-EVENT-{round_id}",
                status="active",
                open_time=clock(),
                close_time=clock() + 900,
                outcome_mapping={"yes": "UP", "no": "DOWN"},
                rule_checks=[],
                current_index="80000",
                opening_reference="80000",
                fee_type=fee_type,
                fee_multiplier="1",
                fees_verified_at=clock(),
                fees_valid_until=clock() + 60,
                price_ranges=[dict(start="0", end="1", step=".01")],
            )
            state.assets[asset]["kalshi"] = {}
            trade_buffers[asset].clear()
            book(asset)
        refresh()

    def settle_round():
        clock.fixed = max(state.assets[a]["market"]["close_time"] for a in ASSETS) + 1
        cycle()
        for asset in ASSETS:
            market = state.assets[asset]["market"]
            broker.settle(
                market["ticker"],
                dict(
                    ticker=market["ticker"],
                    status="finalized",
                    result=rng.choice(("yes", "no")),
                    settlement_ts=clock(),
                    close_time=market["close_time"],
                ),
            )
        audit(broker)

    try:
        start_round()
        for step in range(steps):
            clock.fixed += rng.choice((0.15, 0.35, 0.75))
            if step and step % 60 == 0:
                settle_round()
                clock.fixed += 1
                start_round()
            refresh()
            asset = rng.choice(ASSETS)
            book(asset)
            outcome = rng.choice(("yes", "no"))
            target = state.assets[asset]
            ticker = target["market"]["ticker"]
            if step % 17 == 0:
                state.health(f"kalshi:{asset}:reference", "disconnected")
            elif step % 19 == 0:
                state.health(f"kalshi:{asset}:book", "live", exchange_ts=clock() - 100)
            if rng.random() < 0.12 and db.active():
                broker.cancel(rng.choice(db.active())["id"])
            elif rng.random() < 0.12 and specs:
                spec = rng.choice(specs)
                before = db.account()
                broker.submit(spec)
                assert db.account() == before
                with pytest.raises(IdempotencyConflict):
                    broker.submit(spec.model_copy(update={"quantity": spec.quantity + D(".01")}))
            else:
                held = next(
                    (
                        p
                        for p in db.positions()
                        if p["ticker"] == ticker
                        and p["outcome"] == outcome
                        and D(p["quantity"]) > 0
                    ),
                    None,
                )
                action = "sell" if held and rng.random() < 0.6 else "buy"
                mode = "passive" if rng.random() < 0.3 else "marketable"
                levels = target["kalshi"]["book"][
                    outcome + ("_bids" if (action == "sell") == (mode == "marketable") else "_asks")
                ]
                price = D(levels[0][0])
                qty = (
                    D(rng.randrange(1, 1201)) / 100
                    if action == "buy"
                    else min(D(held["quantity"]), D(rng.randrange(1, 1201)) / 100)
                )
                spec = PaperOrder(
                    client_order_id=f"stress-{seed}-{precision.replace('.', '')}-{step:06}",
                    asset=asset,
                    ticker=ticker,
                    action=action,
                    outcome=outcome,
                    quantity=qty,
                    limit_price=price,
                    mode=mode,
                    max_cost=D(rng.randrange(1, 601)) / 100 if action == "buy" else None,
                    timeout_seconds=rng.choice((1, 5, 30)),
                )
                broker.submit(spec)
                specs.append(spec)
            clock.fixed += 0.02
            cycle()
            resting = [o for o in db.active() if o["status"] == "resting"]
            if resting:
                selected = rng.choice(resting)
                s = selected["spec"]
                a = s["asset"]
                clock.fixed += 0.02
                bid_side = (s["outcome"] == "yes") == (s["action"] == "buy")
                canonical = (
                    D(s["limit_price"]) if s["outcome"] == "yes" else 1 - D(s["limit_price"])
                )
                trade_buffers[a].append(
                    dict(
                        trade_id=f"synthetic-{seed}-{step}",
                        ts_ms=clock() * 1000,
                        yes_price_dollars=str(canonical),
                        no_price_dollars=str(1 - canonical),
                        count_fp=str(rng.randrange(1, 61)),
                        taker_outcome_side="no" if bid_side else "yes",
                        is_block_trade=step % 13 == 0,
                    )
                )
                state.assets[a]["kalshi"]["trades"] = list(trade_buffers[a])
                state.health(f"kalshi:{a}:trade", "live", exchange_ts=clock())
                cycle()
            if step % audit_interval == 0 or step == steps - 1:
                audit(broker)
            if step % 10 == 0:
                before = db.account()
                broker.portfolio()
                assert db.account() == before
            assert recorder.error is None
        settle_round()
        expected = audit(broker)
        assert expected["fills"] > 20 and expected["maker_fills"] > 0
        summary = analytics(db)
        assert abs(summary["net_closed_pnl"] - D(db.account()["realized_pnl"])) <= EPSILON
        assert summary["open_cycles"] == 0
        state.record_event = None
        recorder.close()
        assert recorder.error is None
        result = replay(path)
        assert result["exact_reproduction"] is True
        assert result["performance"]["failed_cycles"] == 0
        print(
            json.dumps(
                dict(
                    fixture="SEEDED SYNTHETIC PAPER STRESS",
                    seed=seed,
                    precision=precision,
                    fee_type=fee_type,
                    steps=steps,
                    audit_interval=audit_interval,
                    events=result["events"],
                    exact_replay=True,
                    **expected,
                )
            )
        )
    finally:
        state.record_event = None
        recorder.close()
        db.close()
