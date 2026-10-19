"""Real local sockets exercise deadlines without relying on an exchange account."""

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from backend.app.connectors.kraken import Kraken, KrakenUnavailable
from backend.app.markets.orderbook import BookError
from backend.app.settings import ASSETS
from backend.app.state import State


def book(asset, timestamp=None):
    row = {
        "symbol": f"{asset}/USD",
        "bids": [{"price": "100.00", "qty": "2.500"}],
        "asks": [{"price": "101.00", "qty": "1.250"}],
        "checksum": 3654185688,
    }
    if timestamp:
        row["timestamp"] = timestamp
    return {"channel": "book", "type": "snapshot", "data": [row]}


def ticker(asset):
    return {
        "channel": "ticker",
        "type": "snapshot",
        "data": [{"symbol": f"{asset}/USD", "bid": "100", "ask": "101", "last": "100"}],
    }


def connector():
    return Kraken(
        State(),
        SimpleNamespace(
            subscription_timeout_seconds=0.15,
            snapshot_verify_seconds=0.15,
            channel_stale_seconds=0.15,
        ),
    )


async def subscriptions(ws, omit_ack=None):
    requests = [json.loads(await ws.recv()) for _ in range(3)]
    for request in requests:
        channel = request["params"]["channel"]
        for symbol in request["params"]["symbol"]:
            if (symbol.split("/")[0], channel) == omit_ack:
                continue
            await ws.send(
                json.dumps(
                    {
                        "method": "subscribe",
                        "success": True,
                        "req_id": request["req_id"],
                        "result": {"channel": channel, "symbol": symbol},
                    }
                )
            )


async def snapshots(ws, omit_book=None, stamp=None):
    for asset in ASSETS:
        await ws.send(json.dumps(ticker(asset)))
        if asset != omit_book:
            await ws.send(json.dumps(book(asset, stamp)))


async def background_traffic(ws, *, omit_book=None, frames=None):
    with suppress(ConnectionClosed):
        while True:
            await ws.send(json.dumps({"channel": "heartbeat"}))
            for asset in ASSETS:
                await ws.send(json.dumps(ticker(asset)))
                if asset != omit_book:
                    await ws.send(json.dumps(book(asset)))
            if frames is not None:
                frames.append(True)
            await asyncio.sleep(0.01)


def point_at(listener, monkeypatch):
    port = listener.sockets[0].getsockname()[1]
    monkeypatch.setattr("backend.app.connectors.kraken.KRAKEN_WS", f"ws://127.0.0.1:{port}")


@pytest.mark.parametrize("missing", ["snapshot", "ack"])
async def test_other_feeds_and_heartbeats_cannot_hide_missing_startup_data(monkeypatch, missing):
    client = connector()
    traffic = []

    async def server(ws):
        await subscriptions(ws, ("SOL", "book") if missing == "ack" else None)
        await snapshots(ws, omit_book="SOL" if missing == "snapshot" else None)
        await background_traffic(
            ws, omit_book="SOL" if missing == "snapshot" else None, frames=traffic
        )

    async with serve(server, "127.0.0.1", 0) as listener:
        point_at(listener, monkeypatch)
        error = "snapshot timed out" if missing == "snapshot" else "acknowledgement timed out"
        with pytest.raises(BookError, match=error):
            await asyncio.wait_for(client.session(), 2)

    assert len(traffic) > 2
    assert all(not value.valid for value in client.books.values())
    assert client.state.sources["kraken:socket"]["status"] == "disconnected"


async def test_one_stalled_book_reconnects_while_other_feeds_stay_busy(monkeypatch):
    client = connector()
    traffic = []

    async def server(ws):
        await subscriptions(ws)
        await snapshots(ws)
        await background_traffic(ws, omit_book="SOL", frames=traffic)

    async with serve(server, "127.0.0.1", 0) as listener:
        point_at(listener, monkeypatch)
        with pytest.raises(BookError, match="feed stopped updating"):
            await asyncio.wait_for(client.session(), 2)

    assert len(traffic) > 2
    assert all(not value.valid for value in client.books.values())


async def test_quiet_trade_tape_does_not_reconnect_and_cancellation_cleans_up(monkeypatch):
    client = connector()
    ready = asyncio.Event()

    async def server(ws):
        await subscriptions(ws)
        await snapshots(ws)
        ready.set()
        await background_traffic(ws)

    async with serve(server, "127.0.0.1", 0) as listener:
        point_at(listener, monkeypatch)
        task = asyncio.create_task(client.session())
        try:
            await asyncio.wait_for(ready.wait(), 1)
            # Several stale windows with no trades, but valid market data continues.
            await asyncio.sleep(client.stale_timeout * 3)
            assert not task.done()
            assert not client._pending_ack
            assert not client._pending_snapshot
            assert all(value.valid for value in client.books.values())
            assert client.state.sources["kraken:BTC:trade"]["status"] == "connecting"
            assert "awaiting a fresh trade" in client.state.sources["kraken:BTC:trade"]["detail"]
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert all(not value.valid for value in client.books.values())
    assert not any(task.get_name().startswith("kraken:") for task in asyncio.all_tasks())


