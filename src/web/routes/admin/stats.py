"""Admin: product statistics — user & subscription breakdowns with a 14-day chart (Продукт)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from src.core.enums import SubscriptionStatus, UserStatus
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import day_bounds_utc

router = APIRouter(prefix="/stats")

_LIVE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL, SubscriptionStatus.LIMITED)


async def _count(uow: Any, stmt: Any) -> int:
    return int(await uow.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)


async def _users_created_since(uow: Any, since: dt.datetime) -> int:
    return await _count(uow, select(User.id).where(User.created_at >= since))


async def _subs_in(uow: Any, *statuses: SubscriptionStatus) -> int:
    return await _count(uow, select(Subscription.id).where(Subscription.status.in_(statuses)))


@router.get("")
async def stats(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    today_start, _ = day_bounds_utc(0)
    async with container.uow() as uow:
        total = await uow.users.count()

        # Users linked to a live subscription (their current one).
        with_sub_stmt = (
            select(User.id)
            .join(Subscription, Subscription.id == User.current_subscription_id)
            .where(Subscription.status.in_(_LIVE))
        )
        with_sub = await _count(uow, with_sub_stmt)
        with_trial = await _count(
            uow,
            select(User.id)
            .join(Subscription, Subscription.id == User.current_subscription_id)
            .where(Subscription.status == SubscriptionStatus.TRIAL),
        )

        # New users per day, last 14 days (oldest -> newest), for the bar chart.
        chart: list[dict[str, Any]] = []
        for days_ago in range(13, -1, -1):
            start, end = day_bounds_utc(days_ago)
            n = await _count(
                uow, select(User.id).where(User.created_at >= start, User.created_at < end)
            )
            chart.append({"date": start.date().isoformat(), "count": n})

        users = {
            "total": total,
            "new_today": await _users_created_since(uow, today_start),
            "new_week": await _users_created_since(uow, now - dt.timedelta(days=7)),
            "new_month": await _users_created_since(uow, now - dt.timedelta(days=30)),
            "with_sub": with_sub,
            "without_sub": max(0, total - with_sub),
            "with_trial": with_trial,
            "blocked": await _count(uow, select(User.id).where(User.status == UserStatus.BLOCKED)),
            "bot_blocked": await _count(
                uow, select(User.id).where(User.bot_blocked_at.is_not(None))
            ),
            "chart": chart,
        }

        live_traffic = (
            select(Subscription.id, Subscription.traffic_limit_bytes)
            .where(Subscription.status.in_(_LIVE))
            .subquery()
        )
        unlimited = int(
            await uow.session.scalar(
                select(func.count()).where(live_traffic.c.traffic_limit_bytes == 0)
            )
            or 0
        )
        limited = int(
            await uow.session.scalar(
                select(func.count()).where(live_traffic.c.traffic_limit_bytes > 0)
            )
            or 0
        )
        subs = {
            "active": await _subs_in(uow, SubscriptionStatus.ACTIVE, SubscriptionStatus.LIMITED),
            "disabled": await _subs_in(uow, SubscriptionStatus.DISABLED),
            "expired": await _subs_in(uow, SubscriptionStatus.EXPIRED),
            "trial": await _subs_in(uow, SubscriptionStatus.TRIAL),
            "unlimited_traffic": unlimited,
            "limited_traffic": limited,
        }

    return {"users": users, "subs": subs}
