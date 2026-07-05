"""PromoService: apply a wallet reward once per user (docs/context/04)."""

from __future__ import annotations

import pytest

from src.application.services.promo import PromoError, PromoService
from src.core.enums import RewardType
from src.infrastructure.database.models.promocode import Promocode
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_user


async def test_balance_promocode_applies_once(uow: UnitOfWork) -> None:
    svc = PromoService()
    async with uow:
        user = await make_user(uow)
        await uow.promocodes.add(
            Promocode(code="TOP", reward_type=RewardType.BALANCE, reward_value=5000)
        )
        await uow.commit()

        reward = await svc.apply(uow, user, "TOP")
        assert reward is RewardType.BALANCE
        assert user.balance_minor == 5000
        await uow.commit()

        with pytest.raises(PromoError):
            await svc.apply(uow, user, "TOP")  # already activated by this user


async def test_unknown_code_raises(uow: UnitOfWork) -> None:
    svc = PromoService()
    async with uow:
        user = await make_user(uow)
        await uow.commit()
        with pytest.raises(PromoError):
            await svc.apply(uow, user, "NOPE")
