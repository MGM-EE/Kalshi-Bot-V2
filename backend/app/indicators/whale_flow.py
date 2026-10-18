"""Venue-specific observed trades; thresholds use prior samples only."""

from bisect import bisect_left, insort
from collections import deque
from decimal import ROUND_CEILING, Decimal

from ..markets.orderbook import number

WINDOWS = (5, 15, 30, 60, 300)


class WhaleFlow:
    def __init__(self, config, fixed_threshold, burst_threshold=None):
        self.config, self.fixed = config, number(fixed_threshold)
        self.burst_threshold = burst_threshold
        self.events, self.samples, self.sorted = deque(), deque(), []
        self.last_timestamp = None
        self.started_at = None
        self.latest_dropped = None
        self.pressure_sign, self.pressure_since = 0, None
        self.burst_events, self.large_events = deque(), deque()
        self.burst_notional, self.large_net = Decimal(0), Decimal(0)

    def add(self, ts, quantity, price, sign, size=None):
        ts, quantity, price = float(ts), number(quantity), number(price)
        if (
            quantity <= 0
            or price <= 0
            or sign not in {-1, 0, 1}
            or (self.last_timestamp is not None and ts < self.last_timestamp)
        ):
            return None
        size = number(size) if size is not None else quantity * price
        prior_count = len(self.sorted)
        percentile, median = None, None
        if prior_count >= self.config.minimum_samples:
            rank = max(
                0,
                int(
                    (self.config.percentile * prior_count).to_integral_value(rounding=ROUND_CEILING)
                )
                - 1,
            )
            percentile = self.sorted[rank]
            median = (
                self.sorted[prior_count // 2]
                if prior_count % 2
                else (self.sorted[prior_count // 2 - 1] + self.sorted[prior_count // 2]) / 2
            )
        large = self.config.fixed_enabled and size >= self.fixed
        large = large or (
            self.config.percentile_enabled and percentile is not None and size > percentile
        )
        large = large or (
            self.config.median_enabled
            and median is not None
            and size >= median * self.config.median_multiple
        )
        notional = price * quantity
        while self.burst_events and ts - self.burst_events[0][0] > 1:
            self.burst_notional -= self.burst_events.popleft()[1]
        self.burst_events.append((ts, notional))
        self.burst_notional += notional
        if len(self.burst_events) > self.config.max_window_trades:
            self.burst_notional -= self.burst_events.popleft()[1]
        burst = self.burst_notional
        if self.config.burst_enabled and self.burst_threshold and burst >= self.burst_threshold:
            large = True
        event = {
            "ts": ts,
            "quantity": quantity,
            "notional": notional,
            "sign": sign,
            "large": bool(large),
            "prior_percentile": percentile,
            "prior_median": median,
            "burst": bool(
                self.config.burst_enabled and self.burst_threshold and burst >= self.burst_threshold
            ),
        }
        if self.started_at is None:
            self.started_at = ts
        self.events.append(event)
        self.samples.append(size)
        insort(self.sorted, size)
        if len(self.samples) > self.config.history_samples:
            old = self.samples.popleft()
            self.sorted.pop(bisect_left(self.sorted, old))
        while self.events and self.events[0]["ts"] < ts - 300:
            self.events.popleft()
        while len(self.events) > self.config.max_window_trades:
            self.latest_dropped = self.events.popleft()["ts"]
        self.last_timestamp = ts
        if large:
            self.large_events.append(event)
            self.large_net += notional * sign
        while self.large_events and (
            ts - self.large_events[0]["ts"] > self.config.flow_window_seconds
            or len(self.large_events) > self.config.max_window_trades
        ):
            old = self.large_events.popleft()
            self.large_net -= old["notional"] * old["sign"]
        net = self.large_net
        direction = 1 if net > 0 else -1 if net < 0 else 0
        if direction != self.pressure_sign:
            self.pressure_sign, self.pressure_since = direction, ts
        return event

    def snapshot(self, now):
        windows = {}
        for seconds in WINDOWS:
            events = [e for e in self.events if 0 <= now - e["ts"] <= seconds]
            large = [e for e in events if e["large"]]
            buys = sum((e["notional"] for e in large if e["sign"] == 1), Decimal(0))
            sells = sum((e["notional"] for e in large if e["sign"] == -1), Decimal(0))
            contracts_buy = sum((e["quantity"] for e in large if e["sign"] == 1), Decimal(0))
            contracts_sell = sum((e["quantity"] for e in large if e["sign"] == -1), Decimal(0))
            total_quantity = sum((e["quantity"] for e in events), Decimal(0))
            total_notional = sum((e["notional"] for e in events), Decimal(0))
            windows[str(seconds)] = {
                "large_buy_usd": buys,
                "large_sell_usd": sells,
                "net_large_usd": buys - sells,
                "large_buy_quantity": contracts_buy,
                "large_sell_quantity": contracts_sell,
                "net_large_quantity": contracts_buy - contracts_sell,
                "buy_sell_ratio": buys / sells if sells else None,
                "largest_observed_usd": max((e["notional"] for e in events), default=None),
                "large_trade_count": len(large),
                "trade_intensity_per_second": Decimal(len(events)) / seconds,
                "cvd_quantity": sum((e["quantity"] * e["sign"] for e in events), Decimal(0)),
                "volume_quantity": total_quantity,
                "volume_usd": total_notional,
                "vwap": total_notional / total_quantity if total_quantity else None,
                "unknown_direction_count": sum(e["sign"] == 0 for e in events),
                "burst_count": sum(e["burst"] for e in events),
                "complete": self.started_at is not None
                and self.started_at <= now - seconds
                and (self.latest_dropped is None or self.latest_dropped < now - seconds),
            }
        fresh = self.last_timestamp is not None and 0 <= now - self.last_timestamp <= 15
        duration = (
            max(0, self.last_timestamp - self.pressure_since)
            if self.pressure_since is not None
            else 0
        )
        direction = (
            "BUY_DOMINANT"
            if self.pressure_sign > 0
            else "SELL_DOMINANT"
            if self.pressure_sign < 0
            else "NEUTRAL"
        )
        pressure = direction if duration >= self.config.confirmation_seconds else "CONFIRMING"
        previous = [e for e in self.events if 5 < now - e["ts"] <= 10 and e["large"]]
        previous_net = sum((e["notional"] * e["sign"] for e in previous), Decimal(0))
        acceleration = (
            (windows["5"]["net_large_usd"] - previous_net) / 25
            if self.started_at is not None and self.started_at <= now - 10
            else None
        )
        return {
            "windows": windows,
            "fresh": fresh,
            "samples": len(self.samples),
            "large_flow_acceleration_usd_per_second_squared": acceleration,
            "pressure": pressure if fresh else "INSUFFICIENT DATA",
            "pressure_duration": duration,
            "interpretation": "Observed aggressive exposure, not participant holdings or intent",
        }
