"""Render the admin-built menu (or the built-in default when none is configured)."""

from __future__ import annotations

from aiogram.types import CallbackQuery, Message

from src.bot.keyboards import menu_keyboard, simple_keyboard
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

# Built-in fallback menu until the admin constructs one (screen 05).
_DEFAULT_BUTTONS: list[tuple[str, str]] = [
    ("🛒 Купить VPN", "act:buy:0"),
    ("👤 Моя подписка", "act:subscription:0"),
    ("💰 Баланс", "act:balance:0"),
    ("🎁 Пригласить друга", "act:referral:0"),
    ("🆘 Поддержка", "act:support:0"),
]


async def send_main_menu(
    target: Message | CallbackQuery, container: AppContainer, db_user: User
) -> None:
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
        cfg = container.bot_config
        start_text = str(await cfg.value(uow, "START_MESSAGE"))
        miniapp_url = str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        trial_enabled = bool(await cfg.value(uow, "TRIAL_ENABLED"))

    if nodes:
        markup = menu_keyboard(nodes, None, miniapp_url=miniapp_url or None)
    else:
        buttons = list(_DEFAULT_BUTTONS)
        if trial_enabled and db_user.is_trial_available:
            buttons.insert(1, ("🎁 Попробовать бесплатно", "act:trial:0"))
        markup = simple_keyboard(buttons)

    if isinstance(target, CallbackQuery):
        if target.message is not None:
            try:
                await target.message.edit_text(start_text, reply_markup=markup)  # type: ignore[union-attr,unused-ignore]
            except Exception:
                await target.message.answer(start_text, reply_markup=markup)
        await target.answer()
    else:
        await target.answer(start_text, reply_markup=markup)
