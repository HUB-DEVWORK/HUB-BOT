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


async def show_photo_screen(
    cb: CallbackQuery,
    photo: object,
    caption: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send a fresh photo screen (banner + caption + buttons) and drop the previous one.

    Telegram can't edit a text message into a photo, so banner screens are re-sent. On any
    delivery error the caption is sent as a plain text screen so the flow never breaks.
    """
    msg = cb.message if isinstance(cb.message, Message) else None
    chat_id = msg.chat.id if msg is not None else (cb.from_user.id if cb.from_user else None)
    if chat_id is None or cb.bot is None:
        return
    try:
        await cb.bot.send_photo(
            chat_id,
            photo,  # type: ignore[arg-type]
            caption=caption[:1024],  # Telegram caps photo captions at 1024 chars
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception:
        await cb.bot.send_message(chat_id, caption, reply_markup=markup, parse_mode="HTML")
    if msg is not None:
        with contextlib.suppress(Exception):
            await msg.delete()


async def safe_answer(cb: CallbackQuery, text: str | None = None) -> None:
    """Answer a callback that may already be answered (chained handlers)."""
    with contextlib.suppress(Exception):
        await cb.answer(text)
