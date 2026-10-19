import asyncio
import json
import time

from backend.app.settings import ASSETS
from scripts import live_check


async def test_live_check_samples_running_feeds_before_shutdown(monkeypatch, capsys):
    stopped = []

    class Kalshi:
        def __init__(self, state, settings):
            self.state = state

        async def run(self):
            try:
                for asset in ASSETS:
                    target = self.state.assets[asset]
                    target["market"] = {
                        "ticker": f"TEST-{asset}", "outcome_mapping": {"yes": "UP", "no": "DOWN"},
                        "current_index": "100",
                    }
                    target["kalshi"] = {"book": {"valid": True}, "trades": [{"trade_id": 1}]}
                    for channel in ("book", "trade", "ticker", "reference"):
                        self.state.health(f"kalshi:{asset}:{channel}", "live", exchange_ts=time.time())
                await asyncio.Future()
            finally:
                for name in list(self.state.sources):
                    if name.startswith("kalshi:"):
                        self.state.health(name, "disconnected")
                stopped.append("kalshi")

    class Kraken:
        def __init__(self, state):
            self.state = state

        async def run(self):
            try:
                for asset in ASSETS:
                    self.state.assets[asset]["kraken"] = {
                        "ticker": {"last": "100"}, "trades": [{"id": 1}],
                    }
                    for channel in ("book", "trade", "ticker"):
                        self.state.health(f"kraken:{asset}:{channel}", "live", exchange_ts=time.time())
                await asyncio.Future()
            finally:
                for name in list(self.state.sources):
                    if name.startswith("kraken:"):
                        self.state.health(name, "disconnected")
                stopped.append("kraken")

    monkeypatch.setattr(live_check, "Kalshi", Kalshi)
    monkeypatch.setattr(live_check, "Kraken", Kraken)
    assert await live_check.check(0.01) == 0
    assert set(stopped) == {"kalshi", "kraken"}
    assert json.loads(capsys.readouterr().out)["blockers"] == []
