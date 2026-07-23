"""Render the admin-built menu (or the built-in default when none is configured)."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

from aiogram.types import CallbackQuery, Message

from src.bot.banners import banner_for
from src.bot.default_menu import SMART_EXTRAS
from src.bot.keyboards import (
    default_menu_markup,
    default_reply_markup,
    menu_keyboard,
    reply_menu_markup,
    simple_keyboard,
    webapp_button,
)
from src.bot.media import answer_media, send_media
from src.bot.screen import show_media_screen
from src.infrastructure.database.models.menu_node import MenuNode
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer


async def send_main_menu(
    target: Message | CallbackQuery, container: AppContainer, db_user: User
) -> None:
    async with container.uow() as uow:
        nodes = list(await uow.menu_nodes.tree())
        cfg = container.bot_config
        start_text = str(await cfg.value(uow, "START_MESSAGE"))
        miniapp_url = str(await cfg.value(uow, "SUBSCRIPTION_MINI_APP_URL") or "")
        welcome_sticker = str(await cfg.value(uow, "WELCOME_STICKER") or "")
        trial_enabled = bool(await cfg.value(uow, "TRIAL_ENABLED"))
        proxy_on = bool(await cfg.value(uow, "MTPROTO_PROXY_ENABLED")) and bool(
            await cfg.value(uow, "MTPROTO_PROXY_URL")
        )
        node_status_on = bool(await cfg.value(uow, "NODE_STATUS_ENABLED"))
        button_color = str(await cfg.value(uow, "BUTTON_COLOR_DEFAULT") or "") or None
        menu_mode = str(await cfg.value(uow, "MAIN_MENU_MODE") or "inline")

    # A constructor-placed «Пробный период» button (action=trial) is rendered unconditionally,
    # so once the user has used their trial (or trials are off) it lingers as a dead-end. Drop
    # trial action nodes when the user is no longer eligible — mirrors the smart-shortcut gate
    # below, which already only appears while the trial is available.
    if not (trial_enabled and db_user.is_trial_available):
        nodes = [n for n in nodes if not (n.kind.value == "action" and n.payload == "trial")]

    # Runtime "smart" shortcuts (trial/proxy/nodes) applicable for this shop + user — shared by
    # the inline menu and the reply bottom-bar so they never drift (default_menu.SMART_EXTRAS).
    tree_actions = {n.payload for n in nodes if n.kind.value == "action"}
    extra_label = {code: label for label, code in SMART_EXTRAS}
    applicable: list[str] = []
    if trial_enabled and db_user.is_trial_available and "trial" not in tree_actions:
        applicable.append("trial")
    if proxy_on and "proxy" not in tree_actions:
        applicable.append("proxy")
    if node_status_on and "nodes" not in tree_actions:
        applicable.append("nodes")

    # Reply mode: the main menu is a persistent bottom-bar (reply keyboard) under the input,
    # with the owner's buttons + smart shortcuts + a one-tap mini-app button. Screens stay inline.
    if menu_mode == "reply":
        await _send_reply_menu(
            target,
            container,
            nodes,
            start_text,
            miniapp_url,
            welcome_sticker,
            [extra_label[c] for c in applicable],
        )
        return

    has_miniapp_node = any(n.kind.value == "miniapp" for n in nodes)
    extras: list[tuple[str, str]] = [(extra_label[c], f"act:{c}:0") for c in applicable]
    if db_user.role.is_staff:
        extras.append(("🛠 Админка", "admin:menu"))

    if nodes:
        markup = menu_keyboard(
            nodes, None, miniapp_url=miniapp_url or None, default_color=button_color
        )
    else:
        markup = default_menu_markup(button_color)

    if extras:
        markup.inline_keyboard.extend(
            simple_keyboard(extras, columns=2, default_color=button_color).inline_keyboard
        )
    # Prominent mini-app CTA on top, unless the owner already placed a mini-app button.
    if miniapp_url.startswith("https://") and not has_miniapp_node:
        markup.inline_keyboard.insert(0, [webapp_button("📱 Открыть приложение", miniapp_url)])

    photo = await banner_for(container, "menu")
    if isinstance(target, CallbackQuery):
        # Smooth in-place swap (edit media/caption) when navigating back to the menu; falls
        # back to a fresh send + delete of the old card, so banners never pile up (NAV-1).
        await show_media_screen(target, photo, start_text, markup)
        await target.answer()
    else:
        # Fresh /start: optional decorative sticker, then the menu as a single banner message.
        if welcome_sticker:
            with contextlib.suppress(Exception):  # bad file_id must not break /start
                await target.answer_sticker(welcome_sticker)
        if photo is not None:
            with contextlib.suppress(Exception):
                # answer_media animates a GIF/MP4 banner (send_photo would freeze/reject it).
                await answer_media(
                    target, photo, caption=start_text, reply_markup=markup, parse_mode="HTML"
                )
                return
        with contextlib.suppress(Exception):  # HTML like every other render; plain on bad entities
            await target.answer(start_text, reply_markup=markup, parse_mode="HTML")
            return
        await target.answer(start_text, reply_markup=markup)


async def _send_reply_menu(
    target: Message | CallbackQuery,
    container: AppContainer,
    nodes: list[MenuNode],
    start_text: str,
    miniapp_url: str,
    welcome_sticker: str,
    extras: Sequence[str],
) -> None:
    """Send the banner + persistent bottom-bar. Reply keyboards can't be attached by editing,
    so this always sends a fresh message (the bar then stays until replaced)."""
    reply_kb = (
        reply_menu_markup(nodes, miniapp_url=miniapp_url or None, extras=extras) if nodes else None
    ) or default_reply_markup(miniapp_url or None, extras)
    photo = await banner_for(container, "menu")
    if isinstance(target, CallbackQuery):
        with contextlib.suppress(Exception):
            await target.answer()
        bot = target.bot
        chat_id = (
            target.message.chat.id
            if isinstance(target.message, Message)
            else (target.from_user.id if target.from_user else None)
        )
    else:
        bot = target.bot
        chat_id = target.chat.id
        if welcome_sticker:
            with contextlib.suppress(Exception):  # bad file_id must not break /start
                await target.answer_sticker(welcome_sticker)
    if bot is None or chat_id is None:
        return
    if photo is not None:
        with contextlib.suppress(Exception):
            await send_media(
                bot, chat_id, photo, caption=start_text, reply_markup=reply_kb, parse_mode="HTML"
            )
            return
    with contextlib.suppress(Exception):
        await bot.send_message(chat_id, start_text, reply_markup=reply_kb, parse_mode="HTML")
        return
    await bot.send_message(chat_id, start_text, reply_markup=reply_kb)
