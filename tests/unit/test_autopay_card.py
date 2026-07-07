"""Card autopay: the saved-card charge branch of the autopay task (respx-mocked YooKassa).

Covers the full vertical: eligibility gates → PENDING txn → charge without confirmation →
standard idempotent pipeline → renewal/notification. The balance path stays first-priority.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from types import SimpleNamespace

import httpx
import respx
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.application.services.payment import PaymentService
from src.application.services.pricing import PricingService
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.application.services.remnawave import RemnawaveService
from src.application.services.subscription import SubscriptionService
from src.core.enums import (
    Currency,
    PaymentGatewayType,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
)
from src.infrastructure.database.models.payment_gateway import PaymentGateway
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.uow import UnitOfWork
from src.infrastructure.payments.crypto import SecretBox
from src.infrastructure.payments.factory import GatewayFactory
from src.infrastructure.taskiq.tasks import _autopay_one, _store_saved_method
from tests.factories import make_plan, make_user
from tests.fakes import FakeRemnawaveClient, RecordingEventBus

API_PAYMENTS = "https://api.yookassa.ru/v3/payments"
METHOD_ID = "pm-22d6d597"


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def notify_user(self, telegram_id: int, text: str) -> None:
        self.sent.append((telegram_id, text))


def make_container(session_factory: async_sessionmaker) -> SimpleNamespace:
    """The AppContainer slice the autopay task actually touches (duck-typed via cast)."""
    fake = FakeRemnawaveClient()
    bus = RecordingEventBus()
    pricing = PricingService()
    subscriptions = SubscriptionService(RemnawaveService(fake))
    purchase = PurchaseService(pricing, subscriptions, bus)
    return SimpleNamespace(
        uow=lambda: UnitOfWork(session_factory),
        pricing=pricing,
        subscriptions=subscriptions,
        purchase=purchase,
        payments=PaymentService(purchase, bus, ReferralService(bus)),
        notifier=RecordingNotifier(),
        gateway_factory=GatewayFactory(),
        secret_box=SecretBox(Fernet.generate_key().decode()),
    )


async def _seed(
    container: SimpleNamespace,
    uow: UnitOfWork,
    *,
    balance_minor: int = 0,
    card_saved: bool = True,
    card_enabled: bool = True,
    attempted_at: dt.datetime | None = None,
    recurrent_enabled: bool = True,
) -> int:
    """User + plan + expiring autopay subscription + active YooKassa row. Returns sub id."""
    async with uow:
        user = await make_user(
            uow,
            balance_minor=balance_minor,
            saved_payment_method_id=(
                container.secret_box.encrypt(METHOD_ID) if card_saved else None
            ),
            saved_payment_method_title="MIR *4444" if card_saved else None,
        )
        plan, _ = await make_plan(uow, price_minor=19900, days=30)
        sub = Subscription(
            user_id=user.id,
            plan_id=plan.id,
            short_id=uuid.uuid4().hex[:12],
            remnawave_uuid=uuid.uuid4(),
            status=SubscriptionStatus.ACTIVE,
            expire_at=dt.datetime.now(dt.UTC) + dt.timedelta(hours=12),
            autopay_enabled=True,
            autopay_period_days=30,
            autopay_card_enabled=card_enabled,
            autopay_card_attempted_at=attempted_at,
        )
        uow.session.add(sub)
        await uow.flush()
        user.current_subscription_id = sub.id
        uow.session.add(
            PaymentGateway(
                type=PaymentGatewayType.YOOKASSA,
                is_active=True,
                currency=Currency.RUB,
                settings={
                    "shop_id": "1",
                    "secret_key": "sk",
                    "recurrent_enabled": recurrent_enabled,
                },
            )
        )
        await uow.commit()
        return sub.id


def _charge_response(status: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "yk-auto-1",
            "status": status,
            "amount": {"value": "199.00", "currency": "RUB"},
        },
    )


@respx.mock
async def test_card_charge_renews_subscription(session_factory: async_sessionmaker) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow)
    route = respx.post(API_PAYMENTS).mock(return_value=_charge_response("succeeded"))

    assert await _autopay_one(container, sub_id) is True

    sent = json.loads(route.calls.last.request.content)
    assert sent["payment_method_id"] == METHOD_ID  # decrypted before the API call
    assert "confirmation" not in sent

    async with uow:
        sub = await uow.subscriptions.get(sub_id)
        assert sub is not None and sub.expire_at is not None
        assert sub.expire_at > dt.datetime.now(dt.UTC) + dt.timedelta(days=29)
        assert sub.autopay_card_attempted_at is not None
        txns = list(await uow.transactions.list(user_id=sub.user_id))
    assert len(txns) == 1
    txn = txns[0]
    assert txn.type is TransactionType.SUBSCRIPTION_PAYMENT
    assert txn.status is TransactionStatus.COMPLETED
    assert txn.external_id == "yk-auto-1"
    assert txn.gateway_type is PaymentGatewayType.YOOKASSA
    assert any("списано" in text for _, text in container.notifier.sent)


@respx.mock
async def test_card_charge_declined_notifies_and_keeps_sub(
    session_factory: async_sessionmaker,
) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow)
    respx.post(API_PAYMENTS).mock(return_value=_charge_response("canceled"))

    assert await _autopay_one(container, sub_id) is False

    async with uow:
        sub = await uow.subscriptions.get(sub_id)
        assert sub is not None and sub.expire_at is not None
        assert sub.expire_at < dt.datetime.now(dt.UTC) + dt.timedelta(days=1)  # not renewed
        txns = list(await uow.transactions.list(user_id=sub.user_id))
    assert len(txns) == 1
    assert txns[0].status is TransactionStatus.CANCELED
    assert any("не удалось списать" in text for _, text in container.notifier.sent)


@respx.mock
async def test_card_charge_http_error_notifies_leaves_pending(
    session_factory: async_sessionmaker,
) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow)
    respx.post(API_PAYMENTS).mock(return_value=httpx.Response(500))

    assert await _autopay_one(container, sub_id) is False

    async with uow:
        sub = await uow.subscriptions.get(sub_id)
        assert sub is not None
        txns = list(await uow.transactions.list(user_id=sub.user_id))
    assert len(txns) == 1
    assert txns[0].status is TransactionStatus.PENDING  # same contract as interactive flow
    assert any("не удалось списать" in text for _, text in container.notifier.sent)


@respx.mock
async def test_no_saved_card_skips_charge(session_factory: async_sessionmaker) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow, card_saved=False)
    route = respx.post(API_PAYMENTS).mock(return_value=_charge_response("succeeded"))

    assert await _autopay_one(container, sub_id) is False
    assert not route.called
    assert container.notifier.sent == []


@respx.mock
async def test_card_toggle_off_skips_charge(session_factory: async_sessionmaker) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow, card_enabled=False)
    route = respx.post(API_PAYMENTS).mock(return_value=_charge_response("succeeded"))

    assert await _autopay_one(container, sub_id) is False
    assert not route.called


@respx.mock
async def test_recent_attempt_is_not_retried(session_factory: async_sessionmaker) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(
        container, uow, attempted_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
    )
    route = respx.post(API_PAYMENTS).mock(return_value=_charge_response("succeeded"))

    assert await _autopay_one(container, sub_id) is False
    assert not route.called


@respx.mock
async def test_recurrent_disabled_gateway_skips_charge(
    session_factory: async_sessionmaker,
) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow, recurrent_enabled=False)
    route = respx.post(API_PAYMENTS).mock(return_value=_charge_response("succeeded"))

    assert await _autopay_one(container, sub_id) is False
    assert not route.called


@respx.mock
async def test_balance_still_first_no_card_charge(session_factory: async_sessionmaker) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow, balance_minor=50000)
    route = respx.post(API_PAYMENTS).mock(return_value=_charge_response("succeeded"))

    assert await _autopay_one(container, sub_id) is True
    assert not route.called  # paid from the wallet, the card was never touched

    async with uow:
        sub = await uow.subscriptions.get(sub_id)
        assert sub is not None
        user = await uow.users.get(sub.user_id)
        assert user is not None and user.balance_minor == 50000 - 19900


async def test_store_saved_method_persists_on_user(session_factory: async_sessionmaker) -> None:
    container = make_container(session_factory)
    uow = UnitOfWork(session_factory)
    sub_id = await _seed(container, uow, card_saved=False)
    async with uow:
        sub = await uow.subscriptions.get(sub_id)
        assert sub is not None
        user_id = sub.user_id
        from src.application.dto.pricing import PurchaseRequest
        from src.core.enums import PurchaseType

        txn, _ = await container.purchase.start(
            uow,
            PurchaseRequest(
                user_id=user_id,
                plan_id=sub.plan_id,
                duration_days=30,
                currency=Currency.RUB,
                purchase_type=PurchaseType.RENEW,
                subscription_id=sub.id,
            ),
        )
        await uow.commit()
        payment_id = txn.payment_id

    await _store_saved_method(container, payment_id, method_enc="enc-token", title="MIR *1234")

    async with uow:
        user = await uow.users.get(user_id)
        assert user is not None
        assert user.saved_payment_method_id == "enc-token"
        assert user.saved_payment_method_title == "MIR *1234"
