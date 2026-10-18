import asyncio
import base64
import json
import time
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from websockets.asyncio.server import serve

from backend.app.connectors.kalshi import Kalshi, Signer
from backend.app.connectors.kraken import Kraken
from backend.app.markets.orderbook import BookError
from backend.app.settings import Settings
from backend.app.state import State


def kraken_snapshot():
    return {
        "channel": "book",
        "type": "snapshot",
        "data": [
            {
                "symbol": "BTC/USD",
                "bids": [{"price": "100.00", "qty": "2.500"}],
                "asks": [{"price": "101.00", "qty": "1.250"}],
                "checksum": 3654185688,
            }
        ],
    }


def test_kraken_aggressor_side_duplicate_and_out_of_order():
    state = State()
    connector = Kraken(state)
    row = {
        "symbol": "BTC/USD",
        "price": "100",
        "qty": "2.5",
        "side": "sell",
        "trade_id": 10,
        "timestamp": "2026-10-18T12:00:00Z",
    }
    connector.handle({"channel": "trade", "type": "update", "data": [row, row]})
    trades = state.assets["BTC"]["kraken"]["trades"]
    assert len(trades) == 1
    assert trades[0]["signed_quantity"] == Decimal("-2.5")
    assert trades[0]["usd_notional"] == Decimal("250.0")
    connector.handle(
        {
            "channel": "trade",
            "type": "update",
            "data": [{**row, "trade_id": 11, "timestamp": "2026-10-18T11:59:59Z"}],
        }
    )
    assert len(state.assets["BTC"]["kraken"]["trades"]) == 1


def test_kraken_mismatch_invalidates_and_requires_resnapshot():
    connector = Kraken(State())
    connector.handle(kraken_snapshot())
    with pytest.raises(BookError):
        connector.handle(
            {
                "channel": "book",
                "type": "update",
                "data": [
                    {
                        "symbol": "BTC/USD",
                        "bids": [{"price": "100.00", "qty": "2.000"}],
                        "checksum": 0,
                    }
                ],
            }
        )
    connector.invalidate()
    assert not connector.state.assets["BTC"]["kraken"]["book"]["valid"]
    connector.handle(kraken_snapshot())
    assert connector.books["BTC"].valid


def test_kalshi_sequence_gap_duplicates_and_reconnect():
    state = State()
    state.assets["BTC"]["market"] = {"ticker": "TEST", "close_time": time.time() + 900}
    connector = Kalshi(state, Settings())
    snap = {
        "type": "orderbook_snapshot",
        "sid": 1,
        "seq": 1,
        "msg": {
            "market_ticker": "TEST",
            "yes_dollars_fp": [["0.4", "5"]],
            "no_dollars_fp": [["0.5", "6"]],
        },
    }
    connector.handle(snap)
    delta = {
        "type": "orderbook_delta",
        "sid": 1,
        "seq": 2,
        "msg": {"market_ticker": "TEST", "side": "yes", "price_dollars": "0.4", "delta_fp": "2"},
    }
    connector.handle(delta)
    connector.handle(delta)
    assert connector.books["TEST"].yes[Decimal("0.4")] == Decimal(7)
    with pytest.raises(BookError, match="sequence gap"):
        connector.handle({**delta, "seq": 4})
    connector.invalidate()
    assert not state.assets["BTC"]["kalshi"]["book"]["valid"]
    connector.sequences.clear()
    connector.handle(snap)
    assert connector.books["TEST"].yes[Decimal("0.4")] == 5


def test_empty_kalshi_snapshot_and_lifecycle_force_revalidation():
    state = State()
    state.assets["BTC"]["market"] = {
        "ticker": "KXBTC15M-TEST",
        "close_time": time.time() + 900,
        "outcome_mapping": {"yes": "UP"},
    }
    connector = Kalshi(state, Settings())
    connector.handle(
        {
            "type": "orderbook_snapshot",
            "sid": 1,
            "seq": 1,
            "msg": {"market_ticker": "KXBTC15M-TEST"},
        }
    )
    assert connector.books["KXBTC15M-TEST"].view()["yes_asks"] == []
    connector.handle(
        {
            "type": "market_lifecycle_v2",
            "msg": {"market_ticker": "KXBTC15M-TEST", "event_type": "determined", "result": "yes"},
        }
    )
    market = state.assets["BTC"]["market"]
    assert market["outcome_mapping"] is None
    assert market["lifecycle_event"]["result"] == "yes"
    assert connector.refresh.is_set()
    assert not state.assets["BTC"]["kalshi"]["book"]["valid"]


