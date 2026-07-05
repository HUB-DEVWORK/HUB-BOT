"""Background tasks. Import path registered with the worker (see compose.local.yml)."""

from __future__ import annotations

from uuid import UUID

from src.core.enums import TransactionStatus
from src.core.logging import get_logger
from src.infrastructure.taskiq.broker import broker, get_container

log = get_logger(__name__)


@broker.task
async def process_payment(payment_id: str, status: str) -> bool:
    """Complete a transaction from a verified webhook (idempotent CAS + fulfilment).

    Enqueued by the payment webhook route; never run inline. Safe to retry — a duplicate
    finds the transaction already terminal and no-ops.
    """
    container = get_container()
    async with container.uow() as uow:
        moved = await container.payments.process(
            uow,
            payment_id=UUID(payment_id),
            status=TransactionStatus(status),
        )
        await uow.commit()
    log.info("process_payment", payment_id=payment_id, status=status, advanced=moved)
    return moved


@broker.task
async def panel_write_retry(subscription_id: int) -> None:
    """Re-drive a failed panel write for a subscription (ADR-0005 retry queue).

    Placeholder for the reconcile/sync implementation — wire to RemnawaveService.apply once
    the sync mapper lands. Kept idempotent by design.
    """
    log.info("panel_write_retry", subscription_id=subscription_id)
