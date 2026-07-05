"""Single dynamic payment-webhook route (ADR-0004).

Verify -> resolve the internal payment_id -> ENQUEUE a taskiq job -> return 200 immediately.
No fulfilment happens inline (gotcha #6).
"""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.application.common.payments import WebhookRequest
from src.core.enums import PaymentGatewayType
from src.core.exceptions import GatewayNotConfigured, NotFound, WebhookVerificationError
from src.infrastructure.di import AppContainer
from src.infrastructure.payments.crypto import SecretBox
from src.infrastructure.taskiq.tasks import process_payment
from src.web.deps import get_container

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

_SECRET_KEYS = {"secret", "api_key", "token", "password", "shop_secret"}


def _decrypt_settings(box: SecretBox | None, settings: dict[str, Any]) -> dict[str, Any]:
    if box is None:
        return settings
    out = dict(settings)
    for key in _SECRET_KEYS & out.keys():
        value = out[key]
        if isinstance(value, str) and value:
            # value may already be plaintext in dev — tolerate a decrypt failure.
            with contextlib.suppress(Exception):
                out[key] = box.decrypt(value)
    return out


@router.post("/{gateway_type}")
async def payment_webhook(
    gateway_type: str, request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    try:
        gt = PaymentGatewayType(gateway_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="unknown gateway") from exc

    async with container.uow() as uow:
        row = await uow.payment_gateways.get_active(gt)
        settings = dict(row.settings) if row else {}
    if row is None:
        raise HTTPException(status_code=404, detail="gateway not configured")

    gateway = container.gateway_factory.create(
        gt, _decrypt_settings(container.secret_box, settings)
    )
    body = await request.body()
    wreq = WebhookRequest(
        body=body,
        headers=dict(request.headers),
        query=dict(request.query_params),
        client_ip=request.client.host if request.client else None,
    )

    try:
        result = await gateway.handle_webhook(wreq)
    except WebhookVerificationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (NotFound, GatewayNotConfigured) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    payment_id = result.payment_id
    if payment_id is None and result.external_id is not None:
        async with container.uow() as uow:
            txn = await uow.transactions.get_by_external(result.external_id, gt)
        payment_id = txn.payment_id if txn else None
    if payment_id is None:
        raise HTTPException(status_code=404, detail="payment not found")

    # Enqueue and return fast — the worker fulfils idempotently.
    await process_payment.kiq(str(payment_id), result.status.value)
    return {"accepted": True}
