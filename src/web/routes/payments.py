"""Single dynamic payment-webhook route (ADR-0004).

Verify -> resolve the internal payment_id -> ENQUEUE a taskiq job -> return 200 immediately.
No fulfilment happens inline (gotcha #6).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.application.common.payments import WebhookRequest
from src.core.enums import PaymentGatewayType, TransactionStatus
from src.core.exceptions import GatewayNotConfigured, NotFound, WebhookVerificationError
from src.infrastructure.di import AppContainer
from src.infrastructure.payments.crypto import decrypt_gateway_settings
from src.infrastructure.taskiq.tasks import process_payment
from src.web.deps import get_container

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


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
        gt, decrypt_gateway_settings(container.secret_box, settings)
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
        # A verified but irrelevant update (e.g. CryptoBot invoice_expired without our
        # payload) carries no ids on purpose — acknowledge it so the provider stops retrying.
        if result.status is TransactionStatus.PENDING:
            return {"accepted": True, "ignored": True}
        raise HTTPException(status_code=404, detail="payment not found")

    # Persist the provider's canonical transaction id so a later refund targets it. Some
    # gateways (CloudPayments) return a create-time order id at checkout but a different
    # TransactionId in the webhook — the refund API needs the latter (#4). No-op when they match.
    if result.status is TransactionStatus.COMPLETED and result.external_id:
        async with container.uow() as uow:
            paid = await uow.transactions.get_by_payment_id(payment_id)
            if paid is not None and paid.external_id != result.external_id:
                paid.external_id = result.external_id
                await uow.commit()

    # The provider saved a card for recurring charges — pass it along encrypted (the raw
    # charge token must not sit plaintext in the broker; stored on the user by the worker).
    saved_method_enc = saved_method_title = None
    if result.saved_method is not None:
        saved_method_enc = (
            container.secret_box.encrypt(result.saved_method.method_id)
            if container.secret_box
            else result.saved_method.method_id
        )
        saved_method_title = result.saved_method.title

    # Enqueue and return fast — the worker fulfils idempotently.
    await process_payment.kiq(
        str(payment_id),
        result.status.value,
        saved_method_enc=saved_method_enc,
        saved_method_title=saved_method_title,
        amount_minor=result.amount.amount_minor if result.amount else None,
    )
    if result.http_body is not None:
        # Robokassa-style providers require an exact plain-text acknowledgement.
        return PlainTextResponse(result.http_body)  # type: ignore[return-value]
    return {"accepted": True}
