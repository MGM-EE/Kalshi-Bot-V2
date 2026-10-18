from decimal import Decimal

import pytest
from test_paper import execute, order
from test_paper import paper as paper

from backend.app.indicators.features import Features
from backend.app.markets.orderbook import KalshiBook
from backend.app.strategy.conditions import evaluate_rules
from backend.app.strategy.engine import StrategyEngine
from backend.app.strategy.entry import evaluate_entry
from backend.app.strategy.exit import evaluate_exit
from backend.app.strategy.profiles import (
    EntrySettings,
    ExitSettings,
    Profile,
    Rule,
    Sizing,
    presets,
)
from backend.app.strategy.store import StrategyStore

D = Decimal


def setup_engine(paper):
    broker, state, clock = paper
    features = Features(state)
    state.observers.append(features.observe)
    engine = StrategyEngine(broker, features)
    profile = Profile(
        name="TEST STRATEGY",
        entry=EntrySettings(
            earliest_remaining_seconds=1000,
            latest_remaining_seconds=0,
            minimum_remaining_after_entry=0,
            confirmation_seconds=0,
            reentry_cooldown_seconds=0,
            max_spread=".1",
            rules=(
                Rule(metric="losing_side", threshold=1),
                Rule(metric="approach_positive", threshold=1),
            ),
        ),
        sizing=Sizing(amount="4", maximum_quantity="10"),
        exit=ExitSettings(require_reversal_at_target=False),
    )
    engine.store.save_profile(profile)
    state.assets["BTC"]["market"]["opening_reference"] = "80000"
    set_book(state, clock, ".36", ".62")
    for value in ("79990", "79995"):
        cf(state, clock, value)
        clock.advance(1)
    return engine, profile


def cf(state, clock, value):
    state.assets["BTC"]["market"]["current_index"] = value
    state.health("kalshi:BTC:reference", "live", exchange_ts=clock())


def set_book(state, clock, yes, no):
    book = KalshiBook()
    book.snapshot([[yes, "100"]], [[no, "100"]])
    state.assets["BTC"]["kalshi"]["book"] = book.view()
    state.health("kalshi:BTC:book", "live", exchange_ts=clock())


def test_baseline_immutable_recovered_rules_only(paper):
    store = StrategyStore(paper[0].db)
    baseline = presets()[0]
    assert baseline.entry.latest_remaining_seconds == 120
    assert baseline.entry.earliest_remaining_seconds == 600
    assert baseline.entry.max_all_in_price == D(".40")
    assert {r.metric for r in baseline.entry.rules} == {"losing_side", "approach_positive"}
    assert "unavailable" in baseline.description
    with pytest.raises(ValueError, match="immutable"):
        store.save_profile(baseline.model_copy(update={"version": 2}))
    custom = Profile(name="CUSTOM V2", version=2)
    store.save_profile(custom)
    with pytest.raises(ValueError, match="immutable"):
        store.save_profile(custom.model_copy(update={"description": "changed"}))
    assert store.get_profile("CUSTOM V2", 2).identifier == custom.identifier


@pytest.mark.parametrize(
    "remaining,eligible", [(119.99, False), (120, True), (600, True), (600.01, False)]
)
def test_baseline_time_window_and_fee_cap(remaining, eligible):
    profile = presets()[0]
    estimate = {
        "quantity": D(10),
        "ask": D(".38"),
        "all_in_price": D(".397"),
        "spread": D(".02"),
        "depth": D(10),
        "slippage": D(0),
    }
    result = evaluate_entry(
        profile, {"losing_side": 1, "approach_positive": 1}, remaining, estimate
    )
    assert result["eligible"] is eligible
    assert not evaluate_entry(
        profile,
        {"losing_side": 1, "approach_positive": 1},
        remaining,
        {**estimate, "all_in_price": D(".401")},
    )["eligible"]


