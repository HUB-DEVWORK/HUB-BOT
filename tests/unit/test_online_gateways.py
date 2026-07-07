"""YooKassa + CryptoBot gateways: create/webhook against respx-mocked HTTP."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import httpx
import pytest
import respx

from src.application.common.payments import PaymentContext, PaymentResultKind, WebhookRequest
from src.core.enums import Currency, TransactionStatus
from src.core.exceptions import PaymentError, WebhookVerificationError
from src.core.money import Money
from src.infrastructure.payments.gateways.cryptobot import CryptobotGateway
from src.infrastructure.payments.gateways.yookassa import YookassaGateway


def _ctx(amount_minor: int = 19900) -> PaymentContext:
    return PaymentContext(
        payment_id=uuid.uuid4(),
        amount=Money(amount_minor, Currency.RUB),
        description="Тариф Про · 30 дн.",
        user_id=1,
        telegram_id=42,
    )


# --- YooKassa ---------------------------------------------------------------


@respx.mock
async def test_yookassa_create_redirect() -> None:
    gateway = YookassaGateway({"shop_id": "123", "secret_key": "sk"})
    ctx = _ctx()
    route = respx.post("https://api.yookassa.ru/v3/payments").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "yk-1",
                "status": "pending",
                "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/yk-1"},
            },
        )
    )
    result = await gateway.create_payment(ctx)
    assert result.kind is PaymentResultKind.REDIRECT
    assert result.external_id == "yk-1"
    assert result.redirect_url == "https://yoomoney.ru/pay/yk-1"
    req = route.calls.last.request
    assert req.headers["Idempotence-Key"] == str(ctx.payment_id)
    sent = json.loads(req.content)
    assert sent["amount"] == {"value": "199.00", "currency": "RUB"}
    assert sent["metadata"]["payment_id"] == str(ctx.payment_id)


@respx.mock
async def test_yookassa_create_error_raises() -> None:
    gateway = YookassaGateway({"shop_id": "123", "secret_key": "sk"})
    respx.post("https://api.yookassa.ru/v3/payments").mock(
        return_value=httpx.Response(401, json={"description": "no"})
    )
    with pytest.raises(PaymentError):
        await gateway.create_payment(_ctx())


@respx.mock
async def test_yookassa_webhook_refetches_and_trusts_api_only() -> None:
    gateway = YookassaGateway({"shop_id": "123", "secret_key": "sk"})
    pid = uuid.uuid4()
    # Attacker claims "succeeded"; the API says canceled -> we follow the API.
    respx.get("https://api.yookassa.ru/v3/payments/yk-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "yk-1",
                "status": "canceled",
                "amount": {"value": "199.00", "currency": "RUB"},
                "metadata": {"payment_id": str(pid)},
            },
        )
    )
    body = json.dumps({"object": {"id": "yk-1", "status": "succeeded"}}).encode()
    result = await gateway.handle_webhook(WebhookRequest(body=body, headers={}))
    assert result.status is TransactionStatus.CANCELED
    assert result.payment_id == pid
    assert result.amount == Money(19900, Currency.RUB)


@respx.mock
async def test_yookassa_webhook_refetch_failure_rejects() -> None:
    gateway = YookassaGateway({"shop_id": "123", "secret_key": "sk"})
    respx.get("https://api.yookassa.ru/v3/payments/yk-1").mock(return_value=httpx.Response(404))
    body = json.dumps({"object": {"id": "yk-1", "status": "succeeded"}}).encode()
    with pytest.raises(WebhookVerificationError):
        await gateway.handle_webhook(WebhookRequest(body=body, headers={}))


@respx.mock
async def test_yookassa_create_saves_method_only_when_recurrent_enabled() -> None:
    route = respx.post("https://api.yookassa.ru/v3/payments").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "yk-1",
                "status": "pending",
                "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/yk-1"},
            },
        )
    )
    await YookassaGateway({"shop_id": "1", "secret_key": "sk"}).create_payment(_ctx())
    assert "save_payment_method" not in json.loads(route.calls.last.request.content)

    await YookassaGateway(
        {"shop_id": "1", "secret_key": "sk", "recurrent_enabled": True}
    ).create_payment(_ctx())
    assert json.loads(route.calls.last.request.content)["save_payment_method"] is True


@respx.mock
async def test_yookassa_webhook_extracts_saved_method() -> None:
    gateway = YookassaGateway({"shop_id": "1", "secret_key": "sk"})
    pid = uuid.uuid4()
    respx.get("https://api.yookassa.ru/v3/payments/yk-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "yk-1",
                "status": "succeeded",
                "amount": {"value": "199.00", "currency": "RUB"},
                "metadata": {"payment_id": str(pid)},
                "payment_method": {
                    "type": "bank_card",
                    "id": "pm-22d6d597",
                    "saved": True,
                    "title": "Bank card *4444",
                    "card": {"card_type": "MIR", "last4": "4444"},
                },
            },
        )
    )
    body = json.dumps({"object": {"id": "yk-1", "status": "succeeded"}}).encode()
    result = await gateway.handle_webhook(WebhookRequest(body=body, headers={}))
    assert result.saved_method is not None
    assert result.saved_method.method_id == "pm-22d6d597"
    assert result.saved_method.title == "Bank card *4444"


@respx.mock
async def test_yookassa_webhook_unsaved_method_ignored() -> None:
    gateway = YookassaGateway({"shop_id": "1", "secret_key": "sk"})
    respx.get("https://api.yookassa.ru/v3/payments/yk-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "yk-1",
                "status": "succeeded",
                "payment_method": {"type": "bank_card", "id": "pm-1", "saved": False},
            },
        )
    )
    body = json.dumps({"object": {"id": "yk-1", "status": "succeeded"}}).encode()
    result = await gateway.handle_webhook(WebhookRequest(body=body, headers={}))
    assert result.saved_method is None


@respx.mock
async def test_yookassa_charge_saved_no_confirmation_and_terminal_status() -> None:
    gateway = YookassaGateway({"shop_id": "1", "secret_key": "sk", "recurrent_enabled": "1"})
    ctx = _ctx()
    route = respx.post("https://api.yookassa.ru/v3/payments").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "yk-auto-1",
                "status": "succeeded",
                "amount": {"value": "199.00", "currency": "RUB"},
                "metadata": {"payment_id": str(ctx.payment_id)},
            },
        )
    )
    result = await gateway.charge_saved(ctx, "pm-22d6d597")
    assert result.status is TransactionStatus.COMPLETED
    assert result.external_id == "yk-auto-1"
    assert result.payment_id == ctx.payment_id
    req = route.calls.last.request
    assert req.headers["Idempotence-Key"] == str(ctx.payment_id)
    sent = json.loads(req.content)
    assert sent["payment_method_id"] == "pm-22d6d597"
    assert "confirmation" not in sent
    assert "save_payment_method" not in sent


@respx.mock
async def test_yookassa_charge_saved_error_raises() -> None:
    gateway = YookassaGateway({"shop_id": "1", "secret_key": "sk"})
    respx.post("https://api.yookassa.ru/v3/payments").mock(
        return_value=httpx.Response(400, json={"description": "payment_method not saved"})
    )
    with pytest.raises(PaymentError):
        await gateway.charge_saved(_ctx(), "pm-dead")


# --- CryptoBot ---------------------------------------------------------------

TOKEN = "12345:AAtesttoken"


def _sign(body: bytes) -> str:
    secret = hashlib.sha256(TOKEN.encode()).digest()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


@respx.mock
async def test_cryptobot_create_fiat_invoice() -> None:
    gateway = CryptobotGateway({"api_token": TOKEN})
    ctx = _ctx()
    route = respx.post("https://pay.crypt.bot/api/createInvoice").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "result": {"invoice_id": 777, "bot_invoice_url": "https://t.me/CryptoBot?start=x"},
            },
        )
    )
    result = await gateway.create_payment(ctx)
    assert result.kind is PaymentResultKind.REDIRECT
    assert result.external_id == "777"
    sent = json.loads(route.calls.last.request.content)
    assert sent["currency_type"] == "fiat"
    assert sent["fiat"] == "RUB"
    assert sent["amount"] == "199.00"
    assert sent["payload"] == str(ctx.payment_id)


async def test_cryptobot_webhook_valid_signature_completes() -> None:
    gateway = CryptobotGateway({"api_token": TOKEN})
    pid = uuid.uuid4()
    body = json.dumps(
        {
            "update_type": "invoice_paid",
            "payload": {"invoice_id": 777, "payload": str(pid)},
        }
    ).encode()
    result = await gateway.handle_webhook(
        WebhookRequest(body=body, headers={"Crypto-Pay-Api-Signature": _sign(body)})
    )
    assert result.status is TransactionStatus.COMPLETED
    assert result.payment_id == pid
    assert result.external_id == "777"


async def test_cryptobot_webhook_reserialized_body_still_verifies() -> None:
    """Proxy rewrote whitespace/unicode: HMAC of the compact form must match."""
    gateway = CryptobotGateway({"api_token": TOKEN})
    pid = uuid.uuid4()
    original = json.dumps(
        {"update_type": "invoice_paid", "payload": {"invoice_id": 1, "payload": str(pid)}},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    rewritten = json.dumps(json.loads(original), indent=2).encode()  # pretty-printed by proxy
    result = await gateway.handle_webhook(
        WebhookRequest(body=rewritten, headers={"crypto-pay-api-signature": _sign(original)})
    )
    assert result.status is TransactionStatus.COMPLETED


async def test_cryptobot_webhook_bad_signature_rejected() -> None:
    gateway = CryptobotGateway({"api_token": TOKEN})
    body = json.dumps({"update_type": "invoice_paid", "payload": {}}).encode()
    with pytest.raises(WebhookVerificationError):
        await gateway.handle_webhook(
            WebhookRequest(body=body, headers={"crypto-pay-api-signature": "deadbeef"})
        )


async def test_cryptobot_webhook_other_update_stays_pending() -> None:
    gateway = CryptobotGateway({"api_token": TOKEN})
    body = json.dumps({"update_type": "invoice_expired", "payload": {}}).encode()
    result = await gateway.handle_webhook(
        WebhookRequest(body=body, headers={"crypto-pay-api-signature": _sign(body)})
    )
    assert result.status is TransactionStatus.PENDING
