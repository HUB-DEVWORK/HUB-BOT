"""RioPay / SeverPay / AuraPay / Antilopay (contracts from live integrations)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.application.common.payments import PaymentContext, WebhookRequest
from src.core.enums import Currency, TransactionStatus
from src.core.exceptions import WebhookVerificationError
from src.core.money import Money
from src.infrastructure.payments.gateways.antilopay import AntilopayGateway
from src.infrastructure.payments.gateways.aurapay import AurapayGateway
from src.infrastructure.payments.gateways.riopay import RiopayGateway
from src.infrastructure.payments.gateways.severpay import SeverpayGateway


def _ctx(amount_minor: int = 19900) -> PaymentContext:
    return PaymentContext(
        payment_id=uuid.uuid4(),
        amount=Money(amount_minor, Currency.RUB),
        description="VPN",
        user_id=7,
        telegram_id=42,
    )


@respx.mock
async def test_riopay_create_201_and_sha512_webhook() -> None:
    gw = RiopayGateway({"api_token": "tok"})
    ctx = _ctx()
    respx.post("https://api.riopay.online/v1/orders").mock(
        return_value=httpx.Response(
            201, json={"id": "rp-1", "paymentLink": "https://riopay.online/rp-1"}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "rp-1"

    body = json.dumps(
        {"id": "rp-1", "externalId": str(ctx.payment_id), "status": "COMPLETED", "amount": "199.00"}
    ).encode()
    sig = hmac.new(b"tok", body, hashlib.sha512).hexdigest()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={"X-Signature": sig}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id
    assert ok.amount == Money(19900, Currency.RUB)

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=body, headers={"X-Signature": "0" * 128}))


@respx.mock
async def test_severpay_signed_create_and_webhook() -> None:
    gw = SeverpayGateway({"token": "tk", "mid": "500"})
    ctx = _ctx()
    route = respx.post("https://severpay.io/api/merchant/payin/create").mock(
        return_value=httpx.Response(
            200, json={"status": True, "data": {"id": "sp-1", "url": "https://severpay.io/sp-1"}}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "sp-1"
    sent = json.loads(route.calls.last.request.content)
    unsigned = {k: v for k, v in sent.items() if k != "sign"}
    canonical = json.dumps(
        {k: unsigned[k] for k in sorted(unsigned)}, ensure_ascii=False, separators=(",", ":")
    )
    assert sent["sign"] == hmac.new(b"tk", canonical.encode(), hashlib.sha256).hexdigest()

    hook = {"id": "sp-1", "order_id": str(ctx.payment_id), "status": "success", "amount": 199.0}
    hook["sign"] = gw._sign(hook, "tk")
    ok = await gw.handle_webhook(WebhookRequest(body=json.dumps(hook).encode(), headers={}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    hook["sign"] = "bad"
    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=json.dumps(hook).encode(), headers={}))


@respx.mock
async def test_aurapay_value_concat_signature() -> None:
    gw = AurapayGateway({"api_key": "ak", "shop_id": "shop", "webhook_secret": "wk"})
    ctx = _ctx()
    respx.post("https://app.aurapay.tech/invoice/create").mock(
        return_value=httpx.Response(
            200, json={"id": "au-1", "payment_data": {"url": "https://aurapay.tech/au-1"}}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "au-1"

    hook = {"id": "au-1", "order_id": str(ctx.payment_id), "status": "PAID", "amount": "199.00"}
    message = "".join("" if hook[k] is None else str(hook[k]) for k in sorted(hook))
    sig = hmac.new(b"wk", message.encode(), hashlib.sha256).hexdigest()
    ok = await gw.handle_webhook(
        WebhookRequest(body=json.dumps(hook).encode(), headers={"X-SIGNATURE": sig})
    )
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(
            WebhookRequest(body=json.dumps(hook).encode(), headers={"X-SIGNATURE": "no"})
        )


@respx.mock
async def test_antilopay_rsa_sign_and_verify() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_der_b64 = base64.b64encode(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode()
    pub_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )

    gw = AntilopayGateway(
        {
            "secret_id": "sid",
            "project_id": "proj",
            "private_key": priv_der_b64,
            "public_key": pub_pem,
        }
    )
    ctx = _ctx()
    route = respx.post("https://lk.antilopay.com/api/v2/payment/create").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "payment_id": "ap-1", "payment_url": "https://antilopay.com/ap-1"}
        )
    )
    result = await gw.create_payment(ctx)
    assert result.external_id == "ap-1"
    # request body was RSA-signed with our private key -> verifiable by the public key
    req = route.calls.last.request
    key.public_key().verify(
        base64.b64decode(req.headers["X-Apay-Sign"]),
        req.content,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )

    body = json.dumps(
        {
            "status": "SUCCESS",
            "order_id": str(ctx.payment_id),
            "payment_id": "ap-1",
            "original_amount": 199.0,
        }
    ).encode()
    sig = base64.b64encode(key.sign(body, padding.PKCS1v15(), hashes.SHA256())).decode()
    ok = await gw.handle_webhook(WebhookRequest(body=body, headers={"X-Apay-Callback": sig}))
    assert ok.status is TransactionStatus.COMPLETED and ok.payment_id == ctx.payment_id
    assert ok.amount == Money(19900, Currency.RUB)

    with pytest.raises(WebhookVerificationError):
        await gw.handle_webhook(WebhookRequest(body=body + b"x", headers={"X-Apay-Callback": sig}))
