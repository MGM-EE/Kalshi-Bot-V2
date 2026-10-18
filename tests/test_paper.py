from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from backend.app.execution.fees import fill_fee, rounded_fee
from backend.app.execution.fill_model import adverse_price, on_grid
from backend.app.execution.models import PaperConfig, PaperOrder, RiskLimits
from backend.app.execution.paper_broker import IdempotencyConflict, PaperBroker
from backend.app.markets.orderbook import KalshiBook
from backend.app.state import State
from backend.app.storage.database import Database

D = Decimal


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds=1):
        self.now += seconds


def ready(state, clock, asset="BTC", ticker="TEST-BTC"):
    state.assets[asset]["market"] = {
        "asset": asset,
        "ticker": ticker,
        "event_ticker": "TEST-EVENT",
        "status": "active",
        "open_time": 900,
        "close_time": 1800,
        "outcome_mapping": {"yes": "UP", "no": "DOWN"},
        "rule_checks": [],
        "current_index": "80000",
        "fee_type": "quadratic",
        "fee_multiplier": "1",
        "fees_verified_at": clock(),
        "fees_valid_until": clock() + 60,
        "price_ranges": [{"start": "0", "end": "1", "step": "0.01"}],
    }
    book = KalshiBook()
    book.snapshot([["0.38", "10"], ["0.37", "20"]], [["0.60", "10"], ["0.59", "10"]])
    state.assets[asset]["kalshi"]["book"] = book.view()
    for source in [
        f"kalshi:{asset}:book",
        f"kalshi:{asset}:reference",
        *[f"kraken:{asset}:{c}" for c in ("book", "ticker", "trade")],
    ]:
        state.health(source, "live", exchange_ts=clock())


@pytest.fixture
def paper(tmp_path, monkeypatch):
    import time

    clock, state = Clock(), State()
    monkeypatch.setattr(time, "time", clock)
    ready(state, clock)
    db = Database(tmp_path / "paper.sqlite3")
    config = PaperConfig(
        latency_ms=100,
        slippage_dollars="0",
        depth_participation="1",
        queue_multiplier="1",
        queue_buffer_contracts="0",
    )
    broker = PaperBroker(db, state, config, clock=clock)
    yield broker, state, clock
    db.close()


def order(order_id="paper-order-0001", **kwargs):
    return PaperOrder(
        client_order_id=order_id,
        asset="BTC",
        ticker="TEST-BTC",
        action=kwargs.pop("action", "buy"),
        outcome=kwargs.pop("outcome", "yes"),
        quantity=kwargs.pop("quantity", "10"),
        limit_price=kwargs.pop("limit_price", "0.40"),
        max_cost=kwargs.pop("max_cost", "5"),
        **kwargs,
    )


def execute(broker, clock, spec):
    broker.submit(spec)
    clock.advance(0.11)
    broker.process()
    return broker.db.order(spec.client_order_id)


def test_official_fee_rounding_example_and_accumulator():
    fee = rounded_fee(D("-.055000"), D(".00363825"))
    assert (fee.trade, fee.rounding, fee.rebate, fee.net) == (
        D(".003639"),
        D(".001361"),
        D(0),
        D(".005000"),
    )
    # Two fills carry .004 each; the third rebates .01 without a negative net fee.
    acc = D(0)
    fees = []
    for _ in range(3):
        fee = rounded_fee(D("-.006"), D(".010000"), acc)
        fees.append(fee)
        acc = fee.accumulator
    assert [f.rebate for f in fees] == [D(0), D(0), D(".01")]
    assert acc == D(".002000")
    assert all(f.net >= 0 for f in fees)


