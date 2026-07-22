"""Merging a web-cabinet account into a Telegram account (account_link service)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from src.application.services.account_link import AccountLinkError, merge_web_into_telegram
from src.core.enums import (
    AuthType,
    Currency,
    RewardType,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
)
from src.infrastructure.database.models.linked_account import LinkedAccount
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.referral import Referral
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.database.uow import UnitOfWork
from tests.factories import make_plan, make_user


async def _make_web_user(uow: UnitOfWork, *, email: str = "web@example.com", **kw: object) -> User:
    from src.application.services.ids import generate_referral_code

    user = User(
        auth_type=AuthType.EMAIL,
        email=email,
        email_verified=True,
        password_hash="scrypt$x",
        referral_code=generate_referral_code(),
        currency=Currency.RUB,
        **kw,  # type: ignore[arg-type]
    )
    await uow.users.add(user)
    return user


async def test_merge_moves_everything(uow: UnitOfWork) -> None:
    async with uow:
        tg = await make_user(uow, telegram_id=111)
        web = await _make_web_user(uow, balance_minor=5000)
        tg.balance_minor = 1500
        sub = Subscription(
            user_id=web.id, short_id="WEB1", status=SubscriptionStatus.ACTIVE, plan_snapshot={}
        )
        uow.session.add(sub)
        uow.session.add(
            Transaction(
                user_id=web.id,
                type=TransactionType.DEPOSIT,
                status=TransactionStatus.COMPLETED,
                amount_minor=5000,
                currency=Currency.RUB,
            )
        )
        uow.session.add(
            LinkedAccount(user_id=web.id, provider="vk", external_id="42", display_name="Иван")
        )
        await uow.flush()
        web.current_subscription_id = sub.id
        await uow.commit()
        tg_id, web_id, sub_id = tg.id, web.id, sub.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        merged = await merge_web_into_telegram(uow, tg_user, web_id)
        await uow.commit()

        assert merged.balance_minor == 6500
        assert merged.email == "web@example.com" and merged.email_verified
        assert merged.password_hash == "scrypt$x"
        assert merged.current_subscription_id == sub_id
        assert await uow.users.get(web_id) is None  # the web row is gone
        moved_sub = await uow.subscriptions.get(sub_id)
        assert moved_sub is not None and moved_sub.user_id == tg_id
        ident = await uow.linked_accounts.get_identity("vk", "42")
        assert ident is not None and ident.user_id == tg_id
        assert await uow.transactions.count(user_id=tg_id) == 1


async def test_merge_refuses_conflicts(uow: UnitOfWork) -> None:
    async with uow:
        tg = await make_user(uow, telegram_id=222, email="tg@example.com", email_verified=True)
        other_tg = await make_user(uow, telegram_id=333)
        web = await _make_web_user(uow, email="other@example.com")
        already_linked = await _make_web_user(uow, email="linked@example.com")
        already_linked.telegram_id = 444
        await uow.commit()
        tg_id, web_id, linked_id = tg.id, web.id, already_linked.id
        other_id = other_tg.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        # different e-mails on both sides
        with pytest.raises(AccountLinkError):
            await merge_web_into_telegram(uow, tg_user, web_id)
        # the "web" account is in fact somebody's telegram account
        with pytest.raises(AccountLinkError):
            await merge_web_into_telegram(uow, tg_user, linked_id)
        # self-link
        with pytest.raises(AccountLinkError):
            await merge_web_into_telegram(uow, tg_user, tg_id)
        # stale code -> missing user
        with pytest.raises(AccountLinkError):
            await merge_web_into_telegram(uow, tg_user, 10_000)
    # sanity: nothing merged, all users still present
    async with uow:
        assert await uow.users.get(web_id) is not None
        assert await uow.users.get(other_id) is not None


async def test_merge_referral_and_promocode_dedup(uow: UnitOfWork) -> None:
    async with uow:
        referrer = await make_user(uow, telegram_id=555)
        tg = await make_user(uow, telegram_id=666)
        web = await _make_web_user(uow)
        web.referred_by_id = referrer.id
        uow.session.add(Referral(referrer_id=referrer.id, referred_id=web.id))
        promo = Promocode(code="WELCOME", reward_type=RewardType.BALANCE, reward_value=100)
        uow.session.add(promo)
        await uow.flush()
        # both accounts activated the same code — after the merge it must stay used ONCE
        uow.session.add(PromocodeActivation(promocode_id=promo.id, user_id=tg.id))
        uow.session.add(PromocodeActivation(promocode_id=promo.id, user_id=web.id))
        await uow.commit()
        referrer_id, tg_id, web_id, promo_id = referrer.id, tg.id, web.id, promo.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        await merge_web_into_telegram(uow, tg_user, web_id)
        await uow.commit()

        # the referral binding moved to the survivor
        assert tg_user.referred_by_id == referrer_id
        binding = (
            await uow.session.scalars(select(Referral).where(Referral.referred_id == tg_id))
        ).first()
        assert binding is not None and binding.referrer_id == referrer_id
        activations = list(
            (
                await uow.session.scalars(
                    select(PromocodeActivation).where(PromocodeActivation.promocode_id == promo_id)
                )
            ).all()
        )
        assert len(activations) == 1 and activations[0].user_id == tg_id


async def test_merge_trial_spent_on_either_side(uow: UnitOfWork) -> None:
    async with uow:
        tg = await make_user(uow, telegram_id=777)
        web = await _make_web_user(uow)
        web.is_trial_available = False  # the web account already used its trial
        await uow.commit()
        tg_id, web_id = tg.id, web.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        await merge_web_into_telegram(uow, tg_user, web_id)
        await uow.commit()
        assert tg_user.is_trial_available is False


async def test_merge_revokes_source_refresh_tokens(uow: UnitOfWork) -> None:
    """A pre-merge web session's refresh token must NOT survive onto the merged account —
    otherwise it keeps working against the (now larger) survivor. It's dropped, not moved."""
    from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken

    async with uow:
        tg = await make_user(uow, telegram_id=888)
        web = await _make_web_user(uow)
        await uow.flush()
        uow.session.add(
            CabinetRefreshToken(
                user_id=web.id,
                token_hash="deadbeef",
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=7),
            )
        )
        await uow.commit()
        tg_id, web_id = tg.id, web.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        await merge_web_into_telegram(uow, tg_user, web_id)
        await uow.commit()
        # neither the survivor nor the (deleted) web account keeps the token
        assert await uow.cabinet_tokens.find_one(token_hash="deadbeef") is None


