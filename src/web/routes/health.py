"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.infrastructure.di import AppContainer
from src.infrastructure.services.health import (
    check_database,
    check_panel,
    check_redis,
    check_worker,
)
from src.web.deps import get_container

router = APIRouter()


@router.get("/health")
async def health(container: AppContainer = Depends(get_container)) -> JSONResponse:
    db_ok = await check_database(container.engine)
    redis_ok = await check_redis(container.redis)
    status = 200 if (db_ok and redis_ok) else 503
    return JSONResponse(
        {"status": "ok" if status == 200 else "degraded", "database": db_ok, "redis": redis_ok},
        status_code=status,
    )


@router.get("/health/deep")
async def health_deep(container: AppContainer = Depends(get_container)) -> JSONResponse:
    """Liveness of every moving part, for an uptime monitor. Returns 503 when a component
    whose failure means the APP is broken is down (db / redis / worker); the panel is
    reported but a panel outage does NOT fail health (that's handled by maintenance mode)."""
    db_ok = await check_database(container.engine)
    redis_ok = await check_redis(container.redis)
    worker_ok = await check_worker(container.redis)
    panel_ok = await check_panel(container)
    critical_ok = db_ok and redis_ok and worker_ok
    return JSONResponse(
        {
            "status": "ok" if critical_ok else "degraded",
            "database": db_ok,
            "redis": redis_ok,
            "worker": worker_ok,
            "panel": panel_ok,
        },
        status_code=200 if critical_ok else 503,
    )
