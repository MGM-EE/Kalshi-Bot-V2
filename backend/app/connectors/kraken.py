import asyncio
import json
from collections import deque
from decimal import Decimal

from websockets.asyncio.client import connect

from ..markets.discovery import timestamp
from ..markets.orderbook import BookError, KrakenBook, number
from ..settings import ASSETS, KRAKEN_WS
from .common import retry


class Kraken:
    def __init__(self, state):
        self.state = state
        self.books = {asset: KrakenBook() for asset in ASSETS}
        self.trades = {asset: deque(maxlen=100) for asset in ASSETS}
        self.ids = {asset: deque(maxlen=2000) for asset in ASSETS}
        self.latest = {}

    def invalidate(self):
        for asset, book in self.books.items():
            book.valid = False
            self.state.assets[asset]["kraken"]["book"] = book.view()
            for channel in ("book", "ticker", "trade"):
                self.state.health(f"kraken:{asset}:{channel}", "disconnected")

    def handle(self, message):
        if message.get("success") is False:
            raise BookError("Kraken subscription rejected")
        channel = message.get("channel")
        if channel not in {"book", "trade", "ticker"}:
            return
        for row in message.get("data", []):
            asset = row["symbol"].split("/")[0]
            if asset not in ASSETS:
                continue
            target = self.state.assets[asset]["kraken"]
            source = f"kraken:{asset}:{channel}"
            exchange_ts = timestamp(row["timestamp"]) if row.get("timestamp") else None
            previous = self.latest.get((asset, channel))
            if exchange_ts is not None and previous is not None and exchange_ts < previous:
                # Applying an out-of-order book could retain apparently valid stale liquidity.
                if channel == "book":
                    raise BookError("Out-of-order Kraken book")
                continue
            if channel == "book":
                self.books[asset].apply(row, snapshot=message["type"] == "snapshot")
                target["book"] = self.books[asset].view()
            elif channel == "ticker":
                bid, ask = number(row["bid"]), number(row["ask"])
                if bid <= 0 or ask < bid:
                    raise BookError("Invalid Kraken ticker")
                target["ticker"] = {
                    "bid": bid,
                    "ask": ask,
                    "last": number(row["last"]),
                    "spread": ask - bid,
                }
            else:
                trade_id = row["trade_id"]
                if trade_id in self.ids[asset]:
                    continue
                price, qty = number(row["price"]), number(row["qty"])
                if row["side"] not in {"buy", "sell"} or price <= 0 or qty <= 0:
                    raise BookError("Invalid Kraken trade")
                self.ids[asset].append(trade_id)
                self.trades[asset].append(
                    {
                        "id": trade_id,
                        "price": price,
                        "quantity": qty,
                        "aggressor_side": row["side"],
                        "usd_notional": price * qty,
                        "signed_quantity": qty if row["side"] == "buy" else -qty,
                        "exchange_ts": exchange_ts,
                    }
                )
                target["trades"] = list(self.trades[asset])
            if exchange_ts is not None:
                self.latest[(asset, channel)] = exchange_ts
            self.state.health(source, "live", "WebSocket v2", exchange_ts)

    async def session(self):
        self.invalidate()
        # A new snapshot establishes a new timestamp baseline after reconnect.
        self.latest.clear()
        async with connect(
            KRAKEN_WS,
            open_timeout=15,
            ping_interval=20,
            ping_timeout=15,
            max_queue=16,
            max_size=2**20,
        ) as ws:
            symbols = [f"{asset}/USD" for asset in ASSETS]
            for request_id, channel in enumerate(("ticker", "trade", "book"), 1):
                params = {"channel": channel, "symbol": symbols, "snapshot": True}
                if channel == "book":
                    params["depth"] = 10
                if channel == "ticker":
                    params["event_trigger"] = "bbo"
                await ws.send(
                    json.dumps({"method": "subscribe", "params": params, "req_id": request_id})
                )
            while True:
                frame = await asyncio.wait_for(ws.recv(), timeout=25)
                self.state.health("kraken:socket", "live", "WebSocket v2 transport")
                self.handle(json.loads(frame, parse_float=Decimal))

    async def run(self):
        await retry(self.state, "kraken:socket", self.session, self.invalidate)
