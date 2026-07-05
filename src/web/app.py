"""FastAPI app factory (the web seam).

Binds behind a reverse proxy. Owns the AppContainer lifecycle. Mounts webhook + health
routers. The bot dispatcher and cabinet API mount here later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import get_settings
from src.core.logging import configure_logging
from src.infrastructure.di import AppContainer
from src.web.routes import health, panel, payments


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log.level, json=settings.log.use_json)
    container = AppContainer(settings)
    app.state.container = container
    try:
        yield
    finally:
        await container.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VPN-shop base", lifespan=lifespan)
    if settings.web.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.web.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(health.router)
    app.include_router(payments.router)
    app.include_router(panel.router)
    return app


app = create_app()
