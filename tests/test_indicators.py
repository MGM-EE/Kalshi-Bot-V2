from decimal import Decimal as D

import pytest
from test_paper import paper as paper

from backend.app.indicators.choppiness import ConfirmedRisk, choppiness
from backend.app.indicators.config import IndicatorConfig
from backend.app.indicators.engine import IndicatorEngine
from backend.app.indicators.features import Features
from backend.app.indicators.liquidity import LiquidityPersistence
from backend.app.indicators.swing_risk import swing_risk
from backend.app.indicators.volatility import price_statistics
from backend.app.indicators.whale_flow import WhaleFlow


def test_prior_thresholds_do_not_include_current_trade():
    config = IndicatorConfig(
        fixed_enabled=False,
        median_enabled=False,
        burst_enabled=False,
        minimum_samples=3,
        percentile=".99",
    )
    flow = WhaleFlow(config, 999999)
    for ts in range(3):
        assert not flow.add(ts, 1, 10, 1)["large"]
    event = flow.add(3, 1, 1000, -1)
    assert event["prior_percentile"] == 10
    assert event["large"]
    assert flow.snapshot(3)["windows"]["5"]["net_large_usd"] == -1000
    assert flow.add(2, 1, 1000, 1) is None


def test_window_coverage_units_capacity_and_unknown():
    flow = WhaleFlow(IndicatorConfig(max_window_trades=100), 1)
    flow.add(0, 10, ".4", 1, 10)
    flow.add(5, 20, ".6", -1, 20)
    flow.add(5, 2, ".5", 0, 2)
    window = flow.snapshot(5)["windows"]["5"]
    assert window["complete"]
    assert window["net_large_quantity"] == -10
    assert window["net_large_usd"] == -8
    assert window["unknown_direction_count"] == 1
    assert not flow.snapshot(30)["fresh"]
    for ts in range(6, 110):
        flow.add(ts, 1, 1, 1)
    assert len(flow.events) == 100
    assert not flow.snapshot(109)["windows"]["300"]["complete"]
    assert flow.snapshot(109)["windows"]["5"]["complete"]


def test_persistence_resets_after_gap_and_cause_unknown():
    wall = LiquidityPersistence(100)
    wall.update([[10, 10]], [], 0)
    wall.update([[10, 12]], [], 2)
    snapshot = wall.snapshot(2)
    assert snapshot["bid_persistence_seconds"] == 2
    assert snapshot["levels"][0]["replenishments"] == 1
    wall.update([], [], 3)
    assert "unknown" in wall.snapshot(3)["events"][0]["reason"]
    wall.update([[10, 10]], [], 4)
    wall.update([[10, 10]], [], 8)
    assert wall.snapshot(8)["bid_persistence_seconds"] == 0
    assert not wall.snapshot(12)["fresh"]
    wall.invalidate()
    assert not wall.snapshot(8)["levels"]


def test_volatility_chop_swing_and_confirmed_hysteresis():
    flat = price_statistics([(i, D(100)) for i in range(16)], 15)
    assert flat["realized_volatility_percent"] == 0
    assert flat["trend_efficiency"] == 1
    trend = price_statistics([(i, D(100 + i)) for i in range(16)], 15)
    noisy = price_statistics([(i, D(100 + i % 2)) for i in range(16)], 15)
    config = IndicatorConfig()
    a = choppiness(trend, 0, D(0), D(1), config.chop_weights)
    b = choppiness(noisy, 6, D(1), D(1), config.chop_weights)
    assert 0 <= a["score"] < b["score"] <= 100
    score = swing_risk(D(0), D(100), 900, noisy, D(".5"), D(".1"), D(20), config.swing_weights)
    assert 0 <= score["score"] <= 100
    assert price_statistics([(0, D(100)), *[(i, D(100)) for i in range(10, 16)]], 15) is None
    risk = ConfirmedRisk(seconds=2)
    assert risk.update(70, 0) == "CONFIRMING"
    assert risk.update(70, 2) == "HIGH"
    assert risk.update(60, 3) == "HIGH"
    assert risk.update(54, 4) == "HIGH"
    assert risk.update(54, 6) == "CAUTION"
    assert risk.update(None, 7) == "INSUFFICIENT DATA"


def test_engine_dedup_disconnect_and_polling_not_complete(paper):
    broker, state, clock = paper
    features = Features(state)
    engine = IndicatorEngine(state, features, broker.db)
    state.observers.append(engine.observe)
    config = engine.config.model_dump(mode="json")
    config.update(version=2, flow_window_seconds=5)
    config["assets"]["BTC"]["spot_fixed_usd"] = "1"
    config["assets"]["BTC"]["kalshi_fixed_contracts"] = "1"
    engine.save_config(config)
    for i in (0, 5):
        clock.advance(i)
        state.assets["BTC"]["kraken"]["trades"] = [
            dict(id=str(i), exchange_ts=clock(), quantity="2", price="100", aggressor_side="buy")
        ]
        state.health("kraken:BTC:trade", "live", exchange_ts=clock())
        state.assets["BTC"]["kalshi"]["trades"] = [
            dict(
                trade_id=str(i),
                ts_ms=clock() * 1000,
                count_fp="10",
                yes_price_dollars=".4",
                no_price_dollars=".6",
                taker_outcome_side="no",
            )
        ]
        state.health("kalshi:BTC:trade", "polling", exchange_ts=clock())
    metrics = engine.metrics("BTC", "yes", clock())
    assert metrics["whale_flow"] == 400
    assert "kalshi_whale_flow" not in metrics
    assert metrics["directional_agreement"] == "INSUFFICIENT DATA"
    state.health("kalshi:BTC:trade", "live", exchange_ts=clock())
    assert "kalshi_whale_flow" not in engine.metrics("BTC", "yes", clock())
    for i in range(2):
        clock.advance(5)
        state.assets["BTC"]["kraken"]["trades"] = [
            dict(
                id=f"live-{i}", exchange_ts=clock(), quantity="2", price="100", aggressor_side="buy"
            )
        ]
        state.health("kraken:BTC:trade", "live", exchange_ts=clock())
        row = state.assets["BTC"]["kalshi"]["trades"][0].copy()
        row.update(trade_id=f"live-{i}", ts_ms=clock() * 1000)
        state.assets["BTC"]["kalshi"]["trades"] = [row]
        state.health("kalshi:BTC:trade", "live", exchange_ts=clock())
    metrics = engine.metrics("BTC", "yes", clock())
    assert metrics["kalshi_whale_flow"] == -20
    assert metrics["directional_agreement"] == "DIVERGENT"
    state.health("kraken:BTC:trade", "disconnected")
    assert "whale_flow" not in engine.metrics("BTC", "yes", clock())
    with pytest.raises(ValueError, match="increase"):
        engine.save_config(config)
    restored = IndicatorEngine(state, features, broker.db)
    assert restored.config.version == 2


def test_indicator_api_validation():
    from fastapi.testclient import TestClient

    from backend.app.main import create_app
    from backend.app.settings import Settings

    with TestClient(
        create_app(Settings(paper_database_path=":memory:"), connect_live=False)
    ) as client:
        config = client.get("/api/indicators/config").json()
        config["version"] = 2
        assert client.post("/api/indicators/config", json={"config": config}).status_code == 200
        config["chop_weights"] = [1, 2, 3, 4]
        assert client.post("/api/indicators/config", json={"config": config}).status_code == 422