def test_unknown_required_cannot_be_bypassed_by_weighted_score():
    rules = [
        Rule(metric="momentum", threshold=0, weight=100),
        Rule(metric="whale_flow", threshold=0, weight=1),
    ]
    result = evaluate_rules(rules, {"momentum": 10}, "WEIGHTED", 10)
    assert result["score"] > 90
    assert not result["eligible"]
    assert result["conditions"][1]["state"] == "gray"
    soft = rules[1].model_copy(update={"required": False})
    assert evaluate_rules([rules[0], soft], {"momentum": 10}, "WEIGHTED", 10)["eligible"]
    assert not evaluate_rules([rules[0], soft], {"momentum": 10}, "WEIGHTED", 99.9)["eligible"]


def test_colored_thresholds_and_confirmation():
    rule = Rule(
        metric="ask", operator="max", threshold=".4", warning_band=".03", confirmation_seconds=1
    )
    assert evaluate_rules([rule], {"ask": ".42"})["conditions"][0]["state"] == "yellow"
    assert evaluate_rules([rule], {"ask": ".44"})["conditions"][0]["state"] == "red"
    assert evaluate_rules([rule], {"ask": ".39"})["conditions"][0]["state"] == "yellow"
    assert evaluate_rules([rule], {"ask": ".39"}, confirmation={"ask": True})["eligible"]


