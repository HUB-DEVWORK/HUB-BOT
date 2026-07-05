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
