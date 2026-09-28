"""
Application factory.

    from aiface_ws import create_app
    app = create_app()          # then: uvicorn myapp:app

Everything the app holds lives in ``app.state.aiface`` (settings, sessions,
registry, handler, relay, events, photos), so you can reach it from your
own routes and listeners.
"""

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

from aiface_ws.api import router
from aiface_ws.config import Settings
from aiface_ws.db import create_tables, make_engine, make_session_factory
from aiface_ws.events import EventBus
from aiface_ws.handler import DeviceHandler
from aiface_ws.photos import PhotoStore
from aiface_ws.registry import ConnectionRegistry
from aiface_ws.relay import Relay

logger = logging.getLogger(__name__)

DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


@dataclass
class AiFace:
    settings: Settings
    engine: object
    sessions: object
    registry: ConnectionRegistry
    events: EventBus
    photos: PhotoStore
    handler: DeviceHandler
    relay: Relay


def create_app(settings: Optional[Settings] = None, *, relay_transport=None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    registry = ConnectionRegistry()
    events = EventBus()
    photos = PhotoStore(settings.photos_dir)
    handler = DeviceHandler(settings, sessions, registry, photos, events)
    relay = Relay(settings, sessions, transport=relay_transport)
    ctx = AiFace(settings, engine, sessions, registry, events, photos, handler, relay)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await create_tables(engine)
        task = asyncio.create_task(relay.run_forever()) if settings.relay_enabled else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await engine.dispose()

    app = FastAPI(title="AiFace WebSocket Server", lifespan=lifespan)
    app.state.aiface = ctx
    app.include_router(router)

    @app.websocket(settings.ws_path)
    async def device_socket(websocket: WebSocket):
        await handler.serve(websocket)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard():
        return DASHBOARD.read_text(encoding="utf-8")

    return app
