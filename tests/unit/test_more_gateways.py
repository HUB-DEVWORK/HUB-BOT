"""Platega / Robokassa / Cryptomus / Heleket / YooMoney / Wata + underpayment gate."""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
import uuid

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.application.common.payments import PaymentContext, PaymentResultKind, WebhookRequest
from src.core.enums import Currency, TransactionStatus
from src.core.exceptions import WebhookVerificationError
from src.core.money import Money
from src.infrastructure.payments.gateways.cryptomus import CryptomusGateway
from src.infrastructure.payments.gateways.heleket import HeleketGateway
from src.infrastructure.payments.gateways.platega import PlategaGateway
from src.infrastructure.payments.gateways.robokassa import RobokassaGateway
from src.infrastructure.payments.gateways.wata import WataGateway
from src.infrastructure.payments.gateways.yoomoney import YoomoneyGateway


def _ctx(amount_minor: int = 19900) -> PaymentContext:
    return PaymentContext(
        payment_id=uuid.uuid4(),
        amount=Money(amount_minor, Currency.RUB),
        description="Тариф Про · 30 дн.",
        user_id=1,
        telegram_id=42,
    )


# --- Platega -----------------------------------------------------------------


@respx.mock
async def test_platega_create_and_webhook() -> None:
    gw = PlategaGateway({"merchant_id": "m-1", "secret": "s-1"})
    respx.post("https://app.platega.io/transaction/process").mock(
        return_value=httpx.Response(
            200, json={"transactionId": "tx-1", "redirect": "https://pay.platega.io/tx-1"}
        )
    )
    result = await gw.create_payment(_ctx())
    assert result.kind is PaymentResultKind.REDIRECT and result.external_id == "tx-1"

    body = json.dumps({"id": "tx-1", "status": "CONFIRMED", "amount": 199}).encode()
    ok = await gw.handle_webhook(
        WebhookRequest(body=body, headers={"X-MerchantId": "m-1", "X-Secret": "s-1"})
    )
    assert ok.status is TransactionStatus.COMPLETED and ok.external_id == "tx-1"

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(
            WebhookRequest(body=body, headers={"X-MerchantId": "m-1", "X-Secret": "wrong"})
        )


# --- Robokassa ----------------------------------------------------------------


async def test_robokassa_link_and_result_ack() -> None:
    gw = RobokassaGateway({"merchant_login": "shop", "password1": "p1", "password2": "p2"})
    ctx = _ctx()
    result = await gw.create_payment(ctx)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result.redirect_url).query))
    assert q["MerchantLogin"] == "shop" and q["OutSum"] == "199.00"
    expected_sig = hashlib.md5(f"shop:199.00:0:p1:Shp_pid={ctx.payment_id}".encode()).hexdigest()
    assert q["SignatureValue"] == expected_sig

    sig = hashlib.md5(f"199.00:7:p2:Shp_pid={ctx.payment_id}".encode()).hexdigest()
    body = urllib.parse.urlencode(
        {"OutSum": "199.00", "InvId": "7", "SignatureValue": sig, "Shp_pid": str(ctx.payment_id)}
    ).encode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={}))
    assert ok.status is TransactionStatus.COMPLETED
    assert ok.payment_id == ctx.payment_id
    assert ok.http_body == "OK7"  # Robokassa's mandatory plain-text ACK
    assert ok.amount == Money(19900, Currency.RUB)

    bad = urllib.parse.urlencode(
        {"OutSum": "199.00", "InvId": "7", "SignatureValue": "0" * 32, "Shp_pid": "x"}
    ).encode()
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=bad, headers={}))


# --- Cryptomus / Heleket --------------------------------------------------------


def _cryptomus_sign(body: dict, key: str) -> str:
    compact = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.md5(base64.b64encode(compact) + key.encode()).hexdigest()


@respx.mock
async def test_cryptomus_create_and_signed_webhook() -> None:
    gw = CryptomusGateway({"merchant_uuid": "mu-1", "api_key": "key-1"})
    ctx = _ctx()
    respx.post("https://api.cryptomus.com/v1/payment").mock(
        return_value=httpx.Response(
            200, json={"result": {"uuid": "cm-1", "url": "https://pay.cryptomus.com/cm-1"}}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "cm-1"

    payload = {"uuid": "cm-1", "order_id": str(ctx.payment_id), "status": "paid"}
    payload["sign"] = _cryptomus_sign({k: v for k, v in payload.items() if k != "sign"}, "key-1")
    ok = await gw.handle_webhook(WebhookRequest(body=json.dumps(payload).encode(), headers={}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    payload["sign"] = "bad"
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=json.dumps(payload).encode(), headers={}))


async def test_heleket_is_cryptomus_scheme_on_own_host() -> None:
    gw = HeleketGateway({"merchant_uuid": "mu", "api_key": "k"})
    assert gw.api_base.startswith("https://api.heleket.com")
    payload = {"order_id": str(uuid.uuid4()), "status": "cancel"}
    payload["sign"] = _cryptomus_sign(payload.copy(), "k")
    ok = await gw.handle_webhook(WebhookRequest(body=json.dumps(payload).encode(), headers={}))
    assert ok.status is TransactionStatus.CANCELED


# --- YooMoney -------------------------------------------------------------------


async def test_yoomoney_link_and_notification() -> None:
    gw = YoomoneyGateway({"wallet": "4100111", "notification_secret": "ns"})
    ctx = _ctx()
    result = await gw.create_payment(ctx)
    assert "yoomoney.ru/quickpay/confirm" in result.redirect_url
    assert str(ctx.payment_id) in result.redirect_url

    f = {
        "notification_type": "p2p-incoming",
        "operation_id": "op-1",
        "amount": "193.03",  # после комиссии
        "currency": "643",
        "datetime": "2026-07-07T10:00:00Z",
        "sender": "410012345",
        "codepro": "false",
        "label": str(ctx.payment_id),
    }
    joined = "&".join(
        [
            f["notification_type"],
            f["operation_id"],
            f["amount"],
            f["currency"],
            f["datetime"],
            f["sender"],
            f["codepro"],
            "ns",
            f["label"],
        ]
    )
    f["sha1_hash"] = hashlib.sha1(joined.encode()).hexdigest()
    ok = await gw.handle_webhook(
        WebhookRequest(body=urllib.parse.urlencode(f).encode(), headers={})
    )
    assert ok.status is TransactionStatus.COMPLETED
    assert ok.payment_id == ctx.payment_id
    assert ok.amount == Money(19303, Currency.RUB)

    f["sha1_hash"] = "0" * 40
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=urllib.parse.urlencode(f).encode(), headers={}))


