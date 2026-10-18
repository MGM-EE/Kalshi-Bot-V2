"""Bounded streaming observations used by strategy rules; no fabricated warmup data."""

from collections import deque
from decimal import Decimal

from ..markets.orderbook import number
from ..settings import ASSETS


class Features:
    def __init__(self, state):
        self.state = state
        self.history = {
            a: {
                "index": deque(maxlen=3600),
                "spot": deque(maxlen=3600),
                "yes": deque(maxlen=600),
                "no": deque(maxlen=600),
            }
            for a in ASSETS
        }
        self.rounds = {}
        self.advanced = None

    def observe(self, source):
        parts = source.split(":")
        if len(parts) != 3 or parts[1] not in ASSETS:
            return
        venue, asset, channel = parts
        target, history = self.state.assets[asset], self.history[asset]
        market = target["market"]
        ticker = market["ticker"] if market else None
        if ticker != self.rounds.get(asset):
            history["index"].clear()
            history["yes"].clear()
            history["no"].clear()
            self.rounds[asset] = ticker
        health = self.state.sources[source]
        ts = health["received_at"] if health["exchange_ts"] is None else health["exchange_ts"]
        if health["status"] not in {"live", "polling"}:
            return
        rows = []
        if (
            venue == "kalshi"
            and channel == "reference"
            and market
            and market.get("current_index") is not None
        ):
            rows.append(("index", market["current_index"]))
        if venue == "kraken" and channel == "ticker":
            last = target["kraken"].get("ticker", {}).get("last")
            if last is not None:
                rows.append(("spot", last))
        if venue == "kalshi" and channel == "book":
            book = target["kalshi"].get("book", {})
            if book.get("valid"):
                rows += [
                    (side, book[side + "_asks"][0][0])
                    for side in ("yes", "no")
                    if book.get(side + "_asks")
                ]
        for key, value in rows:
            buffer = history[key]
            if buffer and ts <= buffer[-1][0]:
                continue
            if key != "index" and buffer and int(ts) == int(buffer[-1][0]):
                buffer[-1] = (ts, number(value))
            else:
                buffer.append((ts, number(value)))
            while buffer and buffer[0][0] < ts - 600:
                buffer.popleft()

    def metrics(self, asset, outcome, now):
        target, history = self.state.assets[asset], self.history[asset]
        market = target["market"]
        result = {}
        if not market or not market.get("outcome_mapping"):
            return result
        direction = Decimal(1) if market["outcome_mapping"][outcome] == "UP" else Decimal(-1)
        current, line = market.get("current_index"), market.get("opening_reference")
        source = self.state.sources.get(f"kalshi:{asset}:reference", {})
        ts = source.get("exchange_ts")
        index = [row for row in history["index"] if now - row[0] <= 60]
        if current is not None and line is not None and ts is not None and -5 <= now - ts <= 15:
            current, line = number(current), number(line)
            delta = current - line
            result.update(
                distance=abs(delta),
                distance_percent=abs(delta) / line * 100 if line else None,
                losing_side=Decimal(int(delta * direction < 0)),
                signed_distance=delta * direction,
                observations=len(history["index"]),
                data_age=Decimal(str(now - ts)),
                source_ts=ts,
            )
            recent = [row for row in index if row[0] >= ts - 5]
            if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
                velocity = (abs(recent[0][1] - line) - abs(recent[-1][1] - line)) / Decimal(
                    str(recent[-1][0] - recent[0][0])
                )
                result.update(
                    approach_velocity=velocity, approach_positive=Decimal(int(velocity > 0))
                )
            if len(index) >= 3:
                average = sum((v for _, v in index), Decimal(0)) / len(index)
                variance = sum(((v - average) ** 2 for _, v in index), Decimal(0)) / (
                    len(index) - 1
                )
                result["normalized_distance"] = abs(delta) / variance.sqrt() if variance else None
        spot = [row for row in history["spot"] if now - row[0] <= 30]
        if len(spot) >= 2 and now - spot[-1][0] <= 15:
            window = [row for row in spot if row[0] >= spot[-1][0] - 5]
            if len(window) >= 2:
                momentum = (window[-1][1] / window[0][1] - 1) * 10000 * direction
                result["momentum"] = max(Decimal(-100), min(Decimal(100), momentum))
            changes = [(b[1] - a[1]) * direction for a, b in zip(spot, spot[1:]) if b[1] != a[1]]
            result["consistency"] = (
                Decimal(sum(c > 0 for c in changes)) / len(changes) if changes else Decimal(0)
            )
            if current and number(current):
                result["divergence"] = abs(spot[-1][1] / number(current) - 1) * 100
        prices = [row for row in history[outcome] if now - row[0] <= 15]
        if len(prices) >= 3:
            a, b, c = prices[-3:]
            if c[0] > b[0] > a[0]:
                first = (b[1] - a[1]) / Decimal(str(b[0] - a[0]))
                last = (c[1] - b[1]) / Decimal(str(c[0] - b[0]))
                result["contract_acceleration"] = (last - first) / Decimal(str(c[0] - a[0]))
        trades = [
            t
            for t in target["kraken"].get("trades", [])
            if t.get("exchange_ts") and 0 <= now - t["exchange_ts"] <= 60
        ]
        if trades:
            volume = sum((number(t["quantity"]) for t in trades), Decimal(0))
            signed = sum((number(t["signed_quantity"]) for t in trades), Decimal(0))
            result["flow_imbalance"] = signed / volume * direction if volume else None
        if self.advanced:
            result.update(self.advanced.metrics(asset, outcome, now))
        return result
