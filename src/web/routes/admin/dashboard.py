"""Admin: dashboard KPIs, revenue chart, system status, event feed (screen 01)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select

from src.core.enums import SubscriptionStatus, TransactionStatus, TransactionType
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import day_bounds_utc, iso

router = APIRouter(prefix="/dashboard")

# Revenue = external money only. A balance purchase (gateway_type NULL) is an internal
# transfer — its rubles already counted as revenue when the deposit came in; summing both
# double-counts every balance-funded subscription.
_EXTERNAL_MONEY = or_(
    Transaction.type == TransactionType.DEPOSIT,
    (Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT)
    & Transaction.gateway_type.is_not(None),
)


async def _revenue_between(uow: Any, start: dt.datetime, end: dt.datetime) -> int:
    stmt = (
        select(func.coalesce(func.sum(Transaction.amount_minor), 0))
        .where(Transaction.status == TransactionStatus.COMPLETED)
        .where(_EXTERNAL_MONEY)
        .where(Transaction.completed_at >= start, Transaction.completed_at < end)
        .where(Transaction.is_test.is_(False))
    )
    return int(await uow.session.scalar(stmt) or 0)


@router.get("")
async def dashboard(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    async with container.uow() as uow:
        today_start, today_end = day_bounds_utc(0)
        yest_start, yest_end = day_bounds_utc(1)
        revenue_today = await _revenue_between(uow, today_start, today_end)
        revenue_yesterday = await _revenue_between(uow, yest_start, yest_end)

        active_subs = int(
            await uow.session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.ACTIVE,
                            SubscriptionStatus.TRIAL,
                            SubscriptionStatus.LIMITED,
                        ]
                    )
                )
            )
            or 0
        )
        total_users = await uow.users.count()
        new_24h = int(
            await uow.session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.created_at >= now - dt.timedelta(hours=24))
            )
            or 0
        )
        new_trials_24h = int(
            await uow.session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.is_trial.is_(True),
                    Subscription.created_at >= now - dt.timedelta(hours=24),
                )
            )
            or 0
        )

        # Revenue series, oldest -> newest (14 days including today).
        series: list[dict[str, Any]] = []
        for back in range(13, -1, -1):
            s, e = day_bounds_utc(back)
            series.append(
                {"date": s.date().isoformat(), "amount_minor": await _revenue_between(uow, s, e)}
            )

        nodes = await uow.server_nodes.list()
        online = sum(n.users_online for n in nodes)

        events = [
            {
                "id": e.id,
                "at": iso(e.created_at),
                "actor": e.actor_label,
                "action": e.action,
                "entity": e.entity,
            }
            for e in await uow.audit.recent(20)
        ]

        # Acquisition sources over 30 days.
        month_ago = now - dt.timedelta(days=30)
        recent_users = select(User).where(User.created_at >= month_ago).subquery()
        src_total = int(
            await uow.session.scalar(select(func.count()).select_from(recent_users)) or 0
        )
        src_campaign = int(
            await uow.session.scalar(
                select(func.count())
                .select_from(recent_users)
                .where(recent_users.c.campaign_id.is_not(None))
            )
            or 0
        )
        src_referral = int(
            await uow.session.scalar(
                select(func.count())
                .select_from(recent_users)
                .where(recent_users.c.referred_by_id.is_not(None))
            )
            or 0
        )

    return {
        "revenue_today_minor": revenue_today,
        "revenue_yesterday_minor": revenue_yesterday,
        "active_subscriptions": active_subs,
        "total_users": total_users,
        "new_users_24h": new_24h,
        "new_trials_24h": new_trials_24h,
        "online_now": online,
        "revenue_14d": series,
        "events": events,
        "sources_30d": {
            "total": src_total,
            "campaigns": src_campaign,
            "referrals": src_referral,
            "organic": max(0, src_total - src_campaign - src_referral),
        },
    }


@router.get("/system")
async def system_status(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    """Panel/API/DB/Redis health for the «Система» panel."""
    out: dict[str, Any] = {"api": "ok"}
    try:
        pong = await container.redis.ping()
        out["redis"] = "ok" if pong else "error"
    except Exception:
        out["redis"] = "error"
    async with container.uow() as uow:
        try:
            size = await uow.session.scalar(select(func.pg_database_size(func.current_database())))
            out["db_size_bytes"] = int(size or 0)
        except Exception:
            await uow.rollback()
            out["db_size_bytes"] = None
        cfg = container.bot_config
        out["maintenance_mode"] = bool(await cfg.value(uow, "MAINTENANCE_MODE"))
        out["backup_enabled"] = bool(await cfg.value(uow, "BACKUP_ENABLED"))
        out["backup_time"] = await cfg.value(uow, "BACKUP_TIME")
    try:
        version = await container.remnawave.ensure_supported()
        out["panel"] = {"status": "ok", "version": version.raw}
    except Exception as exc:
        out["panel"] = {"status": "error", "detail": str(exc)[:200]}
    return out
