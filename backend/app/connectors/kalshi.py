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

from ..credentials import load_for_settings
from ..markets.discovery import INDEX_IDS, normalize_market, select_markets, timestamp
from ..markets.orderbook import BookError, KalshiBook
from ..settings import ASSETS, KALSHI_REST, KALSHI_WS
from .benchmark import Benchmark
from .common import error_label, retry, retry_delay


class Signer:
    def __init__(self, key_id, path):
        self._load(key_id, Path(path).read_bytes())

    def _load(self, key_id, private_pem):
        self.key_id = key_id
        self.key = serialization.load_pem_private_key(private_pem, password=None)
        if not isinstance(self.key, (rsa.RSAPrivateKey, ed25519.Ed25519PrivateKey)):
            raise ValueError("Unsupported signing key")

    @classmethod
    def from_credentials(cls, credentials):
        signer = cls.__new__(cls)
        signer._load(credentials.key_id, credentials.private_pem)
        return signer

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
        # REST snapshots must never become the base of a WebSocket delta chain.
        self.rest_books = {}
        self.book_sids, self.book_received, self.book_versions = {}, {}, {}
        self.book_exchange_ts = {}
        self.requests, self.subscriptions, self.sid_channels = {}, {}, {}
        self.retired_sids, self.awaiting_snapshots = set(), {}
        self.reference_received, self.reference_retry_at = {}, {}
        self.series_cache, self.fee_due = {}, {}
        self.available_indexes = None
        self.socket_open = False
        self.session_active = False
        self.request_id = 0
        self.discovery_generation = 0
        self.request_gate = asyncio.Lock()
        self.next_http_request = self.http_retry_at = 0

    async def get(self, client, path, **params):
        async with self.request_gate:
            delay = max(self.next_http_request, self.http_retry_at) - time.monotonic()
            while delay > 0:
                await asyncio.sleep(delay)
                delay = max(self.next_http_request, self.http_retry_at) - time.monotonic()
            # Bound the aggregate burst across discovery, fees and fallback.
            self.next_http_request = time.monotonic() + 0.05
        headers = self.signer.headers("GET", "/trade-api/v2" + path) if self.signer else {}
        response = await client.get(KALSHI_REST + path, params=params, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if response.status_code == 429:
                self.http_retry_at = max(self.http_retry_at, time.monotonic() + retry_delay(1, exc))
            raise
        return json.loads(response.text, parse_float=Decimal)

    def tickers(self):
        return {
            a["market"]["ticker"]: asset
            for asset, a in self.state.assets.items()
            if a["market"] and a["market"]["close_time"] > time.time()
        }

    async def discover_asset(self, client, asset, series_ticker):
        series_read_at = time.time()
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
        if current and old_ticker == new_ticker:
            old_market = target["market"]
            if current.get("index_id") == old_market.get("index_id"):
                for key in ("current_index", "trailing_average", "reference_status"):
                    if key in old_market:
                        current[key] = old_market[key]
                if current["close_time"] == old_market["close_time"]:
                    current["partial_final_minute_average"] = old_market.get(
                        "partial_final_minute_average"
                    )
            # Fee work has its own schedule. Keep previously verified metadata only
            # for this exact market; its original expiry still applies.
            for key in ("fee_type", "fee_multiplier", "fees_verified_at", "fees_valid_until"):
                if key in old_market:
                    current[key] = old_market[key]
        self.series_cache[asset] = (series, series_read_at)
        target["market"], target["upcoming"] = current, upcoming
        if old_ticker != new_ticker:
            target["kalshi"] = {}
            self.revision += 1
            if old_ticker:
                self.books.pop(old_ticker, None)
                self.trades.pop(old_ticker, None)
                self.trade_ids.pop(old_ticker, None)
                self.rest_books.pop(old_ticker, None)
                self.book_sids.pop(old_ticker, None)
                self.book_received.pop(old_ticker, None)
                self.book_exchange_ts.pop(old_ticker, None)
                self.book_versions.pop(old_ticker, None)
                self.awaiting_snapshots.pop(old_ticker, None)
            self.fee_due[asset] = 0
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

    async def effective_fees(self, client, market, series, series_read_at=None, guard=False):
        series_read_at = time.time() if series_read_at is None else series_read_at
        event = (await self.get(client, f"/events/{market['event_ticker']}"))["event"]
        fee_type = event.get("fee_type_override")
        multiplier = event.get("fee_multiplier_override")
        if (fee_type is None) != (multiplier is None):
            raise ValueError("Incomplete event fee override")
        fee_type = series["fee_type"] if fee_type is None else fee_type
        multiplier = series["fee_multiplier"] if multiplier is None else multiplier
        changes = (
            await self.get(
                client, "/series/fee_changes", series_ticker=series["ticker"], show_historical=True
            )
        )["series_fee_change_arr"]
        cursor, seen = "", set()
        while True:
            data = await self.get(
                client,
                "/events/fee_changes",
                event_ticker=market["event_ticker"],
                limit=1000,
                cursor=cursor,
            )
            changes.extend(data["event_fee_changes"])
            cursor = data.get("cursor", "")
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("Repeated fee-change cursor")
            seen.add(cursor)
        now = time.time()
        if any(series_read_at <= timestamp(change["scheduled_ts"]) <= now for change in changes):
            raise ValueError("Fee change overlapped metadata retrieval; wait for refresh")
        future = [
            timestamp(change["scheduled_ts"])
            for change in changes
            if timestamp(change["scheduled_ts"]) > now
        ]
        if guard:
            active = self.state.assets[market["asset"]]["market"]
            if active is not market:
                identity = ("ticker", "event_ticker", "close_time")
                if not active or any(active.get(key) != market.get(key) for key in identity):
                    # Discovery changed the generation while this request was in flight.
                    self.fee_due[market["asset"]] = 0
                    return
                # A metadata refresh of this same market must not perpetually
                # throw away a slower fee request at the same scheduling phase.
                market = active
        market.update(
            fee_type=fee_type,
            fee_multiplier=multiplier,
            fees_verified_at=now,
            fees_valid_until=min([now + 60, *future]),
        )
        self.state.health(
            f"kalshi:{market['asset']}:fees",
            "live",
            "Effective series/event fees and scheduled changes",
            stale_after=60,
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
                    "ts_ms",
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
        self.stream_live = self.socket_open = False
        for book in self.books.values():
            book.valid = False
        self.book_received.clear()
        for asset in ASSETS:
            self.state.assets[asset]["kalshi"]["book"] = {"valid": False}
            for channel in ("book", "ticker", "trade", "reference"):
                self.state.health(f"kalshi:{asset}:{channel}", "disconnected")

    def book_ready(self, ticker):
        book = self.books.get(ticker)
        if not book or not book.valid:
            return False
        if self.session_active:
            subscription = self.subscriptions.get("orderbook_delta", {})
            if not self.socket_open or not subscription.get("acked"):
                return False
            if self.book_sids.get(ticker) != subscription.get("sid"):
                return False
        age = time.monotonic() - self.book_received.get(ticker, float("-inf"))
        source_ts = self.book_exchange_ts.get(ticker)
        if source_ts is not None:
            exchange_age = time.time() - source_ts
            if exchange_age < -5 or exchange_age > self.settings.stale_seconds:
                return False
        return age <= getattr(self.settings, "snapshot_verify_seconds", 5)

    def _publish_book(self, asset, ticker, book, status, detail, exchange_ts=None):
        self.book_versions[ticker] = self.book_versions.get(ticker, 0) + 1
        self.state.assets[asset]["kalshi"]["book"] = book.view()
        self.state.health(f"kalshi:{asset}:book", status, detail, exchange_ts)

    def _invalidate_sid(self, sid, detail):
        affected = {ticker for ticker, book_sid in self.book_sids.items() if book_sid == sid}
        sub = self.subscriptions.get("orderbook_delta", {})
        if sub.get("sid") == sid:
            affected.update(sub.get("tickers", ()))
        for ticker in affected:
            if ticker in self.books:
                self.books[ticker].valid = False
            self.book_received.pop(ticker, None)
            self.awaiting_snapshots.setdefault(ticker, 0)
            asset = self.tickers().get(ticker)
            if asset:
                self.state.assets[asset]["kalshi"]["book"] = {"valid": False}
                self.state.health(f"kalshi:{asset}:book", "disconnected", detail)

    def _sequence(self, packet):
        # A SID covers multiple markets, including markets just removed at rollover.
        # Control responses also consume its sequence numbers.
        sid, seq = packet.get("sid"), packet.get("seq")
        if sid in self.retired_sids:
            return False
        book_sid = self.subscriptions.get("orderbook_delta", {}).get("sid")
        is_book = packet.get("type") in {"orderbook_snapshot", "orderbook_delta"}
        if seq is None or sid is None or not (is_book or sid == book_sid):
            return True
        previous = self.sequences.get(sid)
        if previous is not None and seq <= previous:
            return False
        self.sequences[sid] = seq
        if previous is not None and seq != previous + 1:
            self._invalidate_sid(sid, "Sequence gap; awaiting fresh snapshots")
            exc = BookError("Kalshi sequence gap; new snapshot required")
            exc.sid = sid
            raise exc
        return True

    def _channel_health(self, channel, detail):
        names = {
            "orderbook_delta": "book",
            "cfbenchmarks_value": "reference",
            "market_lifecycle_v2": "lifecycle",
        }
        for asset in ASSETS:
            self.state.health(
                f"kalshi:{asset}:{names.get(channel, channel)}", "unavailable", detail
            )

    def _reference_failure(self, indexes, detail):
        for asset, index in INDEX_IDS.items():
            if index in indexes:
                self.reference_retry_at[asset] = time.monotonic() + getattr(
                    self.settings, "channel_retry_seconds", 30
                )
                self.state.health(f"kalshi:{asset}:reference", "unavailable", detail)

    def _protocol(self, packet):
        kind = packet.get("type")
        request = self.requests.get(packet.get("id"))
        if kind == "subscribed":
            row = packet["msg"]
            channel, sid = row["channel"], row["sid"]
            subscription = self.subscriptions.get(channel)
            if not subscription or not request or channel not in request["channels"]:
                return True
            if subscription["request_id"] != packet.get("id"):
                return True
            subscription.update(sid=sid, acked=True)
            if channel != "orderbook_delta":
                subscription["failures"] = 0
            self.sid_channels[sid] = channel
            request["channels"].discard(channel)
            if not request["channels"]:
                self.requests.pop(packet.get("id"), None)
            self.stream_live = any(self.book_ready(t) for t in self.tickers())
            return True
        if kind in {"ok", "cfbenchmarks_value_indexlist"}:
            if request:
                if packet.get("sid") is not None and packet["sid"] != request.get("sid"):
                    return True
                self.requests.pop(packet.get("id"), None)
                if request.get("action") == "indexlist":
                    indexes = packet.get("msg", {}).get("index_ids")
                    if isinstance(indexes, list) and all(
                        isinstance(index, str) for index in indexes
                    ):
                        self.available_indexes = set(indexes)
            return True
        if kind == "unsubscribed":
            self.requests.pop(packet.get("id"), None)
            return True
        if kind != "error":
            return False
        if request and request.get("action") == "unsubscribe":
            self.requests.pop(packet.get("id"), None)
            return True
        # A capability refusal must not invalidate unrelated executable books.
        channels = request["channels"] if request else set()
        if not channels and packet.get("sid") in self.sid_channels:
            channels = {self.sid_channels[packet["sid"]]}
        if not channels:
            raise BookError("Uncorrelated Kalshi protocol error")
        self.requests.pop(packet.get("id"), None)
        code = packet.get("msg", {}).get("code", "unknown")
        terminal = code in {10, 25}  # Official AsyncAPI: subscription must be recreated.
        if (
            request
            and request.get("action") in {"subscribe_indices", "unsubscribe_indices"}
            and not terminal
        ):
            self._reference_failure(
                request["index_ids"], f"Index repair unavailable (code {code}); retry scheduled"
            )
            return True
        for channel in channels:
            sub = self.subscriptions.get(channel)
            if request and request.get("action") == "indexlist" and not terminal:
                # Optional capability discovery cannot revoke an already flowing index.
                continue
            if sub:
                sub["retry_at"] = time.monotonic() + (
                    0 if terminal else getattr(self.settings, "channel_retry_seconds", 30)
                )
                sub["failed"] = True
                if channel == "orderbook_delta":
                    self._invalidate_sid(sub.get("sid"), "Book request rejected")
            self._channel_health(
                channel, f"Subscription unavailable (code {code}); retry scheduled"
            )
        return True

    def handle(self, packet):
        if not self._sequence(packet):
            return
        if self._protocol(packet):
            return
        kind = packet.get("type")
        if kind == "cfbenchmarks_value":
            row = packet["msg"]
            asset = next((a for a, index in INDEX_IDS.items() if index == row["index_id"]), None)
            previous = self.benchmark.latest.get(asset)
            try:
                self.benchmark.handle(row)
            except (ValueError, KeyError, TypeError):
                if asset:
                    self.reference_retry_at[asset] = 0
                    self.state.health(
                        f"kalshi:{asset}:reference", "disconnected", "Invalid reference tick"
                    )
                return
            if asset and self.benchmark.latest.get(asset) != previous:
                self.reference_received[asset] = time.monotonic()
                self.reference_retry_at.pop(asset, None)
                if self.available_indexes is not None:
                    self.available_indexes.add(row["index_id"])
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
                    if ticker in self.books:
                        self.books[ticker].valid = False
                        self.awaiting_snapshots[ticker] = 0
                    self.revision += 1
            return
        if kind not in {"orderbook_snapshot", "orderbook_delta", "ticker", "trade"}:
            return
        row = packet["msg"]
        ticker = row.get("market_ticker")
        asset = self.tickers().get(ticker)
        if not asset:
            return
        ts = row["ts_ms"] / 1000 if row.get("ts_ms") is not None else row.get("ts")
        if ts is None and packet.get("sending_ts_ms") is not None:
            ts = packet["sending_ts_ms"] / 1000
        exchange_ts = timestamp(ts) if ts is not None else None
        if kind in {"orderbook_snapshot", "orderbook_delta"}:
            sid = packet["sid"]
            sub = self.subscriptions.get("orderbook_delta")
            if sub and sub.get("sid") is not None and sub["sid"] != sid:
                return
            self.book_sids[ticker] = sid
            # Data may precede its ACK. It proves integrity, but doesn't stop REST
            # fallback or cancel the independent acknowledgement deadline.
            if sub and sub.get("sid") is None:
                sub["sid"] = sid
                self.sid_channels[sid] = "orderbook_delta"
            book = self.books.setdefault(ticker, KalshiBook())
            try:
                if kind == "orderbook_snapshot":
                    book.snapshot(row.get("yes_dollars_fp", []), row.get("no_dollars_fp", []))
                    self.awaiting_snapshots.pop(ticker, None)
                    for request_id, request in list(self.requests.items()):
                        if request.get("action") != "get_snapshot":
                            continue
                        request["tickers"].discard(ticker)
                        if not request["tickers"]:
                            self.requests.pop(request_id, None)
                else:
                    book.delta(row["side"], row["price_dollars"], row["delta_fp"])
            except BookError as exc:
                self._invalidate_sid(sid, "Book integrity failed; awaiting fresh snapshots")
                exc.sid = sid
                raise
            self.book_received[ticker] = time.monotonic()
            self.book_exchange_ts[ticker] = exchange_ts
            if sub and all(
                self.books.get(t) and self.books[t].valid and self.book_sids.get(t) == sid
                for t in sub["tickers"]
            ):
                sub["failures"] = 0
            self._publish_book(asset, ticker, book, "live", "WebSocket", exchange_ts)
            self.stream_live = any(self.book_ready(t) for t in self.tickers())
            return
        if kind == "trade":
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
        self.state.health(f"kalshi:{asset}:{channel}", "live", "WebSocket", exchange_ts)

    async def _send(self, ws, cmd, params, channels, action=None):
        self.request_id += 1
        request_id = self.request_id
        self.requests[request_id] = {
            "channels": set(channels),
            "action": action,
            "deadline": time.monotonic()
            + getattr(self.settings, "subscription_timeout_seconds", 5),
            "sid": params.get("sid"),
            "index_ids": params.get("index_ids", []),
        }
        await asyncio.wait_for(
            ws.send(json.dumps({"id": request_id, "cmd": cmd, "params": params})),
            timeout=getattr(self.settings, "subscription_timeout_seconds", 5),
        )
        return request_id

    async def _subscribe(self, ws, channels):
        params = {"channels": list(channels)}
        tickers = set(self.tickers())
        if channels[0] in {"ticker", "trade", "orderbook_delta"}:
            params["market_tickers"] = sorted(tickers)
        elif channels[0] == "cfbenchmarks_value":
            params["index_ids"] = list(INDEX_IDS.values())
        request_id = await self._send(ws, "subscribe", params, channels)
        now = time.monotonic()
        for channel in channels:
            failures = self.subscriptions.get(channel, {}).get("failures", 0)
            self.subscriptions[channel] = {
                "sid": None,
                "acked": False,
                "request_id": request_id,
                "tickers": tickers.copy(),
                "started": now,
                "failures": failures,
            }
            if channel == "orderbook_delta":
                for ticker in tickers:
                    self.awaiting_snapshots[ticker] = now + getattr(
                        self.settings, "subscription_timeout_seconds", 5
                    )

    async def _replace_channel(self, ws, channel):
        sub = self.subscriptions[channel]
        sid = sub.get("sid")
        failures = sub.get("failures", 0) + 1
        if channel == "orderbook_delta" and failures > 2:
            raise TimeoutError("Kalshi book recovery exhausted; reconnecting")
        for request_id, request in list(self.requests.items()):
            request["channels"].discard(channel)
            if not request["channels"]:
                self.requests.pop(request_id, None)
        if channel != "orderbook_delta" and failures > 2:
            sub.update(
                failed=True,
                failures=0,
                retry_at=time.monotonic() + getattr(self.settings, "channel_retry_seconds", 30),
            )
            return
        if sid is not None:
            self.retired_sids.add(sid)
            self.sid_channels.pop(sid, None)
            if channel == "orderbook_delta":
                self._invalidate_sid(sid, "Resubscribing for fresh book snapshots")
            # Unsubscribe is best effort; a dead transport still fails the session.
            await self._send(ws, "unsubscribe", {"sids": [sid]}, [], "unsubscribe")
        sub["failures"] = failures
        await self._subscribe(ws, [channel])

    async def _book_snapshot(self, ws, tickers):
        sub = self.subscriptions.get("orderbook_delta", {})
        if not sub.get("acked") or sub.get("sid") is None or not tickers:
            return
        pending = set().union(
            *(
                request.get("tickers", set())
                for request in self.requests.values()
                if request.get("action") == "get_snapshot"
            )
        )
        tickers = set(tickers) - pending
        if not tickers:
            return
        request_id = await self._send(
            ws,
            "update_subscription",
            {"sid": sub["sid"], "market_tickers": sorted(tickers), "action": "get_snapshot"},
            ["orderbook_delta"],
            "get_snapshot",
        )
        self.requests[request_id]["tickers"] = set(tickers)
        deadline = time.monotonic() + getattr(self.settings, "subscription_timeout_seconds", 5)
        for ticker in tickers:
            self.awaiting_snapshots[ticker] = deadline

    async def _watchdog(self, ws):
        now = time.monotonic()
        for request_id, request in list(self.requests.items()):
            if request_id not in self.requests or now < request["deadline"]:
                continue
            self.requests.pop(request_id, None)
            if request.get("action") == "indexlist":
                continue
            if request.get("action") in {"subscribe_indices", "unsubscribe_indices"}:
                self._reference_failure(
                    request["index_ids"], "Index repair acknowledgement timed out; retry scheduled"
                )
                continue
            for channel in list(request["channels"]):
                if channel not in self.subscriptions:
                    continue
                self._channel_health(channel, "Acknowledgement timed out; repairing subscription")
                await self._replace_channel(ws, channel)
        desired = set(self.tickers())
        for channel, sub in list(self.subscriptions.items()):
            if sub.get("failed"):
                if now >= sub["retry_at"]:
                    await self._replace_channel(ws, channel)
                continue
            if not sub.get("acked"):
                continue
            if channel in {"ticker", "trade", "orderbook_delta"}:
                added, removed = desired - sub["tickers"], sub["tickers"] - desired
                for action, tickers in (("add_markets", added), ("delete_markets", removed)):
                    if not tickers:
                        continue
                    await self._send(
                        ws,
                        "update_subscription",
                        {"sid": sub["sid"], "market_tickers": sorted(tickers), "action": action},
                        [channel],
                        action,
                    )
                    if channel == "orderbook_delta" and action == "add_markets":
                        for ticker in tickers:
                            self.awaiting_snapshots[ticker] = now + getattr(
                                self.settings, "subscription_timeout_seconds", 5
                            )
                sub["tickers"] = desired.copy()
        book_sub = self.subscriptions.get("orderbook_delta", {})
        if book_sub.get("acked") and not book_sub.get("failed"):
            pending = {t: d for t, d in self.awaiting_snapshots.items() if t in desired}
            if any(deadline and now >= deadline for deadline in pending.values()):
                await self._replace_channel(ws, "orderbook_delta")
            else:
                verify = {
                    ticker
                    for ticker in desired
                    if ticker not in pending and not self.book_ready(ticker)
                }
                verify.update(ticker for ticker, deadline in pending.items() if not deadline)
                await self._book_snapshot(ws, verify)
        cf = self.subscriptions.get("cfbenchmarks_value", {})
        if cf.get("acked") and not cf.get("failed"):
            if (
                not cf.get("indexlist_requested")
                or now >= cf.get("indexlist_refresh_at", float("inf"))
            ) and not any(
                request.get("action") == "indexlist" for request in self.requests.values()
            ):
                await self._send(
                    ws,
                    "update_subscription",
                    {"sid": cf["sid"], "action": "indexlist"},
                    ["cfbenchmarks_value"],
                    "indexlist",
                )
                cf["indexlist_requested"] = True
                cf["indexlist_refresh_at"] = now + getattr(
                    self.settings, "channel_retry_seconds", 30
                )
            missing = []
            for asset, index in INDEX_IDS.items():
                if self.available_indexes is not None and index not in self.available_indexes:
                    name, detail = f"kalshi:{asset}:reference", "Index unavailable on this account"
                    if self.state.sources.get(name, {}).get("detail") != detail:
                        self.state.health(name, "unavailable", detail)
                    continue
                age = now - self.reference_received.get(asset, cf["started"])
                if age <= getattr(self.settings, "reference_stale_seconds", 3):
                    continue
                if now < self.reference_retry_at.get(asset, 0):
                    continue
                self.state.health(
                    f"kalshi:{asset}:reference",
                    "disconnected",
                    "Reference feed silent; repairing index",
                )
                missing.append(index)
                self.reference_retry_at[asset] = now + getattr(
                    self.settings, "channel_retry_seconds", 30
                )
            if missing:
                for action in ("unsubscribe_indices", "subscribe_indices"):
                    await self._send(
                        ws,
                        "update_subscription",
                        {"sid": cf["sid"], "index_ids": missing, "action": action},
                        ["cfbenchmarks_value"],
                        action,
                    )

    async def websocket_session(self):
        if not self.tickers():
            self.state.health("kalshi:socket", "unavailable", "Waiting for market discovery")
            await asyncio.sleep(1)
            return
        self.invalidate()
        self.sequences.clear()
        self.requests.clear()
        self.subscriptions.clear()
        self.sid_channels.clear()
        self.retired_sids.clear()
        self.awaiting_snapshots.clear()
        self.reference_received.clear()
        self.reference_retry_at.clear()
        self.available_indexes = None
        self.request_id = 0
        self.session_active = True
        try:
            async with connect(
                KALSHI_WS,
                additional_headers=self.signer.headers("GET", "/trade-api/ws/v2"),
                open_timeout=10,
                close_timeout=3,
                ping_interval=15,
                ping_timeout=10,
                max_queue=64,
                max_size=2**20,
            ) as ws:
                try:
                    self.socket_open = True
                    await self._subscribe(ws, ["ticker", "trade", "orderbook_delta"])
                    await self._subscribe(ws, ["cfbenchmarks_value"])
                    await self._subscribe(ws, ["market_lifecycle_v2"])
                    last_frame = time.monotonic()
                    while True:
                        try:
                            frame = await asyncio.wait_for(ws.recv(), timeout=0.25)
                        except TimeoutError:
                            if time.monotonic() - last_frame > 30:
                                raise TimeoutError("No Kalshi messages")
                        else:
                            last_frame = time.monotonic()
                            self.state.health(
                                "kalshi:socket", "live", "Authenticated read-only connection"
                            )
                            try:
                                self.handle(json.loads(frame, parse_float=Decimal))
                            except BookError as exc:
                                if not hasattr(exc, "sid"):
                                    raise
                                # A gap affects the complete SID, not unrelated channels.
                                await self._book_snapshot(
                                    ws, set(self.tickers()) & set(self.awaiting_snapshots)
                                )
                        await self._watchdog(ws)
                finally:
                    self.invalidate()
        finally:
            self.session_active = False

    async def _poll_book(self, client, asset, ticker):
        if self.book_ready(ticker):
            return
        target = self.state.assets[asset]["market"]
        version = self.book_versions.get(ticker, 0)
        requested_at = time.time()
        try:
            response = await self.get(client, f"/markets/{ticker}/orderbook", depth=100)
            data = response["orderbook_fp"]
            book = KalshiBook()
            book.snapshot(data["yes_dollars"], data["no_dollars"])
            if self.state.assets[asset]["market"] is not target or ticker not in self.tickers():
                return
            self.rest_books[ticker] = book
            if self.book_ready(ticker) or self.book_versions.get(ticker, 0) != version:
                return
            self._publish_book(
                asset,
                ticker,
                book,
                "polling",
                "REST snapshot; streaming book unavailable",
                requested_at,
            )
        except Exception as exc:
            if self.state.assets[asset]["market"] is not target or self.book_ready(ticker):
                return
            if self.book_versions.get(ticker, 0) == version:
                self.state.assets[asset]["kalshi"]["book"] = {"valid": False}
                self.state.health(f"kalshi:{asset}:book", "disconnected", error_label(exc))

    async def _poll_trades(self, client, asset, ticker):
        sub = self.subscriptions.get("trade", {})
        if self.socket_open and sub.get("acked") and not sub.get("failed"):
            return  # A quiet execution stream is not a failed stream.
        target = self.state.assets[asset]["market"]
        try:
            data = await self.get(client, "/markets/trades", ticker=ticker, limit=100)
            if self.state.assets[asset]["market"] is not target or ticker not in self.tickers():
                return
            current = self.subscriptions.get("trade", {})
            if self.socket_open and current.get("acked") and not current.get("failed"):
                return
            for row in reversed(data["trades"]):
                self.record_trade(asset, ticker, row)
            self.state.health(f"kalshi:{asset}:trade", "polling", "REST recent executions")
        except Exception as exc:
            current = self.subscriptions.get("trade", {})
            if self.state.assets[asset]["market"] is target and not (
                self.socket_open and current.get("acked") and not current.get("failed")
            ):
                self.state.health(f"kalshi:{asset}:trade", "disconnected", error_label(exc))

    async def poll_asset(self, client, asset, ticker):
        # A missing trade response cannot hold up publication of an executable book.
        await asyncio.gather(
            self._poll_book(client, asset, ticker), self._poll_trades(client, asset, ticker)
        )
        if not self.socket_open:
            self.state.health(
                f"kalshi:{asset}:reference",
                "unavailable",
                "Authenticated CF Benchmarks WebSocket required",
            )

    async def _discovery_worker(self, client, semaphore, asset, series):
        next_due, generation, failures = 0, -1, 0
        while True:
            if time.monotonic() < next_due and generation == self.discovery_generation:
                await asyncio.sleep(0.25)
                continue
            generation = self.discovery_generation
            started = time.monotonic()
            try:
                async with semaphore:
                    await self.discover_asset(client, asset, series)
                failures = 0
                interval = self.settings.discovery_seconds
            except Exception as exc:
                failures += 1
                interval = min(self.settings.discovery_seconds, 2 ** min(failures, 5))
                self.state.health(f"kalshi:{asset}:discovery", "disconnected", error_label(exc))
            next_due = max(started + interval, time.monotonic() + 0.25)

    async def _fee_worker(self, client, semaphore, asset):
        while True:
            market = self.state.assets[asset]["market"]
            cached = self.series_cache.get(asset)
            now = time.monotonic()
            if market and cached and now >= self.fee_due.get(asset, 0):
                self.fee_due[asset] = now + getattr(self.settings, "fees_seconds", 30)
                try:
                    async with semaphore:
                        await self.effective_fees(client, market, *cached, guard=True)
                except Exception as exc:
                    if self.state.assets[asset]["market"] is market:
                        market["fees_verified_at"] = None
                        self.state.health(f"kalshi:{asset}:fees", "unavailable", error_label(exc))
            await asyncio.sleep(0.25)

    async def _fallback_worker(self, client, semaphore, asset):
        failures = 0
        while True:
            started = time.monotonic()
            market = self.state.assets[asset]["market"]
            if market and market["close_time"] > time.time():
                async with semaphore:
                    await self.poll_asset(client, asset, market["ticker"])
                source = self.state.sources.get(f"kalshi:{asset}:book", {})
                failures = failures + 1 if source.get("status") == "disconnected" else 0
            interval = self.settings.rest_seconds
            if failures:
                interval = max(interval, min(30, 2 ** min(failures, 5)))
            await asyncio.sleep(max(0.05, interval - (time.monotonic() - started)))

    async def _refresh_worker(self):
        while True:
            await self.refresh.wait()
            self.refresh.clear()
            self.discovery_generation += 1

    async def rest_loop(self):
        concurrency = max(1, int(getattr(self.settings, "rest_concurrency", 3)))
        # Each workload owns a concurrency budget. Slow fee/discovery requests
        # cannot consume all HTTP connections or hold up another asset's fallback.
        discovery_limit = asyncio.Semaphore(concurrency)
        fee_limit = asyncio.Semaphore(concurrency)
        fallback_limit = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(
            timeout=getattr(self.settings, "rest_request_timeout_seconds", 8),
            limits=httpx.Limits(max_connections=concurrency * 4, max_keepalive_connections=12),
        ) as client:
            async with asyncio.TaskGroup() as group:
                group.create_task(self._refresh_worker())
                for asset, series in ASSETS.items():
                    group.create_task(
                        self._discovery_worker(client, discovery_limit, asset, series)
                    )
                    group.create_task(self._fee_worker(client, fee_limit, asset))
                    group.create_task(self._fallback_worker(client, fallback_limit, asset))

    async def run(self):
        auth_ok = False
        try:
            credentials = load_for_settings(self.settings)
            if credentials:
                self.signer = Signer.from_credentials(credentials)
                auth_ok = True
            else:
                self.state.health(
                    "kalshi:socket",
                    "unavailable",
                    "Import local credentials on Windows or configure the credential environment variables",
                )
        except Exception:
            self.state.health(
                "kalshi:socket",
                "unavailable",
                "Cannot unlock signing credentials; check local setup",
            )
        if auth_ok:
            async with asyncio.TaskGroup() as group:
                group.create_task(self.rest_loop())
                group.create_task(
                    retry(self.state, "kalshi:socket", self.websocket_session, self.invalidate)
                )
        else:
            await self.rest_loop()