@pytest.mark.parametrize(
    "action,precision", [("buy", "0.01"), ("sell", "0.01"), ("buy", "0.0001"), ("sell", "0.0001")]
)
def test_fractional_fees_align_cash_and_never_refund_more_than_fee(action, precision):
    acc, total = D(0), D(0)
    for p, q in [(".055", ".01"), (".125", "2.31"), (".999", ".01")]:
        fee = fill_fee(p, q, action, "quadratic", "1", accumulator=acc, precision=D(precision))
        revenue = D(p) * D(q) * (-1 if action == "buy" else 1)
        assert (revenue - fee.net) % D(precision) == 0
        assert fee.net >= 0
        total += fee.net
        acc = fee.accumulator
    assert total > 0
    assert fill_fee(".4", "10", "buy", "quadratic", "1", maker=True).net == 0
    with pytest.raises(ValueError, match="Unsupported"):
        fill_fee(".4", "10", "buy", "flat", "1")


def test_tick_grid_and_adverse_slippage():
    ranges = [
        {"start": "0", "end": ".1", "step": ".001"},
        {"start": ".1", "end": ".9", "step": ".01"},
        {"start": ".9", "end": "1", "step": ".001"},
    ]
    assert on_grid(D(".099"), ranges)
    assert not on_grid(D(".401"), ranges)
    assert adverse_price(".4", "buy", ".001", ranges) == D(".41")
    assert adverse_price(".4", "sell", ".001", ranges) == D(".39")
    assert adverse_price(".999", "buy", ".001", ranges) is None


def test_latency_buy_sell_fees_cash_and_round_trip(paper):
    broker, state, clock = paper
    spec = order()
    broker.submit(spec)
    assert broker.portfolio()["available_cash"] == D("995")
    assert broker.portfolio()["cash"] == "1000.00"
    broker.process()
    assert broker.db.order(spec.client_order_id)["filled_quantity"] == "0"
    clock.advance(0.11)
    broker.process()
    buy = broker.db.order(spec.client_order_id)
    assert buy["status"] == "filled"
    assert D(buy["spent"]) == D("4.17")
    assert D(broker.db.account()["cash"]) == D("995.83")
    assert broker.db.round("TEST-BTC")["entries"] == 1
    state.assets["BTC"]["kalshi"]["book"]["yes_bids"] = [[D(".60"), D(10)]]
    sell = execute(
        broker, clock, order("paper-sell-0001", action="sell", limit_price=".60", max_cost=None)
    )
    assert sell["status"] == "filled"
    assert D(sell["proceeds"]) == D("5.83")
    assert D(sell["realized_pnl"]) == D("1.66")
    assert D(broker.db.account()["cash"]) == D("1001.66")
    assert broker.db.round("TEST-BTC")["round_trips"] == 1
    assert broker.portfolio()["open_positions"] == 0


def test_partial_depth_walk_and_no_reusing_consumed_liquidity(paper):
    broker, state, clock = paper
    first = execute(broker, clock, order(quantity="25", limit_price=".41", max_cost="12"))
    assert first["status"] == "partial"
    assert D(first["filled_quantity"]) == 20
    assert D(first["spent"]) == D("8.44")
    second = execute(broker, clock, order("paper-order-0002", quantity="1", max_cost="1"))
    assert second["status"] == "unfilled"
    assert second["filled_quantity"] == "0"
    # A larger displayed level makes only the additional quantity available.
    state.assets["BTC"]["kalshi"]["book"]["yes_asks"][0][1] = D(12)
    third = execute(broker, clock, order("paper-order-0003", quantity="5", max_cost="3"))
    assert D(third["filled_quantity"]) == 2


def test_partial_cash_budget_includes_fees(paper):
    broker, _, clock = paper
    result = execute(broker, clock, order(max_cost="2"))
    assert result["status"] == "partial"
    assert D(result["filled_quantity"]) == D("4.79")
    assert D(result["spent"]) <= D(2)
    assert broker.portfolio()["reserved_cash"] == 0


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"max_entry_cost": "4"}, "entry cost"),
        ({"max_round_exposure": "4"}, "round exposure"),
        ({"max_asset_exposure": "4"}, "asset exposure"),
        ({"max_total_exposure": "4"}, "portfolio exposure"),
        ({"max_loss_per_trade": "4"}, "per position"),
        ({"max_loss_per_round": "4"}, "per round"),
        ({"max_quantity": "2"}, "Quantity"),
    ],
)
def test_hard_risk_limits(paper, change, reason):
    broker, _, _ = paper
    broker.limits = RiskLimits(**change)
    rejected = broker.submit(order())
    assert rejected["status"] == "rejected"
    assert reason in rejected["reason"]
    assert not broker.db.positions()


