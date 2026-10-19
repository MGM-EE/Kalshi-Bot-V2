"""Fault cases for subscription isolation, recovery deadlines, and REST generations."""

import asyncio
import json
import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from websockets.asyncio.server import serve

from backend.app.connectors.kalshi import Kalshi
from backend.app.markets.orderbook import BookError
from backend.app.settings import ASSETS, Settings
from backend.app.state import State


class Socket:
    def __init__(self):
        self.sent = []

    async def send(self, frame):
        self.sent.append(json.loads(frame))


def connector_for(*assets, **settings):
    state = State()
    for asset in assets or ("BTC",):
        state.assets[asset]["market"] = {
            "ticker": asset + "-CURRENT",
            "asset": asset,
            "close_time": time.time() + 900,
            "event_ticker": asset + "-EVENT",
        }
    return Kalshi(state, replace(Settings(), **settings))


def snapshot(ticker="BTC-CURRENT", sid=7, seq=1, quantity="5"):
    return {
        "type": "orderbook_snapshot",
        "sid": sid,
        "seq": seq,
        "msg": {
            "market_ticker": ticker,
            "yes_dollars_fp": [["0.4", quantity]],
            "no_dollars_fp": [["0.5", "8"]],
        },
    }


def delta(ticker="BTC-CURRENT", sid=7, seq=2):
    return {
        "type": "orderbook_delta",
        "sid": sid,
        "seq": seq,
        "msg": {
            "market_ticker": ticker,
            "side": "yes",
            "price_dollars": "0.4",
            "delta_fp": "1",
        },
    }


def acknowledge(connector, request_id, channel, sid):
    connector.handle(
        {
            "type": "subscribed",
            "id": request_id,
            "msg": {"channel": channel, "sid": sid},
        }
    )


async def initialize(connector):
    ws = Socket()
    connector.session_active = connector.socket_open = True
    await connector._subscribe(ws, ["ticker", "trade", "orderbook_delta"])
    await connector._subscribe(ws, ["cfbenchmarks_value"])
    await connector._subscribe(ws, ["market_lifecycle_v2"])
    for channel, sid in (("ticker", 5), ("trade", 6), ("orderbook_delta", 7)):
        acknowledge(connector, 1, channel, sid)
    acknowledge(connector, 2, "cfbenchmarks_value", 8)
    acknowledge(connector, 3, "market_lifecycle_v2", 9)
    connector.subscriptions["cfbenchmarks_value"]["indexlist_requested"] = True
    return ws