async def test_merge_same_plan_trial_yields_to_paid(uow: UnitOfWork) -> None:
    """Both sides live on the same plan (uq_active_sub): a trial is retired so the move
    doesn't violate the unique index, and the paid subscription becomes current."""
    async with uow:
        plan, _ = await make_plan(uow, code="pro")
        tg = await make_user(uow, telegram_id=999)
        web = await _make_web_user(uow)
        tg_trial = Subscription(
            user_id=tg.id,
            short_id="TGT",
            plan_id=plan.id,
            status=SubscriptionStatus.TRIAL,
            is_trial=True,
            plan_snapshot={},
        )
        web_paid = Subscription(
            user_id=web.id,
            short_id="WBP",
            plan_id=plan.id,
            status=SubscriptionStatus.ACTIVE,
            is_trial=False,
            plan_snapshot={},
        )
        uow.session.add_all([tg_trial, web_paid])
        await uow.flush()
        tg.current_subscription_id = tg_trial.id
        web.current_subscription_id = web_paid.id
        await uow.commit()
        tg_id, web_id, trial_id, paid_id = tg.id, web.id, tg_trial.id, web_paid.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        await merge_web_into_telegram(uow, tg_user, web_id)  # must NOT raise on uq_active_sub
        await uow.commit()
        retired = await uow.subscriptions.get(trial_id)
        paid = await uow.subscriptions.get(paid_id)
        assert retired is not None and retired.status is SubscriptionStatus.EXPIRED
        assert paid is not None and paid.user_id == tg_id and paid.status.is_usable
        # the survivor's current pointer follows the usable paid subscription, not the trial
        assert tg_user.current_subscription_id == paid_id


async def test_merge_two_paid_same_plan_refuses(uow: UnitOfWork) -> None:
    """Two live PAID subscriptions on the same plan can't be silently dropped — refuse."""
    async with uow:
        plan, _ = await make_plan(uow, code="pro2")
        tg = await make_user(uow, telegram_id=1001)
        web = await _make_web_user(uow)
        uow.session.add_all(
            [
                Subscription(
                    user_id=tg.id,
                    short_id="TP1",
                    plan_id=plan.id,
                    status=SubscriptionStatus.ACTIVE,
                    is_trial=False,
                    plan_snapshot={},
                ),
                Subscription(
                    user_id=web.id,
                    short_id="WP1",
                    plan_id=plan.id,
                    status=SubscriptionStatus.ACTIVE,
                    is_trial=False,
                    plan_snapshot={},
                ),
            ]
        )
        await uow.commit()
        tg_id, web_id = tg.id, web.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        with pytest.raises(AccountLinkError):
            await merge_web_into_telegram(uow, tg_user, web_id)


async def test_merge_current_pointer_prefers_usable_over_dead_trial(uow: UnitOfWork) -> None:
    """Bot user's pointer sits on an expired trial; the paid sub bought on the web must
    become current after the merge (else the UI shows 'no subscription' and they pay twice)."""
    async with uow:
        trial_plan, _ = await make_plan(uow, code="trialp")
        paid_plan, _ = await make_plan(uow, code="paidp")
        tg = await make_user(uow, telegram_id=1002)
        web = await _make_web_user(uow)
        dead = Subscription(
            user_id=tg.id,
            short_id="DED",
            plan_id=trial_plan.id,
            status=SubscriptionStatus.EXPIRED,
            is_trial=True,
            plan_snapshot={},
        )
        paid = Subscription(
            user_id=web.id,
            short_id="PAID",
            plan_id=paid_plan.id,
            status=SubscriptionStatus.ACTIVE,
            is_trial=False,
            plan_snapshot={},
        )
        uow.session.add_all([dead, paid])
        await uow.flush()
        tg.current_subscription_id = dead.id  # points at the dead trial
        await uow.commit()
        tg_id, web_id, paid_id = tg.id, web.id, paid.id

    async with uow:
        tg_user = await uow.users.get(tg_id)
        assert tg_user is not None
        await merge_web_into_telegram(uow, tg_user, web_id)
        await uow.commit()
        assert tg_user.current_subscription_id == paid_id