async def test_checksum_failure_closes_then_restart_accepts_a_new_snapshot_baseline(monkeypatch):
    client = connector()
    restarted = asyncio.Event()
    connections = 0

    async def server(ws):
        nonlocal connections
        connections += 1
        await subscriptions(ws)
        if connections == 1:
            await snapshots(ws, stamp="2026-10-18T12:00:01Z")
            await ws.send(
                json.dumps(
                    {
                        "channel": "book",
                        "type": "update",
                        "data": [
                            {
                                "symbol": "BTC/USD",
                                "bids": [{"price": "100.00", "qty": "1"}],
                                "checksum": 0,
                                "timestamp": "2026-10-18T12:00:02Z",
                            }
                        ],
                    }
                )
            )
            await ws.wait_closed()
        else:
            # A restarted exchange can supply a fresh snapshot with an older baseline.
            await snapshots(ws, stamp="2026-10-18T11:59:59Z")
            restarted.set()
            await ws.wait_closed()

    async with serve(server, "127.0.0.1", 0) as listener:
        point_at(listener, monkeypatch)
        with pytest.raises(BookError, match="checksum mismatch"):
            await asyncio.wait_for(client.session(), 1)
        assert not client.books["BTC"].valid
        task = asyncio.create_task(client.session())
        try:
            await asyncio.wait_for(restarted.wait(), 1)
            for _ in range(100):
                if all(value.valid for value in client.books.values()):
                    break
                await asyncio.sleep(0.001)
            assert all(value.valid for value in client.books.values())
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def test_wrong_request_or_symbol_acknowledgement_does_not_satisfy_subscription():
    client = connector()
    client._pending_ack[("BTC", "book")] = 0
    for request_id, symbol in [(1, "BTC/USD"), (3, "BTC/EUR")]:
        client.handle(
            {
                "method": "subscribe",
                "success": True,
                "req_id": request_id,
                "result": {"channel": "book", "symbol": symbol},
            }
        )
    assert ("BTC", "book") in client._pending_ack


def test_maintenance_has_a_retry_floor_and_trading_restrictions_keep_market_data_open():
    client = connector()
    for status in ("online", "cancel_only", "post_only"):
        client.handle({"channel": "status", "data": [{"system": status}]})
    for status in ("maintenance", "offline"):
        with pytest.raises(KrakenUnavailable) as error:
            client.handle({"channel": "status", "data": [{"system": status}]})
        assert error.value.retry_after >= 5
    with pytest.raises(BookError, match="subscription rejected"):
        client.handle({"method": "subscribe", "success": False, "error": "untrusted body"})


def test_out_of_order_book_invalidates_published_book_immediately():
    client = connector()
    client.handle(book("BTC", "2026-10-18T12:00:01Z"))
    with pytest.raises(BookError, match="Out-of-order"):
        client.handle(book("BTC", "2026-10-18T12:00:00Z"))
    assert not client.state.assets["BTC"]["kraken"]["book"]["valid"]


async def test_stalled_subscription_send_is_bounded_and_cancellable(monkeypatch):
    client = connector()
    cancelled = asyncio.Event()

    class BlockedSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send(self, frame):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

    monkeypatch.setattr("backend.app.connectors.kraken.connect", lambda *a, **k: BlockedSocket())
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.session(), 1)
    assert cancelled.is_set()
    assert all(not value.valid for value in client.books.values())


async def test_books_are_invalidated_before_waiting_for_a_slow_close_handshake(monkeypatch):
    client = connector()
    closing = asyncio.Event()
    release_close = asyncio.Event()

    class SlowClose:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closing.set()
            await release_close.wait()

        async def send(self, frame):
            pass

    async def receive(ws):
        for asset in ASSETS:
            client.handle(book(asset))
        raise BookError("A feed needs a fresh session")

    monkeypatch.setattr("backend.app.connectors.kraken.connect", lambda *a, **k: SlowClose())
    monkeypatch.setattr(client, "receive", receive)
    task = asyncio.create_task(client.session())
    try:
        await asyncio.wait_for(closing.wait(), 1)
        assert all(not value.valid for value in client.books.values())
        assert client.state.sources["kraken:socket"]["status"] == "disconnected"
    finally:
        release_close.set()
        result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], BookError)
