"""Bounded market-data state; paper accounting is provided by the durable broker."""

import asyncio
import time
from collections import deque
from decimal import Decimal

from .settings import ASSETS


def wire(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [wire(v) for v in value]
    return value


class State:
    def __init__(self, stale_seconds=15):
        self.stale_seconds = stale_seconds
        self.sources = {}
        self.assets = {
            asset: {"market": None, "upcoming": [], "kalshi": {}, "kraken": {}} for asset in ASSETS
        }
        self.events = deque(maxlen=60)
        self.changed = asyncio.Event()
        self.paper_provider = None

    def health(self, name, status, detail="", exchange_ts=None, stale_after=None):
        now = time.time()
        previous = self.sources.get(name, {})
        if previous.get("status") != status or previous.get("detail") != detail:
            self.events.append({"time": now, "source": name, "status": status, "detail": detail})
        self.sources[name] = {
            "status": status,
            "detail": detail,
            "received_at": now,
            "exchange_ts": exchange_ts,
            "stale_after": self.stale_seconds if stale_after is None else stale_after,
        }
        self.changed.set()

    def snapshot(self, include_paper=True):
        now = time.time()
        health = {}
        for name, raw in self.sources.items():
            age = now - raw["received_at"]
            status = raw["status"]
            exchange_age = None if raw["exchange_ts"] is None else now - raw["exchange_ts"]
            if status in {"live", "polling"} and (
                age > raw["stale_after"]
                or (exchange_age is not None and exchange_age > raw["stale_after"])
            ):
                status = "stale"
            if exchange_age is not None and exchange_age < -5:
                status = "clock_drift"
            health[name] = {
                **raw,
                "status": status,
                "age_seconds": round(age, 2),
                "exchange_age_seconds": exchange_age,
            }
        # Automated strategies remain disabled; a heartbeat is not trading readiness.
        return wire(
            {
                "mode": "DISCONNECTED / PAUSED",
                "execution_enabled": False,
                "phase": 2,
                "timestamp": now,
                "sources": health,
                "assets": self.assets,
                "events": self.events,
                "paper": self.paper_provider() if include_paper and self.paper_provider else None,
            }
        )