def test_pause_enable_fill_no_duplicates_and_restart(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.tick()
    assert not broker.db.active()
    engine.control(enabled=True, profile_name=profile.name)
    clock.advance(0.3)
    engine.tick()
    assert len(broker.db.active()) == 1
    spec = broker.db.active()[0]["spec"]
    clock.advance(0.11)
    broker.process()
    assert broker.db.order(spec["client_order_id"])["status"] == "filled"
    assert broker.db.positions()[0]["strategy_context"]["profile"]["name"] == profile.name
    for _ in range(3):
        clock.advance(0.3)
        engine.tick()
        broker.process()
    assert len(broker.portfolio()["fills"]) == 1
    restored = StrategyEngine(broker, engine.features)
    clock.advance(0.3)
    restored.tick()
    broker.process()
    assert len(broker.portfolio()["fills"]) == 1


def test_revalidation_after_latency_and_pause_cancels_pending(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    engine.tick()
    pending = broker.db.active()[0]
    cf(state, clock, "80005")
    clock.advance(0.11)
    broker.process()
    assert broker.db.order(pending["id"])["status"] == "rejected"
    assert broker.portfolio()["open_positions"] == 0
    engine.control(enabled=False)
    assert not broker.db.active()


def test_profile_change_keeps_position_criteria_partial_target_is_once(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    engine.tick()
    clock.advance(0.11)
    broker.process()
    engine.control(enabled=False, profile_name="CONSERVATIVE", profile_version=1)
    original = broker.db.positions()[0]["strategy_context"]["profile"]
    assert original["name"] == profile.name
    clock.advance(0.3)
    set_book(state, clock, ".85", ".14")
    engine.tick()
    assert len(broker.db.active()) == 1
    assert broker.db.active()[0]["spec"]["action"] == "sell"
    clock.advance(0.11)
    broker.process()
    assert D(broker.db.positions()[0]["quantity"]) == 5
    clock.advance(0.3)
    engine.tick()
    assert not broker.db.active()
    assert broker.db.positions()[0]["strategy_context"]["profile"] == original


def test_exit_priority_cutoff_and_optional_hold():
    profile = Profile(name="TEST")
    position = {"ticker": "T", "quantity": "10", "cost_basis": "4", "opened_at": 0}
    market = {"ticker": "T", "status": "active", "close_time": 900}
    estimate = {"quantity": D(10), "bid": D(".9"), "cost": D("8.8")}
    assert (
        evaluate_exit(profile, position, market, estimate, {}, {}, 100, emergency=True)["priority"]
        == 1
    )
    assert (
        evaluate_exit(profile, position, market, estimate, {}, {}, 900, emergency=True)["action"]
        == "WAIT_SETTLEMENT"
    )
    assert (
        evaluate_exit(profile, position, market, estimate, {}, {}, 100, hard_risk=True)["priority"]
        == 3
    )
    assert (
        evaluate_exit(profile, position, market, {**estimate, "cost": D(-2)}, {}, {}, 100)["action"]
        == "CUT_LOSS"
    )
    assert (
        evaluate_exit(
            profile, position, market, estimate, {}, {"peak_bid": ".95", "peak_profit": "5"}, 100
        )["action"]
        == "PROTECT_PROFIT"
    )
    held = profile.model_copy(
        update={"exit": ExitSettings(hold_to_settlement=True, trailing_price_enabled=False)}
    )
    assert (
        evaluate_exit(held, position, market, estimate, {}, {}, 100)["action"]
        == "HOLD_TO_SETTLEMENT"
    )


def test_partial_entry_cannot_break_actual_all_in_cap(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    engine.tick()
    pending = broker.db.active()[0]
    state.assets["BTC"]["kalshi"]["book"]["yes_asks"][0][1] = D(".01")
    clock.advance(0.11)
    broker.process()
    assert broker.db.order(pending["id"])["status"] == "rejected"
    assert not broker.portfolio()["fills"]


def test_intent_recovery_reuses_exact_client_id(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    engine.tick()
    original = broker.db.active()[0]
    with broker.db.transaction():
        broker.db.connection.execute("UPDATE strategy_intents SET status='pending'")
    engine.dispatch()
    assert [o["id"] for o in broker.db.active()] == [original["id"]]


def test_multiple_round_trips_use_new_ids_and_persist_counts(paper):
    broker, state, clock = paper
    engine, profile = setup_engine(paper)
    engine.control(enabled=True, profile_name=profile.name)
    engine.tick()
    first = broker.db.active()[0]["id"]
    clock.advance(0.11)
    broker.process()
    execute(
        broker,
        clock,
        order("manual-cycle-exit", action="sell", quantity="10", limit_price=".36", max_cost=None),
    )
    assert broker.db.round("TEST-BTC")["round_trips"] == 1
    clock.advance(1)
    cf(state, clock, "79997")
    engine.tick()
    second = broker.db.active()[0]["id"]
    assert second != first
    clock.advance(0.11)
    broker.process()
    assert broker.db.round("TEST-BTC")["entries"] == 2
    assert broker.db.round("TEST-BTC")["round_trips"] == 1


def test_emergency_blocks_and_cancels_manual_pending_buys(paper):
    broker, _, _ = paper
    engine, _ = setup_engine(paper)
    assert broker.submit(order("manual-before-stop"))["status"] == "pending"
    engine.control(emergency=True)
    assert broker.db.order("manual-before-stop")["status"] == "cancelled"
    assert broker.submit(order("manual-after-stop"))["status"] == "rejected"


def test_profile_control_api_and_validation():
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.settings import Settings

    with TestClient(
        create_app(Settings(paper_database_path=":memory:"), connect_live=False)
    ) as client:
        response = client.get("/api/strategy/profiles")
        assert len(response.json()["profiles"]) == 5
        profile = Profile(name="CUSTOM V2", version=2)
        assert (
            client.post("/api/strategy/profiles", json=profile.model_dump(mode="json")).status_code
            == 200
        )
        baseline = presets()[0].model_dump(mode="json")
        baseline["version"] = 2
        assert client.post("/api/strategy/profiles", json=baseline).status_code == 409
        controlled = client.post(
            "/api/strategy/control",
            json={"enabled": True, "profile_name": "CUSTOM V2", "profile_version": 2},
        )
        assert controlled.status_code == 200
        assert client.get("/api/health").json()["execution_enabled"] is True
        assert client.get("/api/paper/portfolio").json()["fills"] == []
