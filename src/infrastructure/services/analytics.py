"""Owner analytics — read-only aggregates over existing tables (no new storage).

Funnel + revenue + ARPU/ARPPU, settled-cohort retention and the top acquisition sources
(ad campaigns with ROI, and referrers). Shared by the web dashboard and the in-bot admin
screen so both show identical numbers. All date math is portable (range filters, never
``date_trunc``), so it runs on Postgres and the sqlite test DB alike.

Revenue = external money in only: a completed DEPOSIT, or a completed SUBSCRIPTION_PAYMENT paid
through a gateway. A balance-funded purchase has ``gateway_type IS NULL`` and was already counted
as revenue when the deposit landed, so it is excluded — no double counting.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from src.core.enums import SubscriptionStatus, TransactionStatus, TransactionType
from src.infrastructure.database.models.campaign import Campaign
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork

_ACTIVE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL, SubscriptionStatus.LIMITED)


def _real_money() -> Any:
    return and_(
        Transaction.status == TransactionStatus.COMPLETED,
        or_(
            Transaction.type == TransactionType.DEPOSIT,
            and_(
                Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT,
                Transaction.gateway_type.is_not(None),
            ),
        ),
    )


async def _n(uow: UnitOfWork, stmt: Any) -> int:
    return int(await uow.session.scalar(stmt) or 0)


async def overview(uow: UnitOfWork) -> dict[str, Any]:
    """Funnel + revenue + per-user economics."""
    now = dt.datetime.now(dt.UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week, month = now - dt.timedelta(days=7), now - dt.timedelta(days=30)

    users = await _n(uow, select(func.count()).select_from(User))
    new_today = await _n(uow, select(func.count()).select_from(User).where(User.created_at >= day))
    new_week = await _n(uow, select(func.count()).select_from(User).where(User.created_at >= week))
    new_month = await _n(
        uow, select(func.count()).select_from(User).where(User.created_at >= month)
    )
    paid_users = await _n(
        uow,
        select(func.count()).select_from(User).where(User.has_had_paid_subscription.is_(True)),
    )
    active = await _n(
        uow,
        select(func.count()).select_from(Subscription).where(Subscription.status.in_(_ACTIVE)),
    )
    on_trial = await _n(
        uow,
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.status == SubscriptionStatus.TRIAL),
    )
    ever_trial = await _n(
        uow,
        select(func.count(func.distinct(Subscription.user_id))).where(
            Subscription.is_trial.is_(True)
        ),
    )

    rev = select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(_real_money())
    revenue = await _n(uow, rev)
    revenue_today = await _n(uow, rev.where(Transaction.created_at >= day))
    revenue_week = await _n(uow, rev.where(Transaction.created_at >= week))
    revenue_month = await _n(uow, rev.where(Transaction.created_at >= month))
    paying = await _n(
        uow,
        select(func.count())
        .select_from(Transaction)
        .where(_real_money(), Transaction.amount_minor > 0),
    )

    return {
        "users": users,
        "new_today": new_today,
        "new_week": new_week,
        "new_month": new_month,
        "on_trial": on_trial,
        "ever_trial": ever_trial,
        "paid_users": paid_users,
        "active_subscriptions": active,
        "revenue_minor": revenue,
        "revenue_today_minor": revenue_today,
        "revenue_week_minor": revenue_week,
        "revenue_month_minor": revenue_month,
        "arpu_minor": revenue // users if users else 0,
        "arppu_minor": revenue // paid_users if paid_users else 0,
        "avg_check_minor": revenue // paying if paying else 0,
        "conversion_paid_pct": round(paid_users / users * 100, 1) if users else 0.0,
        "trial_to_paid_pct": round(paid_users / ever_trial * 100, 1) if ever_trial else 0.0,
    }


async def retention(uow: UnitOfWork) -> dict[str, Any]:
    """Settled-cohort retention: of users who signed up in a fully-elapsed past window, the
    share with an active subscription now. d7 = the 7-14-days-ago cohort; d30 = 30-60-days-ago."""
    now = dt.datetime.now(dt.UTC)

    async def _cohort(lo_days: int, hi_days: int) -> dict[str, Any]:
        lo, hi = now - dt.timedelta(days=lo_days), now - dt.timedelta(days=hi_days)
        window = (User.created_at >= hi, User.created_at < lo)
        size = await _n(uow, select(func.count()).select_from(User).where(*window))
        retained = await _n(
            uow,
            select(func.count(func.distinct(Subscription.user_id)))
            .select_from(Subscription)
            .join(User, User.id == Subscription.user_id)
            .where(*window, Subscription.status.in_(_ACTIVE)),
        )
        return {
            "cohort": size,
            "retained": retained,
            "pct": round(retained / size * 100, 1) if size else 0.0,
        }

    return {"d7": await _cohort(7, 14), "d30": await _cohort(30, 60)}


async def sources(uow: UnitOfWork, limit: int = 5) -> dict[str, Any]:
    """Top acquisition sources: ad campaigns (with users/paid/revenue/ROI) and top referrers."""
    paid_sum = func.coalesce(
        func.sum(case((User.has_had_paid_subscription.is_(True), 1), else_=0)), 0
    )
    camp_rows = (
        await uow.session.execute(
            select(
                Campaign.id,
                Campaign.name,
                Campaign.cost_minor,
                func.count(User.id).label("users"),
                paid_sum.label("paid"),
            )
            .join(User, User.campaign_id == Campaign.id)
            .group_by(Campaign.id, Campaign.name, Campaign.cost_minor)
            .order_by(func.count(User.id).desc())
            .limit(limit)
        )
    ).all()
    # Revenue per campaign (Campaign -> User -> Transaction), merged by id.
    rev_rows = (
        await uow.session.execute(
            select(
                User.campaign_id,
                func.coalesce(func.sum(Transaction.amount_minor), 0).label("revenue"),
            )
            .select_from(Transaction)
            .join(User, User.id == Transaction.user_id)
            .where(_real_money(), User.campaign_id.is_not(None))
            .group_by(User.campaign_id)
        )
    ).all()
    rev_by_campaign = {r.campaign_id: int(r.revenue) for r in rev_rows}
    campaigns = [
        {
            "name": r.name,
            "users": int(r.users),
            "paid": int(r.paid),
            "cost_minor": int(r.cost_minor),
            "revenue_minor": rev_by_campaign.get(r.id, 0),
            "roi_minor": rev_by_campaign.get(r.id, 0) - int(r.cost_minor),
        }
        for r in camp_rows
    ]

    invited = aliased(User)
    ref_rows = (
        await uow.session.execute(
            select(
                User.username,
                User.telegram_id,
                func.count(invited.id).label("invited"),
            )
            .join(invited, invited.referred_by_id == User.id)
            .group_by(User.id, User.username, User.telegram_id)
            .order_by(func.count(invited.id).desc())
            .limit(limit)
        )
    ).all()
    referrers = [
        {
            "label": f"@{r.username}" if r.username else f"id{r.telegram_id}",
            "invited": int(r.invited),
        }
        for r in ref_rows
    ]
    return {"campaigns": campaigns, "referrers": referrers}


async def full(uow: UnitOfWork) -> dict[str, Any]:
    """Everything in one call — for the web dashboard and the in-bot digest."""
    return {
        "overview": await overview(uow),
        "retention": await retention(uow),
        "sources": await sources(uow),
    }
