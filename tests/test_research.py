import gzip
import json
from decimal import Decimal as D

import pytest
from test_paper import order, ready
from test_strategy import set_book

from backend.app.execution.configuration import initialize
from backend.app.execution.models import PaperConfig, RiskLimits
from backend.app.execution.paper_broker import PaperBroker
from backend.app.indicators.engine import IndicatorEngine
from backend.app.indicators.features import Features
from backend.app.research.analytics import analytics
from backend.app.research.clock import EventClock
from backend.app.research.recorder import Recorder
from backend.app.research.replay import replay
from backend.app.state import State
from backend.app.storage.database import Database
from backend.app.strategy.engine import StrategyEngine
from backend.app.strategy.profiles import EntrySettings, ExitSettings, Profile, Rule, Sizing


@pytest.fixture
def recorded(tmp_path):
    clock = EventClock(1000.0)
    state = State(clock=clock)
    db = Database(":memory:")
    config = PaperConfig(latency_ms=100, slippage_dollars="0", depth_participation="1")
    broker = PaperBroker(db, state, config, clock=clock)
    initialize(
        db,
        dict(execution=config.model_dump(mode="json"), risk=RiskLimits().model_dump(mode="json")),
    )
    features = Features(state)
    indicators = IndicatorEngine(state, features, db)
    features.advanced = indicators
    engine = StrategyEngine(broker, features)
    profile = Profile(
        name="REPLAY TEST",
        entry=EntrySettings(
            earliest_remaining_seconds=1000,
            latest_remaining_seconds=0,
            minimum_remaining_after_entry=0,
            confirmation_seconds=0,
            max_spread=".1",
            rules=(
                Rule(metric="losing_side", threshold=1),
                Rule(metric="approach_positive", threshold=1),
            ),
        ),
        exit=ExitSettings(require_reversal_at_target=False),
        sizing=Sizing(amount="4", maximum_quantity="10"),
    )
    engine.store.save_profile(profile)
    path = tmp_path / "recording.jsonl.gz"
    recorder = Recorder(path, state, broker, engine, indicators)
    state.record_event = recorder.event
    state.observers.extend([features.observe, indicators.observe, recorder.observe])
    ready(state, clock)
    state.assets["BTC"]["market"]["opening_reference"] = "80000"
    set_book(state, clock, ".36", ".62")

    def cycle(ts):
        clock.fixed = ts
        recorder.event("cycle", {})
        state.in_cycle = True
        try:
            engine.tick()
            if db.active():
                broker.process()
        finally:
            state.in_cycle = False

    yield broker, state, clock, engine, indicators, recorder, path, cycle, profile
    state.record_event = None
    recorder.close()
    db.close()


def finish(recorded):
    broker, state, clock, engine, indicators, recorder, path, cycle, profile = recorded
    state.record_event = None
    recorder.close()
    assert recorder.error is None
    return replay(path)


def test_record_manual_partial_exit_settlement_and_exact_replay(recorded):
    broker, state, clock, engine, indicators, recorder, path, cycle, _ = recorded
    assert broker.submit(order("record-buy-1"))["status"] == "pending"
    cycle(1000.2)
    assert broker.portfolio()["open_positions"] == 1
    set_book(state, clock, ".6", ".38")
    broker.submit(
        order("record-sell-1", action="sell", quantity="4", limit_price=".6", max_cost=None)
    )
    cycle(1000.4)
    assert D(broker.db.positions()[0]["quantity"]) == 6
    clock.fixed = 1801
    broker.settle(
        "TEST-BTC",
        dict(
            ticker="TEST-BTC", status="finalized", result="yes", settlement_ts=1801, close_time=1800
        ),
    )
    # Reads also affect conservative account peaks and are replayed.
    broker.portfolio()
    summary = analytics(broker.db)
    assert summary["net_closed_pnl"] == D("4.36")
    assert summary["fees"] == D(".24")
    assert summary["win_rate"] == 1
    assert summary["closed_trades"][0]["entry_price"] == D(".38")
    assert summary["total_closed_trades"] == 1
    assert summary["closed_trades"][0]["settled"]
    assert summary["closed_trades"][0]["pnl"] == D(broker.db.account()["realized_pnl"])
    result = finish(recorded)
    assert result["exact_reproduction"] is True
    assert result["analytics"]["total_closed_trades"] == 1
    assert replay(path)["ledger_sha256"] == result["ledger_sha256"]
    with pytest.raises(ValueError, match="manual"):
        replay(path, Profile(name="COMPARISON").model_dump(mode="json"))


