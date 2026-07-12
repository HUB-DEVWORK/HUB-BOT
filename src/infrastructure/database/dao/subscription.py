"""Subscription DAO."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select

from src.core.enums import SubscriptionStatus
from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.subscription import Subscription

_USABLE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL, SubscriptionStatus.LIMITED)


class SubscriptionDAO(BaseDAO[Subscription]):
    model = Subscription

    async def get_by_short_id(self, short_id: str) -> Subscription | None:
        return await self.find_one(short_id=short_id)

    async def get_by_remnawave_uuid(self, remnawave_uuid: uuid.UUID) -> Subscription | None:
        return await self.find_one(remnawave_uuid=remnawave_uuid)

    async def active_for_user(self, user_id: int) -> Sequence[Subscription]:
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(_USABLE),
        )
        return (await self.session.scalars(stmt)).all()

    async def live_with_panel(self, limit: int) -> Sequence[Subscription]:
        """Usable subs that are provisioned on the panel — the resync / device-guard working set.
        Filter is pushed to SQL (hits ix_subscriptions_remnawave_uuid) instead of loading the whole
        table and filtering in Python, which is O(all subs) and unbounded at scale."""
        stmt = (
            select(Subscription)
            .where(
                Subscription.status.in_(_USABLE),
                Subscription.remnawave_uuid.is_not(None),
            )
            .order_by(Subscription.id)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def disabled_with_panel(self, limit: int) -> Sequence[Subscription]:
        """Locally-DISABLED subs still provisioned on the panel — the refund/revoke backstop.
        A refund disables locally + best-effort on the panel; if the panel was down the retry
        can't recover, and live_with_panel never re-checks these (they're not usable). This sweep
        re-asserts the disable so a refunded user can't keep connecting."""
        stmt = (
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.DISABLED,
                Subscription.remnawave_uuid.is_not(None),
            )
            .order_by(Subscription.id)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()
