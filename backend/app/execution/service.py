import asyncio
import json
import time
from pathlib import Path

import httpx

from ..connectors.common import error_label
from ..storage.database import Database
from .configuration import initialize
from .paper_broker import PaperBroker


def open_broker(state, settings):
    data = json.loads(Path(settings.paper_config_path).read_text())
    if set(data) != {"execution", "risk"}:
        raise ValueError("Paper configuration requires execution and risk sections only")
    database = Database(settings.paper_database_path)
    try:
        saved = initialize(database, data)
        return PaperBroker(database, state, saved.execution, saved.risk, clock=state.clock)
    except BaseException:
        database.close()
        raise


async def paper_worker(broker, state, strategy=None):
    last_run, last_heartbeat, last_mark = 0, 0, None
    while True:
        state.changed.clear()
        if time.monotonic() - last_run < 0.05:
            await asyncio.sleep(0.05)
        try:
            with broker.clock.at():
                cycle_error = None
                mark = last_mark is None or broker.clock() - last_mark >= 0.25
                state.in_cycle = True
                try:
                    if mark:
                        with broker.db.transaction():
                            broker.mark_account(broker.clock())
                        last_mark = broker.clock()
                    if strategy is not None:
                        strategy.tick()
                    if broker.db.active():
                        broker.process()
                except Exception as exc:
                    cycle_error = type(exc).__name__
                    raise
                finally:
                    state.in_cycle = False
                    if state.record_event:
                        state.record_event("cycle", {"error": cycle_error, "mark_account": mark})
            if time.monotonic() - last_heartbeat >= 5:
                state.health(
                    "paper:engine",
                    "live",
                    "Paper execution worker; strategy control determines automatic entries",
                )
                last_heartbeat = time.monotonic()
        except Exception as exc:
            state.health("paper:engine", "paused", error_label(exc))
            await asyncio.sleep(1)
        last_run = time.monotonic()
        try:
            await asyncio.wait_for(state.changed.wait(), timeout=0.1)
        except TimeoutError:
            pass


async def settlement_worker(broker, kalshi, state):
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            now = time.time()
            with broker.db.transaction():
                tickers = {
                    p["ticker"]
                    for p in broker.db.positions()
                    if float(p["quantity"]) > 0 and p["close_time"] <= now
                }
            for ticker in tickers:
                try:
                    try:
                        response = await kalshi.get(client, f"/markets/{ticker}")
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code != 404:
                            raise
                        response = await kalshi.get(client, f"/historical/markets/{ticker}")
                    market = response["market"]
                    if market["status"] in {"finalized", "settled"}:
                        broker.settle(ticker, market)
                        state.health(
                            "paper:settlement", "polling", "Official exchange settlement reconciled"
                        )
                    else:
                        state.health(
                            "paper:settlement", "unavailable", "Awaiting final exchange result"
                        )
                except Exception as exc:
                    state.health("paper:settlement", "unavailable", error_label(exc))
            await asyncio.sleep(5)