async def test_open_socket_without_book_keeps_rest_fallback():
    connector = connector_for()
    connector.stream_live = True  # The old global gate incorrectly suppressed all polling.
    calls = []

    async def respond(request):
        calls.append(request.url.path)
        if request.url.path.endswith("orderbook"):
            return httpx.Response(
                200,
                json={
                    "orderbook_fp": {"yes_dollars": [["0.4", "12"]], "no_dollars": []},
                },
            )
        return httpx.Response(200, json={"trades": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await connector.poll_asset(client, "BTC", "BTC-CURRENT")
    assert len(calls) == 2
    assert connector.rest_books["BTC-CURRENT"].valid
    assert "BTC-CURRENT" not in connector.books
    assert connector.state.sources["kalshi:BTC:book"]["status"] == "polling"


async def test_late_rest_snapshot_cannot_overwrite_stream_or_its_delta_base():
    connector = connector_for()
    requested, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if request.url.path.endswith("orderbook"):
            requested.set()
            await release.wait()
            return httpx.Response(
                200,
                json={
                    "orderbook_fp": {"yes_dollars": [["0.4", "100"]], "no_dollars": []},
                },
            )
        return httpx.Response(200, json={"trades": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        pending = asyncio.create_task(connector.poll_asset(client, "BTC", "BTC-CURRENT"))
        await asyncio.wait_for(requested.wait(), 1)
        connector.handle(snapshot())
        connector.handle(delta())
        release.set()
        await pending
    connector.handle(delta(seq=3))
    assert connector.books["BTC-CURRENT"].yes[Decimal("0.4")] == 7
    assert connector.rest_books["BTC-CURRENT"].yes[Decimal("0.4")] == 100
    assert connector.state.assets["BTC"]["kalshi"]["book"]["yes_bids"][0][1] == 7
    assert connector.state.sources["kalshi:BTC:book"]["status"] == "live"


async def test_rest_failure_arriving_after_stream_recovery_preserves_live_book():
    connector = connector_for()
    requested, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        requested.set()
        await release.wait()
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        pending = asyncio.create_task(connector._poll_book(client, "BTC", "BTC-CURRENT"))
        await asyncio.wait_for(requested.wait(), 1)
        connector.handle(snapshot())
        release.set()
        await pending
    assert connector.books["BTC-CURRENT"].valid
    assert connector.state.sources["kalshi:BTC:book"]["status"] == "live"


async def test_missing_ack_repairs_its_channel_while_other_frames_continue():
    connector = connector_for()
    ws = Socket()
    connector.session_active = connector.socket_open = True
    await connector._subscribe(ws, ["ticker", "trade", "orderbook_delta"])
    await connector._subscribe(ws, ["cfbenchmarks_value"])
    for channel, sid in (("ticker", 5), ("trade", 6)):
        acknowledge(connector, 1, channel, sid)
    acknowledge(connector, 2, "cfbenchmarks_value", 8)
    connector.subscriptions["cfbenchmarks_value"]["indexlist_requested"] = True
    connector.handle({"type": "ticker", "sid": 5, "msg": {"market_ticker": "BTC-CURRENT"}})
    connector.requests[1]["deadline"] = time.monotonic() - 1
    await connector._watchdog(ws)
    assert ws.sent[-1]["params"]["channels"] == ["orderbook_delta"]
    assert connector.subscriptions["cfbenchmarks_value"]["sid"] == 8
    assert not connector.book_ready("BTC-CURRENT")


async def test_missing_snapshot_recovery_is_bounded_despite_successful_acks():
    connector = connector_for()
    ws = await initialize(connector)
    for attempt in range(3):
        connector.awaiting_snapshots["BTC-CURRENT"] = time.monotonic() - 1
        if attempt == 2:
            with pytest.raises(TimeoutError, match="recovery exhausted"):
                await connector._watchdog(ws)
        else:
            await connector._watchdog(ws)
            request_id = connector.subscriptions["orderbook_delta"]["request_id"]
            acknowledge(connector, request_id, "orderbook_delta", 20 + attempt)
    assert connector.subscriptions["cfbenchmarks_value"]["sid"] == 8


async def test_sid_sequence_covers_removed_markets_and_control_responses():
    connector = connector_for("BTC", "ETH")
    await initialize(connector)
    connector.handle(snapshot())
    connector.handle(snapshot("ETH-CURRENT", seq=2))
    connector.handle({"type": "ok", "sid": 7, "seq": 3})
    connector.handle(delta("REMOVED-MARKET", seq=4))
    connector.handle(delta(seq=5))
    assert connector.books["BTC-CURRENT"].yes[Decimal("0.4")] == 6
    with pytest.raises(BookError, match="sequence gap"):
        connector.handle(delta(seq=7))
    assert not connector.books["BTC-CURRENT"].valid
    assert not connector.books["ETH-CURRENT"].valid
    assert connector.subscriptions["cfbenchmarks_value"]["acked"]


async def test_snapshot_repair_is_deduplicated_and_completes_without_separate_ok():
    connector = connector_for()
    ws = await initialize(connector)
    connector.handle(snapshot())
    with pytest.raises(BookError):
        connector.handle(delta(seq=4))
    await connector._book_snapshot(ws, {"BTC-CURRENT"})
    for seq in range(5, 10):
        with pytest.raises(BookError):
            connector.handle(delta(seq=seq))
        await connector._book_snapshot(ws, {"BTC-CURRENT"})
    repairs = [r for r in ws.sent if r["params"].get("action") == "get_snapshot"]
    assert len(repairs) == 1
    connector.handle(snapshot(seq=10, quantity="20"))
    assert repairs[0]["id"] not in connector.requests
    assert "BTC-CURRENT" not in connector.awaiting_snapshots
    connector.handle(delta(seq=11))
    assert connector.books["BTC-CURRENT"].yes[Decimal("0.4")] == 21


async def test_cf_refusal_does_not_disconnect_executable_books():
    connector = connector_for()
    ws = Socket()
    await connector._subscribe(ws, ["ticker", "trade", "orderbook_delta"])
    await connector._subscribe(ws, ["cfbenchmarks_value"])
    acknowledge(connector, 1, "orderbook_delta", 7)
    connector.handle(snapshot())
    connector.handle({"id": 2, "type": "error", "msg": {"code": 10}})
    connector.handle(delta())
    assert connector.books["BTC-CURRENT"].valid
    assert connector.state.sources["kalshi:BTC:reference"]["status"] == "unavailable"
    assert connector.state.sources["kalshi:BTC:book"]["status"] == "live"


async def test_silent_cf_index_repairs_without_resetting_other_indexes_or_books():
    connector = connector_for()
    ws = await initialize(connector)
    connector.handle(snapshot())
    now = time.monotonic()
    connector.reference_received = {"BTC": now, "ETH": now - 10, "SOL": now}
    await connector._watchdog(ws)
    await connector._watchdog(ws)
    repairs = [
        r
        for r in ws.sent
        if r["params"].get("action")
        in {
            "subscribe_indices",
            "unsubscribe_indices",
        }
    ]
    assert len(repairs) == 2
    assert all(r["params"]["index_ids"] == ["ETHUSD_RTI"] for r in repairs)
    assert connector.books["BTC-CURRENT"].valid
    assert connector.state.sources["kalshi:ETH:reference"]["status"] == "disconnected"


async def test_official_cf_indexlist_response_identifies_missing_capability():
    connector = connector_for()
    ws = await initialize(connector)
    connector.subscriptions["cfbenchmarks_value"].pop("indexlist_requested")
    await connector._watchdog(ws)
    request = ws.sent[-1]
    connector.handle(
        {
            "type": "cfbenchmarks_value_indexlist",
            "id": request["id"],
            "sid": 8,
            "seq": 1,
            "msg": {"index_ids": ["BRTI"]},
        }
    )
    await connector._watchdog(ws)
    assert connector.available_indexes == {"BRTI"}
    assert request["id"] not in connector.requests
    assert (
        connector.state.sources["kalshi:ETH:reference"]["detail"]
        == "Index unavailable on this account"
    )


async def test_failed_trade_subscription_accepts_its_rest_fallback():
    connector = connector_for()
    await initialize(connector)
    connector.subscriptions["trade"]["failed"] = True

    def respond(request):
        return httpx.Response(200, json={"trades": [{"trade_id": "recover-1", "count_fp": "3"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await connector._poll_trades(client, "BTC", "BTC-CURRENT")
    assert connector.state.assets["BTC"]["kalshi"]["trades"][0]["trade_id"] == "recover-1"
    assert connector.state.sources["kalshi:BTC:trade"]["status"] == "polling"


async def test_rollover_reconciles_market_subscriptions_without_resetting_cf():
    connector = connector_for()
    ws = await initialize(connector)
    connector.handle(snapshot())
    old_cf = connector.subscriptions["cfbenchmarks_value"].copy()
    connector.state.assets["BTC"]["market"] = {
        **connector.state.assets["BTC"]["market"],
        "ticker": "BTC-NEXT",
    }
    connector.revision += 1
    await connector._watchdog(ws)
    commands = [r for r in ws.sent if r["cmd"] == "update_subscription"]
    assert len(commands) == 6
    assert {r["params"]["action"] for r in commands} == {"add_markets", "delete_markets"}
    assert connector.subscriptions["cfbenchmarks_value"] == old_cf
    assert not connector.book_ready("BTC-NEXT")


async def test_metadata_hang_does_not_starve_fallback_for_any_asset(monkeypatch):
    import backend.app.connectors.kalshi as module

    connector = connector_for(*ASSETS, rest_seconds=0.1)
    blocked = asyncio.Event()
    completed = set()
    original_client = httpx.AsyncClient

    async def respond(request):
        if "/series/" in request.url.path:
            await blocked.wait()
        if request.url.path.endswith("orderbook"):
            ticker = request.url.path.split("/")[-2]
            completed.add(ticker)
            return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
        return httpx.Response(200, json={"trades": []})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    task = asyncio.create_task(connector.rest_loop())
    try:
        async with asyncio.timeout(2):
            while len(completed) < 3:
                await asyncio.sleep(0.01)
        assert all(
            connector.state.sources[f"kalshi:{asset}:book"]["status"] == "polling"
            for asset in ASSETS
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_fee_response_survives_same_market_refresh_but_never_crosses_rollover():
    connector = connector_for()
    series = {"ticker": "SERIES", "fee_type": "quadratic", "fee_multiplier": "1"}
    market = connector.state.assets["BTC"]["market"]
    requested, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        if request.url.path.endswith("/events/BTC-EVENT"):
            requested.set()
            await release.wait()
            return httpx.Response(200, json={"event": {}})
        if "/series/" in request.url.path:
            return httpx.Response(200, json={"series_fee_change_arr": []})
        return httpx.Response(200, json={"event_fee_changes": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        pending = asyncio.create_task(connector.effective_fees(client, market, series, guard=True))
        await requested.wait()
        refreshed = dict(market)
        connector.state.assets["BTC"]["market"] = refreshed
        release.set()
        await pending
        assert refreshed["fees_verified_at"] > 0
        requested.clear()
        release.clear()
        pending = asyncio.create_task(
            connector.effective_fees(client, refreshed, series, guard=True)
        )
        await requested.wait()
        successor = {**refreshed, "ticker": "BTC-NEXT", "fees_verified_at": None}
        connector.state.assets["BTC"]["market"] = successor
        connector.fee_due["BTC"] = time.monotonic() + 30
        release.set()
        await pending
        assert successor["fees_verified_at"] is None
        assert connector.fee_due["BTC"] == 0


async def test_real_transport_repairs_missing_snapshot_while_ticker_traffic_continues(monkeypatch):
    import backend.app.connectors.kalshi as module

    connector = connector_for(subscription_timeout_seconds=0.08, reference_stale_seconds=100)
    connector.signer = SimpleNamespace(headers=lambda *args: {})
    repaired = asyncio.Event()
    received = []
    connections = 0

    async def server(ws):
        nonlocal connections
        connections += 1
        for _ in range(3):
            received.append(json.loads(await ws.recv()))
        for request_id, channel, sid in (
            (1, "ticker", 5),
            (1, "trade", 6),
            (1, "orderbook_delta", 7),
            (2, "cfbenchmarks_value", 8),
            (3, "market_lifecycle_v2", 9),
        ):
            await ws.send(
                json.dumps(
                    {
                        "type": "subscribed",
                        "id": request_id,
                        "msg": {"channel": channel, "sid": sid},
                    }
                )
            )

        async def keep_ticker_active():
            while True:
                await ws.send(
                    json.dumps(
                        {"type": "ticker", "sid": 5, "msg": {"market_ticker": "BTC-CURRENT"}}
                    )
                )
                await asyncio.sleep(0.01)

        traffic = asyncio.create_task(keep_ticker_active())
        try:
            async for frame in ws:
                request = json.loads(frame)
                received.append(request)
                if request["cmd"] == "subscribe":
                    assert request["params"]["channels"] == ["orderbook_delta"]
                    await ws.send(
                        json.dumps(
                            {
                                "type": "subscribed",
                                "id": request["id"],
                                "msg": {"channel": "orderbook_delta", "sid": 10},
                            }
                        )
                    )
                    await ws.send(json.dumps(snapshot(sid=10)))
                    repaired.set()
                elif request["params"].get("action") == "indexlist":
                    await ws.send(
                        json.dumps(
                            {
                                "type": "cfbenchmarks_value_indexlist",
                                "id": request["id"],
                                "sid": 8,
                                "msg": {"index_ids": ["BRTI", "ETHUSD_RTI", "SOLUSD_RTI"]},
                            }
                        )
                    )
        finally:
            traffic.cancel()
            await asyncio.gather(traffic, return_exceptions=True)

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        monkeypatch.setattr(module, "KALSHI_WS", f"ws://127.0.0.1:{port}")
        task = asyncio.create_task(connector.websocket_session())
        try:
            await asyncio.wait_for(repaired.wait(), 2)
            async with asyncio.timeout(1):
                while not connector.book_ready("BTC-CURRENT"):
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            assert connections == 1
            assert connector.subscriptions["cfbenchmarks_value"]["sid"] == 8
            assert any(r["cmd"] == "unsubscribe" and r["params"] == {"sids": [7]} for r in received)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert not connector.socket_open
    assert not connector.state.assets["BTC"]["kalshi"]["book"]["valid"]


async def test_send_deadline_covers_a_stuck_socket_write():
    connector = connector_for(subscription_timeout_seconds=0.02)

    class StuckSocket:
        async def send(self, frame):
            await asyncio.Event().wait()

    async with asyncio.timeout(0.5):
        with pytest.raises(TimeoutError):
            await connector._send(StuckSocket(), "subscribe", {}, ["orderbook_delta"])


async def test_books_invalidate_before_transport_close_can_stall(monkeypatch):
    import backend.app.connectors.kalshi as module

    connector = connector_for()
    connector.signer = SimpleNamespace(headers=lambda *args: {})
    closing, finish_close = asyncio.Event(), asyncio.Event()

    class FailingSocket(Socket):
        def __init__(self):
            super().__init__()
            self.frames = [
                {"id": 1, "type": "subscribed", "msg": {"channel": "orderbook_delta", "sid": 7}},
                snapshot(),
            ]

        async def recv(self):
            if self.frames:
                return json.dumps(self.frames.pop(0))
            raise OSError("transport lost")

    class Connection:
        async def __aenter__(self):
            return FailingSocket()

        async def __aexit__(self, *args):
            closing.set()
            await finish_close.wait()

    monkeypatch.setattr(module, "connect", lambda *args, **kwargs: Connection())
    task = asyncio.create_task(connector.websocket_session())
    try:
        await asyncio.wait_for(closing.wait(), 1)
        assert not connector.socket_open
        assert not connector.state.assets["BTC"]["kalshi"]["book"]["valid"]
        assert connector.state.sources["kalshi:BTC:book"]["status"] == "disconnected"
    finally:
        finish_close.set()
        result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], OSError)


async def test_queued_snapshot_uses_exchange_send_time_and_requests_fallback():
    connector = connector_for()
    connector.handle({**snapshot(), "sending_ts_ms": int((time.time() - 60) * 1000)})
    assert not connector.book_ready("BTC-CURRENT")
    health = connector.state.snapshot()["sources"]["kalshi:BTC:book"]
    assert health["status"] == "stale"
    assert health["exchange_age_seconds"] >= 59


async def test_index_capability_is_refreshed_and_recovers_without_restarting():
    connector = connector_for()
    ws = await initialize(connector)
    cf = connector.subscriptions["cfbenchmarks_value"]
    cf.pop("indexlist_requested")
    await connector._watchdog(ws)
    request = ws.sent[-1]
    connector.handle(
        {
            "type": "cfbenchmarks_value_indexlist",
            "id": request["id"],
            "sid": 8,
            "msg": {"index_ids": ["BRTI", "SOLUSD_RTI"]},
        }
    )
    await connector._watchdog(ws)
    assert connector.state.sources["kalshi:ETH:reference"]["status"] == "unavailable"
    cf["indexlist_refresh_at"] = time.monotonic() - 1
    now = time.monotonic()
    connector.reference_received = {"BTC": now, "ETH": now - 40, "SOL": now}
    await connector._watchdog(ws)
    request = ws.sent[-1]
    assert request["params"]["action"] == "indexlist"
    connector.handle(
        {
            "type": "cfbenchmarks_value_indexlist",
            "id": request["id"],
            "sid": 8,
            "msg": {"index_ids": ["BRTI", "ETHUSD_RTI", "SOLUSD_RTI"]},
        }
    )
    await connector._watchdog(ws)
    assert ws.sent[-1]["params"]["action"] == "subscribe_indices"
    assert ws.sent[-1]["params"]["index_ids"] == ["ETHUSD_RTI"]
    assert connector.state.sources["kalshi:ETH:reference"]["status"] == "disconnected"


async def test_late_rest_trade_error_does_not_hide_recovered_stream():
    connector = connector_for()
    ws = Socket()
    requested, release = asyncio.Event(), asyncio.Event()
    connector.socket_open = True
    await connector._subscribe(ws, ["trade"])

    async def respond(request):
        requested.set()
        await release.wait()
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        pending = asyncio.create_task(connector._poll_trades(client, "BTC", "BTC-CURRENT"))
        await requested.wait()
        acknowledge(connector, 1, "trade", 6)
        connector.handle(
            {
                "type": "trade",
                "sid": 6,
                "msg": {"market_ticker": "BTC-CURRENT", "trade_id": "stream-1"},
            }
        )
        release.set()
        await pending
    assert connector.state.sources["kalshi:BTC:trade"]["status"] == "live"


async def test_rest_rate_limit_retry_after_applies_to_next_workload(monkeypatch):
    import backend.app.connectors.kalshi as module

    connector = connector_for()
    started = []
    monkeypatch.setattr(
        module, "retry_delay", lambda failures, exc: float(exc.response.headers["Retry-After"])
    )

    def respond(request):
        started.append(time.monotonic())
        if len(started) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.08"})
        return httpx.Response(200, json={"markets": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await connector.get(client, "/series/FIRST")
        await connector.get(client, "/markets/NEXT/orderbook")
    assert started[1] - started[0] >= 0.075


@pytest.mark.parametrize("failure", ["rejected", "timeout"])
async def test_targeted_index_repair_failure_preserves_other_references(failure):
    connector = connector_for()
    ws = await initialize(connector)
    connector.handle(snapshot())
    now = time.monotonic()
    connector.reference_received = {"BTC": now, "ETH": now - 40, "SOL": now}
    for asset in ("BTC", "SOL"):
        connector.state.health(f"kalshi:{asset}:reference", "live", "CF Benchmarks index stream")
    await connector._watchdog(ws)
    targeted = [
        r
        for r in ws.sent
        if r["params"].get("action")
        in {
            "subscribe_indices",
            "unsubscribe_indices",
        }
    ]
    for request in targeted:
        if failure == "rejected":
            connector.handle({"type": "error", "id": request["id"], "msg": {"code": 13}})
        else:
            connector.requests[request["id"]]["deadline"] = time.monotonic() - 1
    if failure == "timeout":
        await connector._watchdog(ws)
    assert not connector.subscriptions["cfbenchmarks_value"].get("failed")
    assert connector.subscriptions["cfbenchmarks_value"]["sid"] == 8
    assert connector.state.sources["kalshi:ETH:reference"]["status"] == "unavailable"
    assert all(
        connector.state.sources[f"kalshi:{asset}:reference"]["status"] == "live"
        for asset in ("BTC", "SOL")
    )
    assert connector.books["BTC-CURRENT"].valid
    connector.reference_retry_at["ETH"] = time.monotonic() - 1
    await connector._watchdog(ws)
    assert ws.sent[-1]["params"] == {
        "sid": 8,
        "index_ids": ["ETHUSD_RTI"],
        "action": "subscribe_indices",
    }


@pytest.mark.parametrize("code", [10, 25])
async def test_terminal_cf_error_recreates_cf_subscription_and_keeps_books(code):
    connector = connector_for()
    ws = await initialize(connector)
    connector.handle(snapshot())
    request_id = await connector._send(
        ws,
        "update_subscription",
        {"sid": 8, "index_ids": ["ETHUSD_RTI"], "action": "subscribe_indices"},
        ["cfbenchmarks_value"],
        "subscribe_indices",
    )
    connector.handle({"type": "error", "id": request_id, "sid": 8, "msg": {"code": code}})
    await connector._watchdog(ws)
    assert ws.sent[-1]["cmd"] == "subscribe"
    assert ws.sent[-1]["params"]["channels"] == ["cfbenchmarks_value"]
    assert connector.books["BTC-CURRENT"].valid
    assert connector.state.sources["kalshi:BTC:book"]["status"] == "live"
