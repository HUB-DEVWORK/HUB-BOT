"""Admin cabinet REST API — assembled router.

Every subrouter is mounted under ``/api/admin``; all except ``auth`` require a valid
admin JWT (see ``deps.require_admin``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.web.routes.admin import (
    auth,
    broadcasts,
    campaigns,
    catalog,
    dashboard,
    maintenance,
    menu,
    migration,
    miniapp,
    payments,
    promos,
    servers,
    settings,
    smart,
    tickets,
    uploads,
    users,
    withdrawals,
)
from src.web.routes.admin.deps import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])
router.include_router(auth.router)

_protected = APIRouter(dependencies=[Depends(require_admin)])
_protected.include_router(dashboard.router)
_protected.include_router(users.router)
_protected.include_router(catalog.router)
_protected.include_router(promos.router)
_protected.include_router(campaigns.router)
_protected.include_router(broadcasts.router)
_protected.include_router(smart.router)
_protected.include_router(payments.router)
_protected.include_router(tickets.router)
_protected.include_router(servers.router)
_protected.include_router(settings.router)
_protected.include_router(menu.router)
_protected.include_router(miniapp.router)
_protected.include_router(maintenance.router)
_protected.include_router(migration.router)
_protected.include_router(withdrawals.router)
_protected.include_router(uploads.router)
router.include_router(_protected)
