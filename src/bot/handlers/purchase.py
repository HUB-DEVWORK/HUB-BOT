"""Purchase flow: plan -> duration -> pay with balance or Telegram Stars.

Balance: start() -> deduct -> CAS to COMPLETED -> fulfill, all in one transaction
(panel-first inside fulfill; any failure rolls the whole purchase back).
Stars: start() -> XTR invoice with payload=payment_id -> successful_payment ->
PaymentService.process (the same idempotent path webhooks use).
"""

from __future__ import annotations

import math

from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from src.application.dto.pricing import PurchaseRequest
from src.bot.keyboards import simple_keyboard
from src.core.enums import Currency, PurchaseType, TransactionStatus
from src.core.exceptions import DomainError, RemnawaveError
from src.core.logging import get_logger
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

router = Router(name="purchase")

GIB = 1024**3


def fmt_money(minor: int) -> str:
    v = minor / 100
    return f"{v:,.0f} ₽".replace(",", " ") if v == int(v) else f"{v:,.2f} ₽".replace(",", " ")


async def show_plans(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        plans = [p for p in await uow.plans.list_with_durations() if p.is_active and not p.is_trial]
    if not plans:
        await cb.answer("Тарифы ещё не настроены", show_alert=True)
        return
    rows = []
    for p in sorted(plans, key=lambda p: p.order_index):
        cheapest = min(
            (pr.price_minor for d in p.durations for pr in d.prices),
            default=0,
        )
        traffic = f"{(p.traffic_limit_bytes or 0) / GIB:.0f} ГБ" if p.traffic_limit_bytes else "∞"
        rows.append((f"{p.name} · {traffic} · от {fmt_money(cheapest)}", f"plan:{p.id}"))
    rows.append(("‹ Меню", "nav:root"))
    if cb.message is not None:
        await cb.message.edit_text("Выбери тариф:", reply_markup=simple_keyboard(rows))  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data.startswith("plan:"))
async def show_durations(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    plan_id = int((cb.data or "plan:0").split(":")[1])
    async with container.uow() as uow:
        plan = await uow.plans.get_with_durations(plan_id)
    if plan is None or not plan.durations:
        await show_plans(cb, container, db_user)
        return
    rows = []
    for d in plan.durations:
        rub = next((p.price_minor for p in d.prices if p.currency is Currency.RUB), None)
        if rub is None:
            continue
        months = round(d.days / 30) or 1
        rows.append((f"{months} мес · {fmt_money(rub)}", f"dur:{plan.id}:{d.days}"))
    rows.append(("‹ Назад", "act:buy:0"))
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            f"<b>{plan.name}</b>\n{plan.description or ''}\n\nВыбери срок:",
            reply_markup=simple_keyboard(rows),
            parse_mode="HTML",
        )
    await cb.answer()


@router.callback_query(F.data.startswith("dur:"))
async def choose_payment(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    _, plan_id, days = (cb.data or "dur:0:0").split(":")
    async with container.uow() as uow:
        req = _purchase_request(int(plan_id), int(days), db_user)
        try:
            quote = await container.pricing.quote(uow, req)
        except DomainError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
        balance_enabled = bool(await container.bot_config.value(uow, "BALANCE_ENABLED"))
    price = quote.final.amount_minor
    stars = max(1, math.ceil(price / max(1, stars_rate)))
    rows = []
    if balance_enabled:
        ok = "✅" if db_user.balance_minor >= price else "❌"
        rows.append(
            (f"{ok} С баланса ({fmt_money(db_user.balance_minor)})", f"pay:{plan_id}:{days}:bal")
        )
    rows.append((f"⭐ Telegram Stars · {stars} ★", f"pay:{plan_id}:{days}:stars"))
    rows.append(("‹ Назад", f"plan:{plan_id}"))
    discount = f" (−{quote.discount_pct}%)" if quote.discount_pct else ""
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            f"К оплате: <b>{fmt_money(price)}</b>{discount}\n\nСпособ оплаты:",
            reply_markup=simple_keyboard(rows),
            parse_mode="HTML",
        )
    await cb.answer()


def _purchase_request(plan_id: int, days: int, user: User) -> PurchaseRequest:
    renew_sub_id: int | None = None
    purchase_type = PurchaseType.NEW
    # RENEW when the user's current subscription is on this very plan and usable.
    return PurchaseRequest(
        user_id=user.id,
        plan_id=plan_id,
        duration_days=days,
        currency=Currency.RUB,
        purchase_type=purchase_type,
        subscription_id=renew_sub_id,
    )


