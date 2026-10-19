"""Opt-in real-data acceptance check. A blocked/partial run exits nonzero."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.connectors.kalshi import Kalshi
from backend.app.connectors.kraken import Kraken
from backend.app.settings import ASSETS, Settings
from backend.app.state import State


async def check(seconds):
    state = State()
    tasks = [
        asyncio.create_task(Kalshi(state, Settings.from_env()).run()),
        asyncio.create_task(Kraken(state).run()),
    ]
    try:
        await asyncio.sleep(seconds)
        # Connector shutdown intentionally invalidates all books. Sample the
        # running feeds before cancellation, not the expected stopped state.
        snapshot = state.snapshot()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    blockers = []
    for asset in ASSETS:
        item = snapshot["assets"][asset]
        market = item["market"]
        if not market:
            blockers.append(f"{asset}: no current market")
        else:
            if not market["outcome_mapping"]:
                blockers.append(f"{asset}: contract outcome rules unverified")
            if market["current_index"] is None:
                blockers.append(f"{asset}: current CF Benchmarks reference unavailable")
        for venue in ("kalshi", "kraken"):
            channels = (
                ("book", "trade", "ticker", "reference")
                if venue == "kalshi"
                else ("book", "trade", "ticker")
            )
            for channel in channels:
                source = f"{venue}:{asset}:{channel}"
                status = snapshot["sources"].get(source, {}).get("status", "missing")
                if status != "live":
                    blockers.append(f"{source}: {status}")
    observations = {
        asset: {
            "market": item["market"]["ticker"] if item["market"] else None,
            "outcome_mapping": item["market"]["outcome_mapping"] if item["market"] else None,
            "kraken_spot": item["kraken"].get("ticker", {}).get("last"),
            "kraken_trades": len(item["kraken"].get("trades", [])),
            "kalshi_trades": len(item["kalshi"].get("trades", [])),
            "kalshi_book_valid": item["kalshi"].get("book", {}).get("valid", False),
        }
        for asset, item in snapshot["assets"].items()
    }
    print(
        json.dumps(
            {
                "observations": observations,
                "source_status": {k: v["status"] for k, v in snapshot["sources"].items()},
                "blockers": blockers,
            },
            indent=2,
        )
    )
    return 2 if blockers else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error("--seconds must be between 1 and 120")
    raise SystemExit(asyncio.run(check(args.seconds)))