def test_entry_reservations_and_round_trip_limit(paper):
    broker, state, clock = paper
    broker.limits = RiskLimits(max_entries_per_round=1, max_round_trips_per_round=1)
    broker.submit(order())
    assert "entry count" in broker.submit(order("paper-order-0002"))["reason"]
    clock.advance(0.11)
    broker.process()
    execute(
        broker, clock, order("paper-sell-0001", action="sell", limit_price=".38", max_cost=None)
    )
    assert broker.db.round("TEST-BTC")["round_trips"] == 1
    broker.limits = RiskLimits(max_entries_per_round=10, max_round_trips_per_round=1)
    assert "round-trip" in broker.submit(order("paper-order-0003"))["reason"]


def test_sell_reservations_and_no_naked_short(paper):
    broker, _, clock = paper
    assert (
        broker.submit(order("paper-sell-0000", action="sell", max_cost=None))["status"]
        == "rejected"
    )
    execute(broker, clock, order())
    assert (
        broker.submit(order("paper-sell-0001", action="sell", max_cost=None))["status"] == "pending"
    )
    assert (
        "unreserved"
        in broker.submit(order("paper-sell-0002", action="sell", max_cost=None))["reason"]
    )
    broker.cancel("paper-sell-0001")
    assert (
        broker.submit(order("paper-sell-0003", action="sell", max_cost=None))["status"] == "pending"
    )


def test_stale_unknown_data_and_cutoff_never_fill(paper):
    broker, state, clock = paper
    broker.submit(order())
    state.assets["BTC"]["market"]["current_index"] = None
    clock.advance(0.11)
    broker.process()
    assert broker.db.order("paper-order-0001")["status"] == "rejected"
    ready(state, clock)
    broker.submit(order("paper-order-0002"))
    clock.advance(16)
    broker.process()
    assert "Stale" in broker.db.order("paper-order-0002")["reason"]
    ready(state, clock)
    state.assets["BTC"]["market"]["close_time"] = clock() + 0.05
    broker.submit(order("paper-order-0003"))
    clock.advance(0.11)
    broker.process()
    assert broker.db.order("paper-order-0003")["status"] == "expired"
    assert broker.portfolio()["open_positions"] == 0


def trade(state, clock, trade_id, quantity, side="no", price=".38", block=False):
    state.assets["BTC"]["kalshi"].setdefault("trades", []).append(
        {
            "trade_id": trade_id,
            "yes_price_dollars": price,
            "count_fp": quantity,
            "taker_outcome_side": side,
            "ts_ms": int(clock() * 1000),
            "is_block_trade": block,
        }
    )


def test_passive_queue_only_fills_observed_excess_not_touch_or_cancel(paper):
    broker, state, clock = paper
    broker.limits = RiskLimits(max_entries_per_round=1)
    spec = order(mode="passive", quantity="5", limit_price=".38", max_cost="3")
    execute(broker, clock, spec)
    assert broker.db.order(spec.client_order_id)["status"] == "resting"
    state.assets["BTC"]["kalshi"]["book"]["yes_bids"] = []
    broker.process()  # Disappearing/touched book liquidity is not a trade print.
    assert broker.db.order(spec.client_order_id)["filled_quantity"] == "0"
    clock.advance()
    trade(state, clock, "queue-0001", "8")
    broker.process()
    assert D(broker.db.order(spec.client_order_id)["queue_ahead"]) == 2
    clock.advance()
    trade(state, clock, "queue-0002", "5")
    broker.process()
    assert D(broker.db.order(spec.client_order_id)["filled_quantity"]) == 3
    broker.process()
    assert D(broker.db.order(spec.client_order_id)["filled_quantity"]) == 3
    clock.advance()
    trade(state, clock, "queue-0003", "3")
    broker.process()
    assert broker.db.order(spec.client_order_id)["status"] == "filled"
    assert broker.db.round("TEST-BTC")["entries"] == 1


