"""Manual gateway webhook parsing/verification (ADR-0004)."""

from __future__ import annotations

import uuid

import orjson
import pytest

from src.application.common.payments import WebhookRequest
from src.core.enums import TransactionStatus
from src.core.exceptions import WebhookVerificationError
from src.infrastructure.payments.gateways.manual import ManualGateway


def _req(payload: dict[str, object], headers: dict[str, str] | None = None) -> WebhookRequest:
    return WebhookRequest(body=orjson.dumps(payload), headers=headers or {})


async def test_confirm_completes_payment() -> None:
    gateway = ManualGateway({})
    pid = uuid.uuid4()
    result = await gateway.handle_webhook(_req({"payment_id": str(pid), "status": "confirm"}))
    assert result.status is TransactionStatus.COMPLETED
    assert result.payment_id == pid


async def test_reject_cancels_payment() -> None:
    gateway = ManualGateway({})
    pid = uuid.uuid4()
    result = await gateway.handle_webhook(_req({"payment_id": str(pid), "status": "reject"}))
    assert result.status is TransactionStatus.CANCELED


async def test_missing_payment_id_is_rejected() -> None:
    gateway = ManualGateway({})
    with pytest.raises(WebhookVerificationError):
        await gateway.handle_webhook(_req({"status": "confirm"}))


async def test_admin_secret_is_enforced() -> None:
    gateway = ManualGateway({"secret": "s3cr3t"})
    pid = uuid.uuid4()
    with pytest.raises(WebhookVerificationError):
        await gateway.handle_webhook(_req({"payment_id": str(pid)}, {"x-admin-secret": "wrong"}))
    ok = await gateway.handle_webhook(_req({"payment_id": str(pid)}, {"x-admin-secret": "s3cr3t"}))
    assert ok.status is TransactionStatus.COMPLETED
