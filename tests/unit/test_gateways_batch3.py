"""FreeKassa / PayPalych / CloudPayments + provider-API refunds."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
import uuid

import httpx
import pytest
import respx

from src.application.common.payments import PaymentContext, WebhookRequest
from src.core.enums import Currency, TransactionStatus
from src.core.exceptions import PaymentError, WebhookVerificationError
from src.core.money import Money
from src.infrastructure.payments.gateways.cloudpayments import CloudpaymentsGateway
from src.infrastructure.payments.gateways.cryptobot import CryptobotGateway
from src.infrastructure.payments.gateways.cryptomus import CryptomusGateway
from src.infrastructure.payments.gateways.freekassa import FreekassaGateway
from src.infrastructure.payments.gateways.paypalych import PaypalychGateway
from src.infrastructure.payments.gateways.yookassa import YookassaGateway


def _ctx(amount_minor: int = 19900) -> PaymentContext:
    return PaymentContext(
        payment_id=uuid.uuid4(),
        amount=Money(amount_minor, Currency.RUB),
        description="VPN",
        user_id=1,
        telegram_id=42,
    )


# --- FreeKassa -----------------------------------------------------------------


async def test_freekassa_signed_link_and_yes_ack() -> None:
    gw = FreekassaGateway({"shop_id": "777", "secret1": "s1", "secret2": "s2"})
    ctx = _ctx(20000)  # 200 ₽ — целое: FreeKassa подписывает "200", не "200.00"
    result = await gw.create_payment(ctx)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(result.redirect_url).query))
    assert q["oa"] == "200"
    assert q["s"] == hashlib.md5(f"777:200:s1:RUB:{ctx.payment_id}".encode()).hexdigest()

    sign = hashlib.md5(f"777:200:s2:{ctx.payment_id}".encode()).hexdigest()
    body = urllib.parse.urlencode(
        {
            "MERCHANT_ID": "777",
            "AMOUNT": "200",
            "intid": "123456",
            "MERCHANT_ORDER_ID": str(ctx.payment_id),
            "SIGN": sign,
        }
    ).encode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={}))
    assert ok.status is TransactionStatus.COMPLETED
    assert ok.payment_id == ctx.payment_id
    assert ok.http_body == "YES"

    bad = body.replace(sign.encode(), b"0" * 32)
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=bad, headers={}))


# --- PayPalych -------------------------------------------------------------------


@respx.mock
async def test_paypalych_bill_and_callback() -> None:
    gw = PaypalychGateway({"api_token": "tok", "shop_id": "shop"})
    respx.post("https://pal24.pro/api/v1/bill/create").mock(
        return_value=httpx.Response(
            200,
            json={"bill_id": "B-1", "link_page_url": "https://pal24.pro/b/B-1"},
        )
    )
    result = await gw.create_payment(_ctx())
    assert result.external_id == "B-1"

    sig = hashlib.md5(b"199.00:B-1:tok").hexdigest().upper()
    body = urllib.parse.urlencode(
        {"OutSum": "199.00", "InvId": "B-1", "Status": "SUCCESS", "SignatureValue": sig}
    ).encode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={}))
    assert ok.status is TransactionStatus.COMPLETED
    assert ok.external_id == "B-1"
    assert ok.amount == Money(19900, Currency.RUB)

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(
            WebhookRequest(body=body.replace(sig.encode(), b"F" * 32), headers={})
        )


# --- CloudPayments ----------------------------------------------------------------


@respx.mock
async def test_cloudpayments_order_hmac_webhook_and_refund() -> None:
    gw = CloudpaymentsGateway({"public_id": "pk", "api_secret": "sec"})
    ctx = _ctx()
    respx.post("https://api.cloudpayments.ru/orders/create").mock(
        return_value=httpx.Response(
            200,
            json={
                "Success": True,
                "Model": {"Id": "ord-1", "Url": "https://c.cloudpayments.ru/ord-1"},
            },
        )
    )
    result = await gw.create_payment(ctx)
    assert result.redirect_url.endswith("ord-1")

    body = urllib.parse.urlencode(
        {
            "TransactionId": "555001",
            "InvoiceId": str(ctx.payment_id),
            "Amount": "199.00",
            "Status": "Completed",
        }
    ).encode()
    sig = base64.b64encode(hmac.new(b"sec", body, hashlib.sha256).digest()).decode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={"Content-HMAC": sig}))
    assert ok.status is TransactionStatus.COMPLETED
    assert ok.payment_id == ctx.payment_id
    assert ok.http_body == '{"code":0}'

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=body, headers={"Content-HMAC": "AAAA"}))

    respx.post("https://api.cloudpayments.ru/payments/refund").mock(
        return_value=httpx.Response(200, json={"Success": True})
    )
    assert await gw.refund("555001", Money(19900, Currency.RUB)) is True


# --- refunds ------------------------------------------------------------------------


@respx.mock
async def test_yookassa_refund_payload() -> None:
    gw = YookassaGateway({"shop_id": "1", "secret_key": "k"})
    route = respx.post("https://api.yookassa.ru/v3/refunds").mock(
        return_value=httpx.Response(200, json={"id": "rf-1", "status": "succeeded"})
    )
    assert await gw.refund("yk-9", Money(19900, Currency.RUB)) is True
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"payment_id": "yk-9", "amount": {"value": "199.00", "currency": "RUB"}}
    assert route.calls.last.request.headers["Idempotence-Key"] == "refund-yk-9"


@respx.mock
async def test_cryptomus_refund_signed() -> None:
    gw = CryptomusGateway({"merchant_uuid": "m", "api_key": "k"})
    route = respx.post("https://api.cryptomus.com/v1/payment/refund").mock(
        return_value=httpx.Response(200, json={"result": []})
    )
    assert await gw.refund("cm-1", Money(1000, Currency.RUB)) is True
    body = route.calls.last.request.content
    expected_sign = hashlib.md5(base64.b64encode(body) + b"k").hexdigest()
    assert route.calls.last.request.headers["sign"] == expected_sign


async def test_refund_not_supported_raises() -> None:
    gw = CryptobotGateway({"api_token": "t"})
    with pytest.raises(PaymentError):
        await gw.refund("x", Money(100, Currency.RUB))
