"""In-bot administration (text-bot style). Every handler is guarded by ``is_admin`` (set by
ContextMiddleware from ADMIN_IDS / APP__OWNER_IDS / user role).

Full management lives in the web cabinet; this gives quick stats, toggles and branding from
the chat itself.
"""

from __future__ import annotations

from html import escape as hesc

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import and_, func, or_, select

from src.bot.banners import SCREEN_KEYS, banner_config_key
from src.bot.screen import safe_answer, show_screen
from src.core.enums import SubscriptionStatus, TransactionStatus, TransactionType
from src.infrastructure.database.base import utcnow
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.di import AppContainer

router = Router(name="admin")

# Bot-config booleans exposed as one-tap toggles in the admin panel.
_TOGGLES: list[tuple[str, str]] = [
    ("MAINTENANCE_MODE", "Техработы"),
    ("TRIAL_ENABLED", "Триал"),
    ("CHANNEL_SUB_REQUIRED", "Канал-лок"),
    ("AUTO_RENEWAL_ENABLED", "Автопродление"),
    ("REFERRAL_ENABLED", "Рефералка"),
    ("BALANCE_ENABLED", "Оплата с баланса"),
]
_TOGGLE_KEYS = {k for k, _ in _TOGGLES}


def _admin_menu(admin_url: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="📈 Аналитика", callback_data="admin:analytics")],
        [InlineKeyboardButton(text="⚙️ Быстрые настройки", callback_data="admin:settings")],
        [InlineKeyboardButton(text="🖼 Лого / стикер", callback_data="admin:brand")],
    ]
    if admin_url.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🌐 Веб-админка", url=admin_url)])
    rows.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _render_menu(cb: CallbackQuery, container: AppContainer) -> None:
    async with container.uow() as uow:
        admin_url = str(await container.bot_config.value(uow, "ADMIN_PANEL_URL") or "")
    await show_screen(cb, "🛠 <b>Админ-панель</b>", _admin_menu(admin_url))
    await cb.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        return
    async with container.uow() as uow:
        admin_url = str(await container.bot_config.value(uow, "ADMIN_PANEL_URL") or "")
    await message.answer(
        "🛠 <b>Админ-панель</b>", reply_markup=_admin_menu(admin_url), parse_mode="HTML"
    )


@router.message(Command("resetmenu"))
async def cmd_resetmenu(message: Message, container: AppContainer, is_admin: bool) -> None:
    """Reset the bot menu to the lean built-in default (admin only)."""
    if not is_admin:
        return
    from src.web.routes.admin.menu import _default_menu_rows

    async with container.uow() as uow:
        await uow.menu_nodes.delete_by()
        for row in _default_menu_rows():
            await uow.menu_nodes.add(row)
        await uow.commit()
    await message.answer("✅ Меню сброшено к базовому. Открой /start — увидишь новый вид.")


@router.callback_query(F.data == "admin:menu")
async def admin_menu(cb: CallbackQuery, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        await cb.answer()
        return
    await _render_menu(cb, container)


@router.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        await cb.answer()
        return
    async with container.uow() as uow:
        users = await uow.users.count()
        active = int(
            await uow.session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.ACTIVE,
                            SubscriptionStatus.TRIAL,
                            SubscriptionStatus.LIMITED,
                        ]
                    )
                )
            )
            or 0
        )
        day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        revenue = int(
            await uow.session.scalar(
                select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                    Transaction.status == TransactionStatus.COMPLETED,
                    # Only external money: a balance purchase (gateway_type NULL) was
                    # already counted as revenue when the deposit came in.
                    or_(
                        Transaction.type == TransactionType.DEPOSIT,
                        and_(
                            Transaction.type == TransactionType.SUBSCRIPTION_PAYMENT,
                            Transaction.gateway_type.is_not(None),
                        ),
                    ),
                    Transaction.created_at >= day_start,
                )
            )
            or 0
        )
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: <b>{users}</b>\n"
        f"Активных подписок: <b>{active}</b>\n"
        f"Выручка сегодня: <b>{revenue / 100:.0f} ₽</b>"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="admin:menu")]]
    )
    await show_screen(cb, text, kb)
    await cb.answer()


