"""FastAPI app factory (the web seam).

Binds behind a reverse proxy. Owns the AppContainer lifecycle. Mounts webhook + health
routers. The bot dispatcher and cabinet API mount here later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.core.config import get_settings
from src.core.logging import configure_logging, get_logger
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

log = get_logger(__name__)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last-resort handler: report to telemetry, answer clean JSON with an error id.

    HTTPException and validation errors never reach here — only genuine bugs do.
    The stack trace stays server-side; the client gets a short id to quote to support.
    """
    container: AppContainer | None = getattr(request.app.state, "container", None)
    # The route TEMPLATE (/api/users/{user_id}), never the concrete path — concrete
    # paths embed telegram ids / user ids / HWIDs that must not leave the box.
    route = request.scope.get("route")
    endpoint = getattr(route, "path", None) or "unmatched"
    error_id = ""
    if container is not None:
        error_id = container.telemetry.report(
            exc, source="web", context={"endpoint": endpoint, "method": request.method}
        )
    log.error("unhandled web error", error_id=error_id, path=request.url.path, exc_info=exc)
    # This handler runs above CORSMiddleware, so echo the ACAO header ourselves — else a
    # cross-origin cabinet/mini-app can't read error_id from the 500.
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    allowed = get_settings().web.cors_origins
    if origin and ("*" in allowed or origin in allowed):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={"ok": False, "detail": "внутренняя ошибка сервера", "error_id": error_id},
        headers=headers,
    )


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
    app.add_exception_handler(Exception, unhandled_error_handler)
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

    @app.get("/dl", response_class=HTMLResponse)
    async def _deep_link_redirect(to: str) -> HTMLResponse:
        """Hand a client-app deep link (happ://…) to the OS. Telegram's in-app WebView cannot
        open custom schemes — a direct anchor/navigation fails with ERR_UNKNOWN_URL_SCHEME — and
        WebApp.openLink() accepts http(s) only. So the mini-app opens THIS https page via
        openLink (external browser), which then fires the scheme; the OS routes it to the app.
        The scheme is allow-listed so this can't be abused as an open redirect (javascript:, …)."""
        import html as _html
        import json as _json

        allowed = (
            "happ://",
            "v2raytun://",
            "hiddify://",
            "streisand://",
            "clash://",
            "sing-box://",
            "v2box://",
            "shadowrocket://",
            "nekobox://",
        )
        if not to.startswith(allowed):
            raise HTTPException(400, "scheme not allowed")
        js = _json.dumps(to)  # safe JS string literal
        href = _html.escape(to, quote=True)
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Открываю приложение…</title>"
            "<body style='margin:0;font-family:system-ui,-apple-system,sans-serif;"
            "background:#0f1319;color:#e6e9ef;text-align:center;padding:64px 24px'>"
            "<p style='font-size:17px'>Открываю приложение…</p>"
            f"<p style='margin-top:24px'><a href=\"{href}\" style='display:inline-block;"
            "padding:14px 24px;background:#4159c7;color:#fff;border-radius:10px;"
            "text-decoration:none;font-weight:600'>Открыть вручную</a></p>"
            "<p style='color:#8b96a3;font-size:13px;margin-top:20px'>Если приложение не "
            "открылось само — нажмите кнопку выше.</p>"
            f"<script>setTimeout(function(){{location.href={js}}},60)</script>"
        )

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
