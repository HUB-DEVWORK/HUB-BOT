"""Render the admin-built menu (or the built-in default when none is configured)."""

from __future__ import annotations

import contextlib

from aiogram.types import CallbackQuery, Message

from src.bot.default_menu import DEFAULT_MENU
from src.bot.keyboards import menu_keyboard, simple_keyboard, webapp_button
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer

# Built-in fallback menu until the admin builds one (screen 05). Derived from the same
# DEFAULT_MENU the "load default" constructor action seeds, so the two never drift.
_DEFAULT_BUTTONS: list[tuple[str, str]] = [(b.label, f"act:{b.action}:0") for b in DEFAULT_MENU]


async def send_main_menu(
    target: Message | CallbackQuery, container: AppContainer, db_user: User
) -> None:
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
        cfg = container.bot_config
        start_text = str(await cfg.value(uow, "START_MESSAGE"))
        miniapp_url = str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        welcome_image = str(await cfg.value(uow, "WELCOME_IMAGE") or "")
        welcome_sticker = str(await cfg.value(uow, "WELCOME_STICKER") or "")
        trial_enabled = bool(await cfg.value(uow, "TRIAL_ENABLED"))
        proxy_on = bool(await cfg.value(uow, "MTPROTO_PROXY_ENABLED")) and bool(
            await cfg.value(uow, "MTPROTO_PROXY_URL")
        )
        node_status_on = bool(await cfg.value(uow, "NODE_STATUS_ENABLED"))
        button_color = str(await cfg.value(uow, "BUTTON_COLOR_DEFAULT") or "") or None

    if nodes:
        markup = menu_keyboard(
            nodes, None, miniapp_url=miniapp_url or None, default_color=button_color
        )
    else:
        buttons = list(_DEFAULT_BUTTONS)
        buttons.insert(0, ("👤 Личный кабинет", "act:cabinet:0"))
        if trial_enabled and db_user.is_trial_available:
            buttons.insert(1, ("🎁 Попробовать бесплатно", "act:trial:0"))
        if proxy_on:
            buttons.append(("🔌 MTProto-прокси", "act:proxy:0"))
        if node_status_on:
            buttons.append(("🌍 Статус серверов", "act:nodes:0"))
        if db_user.role.is_staff:
            buttons.append(("🛠 Админка", "admin:menu"))
        markup = simple_keyboard(buttons, default_color=button_color)
        # Mini-app integration: a prominent WebApp button when the mini-app URL is configured.
        if miniapp_url.startswith("https://"):
            markup.inline_keyboard.insert(0, [webapp_button("📱 Открыть приложение", miniapp_url)])

    if isinstance(target, CallbackQuery):
        if target.message is not None:
            try:
                await target.message.edit_text(start_text, reply_markup=markup)  # type: ignore[union-attr,unused-ignore]
            except Exception:
                await target.message.answer(start_text, reply_markup=markup)
        await target.answer()
    else:
        # Fresh /start: show the configurable sticker or logo image above the menu.
        if welcome_sticker:
            with contextlib.suppress(Exception):  # bad file_id must not break /start
                await target.answer_sticker(welcome_sticker)
        elif welcome_image:
            with contextlib.suppress(Exception):
                await target.answer_photo(welcome_image)
        await target.answer(start_text, reply_markup=markup)