def _rub(minor: int) -> str:
    return f"{minor / 100:,.0f} ₽".replace(",", " ")


@router.callback_query(F.data == "admin:analytics")
async def admin_analytics(cb: CallbackQuery, container: AppContainer, is_admin: bool) -> None:
    """Funnel + revenue + ARPU + retention + top sources, in one text digest."""
    if not is_admin:
        await cb.answer()
        return
    from src.infrastructure.services import analytics as svc

    async with container.uow() as uow:
        data = await svc.full(uow)
    o, r, s = data["overview"], data["retention"], data["sources"]
    lines = [
        "📈 <b>Аналитика</b>",
        "",
        f"👥 Пользователей: <b>{o['users']}</b> "
        f"(+{o['new_today']} сегодня · +{o['new_week']} за 7д)",
        "",
        "🎯 <b>Воронка</b>",
        f"├ Триал: <b>{o['ever_trial']}</b> → оплатили: <b>{o['paid_users']}</b> "
        f"({o['trial_to_paid_pct']}%)",
        f"├ Активных: <b>{o['active_subscriptions']}</b> · на триале: <b>{o['on_trial']}</b>",
        f"└ Конверсия в оплату: <b>{o['conversion_paid_pct']}%</b>",
        "",
        "💰 <b>Выручка</b>",
        f"├ Сегодня: <b>{_rub(o['revenue_today_minor'])}</b> · "
        f"7д: <b>{_rub(o['revenue_week_minor'])}</b>",
        f"├ 30д: <b>{_rub(o['revenue_month_minor'])}</b> · "
        f"всего: <b>{_rub(o['revenue_minor'])}</b>",
        f"└ ARPU {_rub(o['arpu_minor'])} · ARPPU {_rub(o['arppu_minor'])} · "
        f"чек {_rub(o['avg_check_minor'])}",
        "",
        f"🔁 <b>Удержание</b>: 7д {r['d7']['pct']}% ({r['d7']['retained']}/{r['d7']['cohort']}) · "
        f"30д {r['d30']['pct']}% ({r['d30']['retained']}/{r['d30']['cohort']})",
    ]
    if s["campaigns"]:
        lines += ["", "🚀 <b>Источники (ТОП)</b>"]
        lines += [
            f"• {hesc(c['name'])}: {c['users']} юз · {_rub(c['revenue_minor'])} · "
            f"ROI {_rub(c['roi_minor'])}"
            for c in s["campaigns"]
        ]
    if s["referrers"]:
        lines += ["", "🤝 <b>ТОП рефереров</b>"]
        lines += [f"• {hesc(ref['label'])}: {ref['invited']}" for ref in s["referrers"]]
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="admin:menu")]]
    )
    await show_screen(cb, "\n".join(lines), kb)
    await cb.answer()


@router.callback_query(F.data == "admin:settings")
async def admin_settings(cb: CallbackQuery, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        await cb.answer()
        return
    async with container.uow() as uow:
        states = {k: bool(await container.bot_config.value(uow, k)) for k, _ in _TOGGLES}
    rows = [
        [
            InlineKeyboardButton(
                text=f"{label}: {'✅' if states[k] else '❌'}", callback_data=f"admin:toggle:{k}"
            )
        ]
        for k, label in _TOGGLES
    ]
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data="admin:menu")])
    await show_screen(cb, "⚙️ <b>Быстрые настройки</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(cb)  # admin_toggle chains here after answering


@router.callback_query(F.data.startswith("admin:toggle:"))
async def admin_toggle(cb: CallbackQuery, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        await cb.answer()
        return
    key = (cb.data or "").split(":", 2)[2]
    if key not in _TOGGLE_KEYS:
        await cb.answer()
        return
    async with container.uow() as uow:
        current = bool(await container.bot_config.value(uow, key))
        await container.bot_config.set_values(uow, {key: not current})
        await uow.commit()
    await cb.answer("Переключено")
    await admin_settings(cb, container, is_admin)


@router.callback_query(F.data == "admin:brand")
async def admin_brand(cb: CallbackQuery, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        await cb.answer()
        return
    text = (
        "🖼 <b>Оформление</b>\n\n"
        "• Лого: <code>/setlogo</code> ответом на фото (убрать — <code>/dellogo</code>).\n"
        "• Стикер: <code>/setsticker</code> на стикер (снять — <code>/delsticker</code>).\n"
        "• Баннер экрана: <code>/setbanner экран</code> ответом на фото "
        "(<code>/setbanner</code> без имени — для всех; убрать — <code>/delbanner</code>).\n"
        f"  Экраны: {', '.join(SCREEN_KEYS)}.\n"
        "• Кнопки/цвета/меню — в веб-админке → «Конструктор меню»."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="admin:menu")]]
    )
    await show_screen(cb, text, kb)
    await cb.answer()


