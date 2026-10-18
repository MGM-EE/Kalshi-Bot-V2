"""Official CF channel. Trailing averages and partial final-minute averages stay distinct."""

import json
from decimal import Decimal

from ..markets.discovery import INDEX_IDS
from ..markets.orderbook import number


class Benchmark:
    def __init__(self, state):
        self.state = state
        self.latest = {}

    def handle(self, row):
        asset = next((a for a, index in INDEX_IDS.items() if index == row["index_id"]), None)
        if asset is None:
            return
        raw = json.loads(row["data"], parse_float=Decimal)
        if raw.get("type") != "value" or raw.get("id") != row["index_id"]:
            raise ValueError("CF upstream index mismatch")
        ts = int(raw["time"])
        if ts <= self.latest.get(asset, 0):
            return
        value = number(raw["value"])
        if value <= 0:
            raise ValueError("Invalid CF index value")
        trailing = row["avg_60s_data"]
        final = row.get("last_60s_windowed_average_15min")
        result = {
            "index_id": row["index_id"],
            "value": value,
            "source_ts": ts / 1000,
            "trailing_average": trailing,
            "partial_final_minute_average": final,
        }
        self.latest[asset] = ts
        self.state.assets[asset]["kalshi"]["benchmark"] = result
        market = self.state.assets[asset]["market"]
        if market and market.get("index_id") == row["index_id"]:
            market["current_index"] = value
            market["reference_status"] = "CF Benchmarks via authenticated Kalshi WebSocket"
            market["trailing_average"] = trailing
            market["partial_final_minute_average"] = None
            if final and market.get("averaging_window_seconds") == 60:
                close_ms = int(market["close_time"] * 1000)
                # Metadata names its end 'exclusive', but the channel's documented final-minute
                # accumulation includes the close tick. Check the selected market's own window.
                if (
                    int(final["window_start_ts_ms"]) == close_ms - 60000
                    and close_ms - 60000 < ts <= close_ms
                    and int(final["window_end_ts_exclusive"]) == ts
                ):
                    market["partial_final_minute_average"] = final
            # A partial observed average is not a forecast or the official settlement result.
            market["projected_settlement_average"] = None
        self.state.health(
            f"kalshi:{asset}:reference", "live", "CF Benchmarks index stream", ts / 1000
        )
