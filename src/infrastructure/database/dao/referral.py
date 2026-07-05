"""Referral DAOs."""

from __future__ import annotations

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.referral import Referral, ReferralEarning


class ReferralDAO(BaseDAO[Referral]):
    model = Referral

    async def get_by_referred(self, referred_id: int) -> Referral | None:
        return await self.find_one(referred_id=referred_id)


class ReferralEarningDAO(BaseDAO[ReferralEarning]):
    model = ReferralEarning
