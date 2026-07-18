"""Single dynamic payment-webhook route (ADR-0004).

Verify -> resolve the internal payment_id -> ENQUEUE a taskiq job -> return 200 immediately.
No fulfilment happens inline (gotcha #6).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse

from src.application.common.payments import WebhookRequest
from src.core.enums import PaymentGatewayType, TransactionStatus
from src.core.exceptions import GatewayNotConfigured, NotFound, WebhookVerificationError
from src.core.logging import get_logger
from src.infrastructure.di import AppContainer
from src.infrastructure.payments.crypto import decrypt_gateway_settings
from src.infrastructure.taskiq.tasks import process_payment
from src.web.deps import get_container

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])

# Pauses between enqueue attempts (a verified payment must not be dropped on a Redis blip).
_ENQUEUE_PAUSES = (0.2, 1.0, 0.0)


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

    # Defence in depth: a webhook for gateway X must not complete a transaction that belongs
    # to gateway Y (the internal payment_id is exposed in redirect URLs). Only enforced once
    # the txn has an assigned gateway_type — a still-unrouted pending txn is left to the guard.
    async with container.uow() as uow:
        owner = await uow.transactions.get_by_payment_id(payment_id)
    if owner is not None and owner.gateway_type is not None and owner.gateway_type != gt:
        raise HTTPException(status_code=403, detail="gateway mismatch")

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
        if container.secret_box is not None:
            saved_method_enc = container.secret_box.encrypt(result.saved_method.method_id)
            saved_method_title = result.saved_method.title
        else:
            # Fail closed: with no crypt key we won't drop a raw charge token into the broker/DB.
            # (Prod refuses to boot without APP__CRYPT_KEY; this only bites a misconfigured dev.)
            log.warning("saved payment method dropped: APP__CRYPT_KEY not configured")

    # Forward the provider amount for the underpayment gate ONLY when it's in the transaction's
    # currency — comparing a foreign-currency minor amount against RUB kopeks would be nonsense.
    # Same-currency is the only case today; the guard just keeps the cross-check honest.
    amount_minor = None
    if result.amount is not None and owner is not None and result.amount.currency == owner.currency:
        amount_minor = result.amount.amount_minor
    # Enqueue and return fast — the worker fulfils idempotently. The kiq is the route's only
    # Redis touch: a broker blip here must not turn a VERIFIED payment into a customer wait.
    enqueue_error: Exception | None = None
    for pause in _ENQUEUE_PAUSES:
        try:
            await process_payment.kiq(
                str(payment_id),
                result.status.value,
                saved_method_enc=saved_method_enc,
                saved_method_title=saved_method_title,
                amount_minor=amount_minor,
            )
            enqueue_error = None
            break
        except Exception as exc:
            enqueue_error = exc
            if pause:
                await asyncio.sleep(pause)
    if enqueue_error is not None:
        # Defer to the reconciler ONLY when it will actually sweep this txn: the provider is
        # pollable AND the local row sits inside the widest sweep window (created_at > now-24h
        # in list_stuck_pending; 23h leaves headroom for the 5-min wide-sweep cadence).
        # Otherwise an ack here would strand a verified payment forever.
        reconciler_covers = (
            gateway.can_poll_status()
            and owner is not None
            and owner.created_at > dt.datetime.now(dt.UTC) - dt.timedelta(hours=23)
        )
        if reconciler_covers:
            # Ack the webhook so the provider stops retrying a delivery we already verified —
            # the reconciler polls this provider and recovers the payment within minutes.
            log.error(
                "webhook enqueue failed, deferring to reconciler",
                gateway=gt.value,
                payment_id=str(payment_id),
                error=str(enqueue_error),
            )
        else:
            # No durable fallback — make the provider redeliver the webhook.
            raise HTTPException(status_code=503, detail="enqueue failed") from enqueue_error
    if result.http_body is not None:
        # Robokassa-style providers require an exact plain-text acknowledgement.
        return PlainTextResponse(result.http_body)  # type: ignore[return-value]
    return {"accepted": True}
