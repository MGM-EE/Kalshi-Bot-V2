import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .connectors.kalshi import Kalshi
from .connectors.kraken import Kraken
from .settings import ROOT, Settings
from .state import State


def create_app(settings=None, connect_live=True):
    settings = settings or Settings.from_env()
    state = State(settings.stale_seconds)

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if connect_live:
            tasks = [
                asyncio.create_task(Kalshi(state, settings).run()),
                asyncio.create_task(Kraken(state).run()),
            ]
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Kalshi Terminal V2 — Phase 1", lifespan=lifespan)
    app.state.market_state = state

    @app.get("/api/state")
    async def snapshot():
        return state.snapshot()

    @app.get("/api/health")
    async def health():
        # Liveness only; feed readiness is separately reported in /api/state.
        return {"application": "ok", "phase": 1, "execution_enabled": False}

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
