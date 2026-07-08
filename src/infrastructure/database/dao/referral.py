"""Referral DAOs."""

from __future__ import annotations

from sqlalchemy import func, select

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.referral import Referral, ReferralEarning


class ReferralDAO(BaseDAO[Referral]):
    model = Referral

    async def get_by_referred(self, referred_id: int) -> Referral | None:
        return await self.find_one(referred_id=referred_id)


class ReferralEarningDAO(BaseDAO[ReferralEarning]):
    model = ReferralEarning

    async def total_minor(self, user_id: int) -> int:
        """Sum all referral earnings in the DB — no row cap, no client-side add."""
        stmt = select(func.coalesce(func.sum(ReferralEarning.amount_minor), 0)).where(
            ReferralEarning.user_id == user_id
        )
        return int(await self.session.scalar(stmt) or 0)
