"""User DAO."""

from __future__ import annotations

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.user import User


class UserDAO(BaseDAO[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.find_one(telegram_id=telegram_id)

    async def get_by_referral_code(self, code: str) -> User | None:
        return await self.find_one(referral_code=code)
