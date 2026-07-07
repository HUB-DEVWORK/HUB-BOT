"""Admin: limited-quantity sale campaigns (month-start discounts, screen 07).

The owner runs a discount on days ``start_day``..``end_day`` of each month, capped to
``max_uses`` purchases per month (0 = unlimited). PricingService picks up the active sale
automatically; the quota is consumed at fulfilment and resets every month.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from src.infrastructure.database.models.sale_campaign import SaleCampaign
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/sales")


def _serialize(s: SaleCampaign) -> dict[str, Any]:
    return {
        "id": s.id,
        "title": s.title,
        "discount_pct": s.discount_pct,
        "start_day": s.start_day,
        "end_day": s.end_day,
        "max_uses": s.max_uses,
        "used_count": s.used_count,
        "used_period": s.used_period,
        "enabled": s.enabled,
    }


class SaleIn(BaseModel):
    title: str = Field("Скидка месяца", min_length=1, max_length=128)
    discount_pct: int = Field(ge=1, le=100)
    start_day: int = Field(1, ge=1, le=31)
    end_day: int = Field(3, ge=1, le=31)
    max_uses: int = Field(0, ge=0)  # 0 = unlimited
    enabled: bool = True

    @model_validator(mode="after")
    def _window(self) -> SaleIn:
        if self.start_day > self.end_day:
            raise ValueError("start_day must be <= end_day")
        return self


class SalePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=128)
    discount_pct: int | None = Field(None, ge=1, le=100)
    start_day: int | None = Field(None, ge=1, le=31)
    end_day: int | None = Field(None, ge=1, le=31)
    max_uses: int | None = Field(None, ge=0)
    enabled: bool | None = None


@router.get("")
async def list_sales(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = await uow.sales.ordered()
    return {"items": [_serialize(s) for s in rows]}


@router.post("")
async def create_sale(
    body: SaleIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        sale = SaleCampaign(
            title=body.title,
            discount_pct=body.discount_pct,
            start_day=body.start_day,
            end_day=body.end_day,
            max_uses=body.max_uses,
            enabled=body.enabled,
        )
        await uow.sales.add(sale)
        await audit(uow, identity, "sale.create", None, discount_pct=body.discount_pct)
        await uow.commit()
        return _serialize(sale)


@router.patch("/{sale_id}")
async def update_sale(
    sale_id: int,
    body: SalePatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        sale = await uow.sales.get(sale_id)
        if sale is None:
            raise HTTPException(404, "sale not found")
        for fld in ("title", "discount_pct", "start_day", "end_day", "max_uses", "enabled"):
            val = getattr(body, fld)
            if val is not None:
                setattr(sale, fld, val)
        if sale.start_day > sale.end_day:
            raise HTTPException(400, "start_day must be <= end_day")
        await audit(uow, identity, "sale.update", str(sale_id))
        await uow.commit()
        return _serialize(sale)


@router.delete("/{sale_id}")
async def delete_sale(
    sale_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, bool]:
    async with container.uow() as uow:
        if await uow.sales.get(sale_id) is None:
            raise HTTPException(404, "sale not found")
        await uow.sales.delete_by(id=sale_id)
        await audit(uow, identity, "sale.delete", str(sale_id))
        await uow.commit()
    return {"ok": True}
