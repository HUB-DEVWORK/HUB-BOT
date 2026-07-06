"""Navigation + built-in actions: subscription, balance, referral, trial, support."""

from __future__ import annotations

import contextlib

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.application.dto.pricing import PurchaseRequest
from src.bot.keyboards import menu_keyboard, simple_keyboard, url_keyboard
from src.bot.menu_render import send_main_menu
from src.core.enums import Currency, PurchaseType
from src.core.exceptions import RemnawaveError
from src.core.logging import get_logger
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

router = Router(name="actions")

GIB = 1024**3


def fmt_money(minor: int) -> str:
    v = minor / 100
    return f"{v:,.0f} ₽".replace(",", " ") if v == int(v) else f"{v:,.2f} ₽".replace(",", " ")


# --- navigation over admin-built screens -------------------------------------


@router.callback_query(F.data == "nav:root")
async def nav_root(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    await send_main_menu(cb, container, db_user)


@router.callback_query(F.data.startswith("nav:"))
async def nav_screen(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    parts = (cb.data or "").split(":")
    node_id = int(parts[1]) if parts[1].isdigit() else 0
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
    node = next((n for n in nodes if n.id == node_id), None)
    if node is None:
        await send_main_menu(cb, container, db_user)
        return
    if len(parts) > 2 and parts[2] == "up":
        # back button: show the parent screen (or root)
        parent = next((n for n in nodes if n.id == node.parent_id), None)
        if parent is None:
            await send_main_menu(cb, container, db_user)
            return
        node = parent
    text = node.payload or node.label
    markup = menu_keyboard(nodes, node.id, miniapp_url=miniapp_url or None, with_back=True)
    if cb.message is not None:
        with contextlib.suppress(Exception):  # unchanged content
            await cb.message.edit_text(text, reply_markup=markup)  # type: ignore[union-attr]
    await cb.answer()


# --- built-in actions ---------------------------------------------------------


@router.callback_query(F.data.startswith("act:subscription"))
async def act_subscription(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
        hide_link = bool(await container.bot_config.value(uow, "HIDE_SUBSCRIPTION_LINK"))
    if sub is None or not sub.status.is_usable:
        text = "У тебя пока нет активной подписки."
        markup = simple_keyboard([("🛒 Купить VPN", "act:buy:0"), ("‹ Меню", "nav:root")])
    else:
        days_left = ""
        if sub.expire_at is not None:
            import datetime as dt

            left = max(0, (sub.expire_at - dt.datetime.now(dt.UTC)).days)
            days_left = f"\nОсталось дней: <b>{left}</b> (до {sub.expire_at:%d.%m.%Y})"
        traffic = f"{sub.traffic_used_bytes / GIB:.1f} / " + (
            f"{sub.traffic_limit_bytes / GIB:.0f} ГБ" if sub.traffic_limit_bytes else "∞"
        )
        plan_name = (sub.plan_snapshot or {}).get("name", "—")
        text = f"<b>Твоя подписка</b>\n\nТариф: {plan_name}{days_left}\nТрафик: {traffic}"
        if not hide_link and sub.subscription_url:
            text += f"\n\nСсылка подписки:\n<code>{sub.subscription_url}</code>"
        rows: list[tuple[str, str]] = [("🔄 Продлить", f"plan:{sub.plan_id or 0}")]
        rows.append(("‹ Меню", "nav:root"))
        markup = simple_keyboard(rows)
    if cb.message is not None:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data.startswith("act:balance"))
async def act_balance(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        min_dep = int(await container.bot_config.value(uow, "MIN_DEPOSIT_AMOUNT"))
    text = (
        f"<b>Баланс: {fmt_money(db_user.balance_minor)}</b>\n\n"
        f"Пополнение через Telegram Stars — от {fmt_money(min_dep)}. "
        f"С баланса можно оплачивать подписки."
    )
    markup = simple_keyboard(
        [
            ("⭐ Пополнить", "topup:menu"),
            ("🆘 Поддержка", "act:support:0"),
            ("‹ Меню", "nav:root"),
        ]
    )
    if cb.message is not None:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data.startswith("act:referral"))
async def act_referral(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        cfg = container.bot_config
        enabled = bool(await cfg.value(uow, "REFERRAL_ENABLED"))
        bonus_days = int(await cfg.value(uow, "REFERRAL_BONUS_DAYS"))
        bot_username = str(await cfg.value(uow, "BOT_USERNAME") or "")
        invited = await uow.users.count(referred_by_id=db_user.id)
    if not enabled:
        await cb.answer("Реферальная программа отключена", show_alert=True)
        return
    link = f"https://t.me/{bot_username}?start=ref_{db_user.referral_code}"
    text = (
        f"<b>Пригласи друга — оба получите +{bonus_days} дней</b>\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\nПриглашено: <b>{invited}</b>"
    )
    share = f"https://t.me/share/url?url={link}"
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться", url=share)],
            [InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")],
        ]
    )
    if cb.message is not None:
        await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")  # type: ignore[union-attr]
    await cb.answer()


@router.callback_query(F.data.startswith("act:trial"))
async def act_trial(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        cfg = container.bot_config
        if not bool(await cfg.value(uow, "TRIAL_ENABLED")):
            await cb.answer("Пробный период недоступен", show_alert=True)
            return
        user = await uow.users.get(db_user.id)
        if user is None or not user.is_trial_available:
            await cb.answer("Пробный период уже использован", show_alert=True)
            return
        days = int(await cfg.value(uow, "TRIAL_DURATION_DAYS"))
        traffic_gb = int(await cfg.value(uow, "TRIAL_TRAFFIC_GB"))
        devices = int(await cfg.value(uow, "TRIAL_DEVICE_LIMIT"))

        plan = await uow.plans.find_one(is_trial=True)
        if plan is None:
            plan = Plan(
                public_code="trial",
                name="Trial",
                is_trial=True,
                is_active=False,  # not for sale — granted, not bought
                traffic_limit_bytes=traffic_gb * GIB or None,
                device_limit=devices,
            )
            await uow.plans.add(plan)

        req = PurchaseRequest(
            user_id=user.id,
            plan_id=plan.id,
            duration_days=days,
            currency=Currency.RUB,
            purchase_type=PurchaseType.NEW,
        )
        try:
            sub = await container.subscriptions.grant(
                uow, user=user, plan=plan, req=req, is_trial=True
            )
        except RemnawaveError:
            await cb.answer("Сервис временно недоступен, попробуй позже", show_alert=True)
            return
        await uow.commit()
        url = sub.subscription_url

    text = f"🎁 <b>Пробный период активирован: {days} дн.</b>"
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


@router.callback_query(F.data.startswith("act:support"))
async def act_support(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        cfg = container.bot_config
        mode = str(await cfg.value(uow, "SUPPORT_MODE"))
        redirect = str(await cfg.value(uow, "SUPPORT_REDIRECT_USERNAME") or "")
    if mode == "redirect" and redirect:
        if cb.message is not None:
            await cb.message.edit_text(  # type: ignore[union-attr]
                "Напиши нам — ответим быстро:",
                reply_markup=url_keyboard(
                    [("💬 Написать", f"https://t.me/{redirect.lstrip('@')}")]
                ),
            )
        await cb.answer()
        return
    # tickets mode: hand off to the tickets FSM
    from src.bot.handlers.tickets import begin_ticket

    await begin_ticket(cb, container, db_user)


@router.callback_query(F.data.startswith("act:"))
async def act_unknown(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """Unknown/custom action codes fall back to the buy flow entry or menu."""
    action = (cb.data or "").split(":")[1] if ":" in (cb.data or "") else ""
    if action in ("buy", "shop", "plans"):
        from src.bot.handlers.purchase import show_plans

        await show_plans(cb, container, db_user)
        return
    log.info("unknown action", action=action)
    await send_main_menu(cb, container, db_user)
