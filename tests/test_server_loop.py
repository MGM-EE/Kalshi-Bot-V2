"""Exercise the Windows-selected Uvicorn loop through a real isolated child.

Runs on Linux too, but native Windows accept/signal acceptance requires Windows.
No exchange connectors or credentials are loaded by the probe application.
"""

import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from websockets.sync.client import connect

from backend.app.research.replay import replay
from scripts.launch import backend_command


@pytest.mark.parametrize("record", [False, True], ids=["normal", "recorded"])
def test_selector_server_accepts_repeated_connections_and_stops_cleanly(tmp_path, record):
    probe = tmp_path / "loop_probe.py"
    probe.write_text(
        "import asyncio, os\n"
        "from backend.app.main import create_app\n"
        "from backend.app.settings import Settings\n"
        "app = create_app(Settings(paper_database_path=os.environ['PAPER_DATABASE_PATH'], "
        "recording_path=os.environ.get('RECORDING_PATH', '')), connect_live=False)\n"
        "@app.get('/_test_loop')\n"
        "async def test_loop():\n"
        "    return {'selector': isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)}\n"
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = backend_command("win32")
    command[command.index("backend.app.main:app")] = "loop_probe:app"
    command[command.index("--port") + 1] = str(port)
    command += ["--app-dir", str(tmp_path)]
    environment = os.environ.copy()
    environment["PAPER_DATABASE_PATH"] = str(tmp_path / "account.sqlite3")
    environment.pop("RECORDING_PATH", None)
    recording = tmp_path / "recording.jsonl.gz"
    if record:
        environment["RECORDING_PATH"] = str(recording)
    root = Path(__file__).resolve().parents[1]
    base = f"http://127.0.0.1:{port}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=2) as response:
            return json.load(response)

    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                assert process.poll() is None, log_path.read_text()
                try:
                    assert get("/_test_loop")["selector"] is True
                    break
                except (urllib.error.URLError, TimeoutError):
                    if time.monotonic() > deadline:
                        pytest.fail("Selector server did not start: " + log_path.read_text())
                    time.sleep(0.05)
            # Repeated fresh TCP/WebSocket accepts and full client disconnects.
            for _ in range(12):
                assert get("/api/health")["real_order_execution_enabled"] is False
                with connect(
                    f"ws://127.0.0.1:{port}/api/stream", open_timeout=2, proxy=None
                ) as ws:
                    snapshot = json.loads(ws.recv(timeout=3))
                    assert snapshot["paper"]["mode"] == "PAPER ONLY"
                    assert snapshot["execution_enabled"] is False
                    assert get("/_test_loop")["selector"] is True
            assert get("/api/runtime")["recorder"]["state"] == ("recording" if record else "off")
        finally:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                pytest.fail("Selector server did not shut down cleanly")
    output = log_path.read_text()
    assert "Application shutdown complete" in output, output
    assert "Task exception was never retrieved" not in output, output
    assert "Accept failed" not in output, output
    if record:
        assert replay(recording)["exact_reproduction"] is True