async def _resolve_purchase_type(
    container: AppContainer, user: User, plan_id: int
) -> tuple[PurchaseType, int | None]:
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
    if sub is not None and sub.plan_id == plan_id and sub.status.is_usable:
        return PurchaseType.RENEW, sub.id
    return PurchaseType.NEW, None


@router.callback_query(F.data.startswith("pay:"))
async def pay(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    _, plan_id_s, days_s, method = (cb.data or "pay:0:0:bal").split(":")
    plan_id, days = int(plan_id_s), int(days_s)
    ptype, sub_id = await _resolve_purchase_type(container, db_user, plan_id)
    req = PurchaseRequest(
        user_id=db_user.id,
        plan_id=plan_id,
        duration_days=days,
        currency=Currency.RUB,
        purchase_type=ptype,
        subscription_id=sub_id,
    )

    if method == "bal":
        await _pay_with_balance(cb, container, req)
        return

    # Stars: create the pending transaction, then send an XTR invoice.
    async with container.uow() as uow:
        try:
            txn, quote = await container.purchase.start(uow, req)
        except DomainError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
        title = str((txn.plan_snapshot or {}).get("name") or "VPN")
        await uow.commit()
        payment_id = str(txn.payment_id)
        amount_minor = quote.final.amount_minor

    stars = max(1, math.ceil(amount_minor / max(1, stars_rate)))
    if cb.message is not None:
        await cb.message.answer_invoice(  # type: ignore[union-attr,unused-ignore]
            title=f"{title} · {days} дн.",
            description="Оплата VPN-подписки",
            payload=payment_id,
            currency="XTR",
            prices=[LabeledPrice(label="VPN", amount=stars)],
        )
    await cb.answer()


async def _pay_with_balance(
    cb: CallbackQuery, container: AppContainer, req: PurchaseRequest
) -> None:
    async with container.uow() as uow:
        user = await uow.users.get(req.user_id)
        if user is None:
            await cb.answer("Ошибка", show_alert=True)
            return
        try:
            txn, quote = await container.purchase.start(uow, req)
        except DomainError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        price = quote.final.amount_minor
        if not quote.is_free:
            if user.balance_minor < price:
                await cb.answer("Недостаточно средств на балансе", show_alert=True)
                return
            await uow.users.increment_balance(user, -price)
            moved = await uow.transactions.transition_status(
                txn.payment_id, TransactionStatus.COMPLETED, (TransactionStatus.PENDING,)
            )
            if not moved:
                await cb.answer("Платёж уже обработан", show_alert=True)
                return
            try:
                await container.purchase.fulfill(uow, txn)
            except RemnawaveError as exc:
                log.error("provision failed", error=str(exc))
                await cb.answer(
                    "Оплата не списана: сервис выдачи временно недоступен", show_alert=True
                )
                return  # no commit -> full rollback
        await uow.commit()
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user.current_subscription_id
            else None
        )
        url = sub.subscription_url if sub else None

    text = "✅ <b>Подписка активирована!</b>"
    if url:
        text += f"\n\nСсылка подписки:\n<code>{url}</code>"
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            text,
            reply_markup=simple_keyboard(
                [("👤 Моя подписка", "act:subscription:0"), ("‹ Меню", "nav:root")]
            ),
            parse_mode="HTML",
        )
    await cb.answer("Готово!")


# --- Telegram Stars settlement -------------------------------------------------


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message, container: AppContainer, db_user: User) -> None:
    from uuid import UUID

    sp = message.successful_payment
    assert sp is not None
    try:
        payment_id = UUID(sp.invoice_payload)
    except ValueError:
        log.error("bad invoice payload", payload=sp.invoice_payload)
        return
    async with container.uow() as uow:
        try:
            await container.payments.process(
                uow, payment_id=payment_id, status=TransactionStatus.COMPLETED
            )
            await uow.commit()
        except (DomainError, RemnawaveError) as exc:
            log.error("stars fulfilment failed", error=str(exc))
            await message.answer(
                "Оплата получена, но выдача подписки задерживается — мы уже разбираемся."
            )
            return
        user = await uow.users.get(db_user.id)
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user and user.current_subscription_id
            else None
        )
    text = "✅ <b>Оплата получена — подписка активирована!</b>"
    if sub is not None and sub.subscription_url:
        text += f"\n\nСсылка подписки:\n<code>{sub.subscription_url}</code>"
    await message.answer(text, parse_mode="HTML")
