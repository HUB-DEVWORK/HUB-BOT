"""UserDAO.get_or_create — race-safe user upsert.

Regression guard for the #1 crash in crash-telemetry: two updates from the same brand-new
user (a rapid /start burst, or the bot + mini-app landing at once) both saw "no row" and
both INSERTed, and the loser died with `duplicate key … ix_users_telegram_id`.
"""

from __future__ import annotations

from src.application.services.ids import generate_referral_code
from src.core.enums import Currency
from src.infrastructure.database.models.user import User
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_user


def _new_user(telegram_id: int) -> User:
    return User(
        telegram_id=telegram_id,
        referral_code=generate_referral_code(),
        currency=Currency.RUB,
    )


async def test_get_or_create_is_idempotent(uow: UnitOfWork) -> None:
    """Second call for the same telegram_id returns the same row — never a duplicate."""
    async with uow:
        first, created_first = await uow.users.get_or_create(_new_user(555))
        assert created_first is True

        second, created_second = await uow.users.get_or_create(_new_user(555))
        assert created_second is False
        assert second.id == first.id
        assert await uow.users.count(telegram_id=555) == 1


async def test_get_or_create_recovers_from_lost_race(uow: UnitOfWork, monkeypatch) -> None:
    """The loser of the INSERT race catches the unique violation and re-reads the winner's row
    instead of crashing (the SAVEPOINT keeps the outer transaction usable)."""
    async with uow:  # the "winner" commits the row first
        await make_user(uow, telegram_id=777)
        await uow.commit()

    async with uow:
        real_lookup = uow.users.get_by_telegram_id
        calls = {"n": 0}

        async def flaky(telegram_id: int) -> User | None:
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # simulate: our SELECT ran before the winner's INSERT was visible
            return await real_lookup(telegram_id)

        monkeypatch.setattr(uow.users, "get_by_telegram_id", flaky)

        user, created = await uow.users.get_or_create(_new_user(777))

        assert created is False  # detected the conflict, did not raise
        assert user.telegram_id == 777
        assert calls["n"] == 2  # fast-path miss + fallback re-read
        assert await real_lookup(777) is not None
        assert await uow.users.count(telegram_id=777) == 1
