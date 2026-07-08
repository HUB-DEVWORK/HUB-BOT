"""Admin: funnel + revenue analytics (dashboard screen).

Read-only aggregates over the existing tables — no new storage. Gives the owner the
conversion funnel (users → trial → paid → active → churned) and revenue at a glance.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from src.core.enums import SubscriptionStatus, TransactionStatus, TransactionType
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container

router = APIRouter(prefix="/analytics")


async def _scalar(uow: Any, stmt: Any) -> int:
    return int(await uow.session.scalar(stmt) or 0)


@router.get("")
async def analytics(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        users = await _scalar(uow, select(func.count()).select_from(User))
        paid_users = await _scalar(
            uow,
            select(func.count()).select_from(User).where(User.has_had_paid_subscription.is_(True)),
        )
        active = await _scalar(
            uow,
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL])),
        )
        on_trial = await _scalar(
            uow,
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.status == SubscriptionStatus.TRIAL),
        )
        expired = await _scalar(
            uow,
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.status == SubscriptionStatus.EXPIRED),
        )
        rev_stmt = select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
            Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT,
            Transaction.status == TransactionStatus.COMPLETED,
        )
        revenue_minor = await _scalar(uow, rev_stmt)
        month_start = dt.datetime.now(dt.UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        revenue_month_minor = await _scalar(
            uow, rev_stmt.where(Transaction.created_at >= month_start)
        )
        deposits_minor = await _scalar(
            uow,
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.type == TransactionType.DEPOSIT,
                Transaction.status == TransactionStatus.COMPLETED,
            ),
        )

    conv = round(paid_users / users * 100, 1) if users else 0.0
    return {
        "funnel": {
            "users": users,
            "on_trial": on_trial,
            "paid_users": paid_users,
            "active_subscriptions": active,
            "expired_subscriptions": expired,
        },
        "conversion_paid_pct": conv,
        "revenue_minor": revenue_minor,
        "revenue_month_minor": revenue_month_minor,
        "deposits_minor": deposits_minor,
    }