def test_strategy_no_lookahead_comparison_and_partial_target(recorded):
    broker, state, clock, engine, indicators, recorder, path, cycle, profile = recorded
    engine.control(enabled=True, profile_name=profile.name)
    recorder.event("control", dict(enabled=True, profile_name=profile.name))
    for ts, value in [(1006, "79990"), (1007, "79995")]:
        clock.fixed = ts
        state.assets["BTC"]["market"]["current_index"] = value
        state.health("kalshi:BTC:reference", "live", exchange_ts=ts)
        cycle(ts)
    cycle(1007.2)
    assert len(broker.db.fills()) == 1
    clock.fixed = 1008
    set_book(state, clock, ".85", ".14")
    cycle(1008)
    cycle(1008.2)
    assert len(broker.db.fills()) == 2
    assert D(broker.db.positions()[0]["quantity"]) == 5
    broker.portfolio()
    result = finish(recorded)
    assert result["exact_reproduction"]
    # Future price improvements never affect the earlier entry fill.
    assert result["fills"][-1]["price"] == ".38" or D(result["fills"][-1]["price"]) == D(".38")
    strict = profile.model_copy(
        update={
            "name": "STRICT REPLAY",
            "entry": profile.entry.model_copy(update={"max_all_in_price": D(".1")}),
        }
    )
    other = replay(path, strict.model_dump(mode="json"), period="validation")
    assert other["fills"] == []
    assert other["dataset_sha256"] == result["dataset_sha256"]
    assert other["period"] == "validation"
    assert other["exact_reproduction"] is None


def test_truncated_tampered_and_wrong_version_recording_rejected(recorded, tmp_path):
    _, _, _, _, _, _, path, cycle, _ = recorded
    cycle(1000)
    finish(recorded)
    lines = gzip.open(path, "rt").readlines()
    cases = [
        ("truncated", lines[:-1], "Incomplete"),
        (
            "version",
            [json.dumps({**json.loads(lines[0]), "code": "bad"}) + "\n", *lines[1:]],
            "version",
        ),
    ]
    event = json.loads(lines[1])
    event["seq"] = 99
    cases.append(("tampered", [lines[0], json.dumps(event) + "\n", *lines[2:]], "sequence"))
    for name, data, reason in cases:
        changed = tmp_path / f"{name}.jsonl.gz"
        with gzip.open(changed, "wt") as out:
            out.writelines(data)
        with pytest.raises(ValueError, match=reason):
            replay(changed)


def test_rollover_deletes_old_venue_state_and_trade_append(recorded):
    broker, state, clock, engine, indicators, recorder, path, cycle, _ = recorded
    for i in range(2):
        state.assets["BTC"]["kraken"]["trades"] = [
            dict(
                id=j,
                exchange_ts=1000,
                price="100",
                quantity="1",
                signed_quantity="1",
                aggressor_side="buy",
            )
            for j in range(i + 1)
        ]
        state.health("kraken:BTC:trade", "live", exchange_ts=1000)
    cycle(1000)
    clock.fixed = 1001
    state.assets["BTC"]["kalshi"] = {}
    state.assets["BTC"]["market"] = None
    before = {key: vars(risk).copy() for key, risk in indicators.risk_states.items()}
    indicators.snapshot(1007)
    indicators.snapshot(1009)
    assert {key: vars(risk).copy() for key, risk in indicators.risk_states.items()} == before
    state.health("kalshi:BTC:book", "disconnected")
    cycle(1001)
    assert finish(recorded)["exact_reproduction"]


