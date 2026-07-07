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
from src.bot.gate import ensure_channel
from src.bot.keyboards import simple_keyboard
from src.core.enums import Currency, PurchaseType, TransactionStatus, TransactionType
from src.core.exceptions import (
    DomainError,
    InsufficientBalance,
    InvalidStateTransition,
    RemnawaveError,
)
from src.core.logging import get_logger
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

router = Router(name="purchase")

GIB = 1024**3


def fmt_money(minor: int) -> str:
    v = minor / 100
    return f"{v:,.0f} ₽".replace(",", " ") if v == int(v) else f"{v:,.2f} ₽".replace(",", " ")


async def show_plans(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    if not await ensure_channel(cb, container):  # channel-lock (#1)
        return
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


@router.callback_query(F.data == "check:sub")
async def check_sub(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """'Я подписался' — re-check channel membership, then open the plans on success."""
    if await ensure_channel(cb, container):
        await show_plans(cb, container, db_user)


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
        online_gateways = [
            (g.type.value, g.display_name or g.type.value)
            for g in await uow.payment_gateways.list()
            if g.is_active
            and g.type in container.gateway_factory.supported()
            and g.type.value not in ("manual", "telegram_stars")
        ]
    price = quote.final.amount_minor
    stars = max(1, math.ceil(price / max(1, stars_rate)))
    rows = []
    if balance_enabled:
        ok = "✅" if db_user.balance_minor >= price else "❌"
        rows.append(
            (f"{ok} С баланса ({fmt_money(db_user.balance_minor)})", f"pay:{plan_id}:{days}:bal")
        )
    rows.append((f"⭐ Telegram Stars · {stars} ★", f"pay:{plan_id}:{days}:stars"))
    for gtype, label in online_gateways:
        rows.append((f"💳 {label}", f"pay:{plan_id}:{days}:{gtype}"))
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
        return await container.purchase.resolve_purchase_type(uow, user.id, plan_id)


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

    if method != "stars":
        await _pay_with_gateway(cb, container, req, method)
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


async def _pay_with_gateway(
    cb: CallbackQuery, container: AppContainer, req: PurchaseRequest, method: str
) -> None:
    """Hosted payment: pending tx -> provider invoice -> «Оплатить» button.

    The provider webhook drives fulfilment through the standard pipeline.
    """
    from src.application.common.payments import PaymentContext, PaymentResultKind
    from src.core.enums import PaymentGatewayType
    from src.core.money import Money
    from src.infrastructure.payments.crypto import decrypt_gateway_settings

    try:
        gtype = PaymentGatewayType(method)
    except ValueError:
        await cb.answer("Неизвестный способ оплаты", show_alert=True)
        return
    async with container.uow() as uow:
        row = await uow.payment_gateways.get_active(gtype)
        if row is None or gtype not in container.gateway_factory.supported():
            await cb.answer("Способ оплаты выключен", show_alert=True)
            return
        settings = decrypt_gateway_settings(container.secret_box, dict(row.settings))
        try:
            txn, quote = await container.purchase.start(uow, req)
        except DomainError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        title = str((txn.plan_snapshot or {}).get("name") or "VPN")
        gateway = container.gateway_factory.create(gtype, settings)
        try:
            result = await gateway.create_payment(
                PaymentContext(
                    payment_id=txn.payment_id,
                    amount=Money(quote.final.amount_minor, txn.currency),
                    description=f"{title} · {req.duration_days} дн.",
                    user_id=req.user_id,
                    telegram_id=cb.from_user.id if cb.from_user else None,
                )
            )
        except Exception as exc:
            log.error("gateway create failed", gateway=method, error=str(exc))
            await cb.answer("Платёжка временно недоступна, попробуй другой способ", show_alert=True)
            return
        if result.kind is not PaymentResultKind.REDIRECT or not result.redirect_url:
            await cb.answer("Платёжка не вернула ссылку на оплату", show_alert=True)
            return
        txn.gateway_type = gtype
        txn.external_id = result.external_id
        txn.gateway_display_name = row.display_name or gtype.value
        await uow.commit()
        pay_url = result.redirect_url
        label = row.display_name or gtype.value

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить · {label}", url=pay_url)],
            [InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")],
        ]
    )
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            "Счёт создан — оплати по кнопке ниже.\n"
            "Подписка активируется автоматически сразу после оплаты ⚡",
            reply_markup=markup,
        )
    await cb.answer()


