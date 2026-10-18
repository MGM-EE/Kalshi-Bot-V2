"""Bounded asynchronous gzip JSONL recording of normalized inputs, never credentials."""

import gzip
import hashlib
import json
from pathlib import Path
from queue import Full, Queue
from threading import Thread

from ..state import wire
from ..storage.database import encode

FORMAT_VERSION = 1
TABLES = (
    "account",
    "orders",
    "positions",
    "fills",
    "rounds",
    "liquidity",
    "seen_trades",
    "settlements",
    "strategy_profiles",
    "strategy_runtime",
    "strategy_intents",
    "indicator_versions",
    "execution_versions",
    "strategy_decisions",
)


def code_digest():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ledger_digest(database):
    digest = hashlib.sha256()
    with database.lock:
        for table in TABLES:
            rows = database.connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            digest.update(encode([table, sorted(rows, key=encode)]).encode())
    return digest.hexdigest()


class Recorder:
    def __init__(self, path, state, broker, strategy, indicators, max_queue=2048):
        self.path, self.state, self.broker = Path(path), state, broker
        if state.sources or any(state.assets[a]["market"] for a in state.assets):
            raise ValueError(
                "Recordings must start before connectors; set RECORDING_PATH before launch"
            )
        with broker.db.transaction():
            if (
                broker.db.connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                or broker.db.positions()
                or broker.db.connection.execute(
                    "SELECT COUNT(*) FROM strategy_decisions"
                ).fetchone()[0]
            ):
                raise ValueError(
                    "Use a fresh isolated PAPER_DATABASE_PATH to record a reproducible session"
                )
            account = broker.db.account()
            if (
                any(float(account[key]) != 0 for key in ("fees", "realized_pnl"))
                or account["cash"] != account["starting_balance"]
            ):
                raise ValueError("Recording requires an untouched paper account")
            runtime = {
                key: json.loads(raw)
                for key, raw in broker.db.connection.execute(
                    "SELECT key,data FROM strategy_runtime"
                )
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = gzip.GzipFile(filename=str(self.path), mode="xb", compresslevel=1, mtime=0)
        self.queue = Queue(maxsize=max_queue)
        self.error, self.closed, self.count, self.sequence = None, False, 0, 0
        self.last, self.input_digest = {}, hashlib.sha256()
        header = dict(
            kind="header",
            format=FORMAT_VERSION,
            ts=state.clock(),
            code=code_digest(),
            execution=broker.config.model_dump(mode="json"),
            risk=broker.limits.model_dump(mode="json"),
            execution_settings=broker.db.one(
                "SELECT data FROM execution_versions ORDER BY version DESC LIMIT 1"
            ),
            execution_versions=broker.db.many(
                "SELECT data FROM execution_versions ORDER BY version"
            ),
            indicator_versions=broker.db.many(
                "SELECT data FROM indicator_versions ORDER BY version"
            ),
            account=account,
            profiles=strategy.store.profiles(),
            runtime=runtime,
            indicators=indicators.config.model_dump(mode="json"),
            stale_seconds=state.stale_seconds,
        )
        self.handle.write((encode(header) + "\n").encode())
        self.thread = Thread(target=self._write, name="market-recorder", daemon=True)
        self.thread.start()

    def _write(self):
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    break
                self.handle.write(item)
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            try:
                self.handle.close()
            except Exception as exc:
                self.error = type(exc).__name__

    def event(self, kind, data):
        if self.closed or self.error:
            return
        self.sequence += 1
        raw = (
            encode(dict(kind=kind, ts=self.state.clock(), seq=self.sequence, data=data)) + "\n"
        ).encode()
        try:
            self.queue.put_nowait(raw)
            self.input_digest.update(raw)
            self.count += 1
        except Full:
            self.error = "Recording queue overflow; file is incomplete"

    def observe(self, source):
        patch = []
        present = set()
        for asset, target in self.state.assets.items():
            for path, value in [
                ([asset, "market"], target["market"]),
                ([asset, "upcoming"], target["upcoming"]),
                *[
                    ([asset, v, k], value)
                    for v in ("kraken", "kalshi")
                    for k, value in target[v].items()
                ],
            ]:
                key = tuple(path)
                present.add(key)
                value = wire(value)
                previous = self.last.get(key)
                if previous == value:
                    continue
                change = {"path": path, "value": value}
                if path[-1] == "trades" and previous:
                    id_key = "id" if path[1] == "kraken" else "trade_id"
                    old_id = previous[-1].get(id_key)
                    indices = [i for i, row in enumerate(value) if row.get(id_key) == old_id]
                    if old_id is not None and indices:
                        change = {"path": path, "append": value[indices[-1] + 1 :]}
                patch.append(change)
                self.last[key] = value
        for key in set(self.last) - present:
            patch.append({"path": list(key), "delete": True})
            del self.last[key]
        self.event("source", dict(source=source, health=self.state.sources[source], patch=patch))

    def close(self):
        if self.closed:
            return
        if not self.error:
            self.event(
                "footer",
                dict(
                    ledger=ledger_digest(self.broker.db),
                    events=self.count,
                    input_sha256=self.input_digest.hexdigest(),
                ),
            )
        self.closed = True
        # Drain the bounded queue on shutdown; trading never waits for disk writes.
        if self.thread.is_alive():
            try:
                self.queue.put(None, timeout=10)
            except Full:
                self.error = "Recording writer could not drain the queue"
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            self.error = "Recording writer did not finish"

    def status(self):
        return dict(
            configured=True,
            file=self.path.name,
            events=self.count,
            state="failed" if self.error else "closed" if self.closed else "recording",
            error=self.error,
        )
