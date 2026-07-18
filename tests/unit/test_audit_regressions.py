"""Regressions from the full-repo audit: balance race, promo rewards, referral wiring,
config-cache TTL, stuck-payment reconciliation query."""

from __future__ import annotations

import datetime as dt

import pytest

from src.application.services.bot_config import BotConfigService
from src.application.services.promo import PromoError, PromoService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    RewardType,
    TransactionStatus,
    TransactionType,
)
from src.infrastructure.database.models.promocode import Promocode
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient, RecordingEventBus


async def test_guarded_debit_rejects_overdraft(uow: UnitOfWork) -> None:
    async with uow:
        user = await make_user(uow, balance_minor=10000)
        await uow.commit()
        assert await uow.users.debit_balance_guarded(user, 6000) is True
        assert user.balance_minor == 4000
        # Second concurrent-style debit no longer covered — must fail, not go negative.
        assert await uow.users.debit_balance_guarded(user, 6000) is False
        assert user.balance_minor == 4000


async def test_referral_bind_creates_ledger_row(uow: UnitOfWork) -> None:
    """/start ref_<code> must create the Referral row the commission engine reads."""
    referrals = ReferralService(RecordingEventBus())
    async with uow:
        referrer = await make_user(uow, telegram_id=111)
        referred = await make_user(uow, telegram_id=222)
        await uow.commit()
        bound = await referrals.bind(uow, referred, referrer.referral_code)
        await uow.commit()
    assert bound is not None
    async with uow:
        row = await uow.referrals.get_by_referred(referred.id)
        assert row is not None and row.referrer_id == referrer.id


async def test_promo_duration_extends_active_subscription(uow: UnitOfWork) -> None:
    fake = FakeRemnawaveClient()
    subs = SubscriptionService(RemnawaveService(fake))
    promo = PromoService(subs)
    from src.application.dto.pricing import PurchaseRequest
    from src.core.enums import PurchaseType

    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow, price_minor=30000)
        await uow.commit()
        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=30,
            currency=Currency.RUB,
            purchase_type=PurchaseType.NEW,
        )
        sub = await subs.grant(uow, user=user, plan=plan, req=req, is_trial=False)
        await uow.commit()
        before = sub.expire_at
        assert before is not None

        uow.session.add(Promocode(code="PLUS7", reward_type=RewardType.DURATION, reward_value=7))
        await uow.commit()
        reward = await promo.apply(uow, user, "PLUS7")
        await uow.commit()
    assert reward is RewardType.DURATION
    assert sub.expire_at is not None and (sub.expire_at - before).days == 7


async def test_promo_duration_requires_active_subscription(uow: UnitOfWork) -> None:
    promo = PromoService(SubscriptionService(RemnawaveService(FakeRemnawaveClient())))
    async with uow:
        user = await make_user(uow)
        uow.session.add(Promocode(code="PLUS7", reward_type=RewardType.DURATION, reward_value=7))
        await uow.commit()
        with pytest.raises(PromoError):
            await promo.apply(uow, user, "PLUS7")


async def test_config_cache_expires_between_processes(uow: UnitOfWork) -> None:
    """Another process's override must become visible without invalidate()."""
    cfg = BotConfigService()
    async with uow:
        assert await cfg.value(uow, "TRIAL_ENABLED") in (True, False)
        # Simulate a *different* process writing an override: bypass this instance.
        other = BotConfigService()
        await other.set_values(uow, {"TRIAL_DURATION_DAYS": 9})
        await uow.commit()
        cfg._cache_at -= BotConfigService.CACHE_TTL + 1  # fast-forward past the TTL
        assert int(await cfg.value(uow, "TRIAL_DURATION_DAYS")) == 9


async def test_process_backoff_survives_panel_blip(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient panel error during fulfilment must be retried in-task, not parked
    until the reconciler (the '10 minutes until my subscription updated' complaint)."""
    import uuid
    from types import SimpleNamespace

    from src.core.exceptions import RemnawaveTransientError
    from src.infrastructure.taskiq import tasks

    monkeypatch.setattr(tasks, "PROCESS_BACKOFF", (0.0, 0.0))

    class FlakyPayments:
        calls = 0

        async def process(self, uow: object, **kw: object) -> bool:
            FlakyPayments.calls += 1
            if FlakyPayments.calls <= 2:
                raise RemnawaveTransientError("panel 502")
            return True

    class NoopUow:
        async def __aenter__(self) -> NoopUow:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    container = SimpleNamespace(uow=NoopUow, payments=FlakyPayments())
    moved = await tasks._process_with_backoff(
        container,  # type: ignore[arg-type]
        payment_id=uuid.uuid4(),
        status=TransactionStatus.COMPLETED,
        amount_minor=None,
    )
    assert moved is True
    assert FlakyPayments.calls == 3


async def test_process_backoff_raises_domain_errors_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only infra blips are worth sleeping on — a domain error (bad state, missing txn)
    must fail fast so the middleware/telemetry sees the real bug immediately."""
    import uuid
    from types import SimpleNamespace

    from src.core.exceptions import NotFound
    from src.infrastructure.taskiq import tasks

    monkeypatch.setattr(tasks, "PROCESS_BACKOFF", (0.0, 0.0))

    class BrokenPayments:
        calls = 0

        async def process(self, uow: object, **kw: object) -> bool:
            BrokenPayments.calls += 1
            raise NotFound("transaction gone")

    class NoopUow:
        async def __aenter__(self) -> NoopUow:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            return None

    container = SimpleNamespace(uow=NoopUow, payments=BrokenPayments())
    with pytest.raises(NotFound):
        await tasks._process_with_backoff(
            container,  # type: ignore[arg-type]
            payment_id=uuid.uuid4(),
            status=TransactionStatus.COMPLETED,
            amount_minor=None,
        )
    assert BrokenPayments.calls == 1


async def test_list_stuck_pending_picks_only_gateway_pendings(uow: UnitOfWork) -> None:
    now = dt.datetime.now(dt.UTC)
    async with uow:
        user = await make_user(uow)
        await uow.commit()

        def txn(**kw: object) -> Transaction:
            base: dict = {
                "user_id": user.id,
                "type": TransactionType.SUBSCRIPTION_PAYMENT,
                "status": TransactionStatus.PENDING,
                "amount_minor": 1000,
                "currency": Currency.RUB,
            }
            base.update(kw)
            return Transaction(**base)

        stuck = txn(gateway_type=PaymentGatewayType.YOOKASSA, external_id="yk-1")
        no_gateway = txn()  # balance/stars pending — not reconcilable
        done = txn(
            gateway_type=PaymentGatewayType.YOOKASSA,
            external_id="yk-2",
            status=TransactionStatus.COMPLETED,
        )
        for t in (stuck, no_gateway, done):
            uow.session.add(t)
        await uow.commit()
        # created_at is set by the DB to "now" — look back from 3 minutes in the future.
        rows = await uow.transactions.list_stuck_pending(
            older_than=now + dt.timedelta(minutes=3),
            newer_than=now - dt.timedelta(hours=24),
        )
    assert [t.external_id for t in rows] == ["yk-1"]
