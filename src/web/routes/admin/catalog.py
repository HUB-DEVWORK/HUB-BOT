"""Admin: plans + constructor pricing (screen 03)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.core.enums import Currency, TransactionStatus, TransactionType
from src.infrastructure.database.models.constructor import ConstructorPeriod, TrafficPack
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter()

GIB = 1024**3


# --- plans -------------------------------------------------------------------


def _plan_row(plan: Plan, sales: int) -> dict[str, Any]:
    durations = [
        {
            "id": d.id,
            "days": d.days,
            "prices": {p.currency.value: p.price_minor for p in d.prices},
        }
        for d in plan.durations
    ]
    return {
        "id": plan.id,
        "public_code": plan.public_code,
        "name": plan.name,
        "description": plan.description,
        "traffic_limit_bytes": plan.traffic_limit_bytes,
        "device_limit": plan.device_limit,
        "is_active": plan.is_active,
        "is_trial": plan.is_trial,
        "order_index": plan.order_index,
        "internal_squads": list(plan.internal_squads or []),
        "durations": durations,
        "sales": sales,
    }


@router.get("/plans")
async def list_plans(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        plans = await uow.plans.list_with_durations()
        # Sales per plan (completed subscription payments referencing the plan snapshot).
        sales_stmt = (
            select(
                Transaction.plan_snapshot["plan_id"].as_integer(),
                func.count(),
            )
            .where(
                Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT,
                Transaction.status == TransactionStatus.COMPLETED,
                Transaction.plan_snapshot.is_not(None),
            )
            .group_by(Transaction.plan_snapshot["plan_id"].as_integer())
        )
        sales: dict[int, int] = {}
        try:
            sales = dict((await uow.session.execute(sales_stmt)).all())  # type: ignore[arg-type]
        except Exception:
            await uow.rollback()
            sales = {}
        cfg = container.bot_config
        mode = await cfg.value(uow, "SALES_MODE")
    return {"mode": mode, "items": [_plan_row(p, sales.get(p.id, 0)) for p in plans]}


class DurationIn(BaseModel):
    days: int = Field(..., ge=1, le=3650)
    price_minor: int = Field(..., ge=0)


class PlanIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=1024)
    traffic_limit_gb: int | None = Field(None, ge=0)  # None/0 -> unlimited
    device_limit: int | None = Field(None, ge=1, le=100)
    durations: list[DurationIn] = Field(default_factory=list)
    internal_squads: list[str] = Field(default_factory=list)  # panel squad uuids
    is_active: bool = True


@router.post("/plans")
async def create_plan(
    body: PlanIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        code = body.name.lower().replace(" ", "-")[:64]
        if await uow.plans.find_one(public_code=code):
            raise HTTPException(409, "plan with this name already exists")
        plan = Plan(
            public_code=code,
            name=body.name,
            description=body.description,
            traffic_limit_bytes=(body.traffic_limit_gb or 0) * GIB or None,
            device_limit=body.device_limit,
            internal_squads=list(body.internal_squads),
            is_active=body.is_active,
        )
        await uow.plans.add(plan)
        for i, d in enumerate(body.durations):
            duration = PlanDuration(plan_id=plan.id, days=d.days, order_index=i)
            await uow.session.flush()
            uow.session.add(duration)
            await uow.session.flush()
            uow.session.add(
                PlanPrice(
                    plan_duration_id=duration.id,
                    currency=Currency.RUB,
                    price_minor=d.price_minor,
                )
            )
        await audit(uow, identity, "plan.create", f"plan:{plan.name}")
        await uow.commit()
        return {"ok": True, "id": plan.id}


class PlanPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=1024)
    traffic_limit_gb: int | None = Field(None, ge=0)
    device_limit: int | None = Field(None, ge=1, le=100)
    is_active: bool | None = None
    durations: list[DurationIn] | None = None
    internal_squads: list[str] | None = None


@router.patch("/plans/{plan_id}")
async def patch_plan(
    plan_id: int,
    body: PlanPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    data = body.model_dump(exclude_unset=True)
    async with container.uow() as uow:
        plan = await uow.plans.get_with_durations(plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")
        if data.get("name"):
            plan.name = data["name"]
        if "description" in data:
            plan.description = data["description"]
        if "traffic_limit_gb" in data:
            gb = data["traffic_limit_gb"] or 0
            plan.traffic_limit_bytes = gb * GIB or None
        if "device_limit" in data:
            plan.device_limit = data["device_limit"]
        if "is_active" in data and data["is_active"] is not None:
            plan.is_active = data["is_active"]
        if body.internal_squads is not None:
            plan.internal_squads = list(body.internal_squads)
        if body.durations is not None:
            # Replace the duration/price grid (RUB prices; other currencies later).
            for old in list(plan.durations):
                await uow.session.delete(old)
            await uow.session.flush()
            for i, d in enumerate(body.durations):
                duration = PlanDuration(plan_id=plan.id, days=d.days, order_index=i)
                uow.session.add(duration)
                await uow.session.flush()
                uow.session.add(
                    PlanPrice(
                        plan_duration_id=duration.id,
                        currency=Currency.RUB,
                        price_minor=d.price_minor,
                    )
                )
        await audit(
            uow,
            identity,
            "plan.patch",
            f"plan:{plan.name}",
            **{k: v for k, v in data.items() if k != "durations"},
        )
        await uow.commit()
    return OkOut()


@router.delete("/plans/{plan_id}")
async def delete_plan(
    plan_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        plan = await uow.plans.get(plan_id)
        if plan is None:
            raise HTTPException(404, "plan not found")
        # RESTRICT on subscriptions.plan_id protects sold plans; deactivate instead.
        active_refs = await uow.subscriptions.count(plan_id=plan_id)
        if active_refs:
            raise HTTPException(409, "plan has subscriptions — deactivate it instead")
        await uow.plans.delete(plan)
        await audit(uow, identity, "plan.delete", f"plan:{plan.name}")
        await uow.commit()
    return OkOut()


# --- constructor -------------------------------------------------------------


@router.get("/constructor")
async def get_constructor(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        periods = await uow.constructor_periods.list()
        packs = await uow.traffic_packs.list()
        cfg = container.bot_config
        extra_device = int(await cfg.value(uow, "CONSTRUCTOR_EXTRA_DEVICE_PRICE"))
        max_devices = int(await cfg.value(uow, "CONSTRUCTOR_MAX_DEVICES"))
        trial_enabled = bool(await cfg.value(uow, "TRIAL_ENABLED"))
    return {
        "periods": [
            {"id": p.id, "days": p.days, "price_minor": p.price_minor, "is_active": p.is_active}
            for p in sorted(periods, key=lambda p: p.days)
        ],
        "traffic_packs": [
            {"id": t.id, "gb": t.gb, "price_minor": t.price_minor, "is_active": t.is_active}
            for t in sorted(packs, key=lambda t: (t.gb == 0, t.gb))
        ],
        "extra_device_price_minor": extra_device,
        "max_devices": max_devices,
        "trial_enabled": trial_enabled,
    }


class PeriodIn(BaseModel):
    days: int = Field(..., ge=1, le=3650)
    price_minor: int = Field(..., ge=0)
    is_active: bool = True


class PackIn(BaseModel):
    gb: int = Field(..., ge=0)  # 0 -> unlimited
    price_minor: int = Field(..., ge=0)
    is_active: bool = True


class ConstructorIn(BaseModel):
    periods: list[PeriodIn]
    traffic_packs: list[PackIn]


@router.put("/constructor")
async def save_constructor(
    body: ConstructorIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        await uow.constructor_periods.delete_by()
        await uow.traffic_packs.delete_by()
        for i, p in enumerate(body.periods):
            await uow.constructor_periods.add(
                ConstructorPeriod(
                    days=p.days, price_minor=p.price_minor, is_active=p.is_active, order_index=i
                )
            )
        for i, t in enumerate(body.traffic_packs):
            await uow.traffic_packs.add(
                TrafficPack(
                    gb=t.gb, price_minor=t.price_minor, is_active=t.is_active, order_index=i
                )
            )
        await audit(
            uow,
            identity,
            "constructor.save",
            None,
            periods=len(body.periods),
            packs=len(body.traffic_packs),
        )
        await uow.commit()
    return OkOut()
