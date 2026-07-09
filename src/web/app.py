"""FastAPI app factory (the web seam).

Binds behind a reverse proxy. Owns the AppContainer lifecycle. Mounts webhook + health
routers. The bot dispatcher and cabinet API mount here later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.core.config import get_settings
from src.core.logging import configure_logging
from src.infrastructure.di import AppContainer
from src.web.routes import admin, cabinet, cabinet_auth, health, panel, payments
from src.web.routes.admin.auth import bootstrap_admin
from src.web.routes.admin.maintenance import bootstrap_public_urls, bootstrap_report_topics
from src.web.routes.admin.menu import bootstrap_menu
from src.web.routes.admin.notifications import bootstrap_notifications
from src.web.routes.admin.reminders import bootstrap_reminders

# Built admin SPA (admin/dist) — mounted when present (dev runs vite instead).
_ADMIN_DIST = Path(__file__).resolve().parents[2] / "admin" / "dist"
# End-user mini-app (static, no build step).
_MINIAPP_DIR = Path(__file__).resolve().parents[2] / "miniapp" / "app"
# Standalone browser cabinet (email/OAuth/guest purchase) — served at /web.
_WEB_DIR = Path(__file__).resolve().parents[2] / "web"
# Public marketing site (tariffs + «личный кабинет» CTA), themed per design — served at /.
_SITE_DIR = Path(__file__).resolve().parents[2] / "site"
# Admin-uploaded media (broadcasts, menu screens, covers) — created on demand.
_UPLOADS_DIR = Path("uploads")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(level=settings.log.level, json=settings.log.use_json)
    container = AppContainer(settings)
    app.state.container = container
    await bootstrap_admin(container)
    await bootstrap_menu(container)
    await bootstrap_reminders(container)
    await bootstrap_notifications(container)
    await bootstrap_report_topics(container)
    await bootstrap_public_urls(container)  # auto-wire bot <-> mini-app from WEB__PUBLIC_URL
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
    app.include_router(admin.router)
    app.include_router(cabinet.router)
    app.include_router(cabinet_auth.router)
    if _ADMIN_DIST.is_dir():
        app.mount("/admin", StaticFiles(directory=_ADMIN_DIST, html=True), name="admin-spa")
    if _MINIAPP_DIR.is_dir():
        app.mount("/app", StaticFiles(directory=_MINIAPP_DIR, html=True), name="miniapp")
    if _WEB_DIR.is_dir():
        app.mount("/web", StaticFiles(directory=_WEB_DIR, html=True), name="web-cabinet")
    _UPLOADS_DIR.mkdir(exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=_UPLOADS_DIR), name="uploads")
    # The public site is a catch-all at "/", so it MUST mount last — after every API
    # router and the /admin, /app, /web, /uploads mounts, which still match first.
    if _SITE_DIR.is_dir():
        app.mount("/", StaticFiles(directory=_SITE_DIR, html=True), name="site")
    return app


app = create_app()