# --- branding commands ---------------------------------------------------------


@router.message(Command("setlogo"))
async def set_logo(message: Message, container: AppContainer, is_admin: bool) -> None:
    """Set the /start logo: reply to a photo with /setlogo (or send a photo captioned /setlogo)."""
    if not is_admin:
        return
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.photo)
        else message
    )
    if not source.photo:
        await message.answer("Пришли /setlogo ответом на фото (или фото с подписью /setlogo).")
        return
    await _set_config(container, "WELCOME_IMAGE", source.photo[-1].file_id)
    await message.answer("✅ Лого обновлено — проверь /start.")


@router.message(Command("dellogo"))
async def del_logo(message: Message, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        return
    await _set_config(container, "WELCOME_IMAGE", "")
    await message.answer("Лого убрано.")


@router.message(Command("setsticker"))
async def set_sticker(message: Message, container: AppContainer, is_admin: bool) -> None:
    """Set the /start sticker: reply to a sticker with /setsticker."""
    if not is_admin:
        return
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.sticker)
        else message
    )
    if source.sticker is None:
        await message.answer("Пришли /setsticker ответом на стикер.")
        return
    await _set_config(container, "WELCOME_STICKER", source.sticker.file_id)
    await message.answer("✅ Стикер обновлён — проверь /start.")


@router.message(Command("delsticker"))
async def del_sticker(message: Message, container: AppContainer, is_admin: bool) -> None:
    if not is_admin:
        return
    await _set_config(container, "WELCOME_STICKER", "")
    await message.answer("Стикер убран.")


def _screen_arg(command: CommandObject) -> str:
    """First word of the command args, lowercased; empty -> 'default'."""
    parts = (command.args or "").strip().lower().split()
    return parts[0] if parts else "default"


@router.message(Command("setbanner"))
async def set_banner(
    message: Message, command: CommandObject, container: AppContainer, is_admin: bool
) -> None:
    """Set a screen banner: reply to a photo with /setbanner <экран> (пусто = по умолчанию)."""
    if not is_admin:
        return
    source = (
        message.reply_to_message
        if (message.reply_to_message and message.reply_to_message.photo)
        else message
    )
    if not source.photo:
        await message.answer(
            "Пришли <code>/setbanner экран</code> ответом на фото (или фото с такой подписью).\n"
            f"Экраны: {', '.join(SCREEN_KEYS)}. Пусто — баннер по умолчанию для всех.",
            parse_mode="HTML",
        )
        return
    key = banner_config_key(_screen_arg(command))
    await _set_config(container, key, source.photo[-1].file_id)
    await message.answer(f"✅ Баннер <code>{key}</code> обновлён.", parse_mode="HTML")


@router.message(Command("delbanner"))
async def del_banner(
    message: Message, command: CommandObject, container: AppContainer, is_admin: bool
) -> None:
    """Remove a screen banner: /delbanner <экран> (пусто = баннер по умолчанию)."""
    if not is_admin:
        return
    key = banner_config_key(_screen_arg(command))
    await _set_config(container, key, "")
    await message.answer(f"Баннер <code>{key}</code> убран.", parse_mode="HTML")


async def _set_config(container: AppContainer, key: str, value: str) -> None:
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {key: value})
        await uow.commit()
