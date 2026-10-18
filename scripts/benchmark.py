"""Deterministic synthetic stress fixture; never connected to the application account."""

import argparse
import json
import sys
import time
import tracemalloc
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.indicators.config import IndicatorConfig
from backend.app.indicators.engine import IndicatorEngine
from backend.app.indicators.features import Features
from backend.app.research.clock import EventClock
from backend.app.state import State
from backend.app.storage.database import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=int, default=10000)
    parser.add_argument(
        "--trace-memory",
        action="store_true",
        help="Measure allocations; instrumentation substantially increases timings",
    )
    args = parser.parse_args()
    if not 100 <= args.events <= 100000:
        parser.error("Choose 100..100000 fixture events")
    clock = EventClock(1000.0)
    state = State(clock=clock)
    features = Features(state)
    db = Database(":memory:")
    indicators = IndicatorEngine(state, features, db)
    state.observers.extend([features.observe, indicators.observe])
    histories = {a: deque(maxlen=100) for a in state.assets}
    samples = []
    snapshot_samples = []
    if args.trace_memory:
        tracemalloc.start()
    start = time.perf_counter()
    for i in range(args.events):
        clock.fixed = 1000 + i * 0.02
        asset = ("BTC", "ETH", "SOL")[i % 3]
        histories[asset].append(
            dict(
                id=i,
                price=str(100 + i % 11),
                quantity="100",
                signed_quantity="100" if i % 2 else "-100",
                aggressor_side="buy" if i % 2 else "sell",
                exchange_ts=clock(),
            )
        )
        state.assets[asset]["kraken"]["trades"] = list(histories[asset])
        state.assets[asset]["kraken"]["ticker"] = {"last": str(100 + i % 11)}
        begin = time.perf_counter()
        state.health(f"kraken:{asset}:trade", "live", exchange_ts=clock())
        state.health(f"kraken:{asset}:ticker", "live", exchange_ts=clock())
        samples.append((time.perf_counter() - begin) * 1000)
        if i % 25 == 0:
            begin = time.perf_counter()
            indicators.snapshot(clock())
            snapshot_samples.append((time.perf_counter() - begin) * 1000)
    duration = time.perf_counter() - start
    peak = None
    if args.trace_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    samples.sort()
    snapshot_samples.sort()
    assert all(
        len(flow.events) <= IndicatorConfig().max_window_trades for flow in indicators.flow.values()
    )
    print(
        json.dumps(
            dict(
                fixture="DETERMINISTIC SYNTHETIC STRESS ONLY",
                events=args.events,
                seconds=duration,
                source_pair_p95_ms=samples[int(len(samples) * 0.95)],
                snapshot_all_assets_p95_ms=snapshot_samples[int(len(snapshot_samples) * 0.95)],
                traced_peak_memory_mib=peak / 2**20 if peak is not None else None,
                allocation_tracing=args.trace_memory,
                retained_trades={
                    a: len(indicators.flow[(a, "kraken")].events) for a in state.assets
                },
            ),
            indent=2,
        )
    )
    db.close()


if __name__ == "__main__":
    main()
