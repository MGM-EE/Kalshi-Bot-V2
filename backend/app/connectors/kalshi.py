import asyncio
import base64
import json
import time
from collections import deque
from decimal import Decimal
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa
from websockets.asyncio.client import connect

from ..markets.discovery import INDEX_IDS, normalize_market, select_markets, timestamp
from ..markets.orderbook import BookError, KalshiBook
from ..settings import ASSETS, KALSHI_REST, KALSHI_WS
from .benchmark import Benchmark
from .common import error_label, retry


class Signer:
    def __init__(self, key_id, path):
        self.key_id = key_id
        self.key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(self.key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
            raise ValueError("Unsupported signing key")

    def headers(self, method, path):
        stamp = str(int(time.time() * 1000))
        payload = (stamp + method.upper() + path.split("?")[0]).encode()
        if isinstance(self.key, ed25519.Ed25519PrivateKey):
            signature = self.key.sign(payload)
        else:
            signature = self.key.sign(
                payload,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256(),
            )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": stamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }


class Kalshi:
    def __init__(self, state, settings):
        self.state, self.settings = state, settings
        self.books = {}
        self.trades, self.trade_ids = {}, {}
        self.sequences = {}
        self.revision = 0
        self.signer = None
        self.stream_live = False
        self.benchmark = Benchmark(state)
        self.refresh = asyncio.Event()

    async def get(self, client, path, **params):
        headers = self.signer.headers("GET", "/trade-api/v2" + path) if self.signer else {}
        response = await client.get(KALSHI_REST + path, params=params, headers=headers)
        response.raise_for_status()
        return json.loads(response.text, parse_float=Decimal)

    def tickers(self):
        return {
            a["market"]["ticker"]: asset
            for asset, a in self.state.assets.items()
            if a["market"] and a["market"]["close_time"] > time.time()
        }

    async def discover_asset(self, client, asset, series_ticker):
        series = (await self.get(client, f"/series/{series_ticker}"))["series"]
        markets = {}
        for status in ("open", "unopened"):
            cursor, seen = "", set()
            while True:
                data = await self.get(
                    client,
                    "/markets",
                    series_ticker=series_ticker,
                    status=status,
                    limit=1000,
                    cursor=cursor,
                )
                for raw in data["markets"]:
                    markets[raw["ticker"]] = normalize_market(raw, series, asset)
                cursor = data.get("cursor", "")
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError("Repeated discovery cursor")
                seen.add(cursor)
        current, upcoming = select_markets(list(markets.values()), time.time())
        target = self.state.assets[asset]
        old_ticker = target["market"]["ticker"] if target["market"] else None
        new_ticker = current["ticker"] if current else None
        target["market"], target["upcoming"] = current, upcoming
        if old_ticker != new_ticker:
            target["kalshi"] = {}
            self.revision += 1
            if old_ticker:
                self.books.pop(old_ticker, None)
                self.trades.pop(old_ticker, None)
                self.trade_ids.pop(old_ticker, None)
            for channel in ("book", "ticker", "trade", "reference"):
                self.state.health(
                    f"kalshi:{asset}:{channel}", "unavailable", "New market; awaiting data"
                )
        self.state.health(
            f"kalshi:{asset}:discovery",
            "live" if current else "unavailable",
            "REST discovery" if current else "No current open 15-minute market",
            stale_after=self.settings.discovery_seconds + self.settings.stale_seconds,
        )

    def record_trade(self, asset, ticker, row):
        trade_id = row.get("trade_id")
        ids = self.trade_ids.setdefault(ticker, deque(maxlen=2000))
        if not trade_id or trade_id in ids:
            return
        ids.append(trade_id)
        trades = self.trades.setdefault(ticker, deque(maxlen=100))
        trades.append(
            {
                key: row.get(key)
                for key in (
                    "trade_id",
                    "yes_price_dollars",
                    "no_price_dollars",
                    "count_fp",
                    "created_time",
                    "ts",
                    "taker_outcome_side",
                    "taker_book_side",
                    "taker_side",
                    "is_block_trade",
                )
            }
        )
        # Legacy taker_side is retained as supplied, never relabeled as a buy/sell.
        self.state.assets[asset]["kalshi"]["trades"] = list(trades)

    def invalidate(self):
        self.stream_live = False
        for book in self.books.values():
            book.valid = False
        for asset in ASSETS:
            self.state.assets[asset]["kalshi"]["book"] = {"valid": False}
            for channel in ("book", "ticker", "trade", "reference"):
                self.state.health(f"kalshi:{asset}:{channel}", "disconnected")

    def handle(self, packet):
        kind = packet.get("type")
        if kind == "error":
            raise BookError("Kalshi subscription rejected")
        if kind == "cfbenchmarks_value":
            self.benchmark.handle(packet["msg"])
            return
        if kind == "market_lifecycle_v2":
            row = packet["msg"]
            ticker = row.get("market_ticker", "")
            if any(ticker.startswith(series + "-") for series in ASSETS.values()):
                self.refresh.set()
                for asset, target in self.state.assets.items():
                    market = target["market"]
                    if not market or market["ticker"] != ticker:
                        continue
                    market["lifecycle_event"] = {
                        key: row.get(key)
                        for key in (
                            "event_type",
                            "close_ts",
                            "determination_ts",
                            "settled_ts",
                            "result",
                            "settlement_value",
                        )
                    }
                    market["outcome_mapping"] = None
                    market["rule_checks"] = ["Lifecycle update; awaiting REST rule revalidation"]
                    if row.get("close_ts") is not None:
                        market["close_time"] = row["close_ts"]
                    if row.get("event_type") in {"deactivated", "determined", "settled"}:
                        market["status"] = row["event_type"]
                    target["kalshi"]["book"] = {"valid": False}
                    self.revision += 1
            return
        if kind not in {"orderbook_snapshot", "orderbook_delta", "ticker", "trade"}:
            return
        row = packet["msg"]
        ticker = row.get("market_ticker")
        asset = self.tickers().get(ticker)
        if not asset:
            return
        if kind in {"orderbook_snapshot", "orderbook_delta"}:
            sid, seq = packet["sid"], packet["seq"]
            previous = self.sequences.get(sid)
            if previous is not None:
                if seq <= previous:
                    return
                if seq != previous + 1:
                    raise BookError("Kalshi sequence gap; new snapshot required")
            self.sequences[sid] = seq
            book = self.books.setdefault(ticker, KalshiBook())
            if kind == "orderbook_snapshot":
                # Each side is optional when that side has no resting offers.
                book.snapshot(row.get("yes_dollars_fp", []), row.get("no_dollars_fp", []))
            else:
                book.delta(row["side"], row["price_dollars"], row["delta_fp"])
            self.state.assets[asset]["kalshi"]["book"] = book.view()
            channel = "book"
        elif kind == "trade":
            self.record_trade(asset, ticker, row)
            channel = "trade"
        else:
            self.state.assets[asset]["kalshi"]["ticker"] = {
                key: row.get(key)
                for key in (
                    "yes_bid_dollars",
                    "yes_ask_dollars",
                    "price_dollars",
                    "volume_fp",
                    "open_interest_fp",
                )
            }
            channel = "ticker"
        ts = row["ts_ms"] / 1000 if row.get("ts_ms") is not None else row.get("ts")
        self.state.health(
            f"kalshi:{asset}:{channel}",
            "live",
            "WebSocket",
            timestamp(ts) if ts is not None else None,
        )

    async def websocket_session(self):
        tickers = list(self.tickers())
        if not tickers:
            self.state.health("kalshi:socket", "unavailable", "Waiting for market discovery")
            await asyncio.sleep(2)
            return
        self.invalidate()
        revision = self.revision
        self.sequences.clear()
        async with connect(
            KALSHI_WS,
            additional_headers=self.signer.headers("GET", "/trade-api/ws/v2"),
            open_timeout=15,
            ping_interval=20,
            ping_timeout=15,
            max_queue=16,
            max_size=2**20,
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker", "trade", "orderbook_delta"],
                            "market_tickers": tickers,
                        },
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "id": 2,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["cfbenchmarks_value"],
                            "index_ids": list(INDEX_IDS.values()),
                        },
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {"id": 3, "cmd": "subscribe", "params": {"channels": ["market_lifecycle_v2"]}}
                )
            )
            self.stream_live = True
            last_frame = time.monotonic()
            while revision == self.revision:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=1)
                except TimeoutError:
                    if time.monotonic() - last_frame > 30:
                        raise TimeoutError("No Kalshi messages")
                    continue
                last_frame = time.monotonic()
                self.state.health("kalshi:socket", "live", "Authenticated read-only subscriptions")
                self.handle(json.loads(frame, parse_float=Decimal))
        self.invalidate()

    async def poll_asset(self, client, asset, ticker):
        if not self.stream_live:
            response = await self.get(client, f"/markets/{ticker}/orderbook", depth=100)
            book = self.books.setdefault(ticker, KalshiBook())
            data = response["orderbook_fp"]
            book.snapshot(data["yes_dollars"], data["no_dollars"])
            self.state.assets[asset]["kalshi"]["book"] = book.view()
            self.state.health(
                f"kalshi:{asset}:book", "polling", "REST snapshot; streaming unavailable"
            )
            data = await self.get(client, "/markets/trades", ticker=ticker, limit=100)
            for row in reversed(data["trades"]):
                self.record_trade(asset, ticker, row)
            self.state.health(f"kalshi:{asset}:trade", "polling", "REST recent executions")
        if not self.stream_live:
            self.state.health(
                f"kalshi:{asset}:reference",
                "unavailable",
                "Authenticated CF Benchmarks WebSocket required",
            )

    async def rest_loop(self):
        next_discovery = 0
        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                if time.monotonic() >= next_discovery:
                    for asset, series in ASSETS.items():
                        try:
                            await self.discover_asset(client, asset, series)
                        except Exception as exc:
                            self.state.health(
                                f"kalshi:{asset}:discovery", "disconnected", error_label(exc)
                            )
                    next_discovery = time.monotonic() + self.settings.discovery_seconds
                for ticker, asset in self.tickers().items():
                    try:
                        await self.poll_asset(client, asset, ticker)
                    except Exception as exc:
                        if ticker in self.books:
                            self.books[ticker].valid = False
                        self.state.assets[asset]["kalshi"]["book"] = {"valid": False}
                        self.state.health(f"kalshi:{asset}:book", "disconnected", error_label(exc))
                try:
                    await asyncio.wait_for(self.refresh.wait(), timeout=self.settings.rest_seconds)
                    self.refresh.clear()
                    next_discovery = 0
                except TimeoutError:
                    pass

    async def run(self):
        auth_ok = False
        if self.settings.key_id and self.settings.private_key_path:
            try:
                self.signer = Signer(self.settings.key_id, self.settings.private_key_path)
                auth_ok = True
            except Exception:
                self.state.health(
                    "kalshi:socket",
                    "unavailable",
                    "Cannot load signing key; check local configuration",
                )
        else:
            self.state.health(
                "kalshi:socket",
                "unavailable",
                "Kalshi WebSocket requires API key ID and local signing key",
            )
        if auth_ok:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.rest_loop())
                group.create_task(
                    retry(self.state, "kalshi:socket", self.websocket_session, self.invalidate)
                )
        else:
            await self.rest_loop()
