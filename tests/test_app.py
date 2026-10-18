import time
from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.settings import Settings
from backend.app.state import State


def test_health_staleness_exchange_age_and_clock_drift(monkeypatch):
    now = 1000
    monkeypatch.setattr(time, "time", lambda: now)
    state = State(stale_seconds=15)
    state.health("fresh", "live", exchange_ts=1000)
    state.health("old_exchange", "live", exchange_ts=900)
    state.health("future", "live", exchange_ts=1010)
    state.health("missing", "unavailable")
    state.health("polled", "polling")
    assert state.snapshot()["sources"]["old_exchange"]["status"] == "stale"
    assert state.snapshot()["sources"]["future"]["status"] == "clock_drift"
    now += 16
    assert state.snapshot()["sources"]["fresh"]["status"] == "stale"
    assert state.snapshot()["sources"]["polled"]["status"] == "stale"
    assert state.snapshot()["sources"]["missing"]["status"] == "unavailable"
    assert not state.snapshot()["execution_enabled"]


def test_read_only_app_http_and_dashboard_stream():
    app = create_app(Settings(paper_database_path=":memory:"), connect_live=False)
    app.state.market_state.assets["BTC"]["kraken"]["ticker"] = {"last": Decimal("100000.123456")}
    with TestClient(app) as client:
        assert client.get("/api/health").json()["application"] == "ok"
        assert "PAPER ONLY" in client.get("/").text
        assert (
            client.get("/api/state").json()["assets"]["BTC"]["kraken"]["ticker"]["last"]
            == "100000.123456"
        )
        assert client.post("/api/orders", json={"action": "buy"}).status_code == 404
        with client.websocket_connect("/api/stream", headers={"origin": "http://testserver"}) as ws:
            snapshot = ws.receive_json()
            assert snapshot["execution_enabled"] is False
            assert snapshot["assets"]["ETH"]["market"] is None


def test_dashboard_stream_rejects_foreign_origin():
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with TestClient(
        create_app(Settings(paper_database_path=":memory:"), connect_live=False)
    ) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/stream", headers={"origin": "https://other.example"}
            ):
                pass


def test_dashboard_snapshot_error_is_not_hidden_as_a_disconnect():
    import pytest

    app = create_app(Settings(paper_database_path=":memory:"), connect_live=False)

    def broken_snapshot():
        raise RuntimeError("Snapshot failed")

    with TestClient(app) as client:
        app.state.market_state.snapshot = broken_snapshot
        with pytest.raises(RuntimeError, match="Snapshot failed"):
            with client.websocket_connect("/api/stream") as ws:
                ws.receive_json()