def test_real_app_recording_lifecycle_settings_and_reads(tmp_path):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.settings import Settings

    path = tmp_path / "app.jsonl.gz"
    settings = Settings(
        paper_database_path=str(tmp_path / "live.sqlite3"), recording_path=str(path)
    )
    with TestClient(create_app(settings, connect_live=False)) as client:
        assert client.get("/api/research/recordings").json()["recorder"]["state"] == "recording"
        assert client.post("/api/research/replay", json={"file": path.name}).status_code == 409
        assert (
            client.post(
                "/api/strategy/control",
                json={"enabled": False, "profile_name": "BALANCED", "profile_version": 1},
            ).status_code
            == 200
        )
        config = client.get("/api/indicators/config").json()
        config["version"] = 2
        assert client.post("/api/indicators/config", json={"config": config}).status_code == 200
        execution = client.get("/api/settings/execution").json()
        execution["version"] = 2
        execution["execution"]["latency_ms"] = 300
        assert client.post("/api/settings/execution", json=execution).status_code == 200
        client.get("/api/state")
        client.get("/api/paper/portfolio")
        assert client.get("/api/research/analytics").json()["win_rate"] is None
    result = replay(path)
    assert result["exact_reproduction"]
    # API replay keeps the current account untouched and refuses traversal.
    other = create_app(
        Settings(paper_database_path=":memory:", recording_path=""), connect_live=False
    )
    with TestClient(other) as client:
        assert (
            client.post("/api/research/replay", json={"file": "../app.jsonl.gz"}).status_code == 422
        )


def test_recording_overflow_never_blocks_paper_execution(recorded, monkeypatch):
    from queue import Full

    broker, state, clock, engine, indicators, recorder, path, cycle, _ = recorded

    def fail(_):
        raise Full

    monkeypatch.setattr(recorder.queue, "put_nowait", fail)
    recorder.event("cycle", {})
    assert "overflow" in recorder.status()["error"]
    assert broker.submit(order("overflow-buy"))["status"] == "pending"
    cycle(1000.2)
    assert len(broker.db.fills()) == 1
    state.record_event = None
    recorder.close()
    with pytest.raises(ValueError, match="Incomplete"):
        replay(path)


def test_risk_scores_require_fresh_book(recorded):
    broker, state, clock, engine, indicators, recorder, path, cycle, _ = recorded
    for ts in range(1000, 1008):
        clock.fixed = ts
        state.assets["BTC"]["kraken"]["ticker"] = {"last": str(80000 + ts % 2)}
        state.health("kraken:BTC:ticker", "live", exchange_ts=ts)
        state.assets["BTC"]["market"]["current_index"] = str(80001 + ts % 2)
        state.health("kalshi:BTC:reference", "live", exchange_ts=ts)
    assert indicators.asset_snapshot("BTC", 1007)["swing_risk"]["yes"]["value"] is not None
    before = {key: vars(risk).copy() for key, risk in indicators.risk_states.items()}
    indicators.snapshot(1007)
    indicators.snapshot(1009)
    assert {key: vars(risk).copy() for key, risk in indicators.risk_states.items()} == before
    state.health("kalshi:BTC:book", "disconnected")
    assert indicators.asset_snapshot("BTC", 1007)["swing_risk"]["yes"]["value"] is None
    assert finish(recorded)["exact_reproduction"]


def test_portfolio_reads_are_pure_and_reject_stale_exchange_marks(recorded):
    broker, state, clock, engine, indicators, recorder, path, cycle, _ = recorded
    broker.submit(order("readonly-portfolio"))
    cycle(1000.2)
    before = broker.db.account()
    assert broker.portfolio()["equity_conservative"] > D(before["cash"])
    assert broker.db.account() == before
    state.health("kalshi:BTC:book", "live", exchange_ts=900)
    assert broker.portfolio()["equity_conservative"] == D(before["cash"])
    assert broker.db.account() == before
    state.health("kalshi:BTC:book", "live", exchange_ts=clock())
    state.assets["BTC"]["market"]["fees_valid_until"] = clock() - 1
    state.health("kalshi:BTC:fees", "unavailable")
    assert broker.portfolio()["equity_conservative"] == D(before["cash"])
    assert finish(recorded)["exact_reproduction"]