def test_passive_post_only_duplicates_unknown_and_block_trades(paper):
    broker, state, clock = paper
    crossing = execute(broker, clock, order(mode="passive"))
    assert crossing["status"] == "rejected"
    execute(broker, clock, order("paper-order-0002", mode="passive", limit_price=".38"))
    duplicate = broker.submit(order("paper-order-0003", mode="passive", limit_price=".38"))
    assert duplicate["status"] == "rejected"
    clock.advance()
    trade(state, clock, "block-0001", "100", block=True)
    trade(state, clock, "unknown-0001", "100", side=None)
    broker.process()
    assert broker.db.order("paper-order-0002")["filled_quantity"] == "0"
    broker.cancel("paper-order-0002")
    assert broker.portfolio()["reserved_cash"] == 0


def test_restart_idempotency_preserves_cash_fills_counters_and_debt(paper, tmp_path):
    broker, state, clock = paper
    spec = order()
    execute(broker, clock, spec)
    path = broker.db.connection.execute("PRAGMA database_list").fetchone()[2]
    second = Database(path)
    try:
        restored = PaperBroker(second, state, PaperConfig(starting_balance="9999"), clock=clock)
        repeated = restored.submit(spec)
        assert repeated["status"] == "filled"
        assert D(restored.db.account()["starting_balance"]) == 1000
        assert D(restored.db.account()["cash"]) == D("995.83")
        restored.process()
        assert len(restored.portfolio()["fills"]) == 1
        assert restored.db.round("TEST-BTC")["entries"] == 1
        assert restored.db.debt("TEST-BTC", "ask", D(".40")) == 10
        with pytest.raises(IdempotencyConflict):
            restored.submit(spec.model_copy(update={"quantity": D(5)}))
    finally:
        second.close()


def test_atomic_fill_rolls_back_on_storage_failure(paper, monkeypatch):
    broker, _, clock = paper
    broker.submit(order())
    clock.advance(0.11)
    original = broker.db.save_position

    def fail(_):
        raise RuntimeError("injected storage failure")

    monkeypatch.setattr(broker.db, "save_position", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        broker.process()
    assert D(broker.db.account()["cash"]) == 1000
    assert not broker.portfolio()["fills"]
    assert broker.db.order("paper-order-0001")["status"] == "pending"
    monkeypatch.setattr(broker.db, "save_position", original)
    broker.process()
    assert len(broker.portfolio()["fills"]) == 1


@pytest.mark.parametrize("outcome,expected", [("yes", "1005.83"), ("no", "995.83")])
def test_official_settlement_pays_once_and_never_uses_spot(paper, outcome, expected):
    broker, state, clock = paper
    execute(broker, clock, order())
    clock.now = 1801
    market = {
        "ticker": "TEST-BTC",
        "status": "finalized",
        "result": outcome,
        "settlement_ts": "1970-01-01T00:30:01Z",
        "close_time": "1970-01-01T00:30:00Z",
    }
    state.assets["BTC"]["kraken"]["ticker"] = {"last": "9999999"}
    broker.settle("TEST-BTC", market)
    broker.settle("TEST-BTC", market)
    assert D(broker.db.account()["cash"]) == D(expected)
    assert broker.portfolio()["open_positions"] == 0
    assert broker.db.round("TEST-BTC")["round_trips"] == 1
    with pytest.raises(ValueError, match="Conflicting"):
        broker.settle("TEST-BTC", {**market, "result": "no" if outcome == "yes" else "yes"})
    with pytest.raises(ValueError, match="Final"):
        broker.settle("TEST-BTC", {**market, "status": "determined"})


def test_competing_connections_cannot_overreserve_cash(paper):
    broker, state, clock = paper
    with broker.db.transaction():
        account = broker.db.account()
        account["cash"] = "30"
        broker.db.save_account(account)
    path = broker.db.connection.execute("PRAGMA database_list").fetchone()[2]
    other_db = Database(path)
    try:
        other = PaperBroker(other_db, state, broker.config, clock=clock)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(b.submit, order(f"parallel-order-{i}", max_cost="25"))
                for i, b in enumerate((broker, other))
            ]
            results = [f.result() for f in futures]
        assert sorted(r["status"] for r in results) == ["pending", "rejected"]
        assert broker.portfolio()["available_cash"] == 5
    finally:
        other_db.close()


