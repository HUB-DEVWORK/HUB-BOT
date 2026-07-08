"""Navigation + built-in actions: subscription, balance, referral, trial, support."""

from __future__ import annotations

import contextlib
from html import escape as hesc
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from src.application.dto.pricing import PurchaseRequest
from src.application.services.connection import CLIENT_LABELS, build_deep_links
from src.bot.gate import ensure_channel
from src.bot.keyboards import menu_keyboard, simple_keyboard, url_keyboard, webapp_button
from src.bot.media import photo_input
from src.bot.menu_render import send_main_menu
from src.bot.screen import safe_answer, show_photo_screen, show_screen
from src.core.enums import Currency, PurchaseType, TransactionStatus, TransactionType
from src.core.exceptions import RemnawaveError
from src.core.logging import get_logger
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

router = Router(name="actions")

GIB = 1024**3
# Placeholder banner shipped with the code (owner swaps it for their own art). Lives under
# src/bot/assets/, which deploys with the app (unlike runtime uploads/).
_BANNER = Path(__file__).resolve().parent.parent / "assets" / "banner.png"


def _banner() -> FSInputFile:
    """Fresh FSInputFile for the screen banner (aiogram needs a new one per send)."""
    return FSInputFile(str(_BANNER))


def fmt_money(minor: int) -> str:
    v = minor / 100
    return f"{v:,.0f} ₽".replace(",", " ") if v == int(v) else f"{v:,.2f} ₽".replace(",", " ")


# --- navigation over admin-built screens -------------------------------------


@router.callback_query(F.data == "nav:root")
async def nav_root(
    cb: CallbackQuery, container: AppContainer, db_user: User, state: FSMContext
) -> None:
    # The menu button must abort any pending form (promocode/ticket input) — otherwise the
    # stale FSM state silently eats the user's next message.
    await state.clear()
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
    msg = cb.message if isinstance(cb.message, Message) else None
    if msg is not None and node.image_path:
        # Screens with an image: send a fresh photo message (can't edit text->photo).
        # The image may be a local uploads/ file, a Telegram file_id or a URL.
        try:
            await msg.answer_photo(
                photo_input(node.image_path),
                # Telegram caps photo captions at 1024 chars (text screens allow 4096).
                caption=text[:1024],
                reply_markup=markup,
            )
        except Exception:
            pass  # bad/unreachable image ref -> fall through to a text screen
        else:
            with contextlib.suppress(Exception):
                await msg.delete()
            await cb.answer()
            return
    await show_screen(cb, text, markup, parse_mode=None)
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
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        autopay_global = bool(await container.bot_config.value(uow, "AUTO_RENEWAL_ENABLED"))
    if sub is None or not sub.status.is_usable:
        text = (
            "📶 <b>Подписка</b>\n\n"
            "У тебя пока нет активной подписки.\n"
            "Оформи за пару тапов — и сразу подключайся."
        )
        markup = simple_keyboard([("🛒 Купить VPN", "act:buy:0"), ("‹ Меню", "nav:root")])
    else:
        days_left = ""
        if sub.expire_at is not None:
            import datetime as dt

            left = max(0, (sub.expire_at - dt.datetime.now(dt.UTC)).days)
            days_left = f"\n⏳ Осталось: <b>{left} дн.</b> (до {sub.expire_at:%d.%m.%Y})"
        traffic = f"{sub.traffic_used_bytes / GIB:.1f} / " + (
            f"{sub.traffic_limit_bytes / GIB:.0f} ГБ" if sub.traffic_limit_bytes else "∞"
        )
        plan_name = hesc(str((sub.plan_snapshot or {}).get("name", "—")))
        text = (
            f"📶 <b>Твоя подписка</b>\n\n"
            f"🏷 Тариф: <b>{plan_name}</b>{days_left}\n"
            f"📊 Трафик: <b>{traffic}</b>"
        )
        if not hide_link and sub.subscription_url:
            text += f"\n\nСсылка подписки:\n<code>{sub.subscription_url}</code>"
        kb: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text="🔌 Подключить", callback_data="act:connect:0")],
            [
                InlineKeyboardButton(text="🔄 Продлить", callback_data=f"plan:{sub.plan_id or 0}"),
                InlineKeyboardButton(text="📱 Устройства", callback_data="act:devices:0"),
            ],
            [
                InlineKeyboardButton(text="🔀 Сменить тариф", callback_data="act:buy:0"),
                InlineKeyboardButton(text="➕ Трафик", callback_data="traffic:menu"),
            ],
        ]
        if autopay_global:
            mark = "✅" if sub.autopay_enabled else "❌"
            kb.append(
                [
                    InlineKeyboardButton(
                        text=f"🔁 Автопродление: {mark}", callback_data="autopay:toggle"
                    )
                ]
            )
            if sub.autopay_enabled:
                card_mark = "✅" if sub.autopay_card_enabled else "❌"
                kb.append(
                    [
                        InlineKeyboardButton(
                            text=f"💳 Автосписание картой: {card_mark}",
                            callback_data="autopay:card",
                        )
                    ]
                )
        if miniapp_url.startswith("https://"):
            kb.append([webapp_button("📱 Открыть приложение", miniapp_url)])
        kb.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
        markup = InlineKeyboardMarkup(inline_keyboard=kb)
    await show_screen(cb, text, markup)
    await safe_answer(cb)  # autopay_toggle chains here after answering


