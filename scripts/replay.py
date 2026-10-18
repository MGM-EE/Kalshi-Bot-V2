"""Replay a completed recording without starting connectors or touching the live account."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.research.replay import replay
from backend.app.strategy.profiles import Profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--compare", type=Path, nargs="*", default=[])
    parser.add_argument(
        "--period", choices=["development", "validation", "out-of-sample"], default="development"
    )
    parser.add_argument(
        "--output", type=Path, help="New JSON result file; never overwrites an existing file"
    )
    args = parser.parse_args()
    if len(args.compare) > 3:
        parser.error("Compare at most three profiles at a time")
    profiles = []
    for path in args.compare:
        if path.stat().st_size > 128000:
            parser.error("Profile exceeds 128 KB")
        profiles.append(Profile.model_validate_json(path.read_text()).model_dump(mode="json"))
    results = [replay(args.recording, profile, args.period) for profile in [None, *profiles]]
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(dict(mode="REPLAY", results=results), handle, indent=2)
    print(
        json.dumps(
            [
                dict(
                    exact_reproduction=r["exact_reproduction"],
                    comparison=r["strategy_comparison"],
                    dataset=r["dataset_sha256"],
                    events=r["events"],
                    closed_trades=r["analytics"]["total_closed_trades"],
                    net_closed_pnl=r["analytics"]["net_closed_pnl"],
                    performance=r["performance"],
                )
                for r in results
            ],
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
