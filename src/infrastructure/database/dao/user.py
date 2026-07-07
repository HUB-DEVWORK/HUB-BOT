"""User DAO."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult, select, update

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.user import User


class UserDAO(BaseDAO[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.find_one(telegram_id=telegram_id)

    async def get_by_referral_code(self, code: str) -> User | None:
        return await self.find_one(referral_code=code)

    async def debit_balance_guarded(self, user: User, amount_minor: int) -> bool:
        """Debit iff the balance still covers it — one atomic UPDATE, no check-then-act.

        Two concurrent purchases (bot + mini-app) both pass a Python-side balance check;
        the SQL guard makes the second one fail instead of driving the wallet negative.
        """
        result = await self.session.execute(
            update(User)
            .where(User.id == user.id, User.balance_minor >= amount_minor)
            .values(balance_minor=User.balance_minor - amount_minor)
        )
        ok = (cast("CursorResult[Any]", result).rowcount or 0) > 0
        if ok:
            await self.session.refresh(user, ["balance_minor"])
        return ok

    async def lock_for_update(self, user_id: int) -> User | None:
        """Row-lock a user (no-op on SQLite, real on Postgres) — serializes trial grants."""
        stmt = select(User).where(User.id == user_id).with_for_update()
        return (await self.session.scalars(stmt)).first()

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
