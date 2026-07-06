"""PaymentService — the idempotent transaction-completion pipeline (docs/context/03).

Invoked by the taskiq worker after a verified webhook. Owns the CAS status transition so
duplicate / late / out-of-order callbacks are safe no-ops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from src.application.common.events import EventBus
from src.application.events import PaymentCompleted
from src.application.services.purchase import PurchaseService
from src.application.services.referral import ReferralService
from src.core.enums import TransactionStatus, TransactionType
from src.core.exceptions import NotFound
from src.infrastructure.database.models.transaction import Transaction

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork

# Transaction types that represent external money ENTERING the system — these trigger a
# referral commission (a balance-funded purchase does not; that money was rewarded on top-up).
_REWARDABLE = (TransactionType.DEPOSIT, TransactionType.SUBSCRIPTION_PAYMENT)


class PaymentService:
    def __init__(
        self, purchase: PurchaseService, event_bus: EventBus, referrals: ReferralService
    ) -> None:
        self._purchase = purchase
        self._events = event_bus
        self._referrals = referrals

    async def process(
        self, uow: UnitOfWork, *, payment_id: UUID, status: TransactionStatus
    ) -> bool:
        """Apply a webhook outcome to a transaction. Returns True iff it advanced the state.

        A ``False`` return means the transaction was already in a terminal state — the
        webhook was a duplicate and must be treated as successfully handled.
        """
        txn = await uow.transactions.lock_for_update(payment_id)  # serialize concurrent hooks
        if txn is None:
            raise NotFound(f"transaction {payment_id} not found")

        if status is TransactionStatus.COMPLETED:
            moved = await uow.transactions.transition_status(
                payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
            )
            if not moved:
                return False
            await self._fulfill(uow, txn)
            # Referral commission on money entering (atomic, idempotent per transaction).
            if txn.type in _REWARDABLE and txn.amount_minor > 0:
                payer = await uow.users.get(txn.user_id)
                if payer is not None:
                    await self._referrals.reward_on_topup(
                        uow, payer=payer, amount_minor=txn.amount_minor, transaction_id=txn.id
                    )
            await self._events.publish(
                PaymentCompleted(
                    user_id=txn.user_id,
                    transaction_id=txn.id,
                    amount_minor=txn.amount_minor,
                    currency=txn.currency.value,
                )
            )
            return True

        if status in (TransactionStatus.CANCELED, TransactionStatus.FAILED):
            return await uow.transactions.transition_status(
                payment_id, status, (TransactionStatus.PENDING,)
            )

        return False

    async def _fulfill(self, uow: UnitOfWork, txn: Transaction) -> None:
        if txn.type is TransactionType.SUBSCRIPTION_PAYMENT:
            await self._purchase.fulfill(uow, txn)
        elif txn.type is TransactionType.DEPOSIT:
            user = await uow.users.get(txn.user_id)
            if user is not None:
                await uow.users.increment_balance(user, txn.amount_minor)  # atomic (no lost update)
                user.has_made_first_topup = True
