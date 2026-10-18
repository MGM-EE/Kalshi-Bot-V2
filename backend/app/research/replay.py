"""Offline replay uses the live feature/strategy/broker classes and a private database."""

import gzip
import hashlib
import json
import time

from ..execution.configuration import ExecutionSettings, initialize, save
from ..execution.models import PaperConfig, PaperOrder, RiskLimits
from ..execution.paper_broker import PaperBroker
from ..indicators.config import IndicatorConfig
from ..indicators.engine import IndicatorEngine
from ..indicators.features import Features
from ..state import State, wire
from ..storage.database import Database, encode
from ..strategy.engine import StrategyEngine
from ..strategy.profiles import Profile
from .analytics import analytics
from .clock import EventClock
from .recorder import FORMAT_VERSION, code_digest, ledger_digest

MAX_LINE = 2**20
MAX_BYTES = 512 * 2**20
MAX_EVENTS = 2_000_000


class ReplaySession:
    def __init__(self, header, profile=None):
        self.clock = EventClock(header["ts"])
        self.state = State(header["stale_seconds"], self.clock)
        self.state.replay = True
        self.db = Database(":memory:")
        self.broker = PaperBroker(
            self.db,
            self.state,
            PaperConfig.model_validate(header["execution"]),
            RiskLimits.model_validate(header["risk"]),
            self.clock,
        )
        saved = header["execution_settings"]
        initialize(self.db, dict(execution=saved["execution"], risk=saved["risk"]))
        self.features = Features(self.state)
        self.indicators = IndicatorEngine(self.state, self.features, self.db)
        self.features.advanced = self.indicators
        self.strategy = StrategyEngine(self.broker, self.features)
        with self.db.transaction():
            self.db.save_account(header["account"])
            for table, key in [
                ("execution_versions", "execution_versions"),
                ("indicator_versions", "indicator_versions"),
            ]:
                self.db.connection.execute(f"DELETE FROM {table}")
                for value in header[key]:
                    self.db.connection.execute(
                        f"INSERT INTO {table} VALUES(?,?)", (value["version"], encode(value))
                    )
            for key, value in header["runtime"].items():
                self.strategy.store.write(key, value)
        for value in header["profiles"]:
            self.strategy.store.save_profile(value)
        self.indicators.configure(IndicatorConfig.model_validate(header["indicators"]))
        self.state.observers.extend([self.features.observe, self.indicators.observe])
        self.override = Profile.model_validate(profile) if profile else None
        if self.override:
            self.strategy.store.save_profile(self.override)
            self.strategy.control(
                profile_name=self.override.name, profile_version=self.override.version
            )
        self.performance = []
        self.ticks = 0
        self.failed_cycles = 0

    def apply(self, record):
        self.clock.fixed = record["ts"]
        data, kind = record["data"], record["kind"]
        if kind == "source":
            for change in data["patch"]:
                target = self.state.assets
                for key in change["path"][:-1]:
                    target = target.setdefault(key, {})
                key = change["path"][-1]
                if change.get("delete"):
                    target.pop(key, None)
                elif "append" in change:
                    target[key] = (target.get(key, []) + change["append"])[-100:]
                else:
                    target[key] = change["value"]
            self.state.sources[data["source"]] = data["health"]
            for observer in self.state.observers:
                observer(data["source"])
        elif kind == "cycle":
            start = time.perf_counter()
            self.state.in_cycle = True
            actual_error = None
            try:
                if data.get("mark_account"):
                    with self.db.transaction():
                        self.broker.mark_account(self.clock())
                self.strategy.tick()
                if self.db.active():
                    self.broker.process()
            except Exception as exc:
                actual_error = type(exc).__name__
            finally:
                self.state.in_cycle = False
            if actual_error != data.get("error"):
                raise ValueError("Replay cycle response differs from the recording")
            self.failed_cycles += actual_error is not None
            if len(self.performance) < 10000:
                self.performance.append((time.perf_counter() - start) * 1000)
            self.ticks += 1
        elif kind == "indicator_snapshot":
            self.indicators.snapshot(self.clock())
        elif kind == "control":
            value = dict(data)
            if self.override:
                value.update(profile_name=self.override.name, profile_version=self.override.version)
            self.strategy.control(**value)
        elif kind == "profile":
            self.strategy.store.save_profile(data)
        elif kind == "indicators":
            if self.override:
                raise ValueError(
                    "Strategy comparisons require fixed indicator/execution definitions within the recording"
                )
            self.indicators.save_config(data["config"], data["acknowledge_open_positions"])
        elif kind == "execution":
            if self.override:
                raise ValueError(
                    "Strategy comparisons require fixed indicator/execution definitions within the recording"
                )
            save(self.broker, ExecutionSettings.model_validate(data))
        elif kind in {"submit", "cancel", "settle", "portfolio"}:
            if self.override and kind in {"submit", "cancel"}:
                raise ValueError(
                    "Strategy comparison requires a strategy-only recording; manual orders/cancels are present"
                )
            arguments = dict(data["arguments"])
            if kind == "submit":
                arguments["spec"] = PaperOrder.model_validate(arguments["spec"])
            actual_error = None
            try:
                getattr(self.broker, kind)(**arguments)
            except (ValueError, KeyError) as exc:
                actual_error = type(exc).__name__
            if actual_error != data["error"]:
                raise ValueError(f"Replayed {kind} response differs from the recording")
        else:
            raise ValueError(f"Unsupported recording event: {kind}")

    def report(self):
        summary = analytics(self.db)
        samples = sorted(self.performance)
        return dict(
            mode="REPLAY",
            ledger_sha256=ledger_digest(self.db),
            analytics=summary,
            account=self.db.account(),
            positions=self.db.positions(),
            fills=self.db.fills(10000),
            performance=dict(
                cycles=self.ticks,
                failed_cycles=self.failed_cycles,
                measured_cycles=len(samples),
                cycle_p50_ms=samples[len(samples) // 2] if samples else 0,
                cycle_p95_ms=samples[min(len(samples) - 1, int(len(samples) * 0.95))]
                if samples
                else 0,
                cycle_max_ms=max(samples, default=0),
            ),
        )


def replay(path, profile=None, period="development"):
    if period not in {"development", "validation", "out-of-sample"}:
        raise ValueError("Choose development, validation or out-of-sample")
    session = None
    digest = hashlib.sha256()
    total = 0
    last_ts = None
    expected_seq = 1
    footer = None
    start = time.perf_counter()
    try:
        with gzip.open(path, "rb") as handle:
            first = handle.readline(MAX_LINE + 1)
            if len(first) > MAX_LINE:
                raise ValueError("Recording header exceeds limit")
            header = json.loads(first)
            if header.get("kind") != "header" or header.get("format") != FORMAT_VERSION:
                raise ValueError("Unknown recording format")
            if header["code"] != code_digest():
                raise ValueError("Recording code version differs; use the matching source revision")
            session = ReplaySession(header, profile)
            while True:
                line = handle.readline(MAX_LINE + 1)
                if not line:
                    break
                total += len(line)
                if len(line) > MAX_LINE or total > MAX_BYTES or expected_seq > MAX_EVENTS:
                    raise ValueError("Recording exceeds replay resource limits")
                record = json.loads(line)
                if record["seq"] != expected_seq or (
                    last_ts is not None and record["ts"] < last_ts
                ):
                    raise ValueError("Recording sequence/time moved backwards")
                if record["ts"] < header["ts"]:
                    raise ValueError("Recording event predates its header")
                last_ts = record["ts"]
                expected_seq += 1
                if record["kind"] == "footer":
                    footer = record["data"]
                    if (
                        footer["events"] != record["seq"] - 1
                        or footer["input_sha256"] != digest.hexdigest()
                    ):
                        raise ValueError("Recording integrity check failed")
                    if handle.read(1):
                        raise ValueError("Unexpected data after recording footer")
                    break
                digest.update(line)
                session.apply(record)
        if footer is None:
            raise ValueError("Incomplete recording: clean shutdown/footer required")
        result = session.report()
        matched = result["ledger_sha256"] == footer["ledger"]
        if profile is None and not matched:
            raise ValueError("Replay ledger does not match the recorded paper session")
        result.update(
            exact_reproduction=matched if profile is None else None,
            strategy_comparison=profile is not None,
            period=period,
            dataset_sha256=digest.hexdigest(),
            events=expected_seq - 2,
            start_time=header["ts"],
            end_time=last_ts,
            code_sha256=header["code"],
            wall_seconds=time.perf_counter() - start,
            limitations=[
                "Period label is user assigned; evaluate disjoint recordings and freeze profiles before out-of-sample testing",
                "Recorded inputs may be incomplete or sampled; unknown inputs remain blocking",
                "Paper fills are conservative estimates, not evidence of live execution or profitability",
            ],
        )
        return wire(result)
    finally:
        if session:
            session.db.close()