def test_limits_are_rechecked_after_latency_and_protective_sells_need_only_book(paper):
    broker, state, clock = paper
    broker.submit(order())
    # Simulate a persisted loss pause arriving while an order waits for its latency.
    with broker.db.transaction():
        account = broker.db.account()
        account["consecutive_losses"] = 5
        broker.db.save_account(account)
    clock.advance(0.11)
    broker.process()
    assert "Consecutive-loss" in broker.db.order("paper-order-0001")["reason"]
    with broker.db.transaction():
        account = broker.db.account()
        account["consecutive_losses"] = 0
        broker.db.save_account(account)
    execute(broker, clock, order("paper-order-0002"))
    state.assets["BTC"]["market"]["current_index"] = None
    state.health("kraken:BTC:book", "disconnected")
    sold = execute(
        broker, clock, order("paper-sell-0001", action="sell", limit_price=".38", max_cost=None)
    )
    assert sold["status"] == "filled"


def test_daily_drawdown_asset_separation_and_partial_exit_basis(paper):
    broker, state, clock = paper
    execute(broker, clock, order())
    ready(state, clock, "ETH", "TEST-ETH")
    eth = order("paper-order-0002").model_copy(update={"asset": "ETH", "ticker": "TEST-ETH"})
    execute(broker, clock, eth)
    sell = execute(
        broker,
        clock,
        order("paper-sell-0001", action="sell", quantity="4", limit_price=".38", max_cost=None),
    )
    assert sell["status"] == "filled"
    positions = {(p["ticker"], p["outcome"]): p for p in broker.db.positions()}
    assert D(positions[("TEST-BTC", "yes")]["quantity"]) == 6
    assert D(positions[("TEST-BTC", "yes")]["cost_basis"]) == D("2.502")
    assert D(positions[("TEST-ETH", "yes")]["quantity"]) == 10
    assert D(positions[("TEST-ETH", "yes")]["cost_basis"]) == D("4.17")
    broker.limits = RiskLimits(max_daily_drawdown=".01")
    rejected = broker.submit(order("paper-order-0003"))
    assert "Daily drawdown" in rejected["reason"]
    assert broker.db.round("TEST-BTC")["round_trips"] == 0


def test_local_paper_api_persists_orders_and_rejects_cross_origin(paper, tmp_path):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.settings import Settings

    _, _, clock = paper
    app = create_app(
        Settings(paper_database_path=str(tmp_path / "api.sqlite3")), connect_live=False
    )
    with TestClient(app) as client:
        app.state.paper_broker.clock = clock
        app.state.paper_broker.config = PaperConfig(
            latency_ms=100, slippage_dollars="0", depth_participation="1"
        )
        ready(app.state.market_state, clock)
        spec = order()
        response = client.post("/api/paper/orders", json=spec.model_dump(mode="json"))
        assert response.status_code == 200
        assert response.json()["status"] == "pending"
        clock.advance(0.11)
        app.state.paper_broker.process()
        repeated = client.post("/api/paper/orders", json=spec.model_dump(mode="json"))
        assert repeated.json()["status"] == "filled"
        portfolio = client.get("/api/paper/portfolio").json()
        assert len(portfolio["fills"]) == 1
        assert D(portfolio["cash"]) == D("995.83")
        conflict = spec.model_copy(update={"quantity": D(5)})
        assert (
            client.post("/api/paper/orders", json=conflict.model_dump(mode="json")).status_code
            == 409
        )
        assert (
            client.post(
                "/api/paper/orders",
                json=spec.model_dump(mode="json"),
                headers={"origin": "https://other.example"},
            ).status_code
            == 403
        )
        assert (
            client.get("/api/paper/portfolio", headers={"host": "attacker.example"}).status_code
            == 400
        )
        assert client.post("/api/orders", json={}).status_code == 404
        assert client.get("/api/health").json()["real_order_execution_enabled"] is False