# --- Wata -----------------------------------------------------------------------


@respx.mock
async def test_wata_create_and_rsa_webhook() -> None:
    gw = WataGateway({"api_token": "tok"})
    ctx = _ctx()
    respx.post("https://api.wata.pro/api/h2h/links").mock(
        return_value=httpx.Response(
            200, json={"id": "w-1", "url": "https://link.wata.pro/w-1", "status": "Opened"}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "w-1"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    respx.get("https://api.wata.pro/api/h2h/public-key").mock(
        return_value=httpx.Response(200, json={"value": pem.decode()})
    )
    body = json.dumps(
        {"transactionId": "tr-1", "transactionStatus": "Paid", "orderId": str(ctx.payment_id)}
    ).encode()
    signature = base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA512())).decode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={"X-Signature": signature}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(
            WebhookRequest(body=body + b" ", headers={"X-Signature": signature})
        )
    WataGateway._public_key_pem = None  # do not leak the test key into other tests


# --- underpayment gate ------------------------------------------------------------


async def test_underpaid_webhook_fails_instead_of_fulfilling(uow) -> None:  # type: ignore[no-untyped-def]
    from src.application.dto.pricing import PurchaseRequest
    from src.application.services.payment import PaymentService
    from src.application.services.pricing import PricingService
    from src.application.services.purchase import PurchaseService
    from src.application.services.referral import ReferralService
    from src.application.services.remnawave import RemnawaveService
    from src.application.services.subscription import SubscriptionService
    from tests.factories import make_plan, make_user
    from tests.fakes import FakeRemnawaveClient, RecordingEventBus

    bus = RecordingEventBus()
    purchase = PurchaseService(
        PricingService(), SubscriptionService(RemnawaveService(FakeRemnawaveClient())), bus
    )
    payments = PaymentService(purchase, bus, ReferralService(bus))
    async with uow:
        user = await make_user(uow)
        plan, _ = await make_plan(uow, price_minor=30000)
        await uow.commit()
        txn, _ = await purchase.start(
            uow,
            PurchaseRequest(
                user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB
            ),
        )
        await uow.commit()
        # user edited the quickpay form down to 50 ₽ — must NOT fulfil
        moved = await payments.process(
            uow, payment_id=txn.payment_id, status=TransactionStatus.COMPLETED, amount_minor=5000
        )
        await uow.commit()
        assert moved is True  # advanced: to FAILED, not COMPLETED
        assert txn.status is TransactionStatus.FAILED
        assert (await uow.subscriptions.active_for_user(user.id)) == []


async def _run_gate(uow, price_minor: int, paid_minor: int):  # type: ignore[no-untyped-def]
    """Drive a single payment through the underpayment gate; return the resulting txn."""
    from src.application.dto.pricing import PurchaseRequest
    from src.application.services.payment import PaymentService
    from src.application.services.pricing import PricingService
    from src.application.services.purchase import PurchaseService
    from src.application.services.referral import ReferralService
    from src.application.services.remnawave import RemnawaveService
    from src.application.services.subscription import SubscriptionService
    from tests.factories import make_plan, make_user
    from tests.fakes import FakeRemnawaveClient, RecordingEventBus

    bus = RecordingEventBus()
    purchase = PurchaseService(
        PricingService(), SubscriptionService(RemnawaveService(FakeRemnawaveClient())), bus
    )
    payments = PaymentService(purchase, bus, ReferralService(bus))
    user = await make_user(uow)
    plan, _ = await make_plan(uow, price_minor=price_minor)
    await uow.commit()
    txn, _ = await purchase.start(
        uow,
        PurchaseRequest(user_id=user.id, plan_id=plan.id, duration_days=30, currency=Currency.RUB),
    )
    await uow.commit()
    await payments.process(
        uow, payment_id=txn.payment_id, status=TransactionStatus.COMPLETED, amount_minor=paid_minor
    )
    await uow.commit()
    return txn


async def test_92pct_payment_is_rejected_by_tightened_gate(uow) -> None:  # type: ignore[no-untyped-def]
    # 92% of the invoice used to slip through the old 0.90 gate; the 0.95 gate now fails it —
    # only YooMoney reports net (fee ≤3% → ≥97%), so 92% is an edited-down transfer, not a fee.
    async with uow:
        txn = await _run_gate(uow, price_minor=100_00, paid_minor=92_00)
        assert txn.status is TransactionStatus.FAILED


async def test_97pct_payment_still_fulfils(uow) -> None:  # type: ignore[no-untyped-def]
    # A legitimate full YooMoney payment nets ~97% after its ≤3% fee — must still fulfil.
    async with uow:
        txn = await _run_gate(uow, price_minor=100_00, paid_minor=97_00)
        assert txn.status is TransactionStatus.COMPLETED