async def test_rest_discovery_pagination_and_rollover(market_raw, monkeypatch):
    from backend.app.markets.discovery import timestamp

    now = timestamp("2026-10-18T12:07:30Z")
    monkeypatch.setattr(time, "time", lambda: now)
    calls = []
    future = {
        **market_raw,
        "ticker": "NEXT",
        "open_time": "2026-10-18T12:15:00Z",
        "close_time": "2026-10-18T12:30:00Z",
    }

    def respond(request):
        calls.append(request)
        if "/series/" in request.url.path:
            return httpx.Response(200, json={"series": {"fee_type": "quadratic"}})
        if request.url.params.get("status") == "unopened":
            return httpx.Response(200, json={"markets": [future], "cursor": ""})
        if request.url.params.get("cursor"):
            return httpx.Response(200, json={"markets": [future], "cursor": ""})
        return httpx.Response(200, json={"markets": [market_raw], "cursor": "page2"})

    state = State()
    connector = Kalshi(state, Settings())
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await connector.discover_asset(client, "BTC", "KXBTC15M")
        assert state.assets["BTC"]["market"]["ticker"] == market_raw["ticker"]
        assert state.assets["BTC"]["upcoming"][0]["ticker"] == "NEXT"
        state.assets["BTC"]["kalshi"]["ticker"] = {"price_dollars": ".99"}
        now += 900
        await connector.discover_asset(client, "BTC", "KXBTC15M")
        assert state.assets["BTC"]["market"]["ticker"] == "NEXT"
        assert state.assets["BTC"]["kalshi"] == {}
    assert all(r.method == "GET" for r in calls)
    assert {r.url.params.get("status") for r in calls if r.url.path.endswith("/markets")} == {
        "open",
        "unopened",
    }


@pytest.mark.parametrize("kind", ["rsa", "ed25519"])
def test_signing_matches_official_sdk_format(tmp_path, kind):
    key = (
        rsa.generate_private_key(65537, 2048)
        if kind == "rsa"
        else ed25519.Ed25519PrivateKey.generate()
    )
    path = tmp_path / "test.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    headers = Signer("test-key-id", path).headers("get", "/trade-api/ws/v2?ignored=true")
    payload = (headers["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/ws/v2").encode()
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    if kind == "rsa":
        key.public_key().verify(
            signature,
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    else:
        key.public_key().verify(signature, payload)


async def test_real_websocket_transport_subscribes_and_receives(monkeypatch):
    import backend.app.connectors.kraken as module

    state = State()
    connector = Kraken(state)
    received = []
    ready = asyncio.Event()

    async def server(ws):
        for _ in range(3):
            received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps(kraken_snapshot()))
        ready.set()
        await ws.wait_closed()

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        monkeypatch.setattr(module, "KRAKEN_WS", f"ws://127.0.0.1:{port}")
        task = asyncio.create_task(connector.session())
        try:
            await asyncio.wait_for(ready.wait(), 3)
            for _ in range(100):
                if connector.books["BTC"].valid:
                    break
                await asyncio.sleep(0.01)
            assert connector.books["BTC"].valid
            assert {r["params"]["channel"] for r in received} == {"book", "ticker", "trade"}
            assert all(r["params"]["symbol"] == ["BTC/USD", "ETH/USD", "SOL/USD"] for r in received)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_kalshi_authenticated_transport_subscriptions_and_cf(monkeypatch, tmp_path):
    import backend.app.connectors.kalshi as module

    key = ed25519.Ed25519PrivateKey.generate()
    path = tmp_path / "local-test.pem"
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    state = State()
    state.assets["BTC"]["market"] = {
        "ticker": "TEST",
        "close_time": time.time() + 900,
        "index_id": "BRTI",
        "averaging_window_seconds": 60,
    }
    connector = Kalshi(state, Settings())
    connector.signer = Signer("local-test", path)
    requests = []

    async def server(ws):
        headers = ws.request.headers
        payload = (headers["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/ws/v2").encode()
        key.public_key().verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), payload)
        for _ in range(3):
            requests.append(json.loads(await ws.recv()))
        await ws.send(
            json.dumps(
                {
                    "type": "orderbook_snapshot",
                    "sid": 1,
                    "seq": 1,
                    "msg": {
                        "market_ticker": "TEST",
                        "yes_dollars_fp": [["0.4", "10"]],
                        "no_dollars_fp": [["0.5", "20"]],
                    },
                }
            )
        )
        stamp = int(time.time() * 1000)
        await ws.send(
            json.dumps(
                {
                    "type": "cfbenchmarks_value",
                    "sid": 2,
                    "seq": 1,
                    "msg": {
                        "index_id": "BRTI",
                        "received_at": stamp,
                        "data": json.dumps(
                            {"type": "value", "id": "BRTI", "time": stamp, "value": "80000"}
                        ),
                        "avg_60s_data": {
                            "value": "79999",
                            "window_size": 60,
                            "window_start_ts_ms": stamp - 60000,
                            "window_end_ts_exclusive": stamp,
                        },
                    },
                }
            )
        )
        await ws.wait_closed()

    async with serve(server, "127.0.0.1", 0) as listener:
        port = listener.sockets[0].getsockname()[1]
        monkeypatch.setattr(module, "KALSHI_WS", f"ws://127.0.0.1:{port}/trade-api/ws/v2")
        task = asyncio.create_task(connector.websocket_session())
        try:
            for _ in range(200):
                if state.assets["BTC"]["market"].get("current_index") is not None:
                    break
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            assert state.assets["BTC"]["market"]["current_index"] == Decimal("80000")
            assert state.assets["BTC"]["kalshi"]["book"]["valid"]
            assert len(requests) == 3
            assert all(r["cmd"] == "subscribe" for r in requests)
            assert requests[1]["params"]["index_ids"] == ["BRTI", "ETHUSD_RTI", "SOLUSD_RTI"]
            assert requests[2]["params"] == {"channels": ["market_lifecycle_v2"]}
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
