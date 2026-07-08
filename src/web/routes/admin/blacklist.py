"""Admin: blacklist of banned Telegram ids (screen 12, «Безопасность»).

Gated by ``BLACKLIST_CHECK_ENABLED``: when on, the bot middleware ignores every update
from a listed id. Managed here — add/remove with an optional reason.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.infrastructure.database.models.blacklist import BlacklistEntry
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/blacklist")


def _serialize(e: BlacklistEntry) -> dict[str, Any]:
    return {
        "id": e.id,
        "telegram_id": e.telegram_id,
        "reason": e.reason,
        "created_at": iso(e.created_at),
    }


class BlacklistIn(BaseModel):
    telegram_id: int = Field(gt=0)
    reason: str = Field("", max_length=256)


@router.get("")
async def list_blacklist(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = await uow.blacklist.ordered()
    return {"items": [_serialize(e) for e in rows]}


@router.post("")
async def add_to_blacklist(
    body: BlacklistIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        if await uow.blacklist.has(body.telegram_id):
            raise HTTPException(409, "already blacklisted")
        entry = BlacklistEntry(telegram_id=body.telegram_id, reason=body.reason)
        await uow.blacklist.add(entry)
        await audit(uow, identity, "blacklist.add", str(body.telegram_id))
        await uow.commit()
        return _serialize(entry)


@router.delete("/{telegram_id}")
async def remove_from_blacklist(
    telegram_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    async with container.uow() as uow:
        if not await uow.blacklist.has(telegram_id):
            raise HTTPException(404, "not blacklisted")
        await uow.blacklist.delete_by(telegram_id=telegram_id)
        await audit(uow, identity, "blacklist.remove", str(telegram_id))
        await uow.commit()
    return {"ok": True}
