"""Health endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from src.infrastructure.di import AppContainer
from src.infrastructure.services.health import check_database, check_redis
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
