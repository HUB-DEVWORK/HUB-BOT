"""User DAO."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.user import User


class UserDAO(BaseDAO[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.find_one(telegram_id=telegram_id)

    async def get_or_create(self, user: User) -> tuple[User, bool]:
        """Race-safe get-or-create keyed on ``telegram_id``; returns ``(user, created)``.

        Two updates from the same brand-new user — a rapid ``/start`` burst, or the bot and
        the mini-app landing at once — used to both see "no row" and both INSERT, and the
        loser crashed with ``duplicate key … ix_users_telegram_id``. Here the loser catches
        the unique violation inside a SAVEPOINT (so the outer transaction survives) and
        re-reads the row the winner committed.
        """
        telegram_id = user.telegram_id
        assert telegram_id is not None, "get_or_create requires user.telegram_id"
        existing = await self.get_by_telegram_id(telegram_id)
        if existing is not None:
            return existing, False
        try:
            async with self.session.begin_nested():
                self.session.add(user)
                await self.session.flush()
        except IntegrityError:
            with suppress(Exception):
                self.session.expunge(user)  # drop the INSERT that lost the race
            existing = await self.get_by_telegram_id(telegram_id)
            if existing is None:
                raise  # a different constraint — not the telegram_id race
            return existing, False
        return user, True

    async def find_by_verify_token(self, token: str) -> User | None:
        """Locate a pending-verification user by the token stashed in notification_settings."""
        stmt = select(User).where(User.notification_settings["verify_token"].astext == token)
        return (await self.session.scalars(stmt)).first()

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.strip().lower())
        return (await self.session.scalars(stmt)).first()

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
