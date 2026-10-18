from decimal import Decimal

from fastapi.testclient import TestClient

from backend.app.execution.configuration import ExecutionSettings
from backend.app.execution.models import PaperOrder
from backend.app.main import create_app
from backend.app.settings import Settings
from backend.app.state import State


def test_modes_require_external_live_source_not_worker():
    now = [1000.0]
    state = State(clock=lambda: now[0])
    state.health("paper:engine", "live")
    assert state.snapshot()["mode"] == "DISCONNECTED / PAUSED"
    state.health("kraken:BTC:trade", "live", exchange_ts=1000)
    assert state.snapshot()["mode"] == "LIVE DATA — PAPER TRADING"
    assert not state.snapshot()["execution_enabled"]
    now[0] += 16
    assert state.snapshot()["mode"] == "DISCONNECTED / PAUSED"
    state.replay = True
    assert state.snapshot()["mode"] == "REPLAY"


def test_runtime_reports_storage_and_recording_without_credentials(tmp_path):
    settings = Settings(
        key_id="PRIVATE_API_ID_MUST_NOT_APPEAR",
        private_key_path="/private/signing-file.pem",
        paper_database_path=str(tmp_path / "paper.sqlite3"),
    )
    with TestClient(create_app(settings, connect_live=False)) as client:
        response = client.get("/api/runtime")
        assert response.status_code == 200
        result = response.json()
        assert result["credentials"]["configured"] is True
        assert result["account"] == {"persistent": True, "file": "paper.sqlite3"}
        assert result["recorder"] == {"configured": False, "state": "off"}
        assert result["real_order_execution_enabled"] is False
        assert "PRIVATE_API_ID" not in response.text
        assert "/private" not in response.text
        assert str(tmp_path) not in response.text


def test_runtime_reports_actual_recorder_state(tmp_path):
    settings = Settings(
        paper_database_path=":memory:", recording_path=str(tmp_path / "capture.jsonl.gz")
    )
    app = create_app(settings, connect_live=False)
    with TestClient(app) as client:
        value = client.get("/api/runtime").json()
        assert value["account"]["persistent"] is False
        assert value["recorder"]["state"] == "recording"
        assert value["recorder"]["file"] == "capture.jsonl.gz"
        app.state.recorder.error = "Test-only writer failure"
        assert client.get("/api/runtime").json()["recorder"]["state"] == "failed"


def test_execution_settings_durable_and_no_balance_reset(tmp_path):
    settings = Settings(paper_database_path=str(tmp_path / "paper.sqlite3"))
    with TestClient(create_app(settings, connect_live=False)) as client:
        value = client.get("/api/settings/execution").json()
        value["version"] = 2
        value["execution"]["latency_ms"] = 700
        assert client.post("/api/settings/execution", json=value).status_code == 200
        assert client.post("/api/settings/execution", json=value).status_code == 409
        value["version"] = 3
        value["execution"]["starting_balance"] = "5000"
        assert client.post("/api/settings/execution", json=value).status_code == 409
        assert client.get("/api/paper/portfolio").json()["cash"] == "1000.00"
        assert set(client.get("/api/settings/schema").json()) == {
            "profile",
            "execution",
            "indicators",
        }
    with TestClient(create_app(settings, connect_live=False)) as client:
        assert client.get("/api/settings/execution").json()["execution"]["latency_ms"] == 700
        assert client.get("/api/paper/portfolio").json()["cash"] == "1000.00"


def test_settings_reject_open_positions_and_pending_orders():
    app = create_app(Settings(paper_database_path=":memory:"), connect_live=False)
    with TestClient(app) as client:
        value = client.get("/api/settings/execution").json()
        value["version"] = 2
        db = app.state.paper_broker.db
        with db.transaction():
            db.save_position(
                {
                    "asset": "BTC",
                    "ticker": "T",
                    "outcome": "yes",
                    "quantity": "1",
                    "cost_basis": ".4",
                    "close_time": 2000,
                }
            )
        assert client.post("/api/settings/execution", json=value).status_code == 409
        with db.transaction():
            db.save_position(
                {
                    "asset": "BTC",
                    "ticker": "T",
                    "outcome": "yes",
                    "quantity": "0",
                    "cost_basis": "0",
                    "close_time": 2000,
                }
            )
            spec = PaperOrder(
                client_order_id="test-dashboard",
                asset="BTC",
                ticker="T",
                action="buy",
                outcome="yes",
                quantity=1,
                limit_price=".4",
                max_cost=1,
            )
            db.save_order(
                {
                    "id": spec.client_order_id,
                    "status": "pending",
                    "spec": spec.model_dump(mode="json"),
                    "filled_quantity": "0",
                    "spent": "0",
                }
            )
        assert client.post("/api/settings/execution", json=value).status_code == 409
        assert ExecutionSettings.model_validate(value).execution.starting_balance == Decimal(1000)
