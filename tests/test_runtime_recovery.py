import asyncio
import json

import httpx
import pytest

from backend.app.connectors.common import retry, retry_delay
from backend.app.dashboard import DashboardSnapshots
from backend.app.runtime import supervise
from backend.app.state import State


def test_retry_is_prompt_bounded_and_respects_server_delay(monkeypatch):
    monkeypatch.setattr("backend.app.connectors.common.random.uniform", lambda *_: 1)
    assert retry_delay(1, RuntimeError()) == 0.5
    assert retry_delay(100, RuntimeError()) == 30
    error = RuntimeError()
    error.retry_after = 5
    assert retry_delay(1, error) == 5
    response = httpx.Response(
        429, headers={"Retry-After": "45"}, request=httpx.Request("GET", "https://example.test")
    )
    assert retry_delay(1, httpx.HTTPStatusError("private", request=response.request,
                                             response=response)) == 45


async def test_retry_invalidates_and_recovers_without_exposing_error(monkeypatch):
    state, attempts, delays, invalidated = State(), [], [], []
    ready = asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(delay):
        delays.append(delay)
        await real_sleep(0)

    async def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("PRIVATE_SIGNING_PATH_MUST_NOT_APPEAR")
        ready.set()
        await asyncio.Future()

    monkeypatch.setattr("backend.app.connectors.common.asyncio.sleep", sleep)
    task = asyncio.create_task(retry(state, "test:socket", operation,
                                     lambda: invalidated.append(1)))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert len(attempts) == 2 and invalidated == [1]
        assert 0.4 <= delays[0] <= 0.6
        assert "PRIVATE" not in json.dumps(state.snapshot())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_worker_supervisor_restarts_crash_and_normal_exit(monkeypatch):
    state, attempts, invalidated, status = State(), [], [], {}
    ready = asyncio.Event()
    real_sleep = asyncio.sleep

    async def sleep(_):
        await real_sleep(0)

    async def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("PRIVATE_CONFIG_VALUE")
        if len(attempts) == 2:
            return
        ready.set()
        await asyncio.Future()

    monkeypatch.setattr("backend.app.runtime.asyncio.sleep", sleep)
    task = asyncio.create_task(supervise(state, "test", operation, status,
                                         lambda: invalidated.append(1)))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert len(attempts) == 3 and len(invalidated) == 2
        assert status == {"state": "running", "restarts": 2}
        assert "PRIVATE" not in json.dumps(state.snapshot())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert status["state"] == "stopped" and len(attempts) == 3


def test_dashboard_shares_projection_and_never_refreshes_a_failed_frame():
    clock, calls = [0.0], []
    state = State()
    snapshot = state.snapshot

    def capture():
        calls.append(1)
        return snapshot()

    state.snapshot = capture
    cache = DashboardSnapshots(state, clock=lambda: clock[0])
    initial = cache.frame()
    for _ in range(10):
        assert cache.frame() == initial
    assert len(calls) == 1
    state.assets["BTC"]["kraken"]["ticker"] = {"last": "100"}
    clock[0] = 0.3
    updated = json.loads(cache.frame())
    assert len(calls) == 2
    assert updated["stream_version"]["sequence"] == 2
    assert updated["assets"]["BTC"]["kraken"]["ticker"]["last"] == "100"

    def fail():
        raise RuntimeError("Provider failed")

    state.snapshot = fail
    clock[0] = 0.6
    with pytest.raises(RuntimeError, match="Provider failed"):
        cache.frame()
    assert cache.sequence == 2 and cache.produced_at == 0.3
    state.snapshot = capture
    assert json.loads(cache.frame())["stream_version"]["sequence"] == 3


async def test_supervisor_cancellation_during_backoff_is_stopped(monkeypatch):
    sleeping, status = asyncio.Event(), {}

    async def operation():
        raise OSError("worker failed")

    async def sleep(_):
        sleeping.set()
        await asyncio.Future()

    monkeypatch.setattr("backend.app.runtime.asyncio.sleep", sleep)
    task = asyncio.create_task(supervise(State(), "test", operation, status))
    await asyncio.wait_for(sleeping.wait(), 1)
    assert status["state"] == "restarting"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert status == {"state": "stopped", "restarts": 1}


async def test_supervisor_survives_cleanup_and_health_callback_errors(monkeypatch):
    state, status, attempts = State(), {}, []
    health = state.health
    ready = asyncio.Event()
    real_sleep = asyncio.sleep

    def health_failure_once(*args):
        state.health = health
        raise ValueError("PRIVATE_OBSERVER_VALUE")

    state.health = health_failure_once

    def cleanup():
        raise OSError("PRIVATE_CLEANUP_VALUE")

    async def operation():
        attempts.append(1)
        ready.set()
        await asyncio.Future()

    async def sleep(_):
        await real_sleep(0)

    monkeypatch.setattr("backend.app.runtime.asyncio.sleep", sleep)
    task = asyncio.create_task(supervise(state, "test", operation, status, cleanup))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert attempts == [1] and status["restarts"] == 1
        assert "PRIVATE" not in json.dumps(state.snapshot())
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
