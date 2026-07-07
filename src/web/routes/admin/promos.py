"""Admin: promocodes, promo groups, referral summary (screen 04)."""

from __future__ import annotations

import datetime as dt
import secrets
import string
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.core.enums import RewardType
from src.infrastructure.database.models.promo_group import UserPromoGroup
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.referral import ReferralEarning
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter()

# The cabinet's «тип награды» segment maps to a subset of RewardType.
_UI_REWARDS = {
    "balance": RewardType.BALANCE,
    "days": RewardType.DURATION,
    "trial": RewardType.SUBSCRIPTION,
    "group": RewardType.PROMO_GROUP,
}
_UI_REWARDS_BACK = {v: k for k, v in _UI_REWARDS.items()}


def _gen_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _promo_rows(uow: Any) -> list[dict[str, Any]]:
    promos = await uow.promocodes.list()
    counts: dict[int, int] = {}
    if promos:
        stmt = select(PromocodeActivation.promocode_id, func.count()).group_by(
            PromocodeActivation.promocode_id
        )
        counts = dict((await uow.session.execute(stmt)).all())
    return [
        {
            "id": p.id,
            "code": p.code,
            "reward_type": _UI_REWARDS_BACK.get(p.reward_type, p.reward_type.value),
            "reward_value": p.reward_value,
            "used": counts.get(p.id, 0),
            "max_activations": p.max_activations,
            "expires_at": iso(p.expires_at),
            "is_active": p.is_active,
        }
        for p in sorted(promos, key=lambda p: p.id, reverse=True)
    ]


@router.get("/promocodes")
async def list_promocodes(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        rows = await _promo_rows(uow)
        total_activations = int(
            await uow.session.scalar(select(func.count()).select_from(PromocodeActivation)) or 0
        )
    return {"items": rows, "total_activations": total_activations}


class PromoIn(BaseModel):
    code: str = Field("", max_length=64)
    reward_type: str = Field("balance")
    reward_value: int = Field(0, ge=0)
    max_activations: int | None = Field(None, ge=0)  # 0/None -> unlimited
    expires_at: dt.datetime | None = None


@router.post("/promocodes")
async def create_promocode(
    body: PromoIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    if body.reward_type not in _UI_REWARDS:
        raise HTTPException(400, f"reward_type must be one of {sorted(_UI_REWARDS)}")
    code = (body.code or _gen_code()).strip().upper()
    async with container.uow() as uow:
        if await uow.promocodes.find_one(code=code):
            raise HTTPException(409, "code already exists")
        promo = Promocode(
            code=code,
            reward_type=_UI_REWARDS[body.reward_type],
            reward_value=body.reward_value,
            max_activations=body.max_activations or None,
            expires_at=body.expires_at,
        )
        await uow.promocodes.add(promo)
        await audit(uow, identity, "promo.create", f"promo:{code}")
        await uow.commit()
        return {"ok": True, "id": promo.id, "code": code}


class BulkIn(BaseModel):
    count: int = Field(..., ge=1, le=1000)
    reward_type: str = Field("days")
    reward_value: int = Field(0, ge=0)
    prefix: str = Field("GIFT", max_length=16)
    expires_at: dt.datetime | None = None


@router.post("/promocodes/bulk")
async def bulk_promocodes(
    body: BulkIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    """Mass-generate one-shot gift codes; each row carries a t.me deep-link.

    max_activations is fixed at 1 — a gift is single-claim by definition; reusable
    campaign codes are created one-by-one with an explicit limit instead.
    """
    if body.reward_type not in _UI_REWARDS:
        raise HTTPException(400, f"reward_type must be one of {sorted(_UI_REWARDS)}")
    prefix = "".join(ch for ch in body.prefix.upper() if ch.isalnum())[:16]
    async with container.uow() as uow:
        bot_username = str(await container.bot_config.value(uow, "BOT_USERNAME") or "")
        codes: list[str] = []
        for _ in range(body.count):
            for _attempt in range(5):
                code = f"{prefix}-{_gen_code(8)}" if prefix else _gen_code(10)
                if await uow.promocodes.find_one(code=code) is None:
                    break
            else:
                continue  # astronomically unlikely: 5 collisions in a row
            await uow.promocodes.add(
                Promocode(
                    code=code,
                    reward_type=_UI_REWARDS[body.reward_type],
                    reward_value=body.reward_value,
                    max_activations=1,
                    expires_at=body.expires_at,
                )
            )
            codes.append(code)
        await audit(uow, identity, "promo.bulk", None, count=len(codes))
        await uow.commit()
    link = f"https://t.me/{bot_username}?start=gift_" if bot_username else ""
    return {
        "ok": True,
        "count": len(codes),
        "items": [{"code": c, "gift_link": f"{link}{c}" if link else None} for c in codes],
    }


class PromoPatch(BaseModel):
    is_active: bool | None = None
    max_activations: int | None = None
    expires_at: dt.datetime | None = None


@router.patch("/promocodes/{promo_id}")
async def patch_promocode(
    promo_id: int,
    body: PromoPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    data = body.model_dump(exclude_unset=True)
    async with container.uow() as uow:
        promo = await uow.promocodes.get(promo_id)
        if promo is None:
            raise HTTPException(404, "promocode not found")
        for k, v in data.items():
            setattr(promo, k, v)
        await audit(
            uow,
            identity,
            "promo.patch",
            f"promo:{promo.code}",
            **{k: (iso(v) if isinstance(v, dt.datetime) else v) for k, v in data.items()},
        )
        await uow.commit()
    return OkOut()


@router.delete("/promocodes/{promo_id}")
async def delete_promocode(
    promo_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        promo = await uow.promocodes.get(promo_id)
        if promo is None:
            raise HTTPException(404, "promocode not found")
        await uow.promocodes.delete(promo)
        await audit(uow, identity, "promo.delete", f"promo:{promo.code}")
        await uow.commit()
    return OkOut()


@router.get("/promogroups")
async def list_promogroups(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        groups = await uow.promo_groups.list()
        member_counts: dict[int, int] = dict(
            (
                await uow.session.execute(
                    select(UserPromoGroup.promo_group_id, func.count()).group_by(
                        UserPromoGroup.promo_group_id
                    )
                )
            ).all()  # type: ignore[arg-type]
        )
        rows = [
            {
                "id": g.id,
                "name": g.name,
                "priority": g.priority,
                "is_default": g.is_default,
                "server_discount_pct": g.server_discount_pct,
                "auto_assign_total_spent_minor": g.auto_assign_total_spent_minor,
                "members": member_counts.get(g.id, 0),
            }
            for g in sorted(groups, key=lambda g: g.priority, reverse=True)
        ]
    return {"items": rows}


@router.get("/referral")
async def referral_summary(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        invited = int(
            await uow.session.scalar(
                select(func.count()).select_from(User).where(User.referred_by_id.is_not(None))
            )
            or 0
        )
        paid_minor = int(
            await uow.session.scalar(
                select(func.coalesce(func.sum(ReferralEarning.amount_minor), 0))
            )
            or 0
        )
        cfg = container.bot_config
        enabled = bool(await cfg.value(uow, "REFERRAL_ENABLED"))
        bonus = int(await cfg.value(uow, "REFERRAL_BONUS_RUB"))
        percent = int(await cfg.value(uow, "REFERRAL_PERCENT"))
    return {
        "enabled": enabled,
        "bonus_minor": bonus,
        "percent": percent,
        "invited_total": invited,
        "paid_out_minor": paid_minor,
    }
