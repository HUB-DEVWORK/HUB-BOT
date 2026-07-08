"""Owner-analytics aggregates (src/infrastructure/services/analytics.py)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.core.enums import Currency, TransactionStatus, TransactionType
from src.infrastructure.database.models.campaign import Campaign
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.services import analytics as svc
from tests.factories import make_user


def _txn(
    user_id: int, type_: TransactionType, amount: int, gateway: object | None = None
) -> Transaction:
    return Transaction(
        user_id=user_id,
        type=type_,
        status=TransactionStatus.COMPLETED,
        amount_minor=amount,
        currency=Currency.RUB,
        gateway_type=gateway,  # type: ignore[arg-type]
    )


async def test_overview_revenue_arpu_and_conversion(session_factory: async_sessionmaker) -> None:
    uow = UnitOfWork(session_factory)
    async with uow:
        u1 = await make_user(uow, telegram_id=1, has_had_paid_subscription=True)
        await make_user(uow, telegram_id=2)
        await uow.session.flush()
        uow.session.add(_txn(u1.id, TransactionType.DEPOSIT, 50000))
        await uow.commit()

    async with uow:
        o = await svc.overview(uow)
    assert o["users"] == 2
    assert o["paid_users"] == 1
    assert o["revenue_minor"] == 50000
    assert o["arpu_minor"] == 25000  # 50000 / 2 users
    assert o["arppu_minor"] == 50000  # 50000 / 1 paying user
    assert o["conversion_paid_pct"] == 50.0
    assert o["new_today"] == 2  # freshly seeded


async def test_revenue_excludes_balance_purchases(session_factory: async_sessionmaker) -> None:
    # A balance-funded sub payment has gateway_type NULL and was already counted at deposit time.
    uow = UnitOfWork(session_factory)
    async with uow:
        u = await make_user(uow, telegram_id=1)
        await uow.session.flush()
        uow.session.add_all(
            [
                _txn(u.id, TransactionType.DEPOSIT, 30000),
                _txn(u.id, TransactionType.SUBSCRIPTION_PAYMENT, 30000, gateway=None),
            ]
        )
        await uow.commit()

    async with uow:
        o = await svc.overview(uow)
    assert o["revenue_minor"] == 30000  # not 60000


async def test_sources_campaign_roi_and_top_referrer(session_factory: async_sessionmaker) -> None:
    uow = UnitOfWork(session_factory)
    async with uow:
        camp = Campaign(name="TG Ads", start_param="ads1", cost_minor=10000)
        uow.session.add(camp)
        await uow.session.flush()
        referrer = await make_user(uow, telegram_id=1)
        await uow.session.flush()
        u = await make_user(
            uow,
            telegram_id=2,
            campaign_id=camp.id,
            referred_by_id=referrer.id,
            has_had_paid_subscription=True,
        )
        await uow.session.flush()
        uow.session.add(_txn(u.id, TransactionType.DEPOSIT, 25000))
        await uow.commit()

    async with uow:
        s = await svc.sources(uow)
    top = s["campaigns"][0]
    assert top["name"] == "TG Ads"
    assert top["users"] == 1 and top["paid"] == 1
    assert top["revenue_minor"] == 25000
    assert top["roi_minor"] == 15000  # 25000 revenue - 10000 spend
    assert s["referrers"][0]["invited"] == 1
