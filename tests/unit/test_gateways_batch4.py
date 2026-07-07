"""Lava / MulenPay / KassaAI / RollyPay (contracts ported from live integrations)."""

from __future__ import annotations

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
from src.core.exceptions import WebhookVerificationError
from src.core.money import Money
from src.infrastructure.payments.gateways.kassa_ai import KassaAiGateway
from src.infrastructure.payments.gateways.lava import LavaGateway
from src.infrastructure.payments.gateways.mulenpay import MulenpayGateway
from src.infrastructure.payments.gateways.rollypay import RollypayGateway


def _ctx(amount_minor: int = 19900) -> PaymentContext:
    return PaymentContext(
        payment_id=uuid.uuid4(),
        amount=Money(amount_minor, Currency.RUB),
        description="VPN",
        user_id=1,
        telegram_id=42,
    )


@respx.mock
async def test_lava_signed_create_and_webhook_both_variants() -> None:
    gw = LavaGateway({"shop_id": "shop-1", "secret_key": "sk", "webhook_secret": "wk"})
    ctx = _ctx()
    route = respx.post("https://api.lava.ru/business/invoice/create").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "data": {"id": "lv-1", "url": "https://pay.lava.ru/lv-1"}},
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "lv-1"
    req = route.calls.last.request
    expected_sig = hmac.new(b"sk", req.content, hashlib.sha256).hexdigest()
    assert req.headers["Signature"] == expected_sig  # raw-body signing, header not body

    body = json.dumps(
        {"order_id": str(ctx.payment_id), "status": "success", "amount": 199}
    ).encode()
    sig = hmac.new(b"wk", body, hashlib.sha256).hexdigest()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={"Authorization": sig}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    # legacy shops sign a sorted-keys re-serialization — must also be accepted
    parsed = json.loads(body)
    canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()
    legacy_sig = hmac.new(b"wk", canonical, hashlib.sha256).hexdigest()
    pretty = json.dumps(parsed, indent=2).encode()
    ok2 = await gw.handle_webhook(
        WebhookRequest(body=pretty, headers={"Authorization": legacy_sig})
    )
    assert ok2.status is TransactionStatus.COMPLETED

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=body, headers={"Authorization": "0" * 64}))


@respx.mock
async def test_mulenpay_create_sign_and_webhook() -> None:
    gw = MulenpayGateway({"api_key": "ak", "shop_id": "77", "secret_key": "sec"})
    ctx = _ctx()
    route = respx.post("https://mulenpay.ru/api/v2/payments").mock(
        return_value=httpx.Response(
            200, json={"success": True, "id": 555, "paymentUrl": "https://mulenpay.ru/p/555"}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "555"
    sent = json.loads(route.calls.last.request.content)
    assert sent["sign"] == hashlib.sha1(b"rub199.0077sec").hexdigest()

    hook = {"id": 555, "uuid": str(ctx.payment_id), "amount": "199.00", "status": "paid"}
    joined = "".join(str(v) for v in hook.values())
    hook["sign"] = hashlib.sha1((joined + "sec").encode()).hexdigest()
    ok = await gw.handle_webhook(WebhookRequest(body=json.dumps(hook).encode(), headers={}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    hook["sign"] = "bad"
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=json.dumps(hook).encode(), headers={}))


@respx.mock
async def test_kassa_ai_sorted_pipe_signature_and_yes_ack() -> None:
    gw = KassaAiGateway({"shop_id": "42", "api_key": "ak", "secret2": "s2"})
    ctx = _ctx(20000)
    route = respx.post("https://api.fk.life/v1/orders/create").mock(
        return_value=httpx.Response(
            200, json={"type": "success", "orderId": 900, "location": "https://pay.fk.life/x"}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "900"
    sent = json.loads(route.calls.last.request.content)
    data = {k: v for k, v in sent.items() if k != "signature"}
    msg = "|".join(str(data[k]) for k in sorted(data))
    assert sent["signature"] == hmac.new(b"ak", msg.encode(), hashlib.sha256).hexdigest()

    sign = hashlib.md5(f"42:200:s2:{ctx.payment_id}".encode()).hexdigest()
    body = urllib.parse.urlencode(
        {
            "MERCHANT_ID": "42",
            "AMOUNT": "200",
            "MERCHANT_ORDER_ID": str(ctx.payment_id),
            "SIGN": sign,
        }
    ).encode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={}))
    assert ok.status is TransactionStatus.COMPLETED and ok.http_body == "YES"


@respx.mock
async def test_rollypay_create_and_timestamped_hmac() -> None:
    gw = RollypayGateway({"api_key": "key", "signing_secret": "sig"})
    ctx = _ctx()
    route = respx.post("https://rollypay.io/api/v1/payments").mock(
        return_value=httpx.Response(
            200, json={"payment_id": "rp-1", "pay_url": "https://rollypay.io/p/rp-1"}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "rp-1"
    assert route.calls.last.request.headers["X-API-Key"] == "key"

    body = json.dumps(
        {"payment_id": "rp-1", "order_id": str(ctx.payment_id), "status": "paid"}
    ).encode()
    ts = "1751900000"
    sig = hmac.new(b"sig", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    ok = await gw.handle_webhook(
        WebhookRequest(body=body, headers={"X-Signature": sig, "X-Timestamp": ts})
    )
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    # a different timestamp invalidates the signature (replay protection)
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(
            WebhookRequest(body=body, headers={"X-Signature": sig, "X-Timestamp": "999"})
        )
