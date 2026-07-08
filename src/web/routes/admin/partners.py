"""Admin: resellers / affiliates (screen «Партнёры»).

Onboard a partner, give them a deep-link code, an optional markup and a revenue share.
Turnover/earnings accrue as their referred users pay (wired at payment fulfilment).
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.infrastructure.database.models.partner import Partner
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/partners")


def _serialize(p: Partner) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "telegram_id": p.telegram_id,
        "code": p.code,
        "markup_pct": p.markup_pct,
        "revenue_share_pct": p.revenue_share_pct,
        "turnover_minor": p.turnover_minor,
        "earnings_minor": p.earnings_minor,
        "enabled": p.enabled,
        "created_at": iso(p.created_at),
    }


class PartnerIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    telegram_id: int | None = None
    code: str | None = Field(None, min_length=2, max_length=32)
    markup_pct: int = Field(0, ge=0, le=500)
    revenue_share_pct: int = Field(0, ge=0, le=100)
    enabled: bool = True


class PartnerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    telegram_id: int | None = None
    markup_pct: int | None = Field(None, ge=0, le=500)
    revenue_share_pct: int | None = Field(None, ge=0, le=100)
    enabled: bool | None = None


@router.get("")
async def list_partners(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = await uow.partners.ordered()
    return {"items": [_serialize(p) for p in rows]}


@router.post("")
async def create_partner(
    body: PartnerIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    code = (body.code or secrets.token_hex(4)).lower()
    async with container.uow() as uow:
        if await uow.partners.by_code(code) is not None:
            raise HTTPException(409, "code already in use")
        partner = Partner(
            name=body.name,
            telegram_id=body.telegram_id,
            code=code,
            markup_pct=body.markup_pct,
            revenue_share_pct=body.revenue_share_pct,
            enabled=body.enabled,
        )
        await uow.partners.add(partner)
        await audit(uow, identity, "partner.create", code)
        await uow.commit()
        return _serialize(partner)


@router.patch("/{partner_id}")
async def update_partner(
    partner_id: int,
    body: PartnerPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        partner = await uow.partners.get(partner_id)
        if partner is None:
            raise HTTPException(404, "partner not found")
        for fld in ("name", "telegram_id", "markup_pct", "revenue_share_pct", "enabled"):
            val = getattr(body, fld)
            if val is not None:
                setattr(partner, fld, val)
        await audit(uow, identity, "partner.update", str(partner_id))
        await uow.commit()
        return _serialize(partner)


@router.delete("/{partner_id}")
async def delete_partner(
    partner_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    async with container.uow() as uow:
        if await uow.partners.get(partner_id) is None:
            raise HTTPException(404, "partner not found")
        await uow.partners.delete_by(id=partner_id)
        await audit(uow, identity, "partner.delete", str(partner_id))
        await uow.commit()
    return {"ok": True}
