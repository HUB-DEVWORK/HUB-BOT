"""PurchaseService — turns a PurchaseRequest into a Transaction and fulfils it.

``start`` creates a PENDING transaction with frozen snapshots and (for free purchases) fulfils
immediately. ``fulfill`` provisions the subscription. Payment-driven fulfilment goes through
:class:`~src.application.services.payment.PaymentService` which owns the idempotent CAS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.application.common.events import EventBus
from src.application.dto.pricing import PriceQuote, PurchaseRequest
from src.application.events import SubscriptionPurchased
from src.application.services.pricing import PricingService
from src.application.services.subscription import SubscriptionService, _plan_snapshot
from src.core.enums import PurchaseType, TransactionStatus, TransactionType
from src.core.exceptions import PurchaseError
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork


class PurchaseService:
    def __init__(
        self,
        pricing: PricingService,
        subscriptions: SubscriptionService,
        event_bus: EventBus,
    ) -> None:
        self._pricing = pricing
        self._subscriptions = subscriptions
        self._events = event_bus

    async def start(self, uow: UnitOfWork, req: PurchaseRequest) -> tuple[Transaction, PriceQuote]:
        """Create the PENDING transaction. Free purchases are completed inline."""
        plan = await uow.plans.get(req.plan_id)
        if plan is None:
            raise PurchaseError(f"plan {req.plan_id} not found")
        quote = await self._pricing.quote(uow, req)

        txn = Transaction(
            user_id=req.user_id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            status=TransactionStatus.PENDING,
            amount_minor=quote.final.amount_minor,
            currency=req.currency,
            purchase_type=req.purchase_type,
            plan_snapshot=_plan_snapshot(plan),
            pricing=self._pricing_snapshot(req, quote),
        )
        await uow.transactions.add(txn)

        if quote.is_free:
            moved = await uow.transactions.transition_status(
                txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
            )
            if moved:
                await self.fulfill(uow, txn)
        return txn, quote

    async def fulfill(self, uow: UnitOfWork, txn: Transaction) -> Subscription:
        """Provision the subscription for a completed transaction and emit the event."""
        user = await uow.users.get(txn.user_id)
        if user is None:
            raise PurchaseError(f"user {txn.user_id} not found")
        snapshot = txn.plan_snapshot or {}
        plan = await uow.plans.get(int(snapshot["plan_id"]))
        if plan is None:
            raise PurchaseError("plan referenced by transaction snapshot no longer exists")

        pricing = txn.pricing
        req = PurchaseRequest(
            user_id=txn.user_id,
            plan_id=plan.id,
            duration_days=int(pricing["duration_days"]),
            currency=txn.currency,
            internal_squads=tuple(pricing.get("internal_squads", [])),
            external_squad=pricing.get("external_squad"),
            purchase_type=txn.purchase_type or PurchaseType.NEW,
        )
        subscription = await self._subscriptions.grant(uow, user=user, plan=plan, req=req)
        self._subscriptions.apply_purchase_discount_reset(user, req.purchase_type)
        await uow.flush()  # populate subscription.id

        await self._events.publish(
            SubscriptionPurchased(
                user_id=user.id,
                subscription_id=subscription.id,
                transaction_id=txn.id,
                purchase_type=req.purchase_type,
            )
        )
        return subscription

    @staticmethod
    def _pricing_snapshot(req: PurchaseRequest, quote: PriceQuote) -> dict[str, Any]:
        return {
            "plan_id": req.plan_id,
            "duration_days": req.duration_days,
            "internal_squads": list(req.internal_squads),
            "external_squad": req.external_squad,
            "base_minor": quote.base.amount_minor,
            "discount_pct": quote.discount_pct,
            "final_minor": quote.final.amount_minor,
            "components": quote.components,
        }