@router.callback_query(F.data == "autopay:toggle")
async def autopay_toggle(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
        if sub is None:
            await cb.answer("Нет активной подписки", show_alert=True)
            return
        sub.autopay_enabled = not sub.autopay_enabled
        await uow.commit()
        enabled = sub.autopay_enabled
    await cb.answer("Автопродление включено ✅" if enabled else "Автопродление выключено ❌")
    await act_subscription(cb, container, db_user)


@router.callback_query(F.data == "autopay:card")
async def autopay_card_toggle(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """Opt-in to charge the saved card when the balance can't cover the renewal."""
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
        if sub is None:
            await cb.answer("Нет активной подписки", show_alert=True)
            return
        card_title = None
        if not sub.autopay_card_enabled:  # turning ON requires a saved card
            user = await uow.users.get(db_user.id)
            if user is None or not user.saved_payment_method_id:
                await cb.answer(
                    "Карта ещё не сохранена. Оплати подписку картой через ЮKassa — "
                    "она привяжется автоматически, и автосписание станет доступно.",
                    show_alert=True,
                )
                return
            card_title = user.saved_payment_method_title
        sub.autopay_card_enabled = not sub.autopay_card_enabled
        await uow.commit()
        enabled = sub.autopay_card_enabled
    if enabled:
        card = f" ({card_title})" if card_title else ""
        await cb.answer(f"Автосписание картой{card} включено ✅")
    else:
        await cb.answer("Автосписание картой выключено ❌")
    await act_subscription(cb, container, db_user)


@router.callback_query(F.data.startswith("act:cabinet"))
async def act_cabinet(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """Личный кабинет — one screen: profile, balance, subscription, referral, quick actions."""
    import datetime as dt

    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
        invited = await uow.users.count(referred_by_id=db_user.id)
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        balance_on = bool(await container.bot_config.value(uow, "BALANCE_ENABLED"))
        referral_on = bool(await container.bot_config.value(uow, "REFERRAL_ENABLED"))

    name = hesc(db_user.first_name or db_user.username or "друг")
    lines = [
        "👤 <b>Профиль</b>",
        "",
        f"Привет, {name}! 👋",
        f"Твой ID: <code>{db_user.telegram_id}</code>",
        "",
    ]
    if sub is not None and sub.status.is_usable:
        now = dt.datetime.now(dt.UTC)
        expire = sub.expire_at.strftime("%d.%m.%Y") if sub.expire_at else "—"
        if sub.expire_at is not None:
            delta = sub.expire_at - now
            d_left, h_left = max(0, delta.days), max(0, delta.seconds // 3600)
            left = f"{d_left} дн. {h_left} ч." if d_left else f"{h_left} ч."
        else:
            left = "—"
        traffic = f"{sub.traffic_used_bytes / GIB:.1f} / " + (
            f"{sub.traffic_limit_bytes / GIB:.0f} ГБ" if sub.traffic_limit_bytes else "∞"
        )
        lines += [
            "⭐ <b>Подписка</b>",
            "├ Статус: <b>активна</b>",
            f"├ Действует до: <b>{expire}</b>",
            f"├ Осталось: <b>{left}</b>",
            f"├ Устройств: <b>{sub.device_limit or '—'}</b>",
            f"├ Трафик: <b>{traffic}</b>",
            f"├ Автопродление: <b>{'вкл' if sub.autopay_enabled else 'выкл'}</b>",
            "└ Ключ-ссылка — в разделе «Моя подписка».",
        ]
    else:
        lines += ["⭐ <b>Подписка</b>", "└ Не оформлена — нажми «Купить VPN» в меню."]
    lines += [
        "",
        f"💳 Баланс: <b>{fmt_money(db_user.balance_minor)}</b>   ·   🎁 Друзей: <b>{invited}</b>",
    ]

    # The cabinet is the account hub — everything about the user's account, and the one
    # place «Поддержка» lives (the main menu stays lean). «Подключить»/«Купить» are the
    # primary actions up in the main menu, so they're not duplicated here.
    # A disabled feature must not show its button here — gate by the same flags the
    # feature checks, so «отключил в настройках» actually hides it. The grid reflows.
    entries: list[tuple[str, str]] = [("🔑 Моя подписка", "act:subscription:0")]
    if balance_on:
        entries.append(("💰 Баланс", "act:balance:0"))
    entries.append(("📊 История", "act:history:0"))
    if referral_on:
        entries.append(("🎁 Рефералка", "act:referral:0"))
    entries.append(("🎟 Промокод", "act:promocode"))
    entries.append(("🆘 Поддержка", "act:support:0"))
    kb: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in entries[i : i + 2]]
        for i in range(0, len(entries), 2)
    ]
    if miniapp_url.startswith("https://"):
        kb.append([webapp_button("📱 Открыть приложение", miniapp_url)])
    kb.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
    await show_photo_screen(
        cb, _banner(), "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("act:connect"))
async def act_connect(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """Mini-app-parity Connect screen: subscription URL + per-client import links + WebApp."""
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
        miniapp_url = str(await container.bot_config.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
    if sub is None or not sub.status.is_usable or not sub.subscription_url:
        await cb.answer("Сначала оформи подписку", show_alert=True)
        return
    links = build_deep_links(sub.subscription_url, sub.crypto_link)
    apps = "\n".join(f"• {CLIENT_LABELS[k]}: <code>{v}</code>" for k, v in links.items())
    text = (
        "<b>🔌 Подключение</b>\n\n"
        "1) Установи приложение: Happ, v2RayTun, Hiddify или Streisand.\n"
        "2) Открой мини-приложение (импорт в один тап + QR) или вставь ссылку подписки вручную:\n\n"
        f"<code>{sub.subscription_url}</code>\n\n"
        f"Ссылки-импорт:\n{apps}"
    )
    kb: list[list[InlineKeyboardButton]] = []
    if miniapp_url.startswith("https://"):
        kb.append([webapp_button("📱 Открыть приложение", miniapp_url)])
    kb.append([InlineKeyboardButton(text="👤 Моя подписка", callback_data="act:subscription:0")])
    kb.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
    await show_screen(cb, text, InlineKeyboardMarkup(inline_keyboard=kb))
    await cb.answer()


_TXN_LABEL: dict[TransactionType, str] = {
    TransactionType.DEPOSIT: "Пополнение",
    TransactionType.SUBSCRIPTION_PAYMENT: "Подписка",
    TransactionType.REFERRAL_REWARD: "Реф. бонус",
    TransactionType.REFUND: "Возврат",
    TransactionType.WITHDRAWAL: "Вывод",
    TransactionType.GIFT: "Подарок",
}
_TXN_STATUS_EMOJI: dict[TransactionStatus, str] = {
    TransactionStatus.COMPLETED: "✅",
    TransactionStatus.PENDING: "⏳",
    TransactionStatus.CANCELED: "✖️",
    TransactionStatus.FAILED: "❌",
    TransactionStatus.REFUNDED: "↩️",
}


@router.callback_query(F.data.startswith("act:history"))
async def act_history(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        txns = list(await uow.transactions.list(user_id=db_user.id))
    txns.sort(key=lambda t: t.created_at, reverse=True)
    if not txns:
        text = "История операций пуста."
    else:
        lines = [
            f"{_TXN_STATUS_EMOJI.get(t.status, '')} {t.created_at:%d.%m} · "
            f"{_TXN_LABEL.get(t.type, t.type.value)} · {fmt_money(t.amount_minor)}"
            for t in txns[:10]
        ]
        text = "<b>История операций</b>\n\n" + "\n".join(lines)
    await show_screen(cb, text, simple_keyboard([("‹ Меню", "nav:root")]))
    await cb.answer()


@router.callback_query(F.data.startswith("act:balance"))
async def act_balance(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        min_dep = int(await container.bot_config.value(uow, "MIN_DEPOSIT_AMOUNT"))
    text = (
        "💳 <b>Баланс</b>\n\n"
        f"Сейчас на счету: <b>{fmt_money(db_user.balance_minor)}</b>\n\n"
        f"Пополни через Telegram Stars (от {fmt_money(min_dep)}) — "
        "с баланса подписки оплачиваются в один тап."
    )
    markup = simple_keyboard(
        [
            ("⭐ Пополнить", "topup:menu"),
            ("🆘 Поддержка", "act:support:0"),
            ("‹ Меню", "nav:root"),
        ]
    )
    await show_photo_screen(cb, _banner(), text, markup)
    await cb.answer()


@router.callback_query(F.data.startswith("act:referral"))
async def act_referral(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        cfg = container.bot_config
        enabled = bool(await cfg.value(uow, "REFERRAL_ENABLED"))
        bonus_days = int(await cfg.value(uow, "REFERRAL_BONUS_DAYS"))
        bot_username = str(await cfg.value(uow, "BOT_USERNAME") or "")
        invited = await uow.users.count(referred_by_id=db_user.id)
        withdrawals_on = bool(await cfg.value(uow, "REFERRAL_WITHDRAWAL_ENABLED"))
    if not enabled:
        await cb.answer("Реферальная программа отключена", show_alert=True)
        return
    link = f"https://t.me/{bot_username}?start=ref_{db_user.referral_code}"
    text = (
        "🎁 <b>Пригласи друга</b>\n\n"
        f"За каждого друга вы <b>оба</b> получаете <b>+{bonus_days} дн.</b> подписки.\n\n"
        "Твоя ссылка (нажми, чтобы скопировать):\n"
        f"<code>{link}</code>\n\n"
        f"👥 Приглашено: <b>{invited}</b>"
    )
    share = f"https://t.me/share/url?url={link}"
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb_rows = [[InlineKeyboardButton(text="📤 Поделиться", url=share)]]
    if withdrawals_on:
        from src.bot.handlers.withdraw import available_minor

        avail = await available_minor(container, db_user.id)
        text += f"\nЗаработано и доступно к выводу: <b>{avail / 100:.2f} ₽</b>"
        kb_rows.append([InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw:start")])
    kb_rows.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
    markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await show_photo_screen(cb, _banner(), text, markup)
    await cb.answer()


@router.callback_query(F.data.startswith("act:trial"))
async def act_trial(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    if not await ensure_channel(cb, container, scope="trial"):  # channel-lock (#1)
        return
    async with container.uow() as uow:
        cfg = container.bot_config
        if not bool(await cfg.value(uow, "TRIAL_ENABLED")):
            await cb.answer("Пробный период недоступен", show_alert=True)
            return
        # FOR UPDATE: double-tap on the trial button must not grant twice.
        user = await uow.users.lock_for_update(db_user.id)
        if user is None or not user.is_trial_available:
            await cb.answer("Пробный период уже использован", show_alert=True)
            return
        days = int(await cfg.value(uow, "TRIAL_DURATION_DAYS"))
        traffic_gb = int(await cfg.value(uow, "TRIAL_TRAFFIC_GB"))
        devices = int(await cfg.value(uow, "TRIAL_DEVICE_LIMIT"))
        trial_price = int(await cfg.value(uow, "TRIAL_PRICE"))

        # Paid trial: charge the wallet (guarded) before provisioning.
        if trial_price > 0 and not await uow.users.debit_balance_guarded(user, trial_price):
            await cb.answer(
                f"Пробный стоит {trial_price / 100:.0f} ₽ — пополни баланс и повтори",
                show_alert=True,
            )
            return

        plan = await uow.plans.find_one(is_trial=True) or await uow.plans.find_one(name="Trial")
        if plan is not None and not plan.is_trial:
            plan.is_trial = True
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
        from src.application.events import TrialGranted

        await container.event_bus.publish(TrialGranted(user_id=user.id, subscription_id=sub.id))
        url = sub.subscription_url

    text = f"🎁 <b>Пробный период активирован: {days} дн.</b>"
    if url:
        text += f"\n\nСсылка подписки:\n<code>{url}</code>"
    await show_screen(
        cb,
        text,
        simple_keyboard([("👤 Моя подписка", "act:subscription:0"), ("‹ Меню", "nav:root")]),
    )
    await cb.answer("Готово!")


@router.callback_query(F.data.startswith("act:support"))
async def act_support(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    async with container.uow() as uow:
        cfg = container.bot_config
        mode = str(await cfg.value(uow, "SUPPORT_MODE"))
        redirect = str(await cfg.value(uow, "SUPPORT_REDIRECT_USERNAME") or "")
        support_bot = str(await cfg.value(uow, "SUPPORT_BOT_USERNAME") or "")
        miniapp_url = str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
    if mode == "bot" and support_bot:
        await show_screen(
            cb,
            "🆘 <b>Поддержка</b>\n\nНапиши в наш саппорт-бот — оператор ответит прямо там:",
            url_keyboard([("💬 Открыть поддержку", f"https://t.me/{support_bot.lstrip('@')}")]),
        )
        await cb.answer()
        return
    if mode == "redirect" and redirect:
        await show_screen(
            cb,
            "🆘 <b>Поддержка</b>\n\nНапиши нам — ответим быстро:",
            url_keyboard([("💬 Написать", f"https://t.me/{redirect.lstrip('@')}")]),
        )
        await cb.answer()
        return
    if mode == "miniapp" and miniapp_url.startswith("https://"):
        await show_screen(
            cb,
            "🆘 <b>Поддержка</b>\n\nОткрой чат поддержки в приложении:",
            InlineKeyboardMarkup(
                inline_keyboard=[[webapp_button("💬 Открыть поддержку", miniapp_url)]]
            ),
        )
        await cb.answer()
        return
    # tickets mode (default): hand off to the tickets FSM
    from src.bot.handlers.tickets import begin_ticket

    await begin_ticket(cb, container, db_user)


@router.message(Command("bug"))
async def cmd_bug(message: Message, container: AppContainer, db_user: User) -> None:
    """User bug report -> DM'd to admins with the reporter's handle."""
    text = (message.text or "").removeprefix("/bug").strip()
    if not text:
        await message.answer(
            "🐞 Опиши проблему одним сообщением: <code>/bug что не работает</code>"
        )
        return
    tg = message.from_user
    who = f"@{tg.username}" if tg and tg.username else f"id{db_user.telegram_id}"
    await container.notifier.notify_admins(f"🐞 Баг-репорт от {who}:\n{text}", topic="bug")
    await message.answer("🐞 Спасибо! Отправил разработчикам — разберёмся.")


@router.callback_query(F.data.startswith("act:devices"))
async def act_devices(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """HWID devices of the current subscription: list + one-tap unbind."""
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
    if sub is None or not sub.status.is_usable or sub.remnawave_uuid is None:
        await cb.answer("Сначала оформи подписку", show_alert=True)
        return
    try:
        devices = await container.remnawave_client.get_devices(sub.remnawave_uuid)
    except Exception:
        await cb.answer("Панель временно недоступна, попробуй позже", show_alert=True)
        return
    limit = f" (лимит {sub.device_limit})" if sub.device_limit else ""
    if not devices:
        text = f"<b>📱 Устройства{limit}</b>\n\nПока ни одно устройство не подключалось."
        kb = [[InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")]]
    else:
        lines = [f"<b>📱 Устройства{limit}</b>", "", "Нажми на устройство, чтобы отвязать:"]
        kb = []
        for d in devices[:10]:
            label = " · ".join(x for x in (d.platform, d.device_model) if x) or d.hwid[:12]
            kb.append(
                [InlineKeyboardButton(text=f"❌ {label}", callback_data=f"devdel:{d.hwid[:48]}")]
            )
        text = "\n".join(lines)
        kb.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
    await show_screen(cb, text, InlineKeyboardMarkup(inline_keyboard=kb))
    await safe_answer(cb)  # devdel chains here after answering


@router.callback_query(F.data.startswith("devdel:"))
async def devdel(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    hwid = (cb.data or "")[len("devdel:") :]
    async with container.uow() as uow:
        sub = (
            await uow.subscriptions.get(db_user.current_subscription_id)
            if db_user.current_subscription_id
            else None
        )
    if sub is None or sub.remnawave_uuid is None or not hwid:
        await cb.answer("Нет активной подписки", show_alert=True)
        return
    try:
        await container.remnawave_client.delete_device(sub.remnawave_uuid, hwid)
    except Exception:
        await cb.answer("Не получилось — попробуй позже", show_alert=True)
        return
    await cb.answer("Устройство отвязано ✅")
    await act_devices(cb, container, db_user)


@router.callback_query(F.data.startswith("act:nodes"))
async def act_nodes(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """User-facing server status: 🟢/🟠/🔴 per node with online counts."""
    from src.core.enums import ServerNodeStatus

    async with container.uow() as uow:
        if not bool(await container.bot_config.value(uow, "NODE_STATUS_ENABLED")):
            await cb.answer("Раздел недоступен", show_alert=True)
            return
        nodes = sorted(await uow.server_nodes.list(), key=lambda n: n.name)
    if not nodes:
        text = "🌍 <b>Статус серверов</b>\n\nДанные ещё не собраны."
    else:
        glyph = {
            ServerNodeStatus.ONLINE: "🟢",
            ServerNodeStatus.OFFLINE: "🔴",
            ServerNodeStatus.MAINTENANCE: "🟠",
        }
        lines = ["🌍 <b>Статус серверов</b>", ""]
        for n in nodes[:30]:
            flag = f"{n.country_code} " if n.country_code else ""
            lines.append(
                f"{glyph.get(n.status, '⚪')} {flag}{hesc(n.name)} · онлайн {n.users_online}"
            )
        text = "\n".join(lines)
    await show_screen(cb, text, simple_keyboard([("‹ Меню", "nav:root")]))
    await cb.answer()


@router.callback_query(F.data.startswith("act:proxy"))
async def act_proxy(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """MTProto proxy button: one tap connects Telegram through the owner's proxy."""
    async with container.uow() as uow:
        cfg = container.bot_config
        enabled = bool(await cfg.value(uow, "MTPROTO_PROXY_ENABLED"))
        raw = str(await cfg.value(uow, "MTPROTO_PROXY_URL") or "").strip()
    if not enabled or not raw:
        await cb.answer("Прокси не настроен", show_alert=True)
        return
    if raw.startswith("tg://proxy"):
        raw = "https://t.me/proxy" + raw.removeprefix("tg://proxy")
    elif raw.startswith("t.me/"):
        raw = "https://" + raw
    text = (
        "🔌 <b>MTProto-прокси</b>\n\nНажми кнопку — Telegram предложит подключить прокси. "
        "Работает даже там, где Telegram ограничен."
    )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔌 Подключить прокси", url=raw)],
            [InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")],
        ]
    )
    await show_screen(cb, text, markup)
    await cb.answer()


@router.callback_query(F.data.startswith("act:"))
async def act_unknown(cb: CallbackQuery, container: AppContainer, db_user: User) -> None:
    """Unknown/custom action codes fall back to the buy flow entry or menu."""
    action = (cb.data or "").split(":")[1] if ":" in (cb.data or "") else ""
    if action in ("buy", "shop", "plans"):
        from src.bot.handlers.purchase import open_buy

        await open_buy(cb, container, db_user)
        return
    log.info("unknown action", action=action)
    await send_main_menu(cb, container, db_user)
