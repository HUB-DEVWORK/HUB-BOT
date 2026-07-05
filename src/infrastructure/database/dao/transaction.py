"""Transaction DAO — the idempotent payment state machine lives here (docs/context/03)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any, cast

from sqlalchemy import CursorResult, select, update

from src.core.enums import PaymentGatewayType, TransactionStatus
from src.infrastructure.database.base import utcnow
from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.transaction import Transaction


class TransactionDAO(BaseDAO[Transaction]):
    model = Transaction

    async def get_by_payment_id(self, payment_id: uuid.UUID) -> Transaction | None:
        return await self.find_one(payment_id=payment_id)

    async def get_by_external(
        self, external_id: str, gateway_type: PaymentGatewayType
    ) -> Transaction | None:
        return await self.find_one(external_id=external_id, gateway_type=gateway_type)

    async def lock_for_update(self, payment_id: uuid.UUID) -> Transaction | None:
        """Row-lock a transaction for the duration of the transaction (gotcha #6).

        ``with_for_update`` is a no-op on SQLite but correct on Postgres.
        """
        stmt = select(Transaction).where(Transaction.payment_id == payment_id).with_for_update()
        return (await self.session.scalars(stmt)).first()

    async def transition_status(
        self,
        payment_id: uuid.UUID,
        to_status: TransactionStatus,
        allowed_from: Iterable[TransactionStatus],
    ) -> bool:
        """Atomic CAS status change. Returns True iff a row moved (idempotent).

        Duplicate / late / out-of-order webhooks find the row already advanced and get
        ``False`` — the caller treats that as "already handled".
        """
        values: dict[str, object] = {"status": to_status}
        if to_status is TransactionStatus.COMPLETED:
            values["completed_at"] = utcnow()
        stmt = (
            update(Transaction)
            .where(
                Transaction.payment_id == payment_id,
                Transaction.status.in_(tuple(allowed_from)),
            )
            .values(**values)
        )
        result = await self.session.execute(stmt)
        return (cast("CursorResult[Any]", result).rowcount or 0) > 0
