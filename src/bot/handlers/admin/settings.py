"""Quick config toggles (bot-config booleans, hot-reload)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.bot.handlers.admin._common import MENU_CB
from src.bot.screen import safe_answer, show_screen
from src.infrastructure.di import AppContainer

router = Router(name="admin-settings")

_TOGGLES: list[tuple[str, str]] = [
    ("MAINTENANCE_MODE", "Техработы"),
    ("TRIAL_ENABLED", "Триал"),
    ("CHANNEL_SUB_REQUIRED", "Канал-лок"),
    ("AUTO_RENEWAL_ENABLED", "Автопродление"),
    ("REFERRAL_ENABLED", "Рефералка"),
    ("BALANCE_ENABLED", "Оплата с баланса"),
]
_TOGGLE_KEYS = {k for k, _ in _TOGGLES}


@router.callback_query(F.data == "admin:settings")
async def admin_settings(cb: CallbackQuery, container: AppContainer) -> None:
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
    rows.append([InlineKeyboardButton(text="‹ Назад", callback_data=MENU_CB)])
    await show_screen(cb, "⚙️ <b>Быстрые настройки</b>", InlineKeyboardMarkup(inline_keyboard=rows))
    await safe_answer(cb)  # admin_toggle chains here after answering


@router.callback_query(F.data.startswith("admin:toggle:"))
async def admin_toggle(cb: CallbackQuery, container: AppContainer) -> None:
    key = (cb.data or "").split(":", 2)[2]
    if key not in _TOGGLE_KEYS:
        await cb.answer()
        return
    async with container.uow() as uow:
        current = bool(await container.bot_config.value(uow, key))
        await container.bot_config.set_values(uow, {key: not current})
        await uow.commit()
    await cb.answer("Переключено")
    await admin_settings(cb, container)