async def test_settlement_worker_reconciles_finalized_rest_result_with_historical_fallback(
    paper, monkeypatch
):
    import asyncio

    import httpx

    from backend.app.connectors.kalshi import Kalshi
    from backend.app.execution import service
    from backend.app.settings import Settings

    broker, state, clock = paper
    execute(broker, clock, order())
    clock.now = 1801
    calls = []

    def respond(request):
        calls.append(request)
        if "/historical/" not in request.url.path:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "market": {
                    "ticker": "TEST-BTC",
                    "status": "finalized",
                    "result": "yes",
                    "settlement_ts": "1970-01-01T00:30:01Z",
                    "close_time": "1970-01-01T00:30:00Z",
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: client)
    task = asyncio.create_task(service.settlement_worker(broker, Kalshi(state, Settings()), state))
    try:
        for _ in range(100):
            if D(broker.db.account()["cash"]) == D("1005.83"):
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert D(broker.db.account()["cash"]) == D("1005.83")
        assert len(calls) == 2
        assert all(c.method == "GET" for c in calls)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_event_fee_override_and_scheduled_change_expire_execution_metadata(paper):
    import httpx

    from backend.app.connectors.kalshi import Kalshi
    from backend.app.settings import Settings

    broker, state, clock = paper
    future = datetime.fromtimestamp(1010, timezone.utc).isoformat()

    def respond(request):
        if request.url.path.endswith("/series/fee_changes"):
            return httpx.Response(200, json={"series_fee_change_arr": [{"scheduled_ts": future}]})
        if request.url.path.endswith("/events/fee_changes"):
            return httpx.Response(200, json={"event_fee_changes": [], "cursor": ""})
        return httpx.Response(
            200,
            json={
                "event": {
                    "fee_type_override": "quadratic_with_maker_fees",
                    "fee_multiplier_override": 2,
                }
            },
        )

    market = state.assets["BTC"]["market"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await Kalshi(state, Settings()).effective_fees(
            client, market, {"ticker": "KXBTC15M", "fee_type": "quadratic", "fee_multiplier": 1}
        )
    assert market["fee_type"] == "quadratic_with_maker_fees"
    assert market["fee_multiplier"] == 2
    assert market["fees_valid_until"] == 1010
    clock.now = 1011
    rejected = broker.submit(order())
    assert rejected["status"] == "rejected"
    assert "fee metadata" in rejected["reason"]


def test_partial_passive_fills_can_finish_below_initial_minimum_quantity(paper):
    broker, state, clock = paper
    broker.limits = RiskLimits(min_quantity="5", max_entries_per_round=1)
    spec = order(mode="passive", quantity="5", limit_price=".38", max_cost="3")
    execute(broker, clock, spec)
    clock.advance()
    trade(state, clock, "queue-min-0001", "13")
    broker.process()
    assert D(broker.db.order(spec.client_order_id)["filled_quantity"]) == 3
    clock.advance()
    trade(state, clock, "queue-min-0002", "2")
    broker.process()
    assert broker.db.order(spec.client_order_id)["status"] == "filled"


def test_partial_position_can_be_closed_below_minimum_entry_size(paper):
    broker, state, clock = paper
    broker.limits = RiskLimits(min_quantity="5")
    execute(broker, clock, order(mode="passive", quantity="5", limit_price=".38", max_cost="3"))
    clock.advance()
    trade(state, clock, "queue-min-exit", "13")
    broker.process()
    broker.cancel("paper-order-0001")
    sold = execute(
        broker,
        clock,
        order("paper-small-exit", action="sell", quantity="3", limit_price=".38", max_cost=None),
    )
    assert sold["status"] == "filled"
    assert broker.portfolio()["open_positions"] == 0
