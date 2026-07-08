"""Admin: users list/detail + drawer actions (screen 02)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, Select, func, or_, select

from src.core.enums import (
    Role,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
    UserStatus,
)
from src.core.exceptions import DomainError, RemnawaveError
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import OkOut, Page, audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/users")


def _like_escape(q: str) -> str:
    """Escape LIKE wildcards in user input so «%» doesn't match everything."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_SUB_FILTERS: dict[str, tuple[SubscriptionStatus, ...]] = {
    "active": (SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED),
    "trial": (SubscriptionStatus.TRIAL,),
    "expired": (SubscriptionStatus.EXPIRED, SubscriptionStatus.DISABLED),
}


def _list_stmt(status_filter: str, q: str) -> Select[Any]:
    stmt = (
        select(User, Subscription)
        .outerjoin(Subscription, Subscription.id == User.current_subscription_id)
        .where(User.role != Role.SYSTEM)
    )
    if status_filter == "blocked":
        stmt = stmt.where(User.status == UserStatus.BLOCKED)
    elif status_filter in _SUB_FILTERS:
        stmt = stmt.where(
            User.status == UserStatus.ACTIVE,
            Subscription.status.in_(_SUB_FILTERS[status_filter]),
        )
    if q:
        needle = f"%{_like_escape(q.lstrip('@').lower())}%"
        clauses: list[ColumnElement[bool]] = [
            func.lower(func.coalesce(User.username, "")).like(needle),
            func.lower(func.coalesce(User.first_name, "")).like(needle),
            func.lower(func.coalesce(User.last_name, "")).like(needle),
        ]
        if q.isdigit():
            clauses.append(User.telegram_id == int(q))
        stmt = stmt.where(or_(*clauses))
    return stmt


def _user_status(user: User, sub: Subscription | None) -> str:
    if user.status is UserStatus.BLOCKED:
        return "blocked"
    if sub is None:
        return "none"
    if sub.status is SubscriptionStatus.TRIAL:
        return "trial"
    if sub.status.is_usable:
        return "active"
    return "expired"


def _row(user: User, sub: Subscription | None) -> dict[str, Any]:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "name": " ".join(filter(None, [user.first_name, user.last_name])) or None,
        "status": _user_status(user, sub),
        "role": user.role.name,
        "balance_minor": user.balance_minor,
        "currency": user.currency.value,
        "plan_name": (sub.plan_snapshot or {}).get("name") if sub else None,
        "expire_at": iso(sub.expire_at) if sub else None,
        "traffic_used_bytes": sub.traffic_used_bytes if sub else 0,
        "traffic_limit_bytes": sub.traffic_limit_bytes if sub else 0,
        "device_limit": sub.device_limit if sub else None,
        "created_at": iso(user.created_at),
        "last_seen_at": iso(user.updated_at),
    }


