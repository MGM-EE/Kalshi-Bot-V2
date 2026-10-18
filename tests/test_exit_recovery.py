"""Synthetic exit/recovery regressions; no network or application account."""

from decimal import Decimal as D

import pytest
from test_paper import execute, order
from test_paper import paper as paper
from test_research import recorded as recorded
from test_strategy import cf, set_book, setup_engine

from backend.app.execution.models import RiskLimits


def entered(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    engine.tick()
    clock.advance(0.11)
    broker.process()
    engine.control(enabled=False)
    assert D(broker.db.positions()[0]["quantity"]) == 10
    clock.advance(1)
    cf(state, clock, "80002")
    engine.tick()
    assert not broker.db.active()
    return engine


def crossed(paper):
    broker, state, clock = paper
    engine = entered(paper)
    clock.advance(1)
    cf(state, clock, "79999")
    engine.tick()
    return engine


def test_reference_cross_survives_submit_and_latency_revalidation(paper):
    broker, state, clock = paper
    engine = crossed(paper)
    pending = broker.db.active()
    assert len(pending) == 1
    assert pending[0]["spec"]["action"] == "sell"
    assert "crossing" in pending[0]["spec"]["reason"]
    clock.advance(0.11)
    broker.process()
    assert D(broker.db.positions()[0]["quantity"]) == 0
    assert broker.db.order(pending[0]["id"])["status"] == "filled"
    assert engine.store.read("control")["enabled"] is False


def test_reference_recovery_before_fill_cancels_cross_exit(paper):
    broker, state, clock = paper
    crossed(paper)
    pending = broker.db.active()
    assert len(pending) == 1
    clock.advance(0.11)
    cf(state, clock, "80003")
    broker.process()
    assert broker.db.order(pending[0]["id"])["status"] == "rejected"
    assert D(broker.db.positions()[0]["quantity"]) == 10
    assert len(broker.db.fills()) == 1


def test_emergency_reduces_accumulated_position_in_valid_order_sizes(paper):
    broker, state, clock = paper
    engine, _ = setup_engine(paper)
    broker.limits = RiskLimits(max_quantity=6)
    for i in range(2):
        filled = execute(broker, clock, order(f"accumulate-{i}", quantity="6"))
        assert filled["status"] == "filled"
    assert D(broker.db.positions()[0]["quantity"]) == 12
    engine.control(emergency=True)
    for _ in range(2):
        clock.advance(1.1)
        set_book(state, clock, ".36", ".62")
        engine.tick()
        pending = broker.db.active()
        assert len(pending) == 1
        assert D(pending[0]["spec"]["quantity"]) == 6
        clock.advance(0.11)
        broker.process()
        assert broker.db.order(pending[0]["id"])["status"] == "filled"
    assert D(broker.db.positions()[0]["quantity"]) == 0
    assert broker.db.round("TEST-BTC")["round_trips"] == 1


def test_manual_reduction_does_not_consume_automatic_profit_target(paper):
    broker, state, clock = paper
    engine = entered(paper)
    sold = execute(
        broker,
        clock,
        order(
            "manual-reduce-before-target",
            action="sell",
            quantity="2",
            limit_price=".36",
            max_cost=None,
        ),
    )
    assert sold["status"] == "filled"
    clock.advance(0.3)
    engine.tick()
    clock.advance(1.1)
    set_book(state, clock, ".85", ".14")
    engine.tick()
    pending = broker.db.active()
    assert len(pending) == 1
    assert pending[0]["strategy_context"]["action"] == "SELL_PARTIAL"
    assert D(pending[0]["spec"]["quantity"]) == 4
    broker.cancel(pending[0]["id"])
    clock.advance(1.1)
    set_book(state, clock, ".85", ".14")
    engine.tick()
    retry = broker.db.active()
    assert len(retry) == 1
    assert retry[0]["id"] != pending[0]["id"]
    clock.advance(0.11)
    broker.process()
    assert D(broker.db.positions()[0]["quantity"]) == 4
    clock.advance(1.1)
    set_book(state, clock, ".85", ".14")
    engine.tick()
    assert not broker.db.active()


@pytest.mark.parametrize(
    "settlement_time", [1802, "not-a-timestamp", float("nan"), float("inf"), 1799]
)
def test_settlement_requires_coherent_observed_timestamps(paper, settlement_time):
    broker, state, clock = paper
    execute(broker, clock, order("settlement-time-buy"))
    clock.now = 1801
    before = broker.db.account()
    official = dict(
        ticker="TEST-BTC",
        status="finalized",
        result="yes",
        settlement_ts=settlement_time,
        close_time=1800,
    )
    with pytest.raises(ValueError, match="settlement|timestamp"):
        broker.settle("TEST-BTC", official)
    assert broker.db.account() == before
    assert D(broker.db.positions()[0]["quantity"]) == 10


def test_adverse_cross_survives_engine_restart_and_cancelled_order(paper):
    from backend.app.strategy.engine import StrategyEngine

    broker, state, clock = paper
    engine = crossed(paper)
    pending = broker.db.active()[0]
    broker.cancel(pending["id"])
    restored = StrategyEngine(broker, engine.features)
    clock.advance(1.1)
    set_book(state, clock, ".36", ".62")
    restored.tick()
    retry = broker.db.active()
    assert len(retry) == 1 and retry[0]["id"] != pending["id"]
    clock.advance(0.11)
    broker.process()
    assert D(broker.db.positions()[0]["quantity"]) == 0


def test_emergency_obeys_model_limit_when_global_limit_is_larger(paper):
    broker, state, clock = paper
    engine, _ = setup_engine(paper)
    broker.limits = RiskLimits(max_quantity=150000, max_loss_per_trade=100)
    market = state.assets["BTC"]["market"]
    market["price_ranges"] = [dict(start="0", end="1", step=".0001")]
    from backend.app.markets.orderbook import KalshiBook

    for i in range(2):
        snapshot = KalshiBook()
        snapshot.snapshot([[".0001", "300000"]], [[".9998", "300000"]])
        state.assets["BTC"]["kalshi"]["book"] = snapshot.view()
        state.health("kalshi:BTC:book", "live", exchange_ts=clock())
        filled = execute(
            broker,
            clock,
            order(f"large-inventory-{i}", quantity="100000", limit_price=".0002", max_cost="25"),
        )
        assert filled["status"] == "filled"
    engine.control(emergency=True)
    clock.advance(1.1)
    engine.tick()
    pending = broker.db.active()[0]
    assert D(pending["spec"]["quantity"]) == 100000
    clock.advance(0.11)
    broker.process()
    assert D(broker.db.positions()[0]["quantity"]) == 100000


@pytest.mark.parametrize("fault", ["paused", "cutoff", "future_receipt", "future_fees"])
def test_conservative_marks_require_executable_market_and_timestamps(paper, fault):
    broker, state, clock = paper
    execute(broker, clock, order("mark-safety-buy"))
    market = state.assets["BTC"]["market"]
    if fault == "paused":
        market["status"] = "paused"
    elif fault == "cutoff":
        clock.now = market["close_time"] + 0.1
        market.update(fees_verified_at=clock(), fees_valid_until=clock() + 60)
        state.health("kalshi:BTC:book", "live", exchange_ts=clock())
    elif fault == "future_receipt":
        state.sources["kalshi:BTC:book"].update(received_at=clock() + 100, exchange_ts=None)
    else:
        market.update(fees_verified_at=clock() + 100, fees_valid_until=clock() + 160)
    portfolio = broker.portfolio()
    assert portfolio["equity_conservative"] == D(portfolio["cash"])


def test_conservative_marks_do_not_reuse_consumed_bids(paper):
    broker, state, clock = paper
    bought = execute(
        broker, clock, order("mark-depth-buy", quantity="20", limit_price=".42", max_cost="10")
    )
    assert bought["status"] == "filled"
    sold = execute(
        broker,
        clock,
        order("mark-depth-sell", action="sell", quantity="10", limit_price=".38", max_cost=None),
    )
    assert sold["status"] == "filled"
    portfolio = broker.portfolio()
    assert portfolio["equity_conservative"] == D(portfolio["cash"]) + D("3.53")


def test_unknown_fee_model_on_other_asset_does_not_strand_exits(paper):
    from test_paper import ready

    broker, state, clock = paper
    engine = entered(paper)
    ready(state, clock, "ETH", "UNKNOWN-FEE-ETH")
    state.assets["ETH"]["market"]["fee_type"] = "flat"
    clock.advance(1.1)
    set_book(state, clock, ".85", ".14")
    engine.tick()
    pending = broker.db.active()
    assert len(pending) == 1
    assert pending[0]["spec"]["asset"] == "BTC"
    clock.advance(0.11)
    broker.process()
    assert D(broker.db.positions()[0]["quantity"]) == 5
    assert not engine.views["ETH"]["entries"]["yes"]["eligible"]


def test_expired_quote_does_not_consume_entry_and_recovers_on_refresh(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    market = state.assets["BTC"]["market"]
    market["fees_valid_until"] = clock() - 1
    engine.tick()
    assert broker.db.connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    clock.advance(0.3)
    market.update(fees_verified_at=clock(), fees_valid_until=clock() + 60)
    engine.tick()
    pending = broker.db.active()
    assert len(pending) == 1
    clock.advance(0.11)
    broker.process()
    assert broker.db.order(pending[0]["id"])["status"] == "filled"


def test_reference_cross_trade_replays_exactly(recorded):
    from test_research import finish

    broker, state, clock, engine, indicators, recorder, path, cycle, profile = recorded
    engine.control(enabled=True, profile_name=profile.name)
    recorder.event("control", dict(enabled=True, profile_name=profile.name))
    for ts, value in [(1006, "79990"), (1007, "79995")]:
        clock.fixed = ts
        state.assets["BTC"]["market"]["current_index"] = value
        state.health("kalshi:BTC:reference", "live", exchange_ts=ts)
        cycle(ts)
    cycle(1007.2)
    engine.control(enabled=False)
    recorder.event("control", dict(enabled=False))
    for ts, value in [(1008, "80002"), (1009, "79999")]:
        clock.fixed = ts
        state.assets["BTC"]["market"]["current_index"] = value
        state.health("kalshi:BTC:reference", "live", exchange_ts=ts)
        cycle(ts)
    assert len(broker.db.active()) == 1
    cycle(1009.2)
    assert D(broker.db.positions()[0]["quantity"]) == 0
    assert finish(recorded)["exact_reproduction"]


@pytest.mark.parametrize(
    "source", ["kraken:BTC:book", "kraken:BTC:ticker", "kraken:BTC:trade", "kalshi:BTC:reference"]
)
def test_required_feed_recovery_rearms_eligible_entry(paper, source):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    state.health(source, "disconnected")
    engine.tick()
    assert broker.db.connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
    clock.advance(0.3)
    state.health(source, "live", exchange_ts=clock())
    engine.tick()
    pending = broker.db.active()
    assert len(pending) == 1
    clock.advance(0.11)
    broker.process()
    assert broker.db.order(pending[0]["id"])["status"] == "filled"
