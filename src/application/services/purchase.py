"""PurchaseService — turns a PurchaseRequest into a Transaction and fulfils it.

``start`` creates a PENDING transaction with frozen snapshots and (for free purchases) fulfils
immediately. ``fulfill`` provisions the subscription. Payment-driven fulfilment goes through
:class:`~src.application.services.payment.PaymentService` which owns the idempotent CAS.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.application.common.events import EventBus
from src.application.dto.pricing import PriceQuote, PurchaseRequest
from src.application.events import SubscriptionPurchased
from src.application.services.pricing import PricingService
from src.application.services.subscription import SubscriptionService, _plan_snapshot
from src.core.constants import BYTES_PER_GB
from src.core.enums import (
    Currency,
    PurchaseType,
    SubscriptionStatus,
    TransactionStatus,
    TransactionType,
)
from src.core.exceptions import InsufficientBalance, InvalidStateTransition, PurchaseError
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction

if TYPE_CHECKING:
    from src.application.services.bot_config import BotConfigService
    from src.infrastructure.database.uow import UnitOfWork

# The hidden service plan constructor purchases hang off (snapshot + subscriptions.plan_id FK).
# Inactive so it never shows up in the buyable catalogue; created lazily like the trial plan.
CONSTRUCTOR_PLAN_CODE = "constructor"
CONSTRUCTOR_PLAN_NAME = "Конструктор"


class PurchaseService:
    def __init__(
        self,
        pricing: PricingService,
        subscriptions: SubscriptionService,
        event_bus: EventBus,
        config: BotConfigService | None = None,
    ) -> None:
        self._pricing = pricing
        self._subscriptions = subscriptions
        self._events = event_bus
        self._config = config

    async def start(self, uow: UnitOfWork, req: PurchaseRequest) -> tuple[Transaction, PriceQuote]:
        """Create the PENDING transaction. Free purchases are completed inline."""
        plan = await uow.plans.get(req.plan_id)
        if plan is None:
            raise PurchaseError(f"plan {req.plan_id} not found")
        quote = await self._pricing.quote(uow, req)

        snapshot = _plan_snapshot(plan)
        if req.traffic_limit_bytes is not None:
            snapshot["traffic_limit_bytes"] = req.traffic_limit_bytes
        if req.device_limit is not None:
            snapshot["device_limit"] = req.device_limit
        if req.constructor_period_id is not None:
            gb = (req.traffic_limit_bytes or 0) // BYTES_PER_GB
            snapshot["name"] = f"{plan.name} · {gb} ГБ" if gb else f"{plan.name} · ∞"
        if req.purchase_type is PurchaseType.TRAFFIC_TOPUP and req.traffic_pack_id is not None:
            pack = await uow.traffic_packs.get(req.traffic_pack_id)
            if pack is not None:
                snapshot["name"] = f"+{pack.gb} ГБ трафика"

        txn = Transaction(
            user_id=req.user_id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            status=TransactionStatus.PENDING,
            amount_minor=quote.final.amount_minor,
            currency=req.currency,
            purchase_type=req.purchase_type,
            plan_snapshot=snapshot,
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

    async def ensure_constructor_plan(self, uow: UnitOfWork) -> Plan:
        """Get-or-create the hidden service plan constructor purchases are booked under."""
        plan = await uow.plans.find_one(
            public_code=CONSTRUCTOR_PLAN_CODE
        ) or await uow.plans.find_one(name=CONSTRUCTOR_PLAN_NAME)
        if plan is None:
            plan = Plan(
                public_code=CONSTRUCTOR_PLAN_CODE,
                name=CONSTRUCTOR_PLAN_NAME,
                is_active=False,  # not in the catalogue — bought through the constructor flow
            )
            await uow.plans.add(plan)
        return plan

    async def build_constructor_request(
        self,
        uow: UnitOfWork,
        *,
        user_id: int,
        period_id: int,
        pack_id: int,
        currency: Currency = Currency.RUB,
        device_limit: int | None = None,
    ) -> PurchaseRequest:
        """Turn a period+pack selection into a PurchaseRequest (shared by bot + mini-app).

        Validates the rows, freezes the traffic limit from the pack (0 GB -> unlimited) and
        resolves NEW/RENEW against the hidden constructor plan.
        """
        plan = await self.ensure_constructor_plan(uow)
        period = await uow.constructor_periods.get(period_id)
        pack = await uow.traffic_packs.get(pack_id)
        if period is None or not period.is_active:
            raise PurchaseError("выбранный период недоступен")
        if pack is None or not pack.is_active:
            raise PurchaseError("выбранный пакет трафика недоступен")
        ptype, sub_id = await self.resolve_purchase_type(uow, user_id, plan.id)
        return PurchaseRequest(
            user_id=user_id,
            plan_id=plan.id,
            duration_days=period.days,
            currency=currency,
            purchase_type=ptype,
            subscription_id=sub_id,
            constructor_period_id=period.id,
            traffic_pack_id=pack.id,
            traffic_limit_bytes=pack.gb * BYTES_PER_GB,  # 0 -> unlimited
            device_limit=device_limit,
        )

    async def resolve_purchase_type(
        self, uow: UnitOfWork, user_id: int, plan_id: int
    ) -> tuple[PurchaseType, int | None]:
        """Decide NEW / RENEW / CHANGE for a plan purchase (shared by bot + mini-app).

        A brand-new panel account is minted ONLY when the user has no existing panel-backed
        subscription to extend. If they already have one — even EXPIRED/DISABLED, or a
        **migrated** sub with no local plan (``plan_id`` NULL) — the purchase RENEWs/CHANGEs
        the SAME Remnawave account instead of orphaning it with a duplicate. This was the
        "оплатил → создался новый аккаунт, подписка не продлилась" bug: on migrated installs
        every sub has ``plan_id = NULL``, so the old ``plan_id == plan_id``/``is not None``
        checks fell straight through to NEW for all 273 imported users. Same-or-absent plan ->
        RENEW (extend, keep uuid); a different KNOWN plan -> CHANGE (switch, prorated credit).
        """
        user = await uow.users.get(user_id)
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user is not None and user.current_subscription_id
            else None
        )
        if (
            sub is not None
            and sub.remnawave_uuid is not None  # a real panel user we can extend/revive
            and sub.status is not SubscriptionStatus.DELETED
        ):
            if sub.plan_id is not None and sub.plan_id != plan_id:
                return PurchaseType.CHANGE, sub.id
            return PurchaseType.RENEW, sub.id  # same plan OR plan-less (migrated) -> extend
        return PurchaseType.NEW, None

    async def checkout_from_balance(
        self, uow: UnitOfWork, req: PurchaseRequest
    ) -> tuple[Transaction, PriceQuote]:
        """Buy from the wallet balance in one transaction (shared by bot + mini-app).

        Does NOT commit — the caller owns the boundary, so a panel failure in ``fulfill`` rolls
        the whole purchase (including the balance debit) back. Raises ``InsufficientBalance`` /
        ``InvalidStateTransition`` / ``RemnawaveError`` for the caller to map to its response.
        """
        # Serialize concurrent taps on the same wallet (aiogram runs updates in parallel): without
        # the lock two near-simultaneous buys both resolve NEW and both grant a panel user,
        # orphaning the first (#6). Under the lock the 2nd re-resolves to RENEW against the 1st.
        if await uow.users.lock_for_update(req.user_id) is None:
            raise PurchaseError(f"user {req.user_id} not found")
        # A plan buy re-resolves NEW/RENEW/CHANGE under the lock so the 2nd tap renews the 1st
        # tap's sub instead of orphaning it. Traffic top-ups keep their type (resolve only knows
        # NEW/RENEW/CHANGE); the lock still serializes them.
        if req.purchase_type is not PurchaseType.TRAFFIC_TOPUP:
            ptype, sub_id = await self.resolve_purchase_type(uow, req.user_id, req.plan_id)
            req = replace(req, purchase_type=ptype, subscription_id=sub_id)
        txn, quote = await self.start(uow, req)
        if quote.is_free:
            return txn, quote  # start() already completed + fulfilled the free purchase
        user = await uow.users.get(req.user_id)
        if user is None:
            raise PurchaseError(f"user {req.user_id} not found")
        if not await uow.users.debit_balance_guarded(user, quote.final.amount_minor):
            raise InsufficientBalance("insufficient balance")
        moved = await uow.transactions.transition_status(
            txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
        )
        if not moved:
            raise InvalidStateTransition("transaction already processed")
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
        # Re-resolve NEW/RENEW/CHANGE under a user row-lock at *fulfilment* time. A hosted
        # payment (or Stars) can settle long after the invoice was created, by which point the
        # buyer may already have a subscription; a frozen NEW would mint a duplicate panel
        # account and orphan the paid one. checkout_from_balance already re-resolves under a
        # lock — this closes the same hole on the webhook/Stars fulfilment path.
        purchase_type = txn.purchase_type or PurchaseType.NEW
        subscription_id = pricing.get("subscription_id")
        if purchase_type is not PurchaseType.TRAFFIC_TOPUP:
            await uow.users.lock_for_update(txn.user_id)
            purchase_type, subscription_id = await self.resolve_purchase_type(
                uow, txn.user_id, plan.id
            )
        req = PurchaseRequest(
            user_id=txn.user_id,
            plan_id=plan.id,
            duration_days=int(pricing["duration_days"]),
            currency=txn.currency,
            internal_squads=tuple(pricing.get("internal_squads", [])),
            external_squad=pricing.get("external_squad"),
            purchase_type=purchase_type,
            subscription_id=subscription_id,
            constructor_period_id=pricing.get("constructor_period_id"),
            traffic_pack_id=pricing.get("traffic_pack_id"),
            traffic_limit_bytes=pricing.get("traffic_limit_bytes"),
            device_limit=pricing.get("device_limit"),
        )
        subscription = await self._provision(uow, user=user, plan=plan, req=req)
        self._subscriptions.apply_purchase_discount_reset(user, req.purchase_type)
        sale_id = (txn.pricing or {}).get("sale_campaign_id")
        if sale_id:
            import datetime as _dt

            await uow.sales.consume(int(sale_id), _dt.datetime.now(_dt.UTC))
        await uow.flush()  # populate subscription.id
        # Link the payment to the subscription it provisioned — the plan-change credit
        # (PricingService._change_credit) needs this to value the unused remainder.
        txn.pricing = {**(txn.pricing or {}), "subscription_id": subscription.id}

        await self._events.publish(
            SubscriptionPurchased(
                user_id=user.id,
                subscription_id=subscription.id,
                transaction_id=txn.id,
                purchase_type=req.purchase_type,
            )
        )
        return subscription

    async def _provision(
        self, uow: UnitOfWork, *, user: Any, plan: Any, req: PurchaseRequest
    ) -> Subscription:
        """Route fulfilment by purchase type so a paid RENEW/CHANGE never mints a duplicate."""
        if req.purchase_type is PurchaseType.NEW:
            return await self._subscriptions.grant(uow, user=user, plan=plan, req=req)
        if req.purchase_type is PurchaseType.RENEW:
            if req.subscription_id is None:
                raise PurchaseError("RENEW requires subscription_id")
            sub = await uow.subscriptions.get(req.subscription_id)
            if sub is None or sub.user_id != user.id:
                raise PurchaseError(f"subscription {req.subscription_id} not found for renew")
            # Constructor renew may carry a different traffic pack — apply it before the
            # panel push (renew() builds the spec from the subscription's fields).
            if req.traffic_limit_bytes is not None:
                sub.traffic_limit_bytes = req.traffic_limit_bytes
            if req.device_limit is not None:
                sub.device_limit = req.device_limit
            # A migrated/plan-less sub (plan_id NULL) adopts the purchased plan on renew, so it
            # becomes a proper plan sub and future same-plan buys resolve cleanly to RENEW.
            return await self._subscriptions.renew(
                uow,
                sub,
                days=req.duration_days,
                telegram_id=user.telegram_id,
                adopt_plan=plan if sub.plan_id is None else None,
            )
        if req.purchase_type is PurchaseType.CHANGE:
            if req.subscription_id is None:
                raise PurchaseError("CHANGE requires subscription_id")
            sub = await uow.subscriptions.get(req.subscription_id)
            if sub is None or sub.user_id != user.id:
                raise PurchaseError(f"subscription {req.subscription_id} not found for change")
            carryover = True
            if self._config is not None:
                carryover = bool(await self._config.value(uow, "TRIAL_CARRYOVER_DAYS"))
            # Proration: the paid remainder of the current period carries over as bonus days on
            # the new plan (computed here, at fulfilment, so it reflects the up-to-date remaining).
            bonus_days = await self._pricing.change_bonus_days(uow, req)
            return await self._subscriptions.change(
                uow,
                sub,
                user=user,
                plan=plan,
                req=req,
                carryover_trial=carryover,
                bonus_days=bonus_days,
            )
        if req.purchase_type is PurchaseType.TRAFFIC_TOPUP:
            if req.subscription_id is None or req.traffic_pack_id is None:
                raise PurchaseError("traffic top-up requires subscription_id and traffic_pack_id")
            sub = await uow.subscriptions.get(req.subscription_id)
            pack = await uow.traffic_packs.get(req.traffic_pack_id)
            if sub is None or sub.user_id != user.id or pack is None:
                raise PurchaseError("subscription or traffic pack not found")
            if sub.traffic_limit_bytes <= 0:
                raise PurchaseError("subscription is already unlimited")
            sub.traffic_limit_bytes += pack.gb * BYTES_PER_GB
            return await self._subscriptions.push_limits(uow, sub, telegram_id=user.telegram_id)
        raise PurchaseError(f"purchase_type {req.purchase_type.value} is not supported yet")

    @staticmethod
    def _pricing_snapshot(req: PurchaseRequest, quote: PriceQuote) -> dict[str, Any]:
        return {
            "plan_id": req.plan_id,
            "duration_days": req.duration_days,
            "internal_squads": list(req.internal_squads),
            "external_squad": req.external_squad,
            "subscription_id": req.subscription_id,
            "base_minor": quote.base.amount_minor,
            "discount_pct": quote.discount_pct,
            "final_minor": quote.final.amount_minor,
            "components": quote.components,
            "constructor_period_id": req.constructor_period_id,
            "traffic_pack_id": req.traffic_pack_id,
            "traffic_limit_bytes": req.traffic_limit_bytes,
            "device_limit": req.device_limit,
            "sale_campaign_id": quote.sale_campaign_id,
        }