@router.get("", response_model=Page)
async def list_users(
    q: str = Query("", max_length=64),
    status: str = Query("all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    container: AppContainer = Depends(get_container),
) -> Page:
    async with container.uow() as uow:
        stmt = _list_stmt(status, q)
        total = int(
            await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        rows = (
            await uow.session.execute(stmt.order_by(User.id.desc()).limit(limit).offset(offset))
        ).all()
        items = [_row(u, s) for u, s in rows]
    return Page(items=items, total=total, limit=limit, offset=offset)


class CountersOut(BaseModel):
    all: int
    active: int
    trial: int
    expired: int
    blocked: int


@router.get("/counters", response_model=CountersOut)
async def counters(container: AppContainer = Depends(get_container)) -> CountersOut:
    async with container.uow() as uow:
        out: dict[str, int] = {}
        for name in ("all", "active", "trial", "expired", "blocked"):
            stmt = _list_stmt(name, "")
            out[name] = int(
                await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
            )
    return CountersOut(**out)


@router.get("/{user_id}")
async def user_detail(
    user_id: int, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
        txs = await uow.transactions.list_recent(user_id, limit=10)
        invited = await uow.users.count(referred_by_id=user_id)
        earned_minor = await uow.referral_earnings.total_minor(user_id)

        detail = _row(user, sub)
        detail.update(
            {
                "referral_code": user.referral_code,
                "referral_invited": invited,
                "referral_earned_minor": earned_minor,
                "is_trial_available": user.is_trial_available,
                "personal_discount_pct": user.personal_discount_pct,
                "purchase_discount_pct": user.purchase_discount_pct,
                "subscription": None,
                "transactions": [
                    {
                        "id": t.id,
                        "type": t.type.value,
                        "status": t.status.value,
                        "amount_minor": t.amount_minor,
                        "currency": t.currency.value,
                        "gateway": t.gateway_type.value if t.gateway_type else None,
                        "created_at": iso(t.created_at),
                    }
                    for t in sorted(txs, key=lambda t: t.id, reverse=True)
                ],
            }
        )
        if sub is not None:
            detail["subscription"] = {
                "id": sub.id,
                "status": sub.status.value,
                "is_trial": sub.is_trial,
                "short_id": sub.short_id,
                "remnawave_uuid": str(sub.remnawave_uuid) if sub.remnawave_uuid else None,
                "plan_snapshot": sub.plan_snapshot,
                "expire_at": iso(sub.expire_at),
                "traffic_used_bytes": sub.traffic_used_bytes,
                "traffic_limit_bytes": sub.traffic_limit_bytes,
                "device_limit": sub.device_limit,
                "subscription_url": sub.subscription_url,
                "autopay_enabled": sub.autopay_enabled,
            }
        return detail


class BalanceIn(BaseModel):
    amount_minor: int = Field(..., ge=-10_000_000_00, le=10_000_000_00)
    comment: str = Field("", max_length=256)


@router.post("/{user_id}/balance", response_model=OkOut)
async def change_balance(
    user_id: int,
    body: BalanceIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    if body.amount_minor == 0:
        raise HTTPException(400, "amount must be non-zero")
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        await uow.users.increment_balance(user, body.amount_minor)
        now = dt.datetime.now(dt.UTC)
        await uow.transactions.add(
            Transaction(
                user_id=user.id,
                type=TransactionType.GIFT if body.amount_minor > 0 else TransactionType.WITHDRAWAL,
                status=TransactionStatus.COMPLETED,
                amount_minor=abs(body.amount_minor),
                currency=user.currency,
                payment_method="admin",
                gateway_display_name=f"admin @{identity.username}",
                completed_at=now,
            )
        )
        await audit(
            uow,
            identity,
            "user.balance",
            f"user:{user_id}",
            amount_minor=body.amount_minor,
            comment=body.comment,
        )
        await uow.commit()
    return OkOut()


class ExtendIn(BaseModel):
    days: int = Field(..., ge=1, le=3650)


@router.post("/{user_id}/extend", response_model=OkOut)
async def extend_subscription(
    user_id: int,
    body: ExtendIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        if not user.current_subscription_id:
            raise HTTPException(400, "user has no subscription")
        sub = await uow.subscriptions.get(user.current_subscription_id)
        if sub is None:
            raise HTTPException(400, "subscription missing")
        try:
            await container.subscriptions.renew(
                uow, sub, days=body.days, telegram_id=user.telegram_id
            )
        except RemnawaveError as exc:
            raise HTTPException(502, f"panel error: {exc}") from exc
        except DomainError as exc:
            raise HTTPException(400, str(exc)) from exc
        await audit(uow, identity, "user.extend", f"user:{user_id}", days=body.days)
        await uow.commit()
    return OkOut()


@router.post("/{user_id}/block", response_model=OkOut)
async def block_user(
    user_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    return await _set_status(container, identity, user_id, UserStatus.BLOCKED)


@router.post("/{user_id}/unblock", response_model=OkOut)
async def unblock_user(
    user_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    return await _set_status(container, identity, user_id, UserStatus.ACTIVE)


async def _set_status(
    container: AppContainer, identity: AdminIdentity, user_id: int, status: UserStatus
) -> OkOut:
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        if user.role.is_staff and status is UserStatus.BLOCKED:
            raise HTTPException(400, "cannot block a staff account")
        user.status = status
        await audit(uow, identity, f"user.{status.value}", f"user:{user_id}")
        await uow.commit()
    return OkOut()


class DeviceLimitIn(BaseModel):
    delta: int = Field(1, ge=-10, le=10)


@router.post("/{user_id}/hwid", response_model=OkOut)
async def change_device_limit(
    user_id: int,
    body: DeviceLimitIn,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None or not user.current_subscription_id:
            raise HTTPException(400, "user has no subscription")
        sub = await uow.subscriptions.get(user.current_subscription_id)
        if sub is None:
            raise HTTPException(400, "subscription missing")
        new_limit = max(1, (sub.device_limit or 1) + body.delta)
        sub.device_limit = new_limit
        if sub.remnawave_uuid is not None and sub.expire_at is not None:
            spec = container.remnawave.build_spec(
                short_id=sub.short_id,
                telegram_id=user.telegram_id,
                expire_at=sub.expire_at,
                traffic_limit_bytes=sub.traffic_limit_bytes,
                device_limit=new_limit,
                internal_squads=tuple(sub.internal_squads or ()),
                external_squad=sub.external_squad,
            )
            try:
                await container.remnawave.apply(sub.remnawave_uuid, spec)
            except RemnawaveError as exc:
                raise HTTPException(502, f"panel error: {exc}") from exc
        await audit(uow, identity, "user.hwid", f"user:{user_id}", new_limit=new_limit)
        await uow.commit()
    return OkOut()


@router.post("/{user_id}/reset-traffic", response_model=OkOut)
async def reset_traffic(
    user_id: int,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> OkOut:
    async with container.uow() as uow:
        user = await uow.users.get(user_id)
        if user is None or not user.current_subscription_id:
            raise HTTPException(400, "user has no subscription")
        sub = await uow.subscriptions.get(user.current_subscription_id)
        if sub is None:
            raise HTTPException(400, "subscription missing")
        # Panel-first: reset on Remnawave, else a user.updated webhook re-mirrors the real usage
        # and the local zero is reverted — a traffic-LIMITED user would stay throttled (#3).
        if sub.remnawave_uuid is not None:
            try:
                await container.remnawave_client.reset_traffic(sub.remnawave_uuid)
            except RemnawaveError as exc:
                raise HTTPException(502, f"panel error: {exc}") from exc
        sub.traffic_used_bytes = 0
        await audit(uow, identity, "user.reset_traffic", f"user:{user_id}")
        await uow.commit()
    return OkOut()
