import asyncio
import json
import time
from collections import deque
from decimal import Decimal

from websockets.asyncio.client import connect

from ..markets.discovery import timestamp
from ..markets.orderbook import BookError, KrakenBook, number
from ..settings import ASSETS, KRAKEN_WS
from .common import retry


class KrakenUnavailable(BookError):
    """An exchange maintenance notice needs a pause before opening another socket."""

    retry_after = 5


class Kraken:
    def __init__(self, state, settings=None):
        self.state = state
        self.ack_timeout = getattr(settings, "subscription_timeout_seconds", 10)
        self.snapshot_timeout = getattr(settings, "snapshot_verify_seconds", 10)
        self.stale_timeout = getattr(settings, "channel_stale_seconds", 30)
        self.transport_timeout = 25
        self.watchdog_interval = min(
            1, self.ack_timeout / 4, self.snapshot_timeout / 4, self.stale_timeout / 4
        )
        self.books = {asset: KrakenBook() for asset in ASSETS}
        self.trades = {asset: deque(maxlen=100) for asset in ASSETS}
        self.ids = {asset: deque(maxlen=2000) for asset in ASSETS}
        self.latest = {}
        self._pending_ack = {}
        self._pending_snapshot = {}
        self._seen = {}
        self._last_frame = 0
        self._request_channels = {1: "ticker", 2: "trade", 3: "book"}

    def invalidate(self):
        # Reconnect snapshots establish a new baseline, including after an exchange restart.
        self.latest.clear()
        for asset, book in self.books.items():
            book.valid = False
            self.state.assets[asset]["kraken"]["book"] = book.view()
            for channel in ("book", "ticker", "trade"):
                self.state.health(f"kraken:{asset}:{channel}", "disconnected")

    def handle(self, message):
        if message.get("success") is False:
            raise BookError("Kraken subscription rejected")
        if message.get("method") == "subscribe" and message.get("success") is True:
            result = message.get("result", {})
            symbol, channel = result.get("symbol", ""), result.get("channel")
            asset = symbol.split("/")[0]
            key = (asset, channel)
            if (
                symbol == f"{asset}/USD"
                and self._request_channels.get(message.get("req_id")) == channel
                and key in self._pending_ack
            ):
                self._pending_ack.pop(key)
                if channel == "trade" and key not in self._seen:
                    self.state.health(
                        f"kraken:{asset}:trade",
                        "connecting",
                        "Subscription active; awaiting a fresh trade",
                    )
            return
        channel = message.get("channel")
        if channel == "status":
            if any(
                row.get("system") in {"maintenance", "offline"}
                for row in message.get("data", [])
            ):
                raise KrakenUnavailable("Kraken market data unavailable during maintenance")
            return
        if channel not in {"book", "trade", "ticker"}:
            return
        for row in message.get("data", []):
            asset = row["symbol"].split("/")[0]
            if asset not in ASSETS or row["symbol"] != f"{asset}/USD":
                continue
            target = self.state.assets[asset]["kraken"]
            source = f"kraken:{asset}:{channel}"
            exchange_ts = timestamp(row["timestamp"]) if row.get("timestamp") else None
            previous = self.latest.get((asset, channel))
            if exchange_ts is not None and previous is not None and exchange_ts < previous:
                # Applying an out-of-order book could retain apparently valid stale liquidity.
                if channel == "book":
                    self.books[asset].valid = False
                    target["book"] = self.books[asset].view()
                    self.state.health(source, "disconnected", "Fresh Kraken snapshot required")
                    raise BookError("Out-of-order Kraken book")
                continue
            if channel == "book":
                try:
                    self.books[asset].apply(row, snapshot=message["type"] == "snapshot")
                except BookError:
                    target["book"] = self.books[asset].view()
                    self.state.health(source, "disconnected", "Fresh Kraken snapshot required")
                    raise
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
            self._seen[(asset, channel)] = time.monotonic()
            if channel in {"book", "ticker"}:
                self._pending_snapshot.pop((asset, channel), None)
            self.state.health(source, "live", "WebSocket v2", exchange_ts)

    def check_liveness(self, now):
        """Check each requested feed even while unrelated frames keep the socket busy."""
        for pending, timeout, reason in (
            (self._pending_ack, self.ack_timeout, "Subscription acknowledgement timed out"),
            (self._pending_snapshot, self.snapshot_timeout, "Initial Kraken snapshot timed out"),
        ):
            for (asset, channel), started in pending.items():
                if now - started >= timeout:
                    self.state.health(f"kraken:{asset}:{channel}", "disconnected", reason)
                    raise BookError(reason)
        for asset in ASSETS:
            # Trades are event driven: a quiet tape is not a broken subscription.
            for channel in ("book", "ticker"):
                last = self._seen.get((asset, channel))
                if last is not None and now - last >= self.stale_timeout:
                    self.state.health(
                        f"kraken:{asset}:{channel}", "disconnected", "Kraken feed stopped updating"
                    )
                    raise BookError("Kraken feed stopped updating; fresh snapshot required")
        if now - self._last_frame >= self.transport_timeout:
            raise TimeoutError("Kraken transport stopped responding")

    async def receive(self, ws):
        while True:
            frame = await ws.recv()
            self._last_frame = time.monotonic()
            self.state.health("kraken:socket", "live", "WebSocket v2 transport")
            self.handle(json.loads(frame, parse_float=Decimal))

    async def watchdog(self):
        while True:
            await asyncio.sleep(self.watchdog_interval)
            self.check_liveness(time.monotonic())

    async def session(self):
        self.invalidate()
        self._pending_ack.clear()
        self._pending_snapshot.clear()
        self._seen.clear()
        try:
            async with connect(
                KRAKEN_WS,
                open_timeout=15,
                close_timeout=3,
                ping_interval=20,
                ping_timeout=15,
                max_queue=16,
                max_size=2**20,
            ) as ws:
                self._last_frame = time.monotonic()
                symbols = [f"{asset}/USD" for asset in ASSETS]
                for request_id, channel in enumerate(("ticker", "trade", "book"), 1):
                    started = time.monotonic()
                    for asset in ASSETS:
                        self._pending_ack[(asset, channel)] = started
                        if channel in {"book", "ticker"}:
                            self._pending_snapshot[(asset, channel)] = started
                        self.state.health(
                            f"kraken:{asset}:{channel}", "connecting", "Awaiting Kraken subscription"
                        )
                    params = {"channel": channel, "symbol": symbols, "snapshot": True}
                    if channel == "book":
                        params["depth"] = 10
                    if channel == "ticker":
                        params["event_trigger"] = "bbo"
                    await asyncio.wait_for(
                        ws.send(
                            json.dumps(
                                {"method": "subscribe", "params": params, "req_id": request_id}
                            )
                        ),
                        timeout=self.ack_timeout,
                    )
                tasks = [
                    asyncio.create_task(self.receive(ws), name="kraken:receive"),
                    asyncio.create_task(self.watchdog(), name="kraken:watchdog"),
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    # Invalidate before the close handshake, which can itself take seconds.
                    self.invalidate()
                    self.state.health("kraken:socket", "disconnected", "WebSocket session closed")
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # No previous book remains executable during backoff or shutdown.
            self.invalidate()
            self.state.health("kraken:socket", "disconnected", "WebSocket session closed")

    async def run(self):
        await retry(self.state, "kraken:socket", self.session, self.invalidate)
