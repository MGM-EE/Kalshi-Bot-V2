"""Launcher ownership: repeated opens must not create/kill another backend."""

import asyncio
import io
import json
import socket
import sys

import pytest
import uvicorn

from scripts import launch


def test_windows_server_command_resolves_to_a_selector_loop():
    command = launch.backend_command("win32")
    factory_name = command[command.index("--loop") + 1]
    # Resolve through the installed Uvicorn, rather than just checking a string.
    factory = uvicorn.Config("backend.app.main:app", loop=factory_name).get_loop_factory()
    loop = factory()
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
    finally:
        loop.close()


def test_non_windows_launch_keeps_uvicorn_default_loop():
    assert "--loop" not in launch.backend_command("linux")


class TestSocket:
    __test__ = False

    def __init__(self, occupied):
        self.occupied = occupied

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def bind(self, _):
        if self.occupied:
            raise OSError("Already bound")


def test_repeated_open_reuses_healthy_terminal_without_starting_or_stopping_it(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launch.py"])
    monkeypatch.setattr(socket, "socket", lambda: TestSocket(True))
    monkeypatch.setattr(
        launch.urllib.request,
        "urlopen",
        lambda *a, **k: io.BytesIO(
            json.dumps(
                {"application": "ok", "paper_api_available": True, "real_order_execution_enabled": False}
            ).encode()
        ),
    )
    opened = []
    monkeypatch.setattr(launch.webbrowser, "open", opened.append)

    def unexpected_child(*a, **k):
        pytest.fail("A second backend must not be started")

    monkeypatch.setattr(launch.subprocess, "Popen", unexpected_child)
    launch.main()
    assert opened == ["http://127.0.0.1:8000"]


def test_other_service_on_port_is_not_treated_as_this_terminal(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launch.py"])
    monkeypatch.setattr(socket, "socket", lambda: TestSocket(True))
    monkeypatch.setattr(launch.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"[]"))
    monkeypatch.setattr(launch.webbrowser, "open", lambda *_: pytest.fail("Wrong service"))
    with pytest.raises(SystemExit, match="Port 8000 is occupied"):
        launch.main()


def test_recording_does_not_silently_reuse_an_existing_normal_account(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launch.py", "--record", "pytest_unused_session"])
    monkeypatch.setattr(socket, "socket", lambda: TestSocket(True))
    monkeypatch.setattr(launch.urllib.request, "urlopen", lambda *_: pytest.fail("Do not reuse"))
    with pytest.raises(SystemExit, match="recorded session needs its own fresh launch"):
        launch.main()


def test_backend_crash_is_reported_as_a_launch_failure(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launch.py"])
    monkeypatch.setattr(socket, "socket", lambda: TestSocket(False))

    class Ready(io.BytesIO):
        status = 200

    monkeypatch.setattr(launch.urllib.request, "urlopen", lambda *a, **k: Ready(b"{}"))
    monkeypatch.setattr(launch.webbrowser, "open", lambda *_: None)

    class Child:
        result = None

        def poll(self):
            return self.result

        def wait(self):
            self.result = 2
            return 2

    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: Child())
    with pytest.raises(SystemExit, match="exit code 2"):
        launch.main()
