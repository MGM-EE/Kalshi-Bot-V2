import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .connectors.kalshi import Kalshi
from .connectors.kraken import Kraken
from .execution.models import PaperOrder
from .execution.paper_broker import IdempotencyConflict
from .execution.service import open_broker, paper_worker, settlement_worker
from .settings import ROOT, Settings
from .state import State, wire


def create_app(settings=None, connect_live=True):
    settings = settings or Settings.from_env()
    state = State(settings.stale_seconds)

    @asynccontextmanager
    async def lifespan(app):
        broker = open_broker(state, settings)
        app.state.paper_broker = broker
        state.paper_provider = broker.portfolio
        tasks = [asyncio.create_task(paper_worker(broker, state))]
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
            state.paper_provider = None
            broker.db.close()

    app = FastAPI(title="Kalshi Terminal V2 — Paper research", lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost"] + ([] if connect_live else ["testserver"]),
    )
    app.state.market_state = state
    app.state.paper_broker = None

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

    @app.get("/api/health")
    async def health():
        # Liveness only; feed readiness is separately reported in /api/state.
        return {
            "application": "ok",
            "phase": 2,
            "execution_enabled": False,
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
