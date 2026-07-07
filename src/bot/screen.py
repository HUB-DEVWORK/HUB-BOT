"""edit-or-send: the one safe way to replace a callback's screen.

``cb.message.edit_text`` breaks in three real-world cases the handlers kept hitting:
photo screens (no text to edit), messages older than 48h (InaccessibleMessage without
methods), and unchanged content. All three used to leave the user with an eternal
spinner. This helper falls back to a fresh message and never raises.
"""

from __future__ import annotations

import contextlib

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


async def show_screen(
    cb: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    """Edit the callback's message in place; fall back to sending a new one."""
    msg = cb.message if isinstance(cb.message, Message) else None
    if msg is not None:
        try:
            await msg.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            # "no text in the message to edit" (photo screen) etc. -> send fresh below
    chat_id = msg.chat.id if msg is not None else (cb.from_user.id if cb.from_user else None)
    if chat_id is None or cb.bot is None:
        return
    await cb.bot.send_message(chat_id, text, reply_markup=markup, parse_mode=parse_mode)
    if msg is not None:
        with contextlib.suppress(Exception):  # old screen may already be gone
            await msg.delete()
