"""In-bot administration (text-bot style). Every handler is guarded by ``is_admin`` (set by
ContextMiddleware from ADMIN_IDS / APP__OWNER_IDS / user role).

Full management lives in the web cabinet; this gives quick stats, toggles and branding from
the chat itself.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import and_, func, or_, select

from src.bot.screen import show_screen
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
    await cb.answer()


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


async def _set_config(container: AppContainer, key: str, value: str) -> None:
    async with container.uow() as uow:
        await container.bot_config.set_values(uow, {key: value})
        await uow.commit()
