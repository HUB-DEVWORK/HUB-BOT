"""User DAO."""

from __future__ import annotations

from sqlalchemy import update

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.user import User


class UserDAO(BaseDAO[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.find_one(telegram_id=telegram_id)

    async def get_by_referral_code(self, code: str) -> User | None:
        return await self.find_one(referral_code=code)

    async def increment_balance(self, user: User, delta_minor: int) -> None:
        """Atomically add ``delta_minor`` to the wallet balance.

        Uses an SQL-side ``balance = balance + :delta`` (not a Python read-modify-write) so
        concurrent credits to the same user cannot lose updates. The in-memory ``user`` is
        refreshed so callers see the new value.
        """
        await self.session.execute(
            update(User)
            .where(User.id == user.id)
            .values(balance_minor=User.balance_minor + delta_minor)
        )
        await self.session.refresh(user, attribute_names=["balance_minor"])
