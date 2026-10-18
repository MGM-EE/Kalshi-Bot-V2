import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .connectors.kalshi import Kalshi
from .connectors.kraken import Kraken
from .credentials import credential_status
from .execution.configuration import ExecutionSettings, current, save
from .execution.models import PaperOrder
from .execution.paper_broker import IdempotencyConflict
from .execution.service import open_broker, paper_worker, settlement_worker
from .indicators.config import IndicatorConfig
from .indicators.engine import IndicatorEngine
from .indicators.features import Features
from .research.analytics import analytics
from .research.clock import EventClock
from .research.recorder import Recorder
from .research.replay import replay
from .settings import ROOT, Settings
from .state import State, wire
from .strategy.engine import StrategyEngine
from .strategy.profiles import METRICS, Profile


def create_app(settings=None, connect_live=True):
    settings = settings or Settings.from_env()
    state = State(settings.stale_seconds, EventClock())
    features = Features(state)
    state.observers.append(features.observe)

    @asynccontextmanager
    async def lifespan(app):
        broker = open_broker(state, settings)
        app.state.paper_broker = broker
        state.paper_provider = broker.portfolio
        indicators = IndicatorEngine(state, features, broker.db)
        features.advanced = indicators
        state.observers.append(indicators.observe)

        def indicator_snapshot():
            with state.clock.at():
                if state.record_event:
                    state.record_event("indicator_snapshot", {})
                return indicators.snapshot(broker.clock())

        state.indicator_provider = indicator_snapshot
        app.state.indicators = indicators
        strategy = StrategyEngine(broker, features)
        app.state.strategy_engine = strategy
        state.strategy_provider = strategy.snapshot
        recorder = None
        if settings.recording_path:
            try:
                recorder = Recorder(settings.recording_path, state, broker, strategy, indicators)
            except BaseException:
                broker.db.close()
                raise
            state.record_event = recorder.event
            state.observers.append(recorder.observe)
        app.state.recorder = recorder
        tasks = [asyncio.create_task(paper_worker(broker, state, strategy))]
        kalshi = Kalshi(state, settings)
        if connect_live:
            tasks += [
                asyncio.create_task(kalshi.run()),
                asyncio.create_task(Kraken(state).run()),
                asyncio.create_task(settlement_worker(broker, kalshi, state)),
            ]
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            state.record_event = None
            if recorder:
                state.observers.remove(recorder.observe)
                recorder.close()
            state.paper_provider = None
            state.strategy_provider = None
            state.indicator_provider = None
            state.observers.remove(indicators.observe)
            broker.db.close()

    app = FastAPI(title="Kalshi Terminal V2 — Paper research", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"] + ([] if connect_live else ["testserver"]),
    )
    app.state.market_state = state
    app.state.paper_broker = None
    app.state.strategy_engine = None

    replay_lock = asyncio.Lock()
    recording_directory = (
        Path(settings.recording_path).parent
        if settings.recording_path
        else ROOT / "data" / "recordings"
    )

    class ReplayRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        file: str
        profiles: list[Profile] = []
        period: Literal["development", "validation", "out-of-sample"] = "development"

    def record_command(kind, data):
        if state.record_event:
            state.record_event(kind, data)

    class StrategyControl(BaseModel):
        model_config = ConfigDict(extra="forbid")
        enabled: bool | None = None
        emergency: bool | None = None
        profile_name: str | None = None
        profile_version: int | None = None

    class IndicatorUpdate(BaseModel):
        model_config = ConfigDict(extra="forbid")
        config: IndicatorConfig
        acknowledge_open_positions: bool = False

    def broker():
        if app.state.paper_broker is None:
            raise HTTPException(503, "Paper database is not open")
        return app.state.paper_broker

    async def local_write(request: Request):
        origin, host = request.headers.get("origin"), request.headers.get("host")
        if origin and origin not in {f"http://{host}", f"https://{host}"}:
            raise HTTPException(403, "Cross-origin paper mutations are disabled")
        if (
            request.method == "POST"
            and request.headers.get("content-type", "").split(";")[0] != "application/json"
        ):
            raise HTTPException(415, "Paper order requests require application/json")

    @app.get("/api/paper/portfolio")
    async def portfolio():
        return wire(broker().portfolio())

    @app.post("/api/paper/orders", dependencies=[Depends(local_write)])
    async def submit_order(spec: PaperOrder):
        try:
            order = broker().submit(spec)
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        state.changed.set()
        return wire(order)

    @app.delete("/api/paper/orders/{order_id}", dependencies=[Depends(local_write)])
    async def cancel_order(order_id: str):
        try:
            return wire(broker().cancel(order_id))
        except KeyError as exc:
            raise HTTPException(404, "Paper order not found") from exc

    @app.get("/api/state")
    async def snapshot():
        return state.snapshot()

    @app.get("/api/credentials/status")
    async def credentials_status():
        return credential_status(settings)

    @app.get("/api/strategy/profiles")
    async def profiles():
        return {
            "profiles": app.state.strategy_engine.store.profiles(),
            "metrics": METRICS,
            "control": app.state.strategy_engine.store.read("control"),
        }

    @app.get("/api/indicators/config")
    async def indicator_config():
        return app.state.indicators.config.model_dump(mode="json")

    @app.post("/api/indicators/config", dependencies=[Depends(local_write)])
    async def update_indicators(value: IndicatorUpdate):
        try:
            result = app.state.indicators.save_config(
                value.config, value.acknowledge_open_positions
            ).model_dump(mode="json")
            record_command("indicators", value.model_dump(mode="json"))
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/strategy/profiles", dependencies=[Depends(local_write)])
    async def save_profile(profile: Profile):
        try:
            saved = app.state.strategy_engine.store.save_profile(profile)
            record_command("profile", saved.model_dump(mode="json"))
            return {"profile": saved.model_dump(mode="json"), "identifier": saved.identifier}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/strategy/control", dependencies=[Depends(local_write)])
    async def strategy_control(value: StrategyControl):
        try:
            result = app.state.strategy_engine.control(**value.model_dump(exclude_none=True))
            record_command("control", value.model_dump(exclude_none=True))
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/settings/schema")
    async def settings_schema():
        return {
            "profile": Profile.model_json_schema(),
            "indicators": IndicatorConfig.model_json_schema(),
            "execution": ExecutionSettings.model_json_schema(),
        }

    @app.get("/api/settings/execution")
    async def execution_settings():
        return current(broker())

    @app.post("/api/settings/execution", dependencies=[Depends(local_write)])
    async def execution_update(value: ExecutionSettings):
        try:
            result = save(broker(), value)
            record_command("execution", value.model_dump(mode="json"))
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/research/decisions")
    async def research_decisions():
        with broker().db.lock:
            return broker().db.many("SELECT data FROM strategy_decisions ORDER BY id DESC LIMIT 50")

    @app.get("/api/research/analytics")
    async def research_analytics():
        with broker().db.lock:
            return wire(analytics(broker().db))

    @app.get("/api/research/recordings")
    async def recordings():
        active = app.state.recorder
        return {
            "recorder": active.status() if active else {"configured": False, "state": "off"},
            "files": [
                p.name
                for p in sorted(recording_directory.glob("*.jsonl.gz"))
                if p.is_file() and not p.is_symlink()
            ],
        }

    @app.post("/api/research/replay", dependencies=[Depends(local_write)])
    async def research_replay(value: ReplayRequest):
        if not re.fullmatch(r"[A-Za-z0-9_-]+[.]jsonl[.]gz", value.file) or len(value.profiles) > 3:
            raise HTTPException(422, "Choose a listed recording and at most three profiles")
        path = recording_directory / value.file
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "Recording not found")
        active = app.state.recorder
        if active and not active.closed and path.resolve() == active.path.resolve():
            raise HTTPException(409, "Stop the recorded app cleanly before replaying its file")
        if replay_lock.locked():
            raise HTTPException(409, "A replay is already running")
        async with replay_lock:
            try:
                profiles = [None] + [p.model_dump(mode="json") for p in value.profiles]
                results = []
                for profile in profiles:
                    results.append(await asyncio.to_thread(replay, path, profile, value.period))
                return {"mode": "REPLAY", "results": results}
            except (ValueError, KeyError, OSError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.get("/api/health")
    async def health():
        # Liveness only; feed readiness is separately reported in /api/state.
        control = (
            app.state.strategy_engine.store.read("control") if app.state.strategy_engine else {}
        )
        return {
            "application": "ok",
            "phase": 6,
            "execution_enabled": bool(control.get("enabled") and not control.get("emergency")),
            "paper_api_available": True,
            "real_order_execution_enabled": False,
        }

    @app.websocket("/api/stream")
    async def stream(websocket: WebSocket):
        # Read-only but avoid disclosing local market/config metadata cross-origin.
        origin = websocket.headers.get("origin")
        host = websocket.headers.get("host")
        if origin and origin not in {f"http://{host}", f"https://{host}"}:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(state.snapshot())
                await asyncio.sleep(0.5)
        except (WebSocketDisconnect, RuntimeError):
            pass

    @app.get("/")
    async def index():
        return FileResponse(ROOT / "frontend" / "index.html")

    app.mount("/static", StaticFiles(directory=ROOT / "frontend" / "dist", check_dir=False))
    return app


app = create_app()
