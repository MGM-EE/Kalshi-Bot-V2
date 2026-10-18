from collections import deque

from ..markets.orderbook import number


class LiquidityPersistence:
    def __init__(self, threshold, gap_seconds=3, notional=True):
        self.threshold, self.gap_seconds, self.notional = number(threshold), gap_seconds, notional
        self.levels, self.last_ts = {}, None
        self.events = deque(maxlen=30)
        self.valid = False

    def invalidate(self):
        self.levels.clear()
        self.valid = False
        self.last_ts = None

    def update(self, bids, asks, ts):
        ts = float(ts)
        if self.last_ts is not None and ts < self.last_ts:
            return
        if self.last_ts is not None and ts - self.last_ts > self.gap_seconds:
            self.levels.clear()
        current = {}
        for side, rows in (("bid", bids), ("ask", asks)):
            for price, qty in rows:
                price, qty = number(price), number(qty)
                magnitude = price * qty if self.notional else qty
                if magnitude < self.threshold:
                    continue
                key = (side, price)
                previous = self.levels.get(key)
                current[key] = {
                    "side": side,
                    "price": price,
                    "quantity": qty,
                    "first_seen": previous["first_seen"] if previous else ts,
                    "last_seen": ts,
                    "replenishments": (previous["replenishments"] + int(qty > previous["quantity"]))
                    if previous
                    else 0,
                }
        for key, previous in self.levels.items():
            if key not in current:
                self.events.append(
                    {
                        "time": ts,
                        "status": "Large bid no longer visible"
                        if key[0] == "bid"
                        else "Large ask no longer visible",
                        "price": previous["price"],
                        "reason": "Left subscribed depth or fell below threshold; fill/cancel cause unknown",
                    }
                )
        self.levels, self.last_ts, self.valid = current, ts, True

    def snapshot(self, now):
        fresh = (
            self.valid and self.last_ts is not None and 0 <= now - self.last_ts <= self.gap_seconds
        )
        levels = (
            [
                {**row, "persistence_seconds": row["last_seen"] - row["first_seen"]}
                for row in self.levels.values()
            ]
            if fresh
            else []
        )
        bid = max((r["persistence_seconds"] for r in levels if r["side"] == "bid"), default=0)
        ask = max((r["persistence_seconds"] for r in levels if r["side"] == "ask"), default=0)
        return {
            "fresh": fresh,
            "levels": levels,
            "bid_persistence_seconds": bid,
            "ask_persistence_seconds": ask,
            "events": list(self.events),
            "visibility": "Aggregated L2 price levels; individual orders and holdings are unknown",
        }