async def _pay_with_balance(
    cb: CallbackQuery, container: AppContainer, req: PurchaseRequest
) -> None:
    async with container.uow() as uow:
        try:
            await container.purchase.checkout_from_balance(uow, req)  # shared with the mini-app
        except RemnawaveError as exc:
            log.error("provision failed", error=str(exc))
            await cb.answer("Оплата не списана: сервис выдачи временно недоступен", show_alert=True)
            return  # no commit -> full rollback
        except InsufficientBalance:
            await cb.answer("Недостаточно средств на балансе", show_alert=True)
            return
        except InvalidStateTransition:
            await cb.answer("Платёж уже обработан", show_alert=True)
            return
        except DomainError as exc:
            await cb.answer(str(exc), show_alert=True)
            return
        await uow.commit()
        user = await uow.users.get(req.user_id)
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user and user.current_subscription_id
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


# --- balance top-up (Telegram Stars deposit) -----------------------------------

_TOPUP_PRESETS_RUB = (100, 250, 500, 1000)


@router.callback_query(F.data == "topup:menu")
async def topup_menu(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        min_dep = int(await container.bot_config.value(uow, "MIN_DEPOSIT_AMOUNT"))
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
    amounts_minor = [r * 100 for r in _TOPUP_PRESETS_RUB if r * 100 >= min_dep] or [min_dep]
    rows = []
    for minor in amounts_minor:
        stars = max(1, math.ceil(minor / max(1, stars_rate)))
        rows.append((f"{fmt_money(minor)} · {stars} ★", f"topup:{minor}"))
    rows.append(("‹ Назад", "act:balance:0"))
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            "Пополнение баланса через Telegram Stars.\nВыбери сумму:",
            reply_markup=simple_keyboard(rows),
        )
    await cb.answer()


@router.callback_query(F.data.startswith("topup:"))
async def topup_amount(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    amount_minor = int((cb.data or "topup:0").split(":")[1])
    if amount_minor <= 0:
        await cb.answer("Некорректная сумма", show_alert=True)
        return
    async with container.uow() as uow:
        stars_rate = int(await container.bot_config.value(uow, "STARS_RATE_RUB"))
        txn = Transaction(
            user_id=db_user.id,
            type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            amount_minor=amount_minor,
            currency=Currency.RUB,
        )
        await uow.transactions.add(txn)
        await uow.commit()
        payment_id = str(txn.payment_id)
    stars = max(1, math.ceil(amount_minor / max(1, stars_rate)))
    if cb.message is not None:
        await cb.message.answer_invoice(  # type: ignore[union-attr,unused-ignore]
            title="Пополнение баланса",
            description=f"Пополнение на {fmt_money(amount_minor)}",
            payload=payment_id,
            currency="XTR",
            prices=[LabeledPrice(label="Баланс", amount=stars)],
        )
    await cb.answer()


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
            await message.answer("Оплата получена, но выдача задерживается — мы уже разбираемся.")
            return
        txn = await uow.transactions.get_by_payment_id(payment_id)
        user = await uow.users.get(db_user.id)
        sub = (
            await uow.subscriptions.get(user.current_subscription_id)
            if user and user.current_subscription_id
            else None
        )

    if txn is not None and txn.type is TransactionType.DEPOSIT:
        balance = fmt_money(user.balance_minor) if user else "—"
        await message.answer(
            f"✅ <b>Баланс пополнен.</b>\nТекущий баланс: {balance}", parse_mode="HTML"
        )
        return
    text = "✅ <b>Оплата получена — подписка активирована!</b>"
    if sub is not None and sub.subscription_url:
        text += f"\n\nСсылка подписки:\n<code>{sub.subscription_url}</code>"
    await message.answer(text, parse_mode="HTML")
