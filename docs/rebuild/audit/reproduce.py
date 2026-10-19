"""Read-only, local reproductions against the existing V2 code. No exchange access."""

import asyncio
import json
import sys
import time
from pathlib import Path

# Run from the repository root, even if this script is stored outside it.
sys.path.insert(0, str(Path.cwd()))

import backend.app.connectors.kalshi as module
from backend.app.connectors.common import retry
from backend.app.connectors.kalshi import Kalshi
from backend.app.settings import Settings
from backend.app.state import State


class Signer:
    def headers(self, *args):
        return {}


class Socket:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))

    async def recv(self):
        await asyncio.sleep(0.005)
        # Other messages keep arriving, but a required book never arrives.
        return json.dumps({"type": "subscribed", "msg": {"channel": "ticker", "sid": 1}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Client:
    def __init__(self):
        self.calls = 0

    async def get(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("This reproduction should not contact a network")


async def run():
    state = State()
    state.assets["BTC"]["market"] = {"ticker": "TEST-BTC", "close_time": time.time() + 900}
    connector = Kalshi(state, Settings())
    connector.signer = Signer()
    socket = Socket()
    original_connect = module.connect
    module.connect = lambda *args, **kwargs: socket
    task = asyncio.create_task(connector.websocket_session())
    try:
        await asyncio.sleep(0.04)
        client = Client()
        await connector.poll_asset(client, "BTC", "TEST-BTC")
        missing_book = {
            "stream_live": connector.stream_live,
            "socket_health": state.sources["kalshi:socket"]["status"],
            "book_valid": state.assets["BTC"]["kalshi"]["book"]["valid"],
            "fallback_requests": client.calls,
            "session_still_running": not task.done(),
        }
        assert missing_book == {
            "stream_live": True,
            "socket_health": "live",
            "book_valid": False,
            "fallback_requests": 0,
            "session_still_running": True,
        }
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        module.connect = original_connect

    # An error belonging to CF subscription id=2 reaches the generic handler.
    for asset, ticker, sid in (("BTC", "TEST-BTC", 1), ("ETH", "TEST-ETH", 2)):
        state.assets[asset]["market"] = {"ticker": ticker, "close_time": time.time() + 900}
        connector.handle({
            "type": "orderbook_snapshot", "sid": sid, "seq": 1,
            "msg": {"market_ticker": ticker, "yes_dollars_fp": [["0.4", "5"]],
                    "no_dollars_fp": [["0.5", "6"]]},
        })
    assert all(state.assets[a]["kalshi"]["book"]["valid"] for a in ("BTC", "ETH"))
    invalidated = asyncio.Event()

    async def operation():
        connector.handle({"id": 2, "type": "error", "msg": {"code": 24}})

    def invalidate():
        connector.invalidate()
        invalidated.set()

    task = asyncio.create_task(retry(state, "kalshi:socket", operation, invalidate))
    try:
        await asyncio.wait_for(invalidated.wait(), timeout=1)
        blast_radius = {
            asset: state.assets[asset]["kalshi"]["book"]["valid"]
            for asset in ("BTC", "ETH")
        }
        assert blast_radius == {"BTC": False, "ETH": False}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    print(json.dumps({"missing_required_channel": missing_book,
                      "book_valid_after_cf_subscription_error": blast_radius}, indent=2))


asyncio.run(run())
