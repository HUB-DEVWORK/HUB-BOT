"""edit-or-send: the one safe way to put a screen in front of the user.

A screen can be triggered two ways: an inline-button tap (``CallbackQuery`` — there is a bot
message to edit/replace in place) or a reply-keyboard tap / command (``Message`` — nothing to
edit, always send fresh). Every helper here accepts either, so the same action handlers render
from both. ``edit_text``/``edit_media`` break on photo screens, 48h-old messages and unchanged
content; these helpers fall back to a fresh message and never raise (no eternal spinner).
"""

from __future__ import annotations

import contextlib
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InputMediaAnimation,
    InputMediaPhoto,
    Message,
)

from src.bot.media import is_animated, media_input, send_media

Target = CallbackQuery | Message

# Telegram caps photo captions at 1024 chars; longer screens render as plain text instead.
_CAPTION_LIMIT = 1024


def _origin(target: Target) -> tuple[Message | None, int | None, Bot | None]:
    """(editable bot message, chat_id, bot) for the trigger.

    A CallbackQuery carries the bot message that IS the current screen (edit/replace it). A
    plain Message is the user's own text (reply-keyboard/command) — nothing to edit, so the
    editable message is None and callers just send a fresh screen.
    """
    if isinstance(target, CallbackQuery):
        msg = target.message if isinstance(target.message, Message) else None
        chat_id = msg.chat.id if msg else (target.from_user.id if target.from_user else None)
        return msg, chat_id, target.bot
    return None, target.chat.id, target.bot


async def show_screen(
    target: Target,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    """Edit the current screen in place (callback) or send a fresh one (message)."""
    msg, chat_id, bot = _origin(target)
    if msg is not None:
        try:
            await msg.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                return
            # "no text in the message to edit" (photo screen) etc. -> send fresh below
    if chat_id is None or bot is None:
        return
    await bot.send_message(chat_id, text, reply_markup=markup, parse_mode=parse_mode)
    if msg is not None:
        with contextlib.suppress(Exception):  # old screen may already be gone
            await msg.delete()


async def show_photo_screen(
    target: Target,
    photo: object,
    caption: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send a fresh photo screen (banner + caption + buttons) and drop the previous one.

    Telegram can't edit a text message into a photo, so banner screens are re-sent. On any
    delivery error the caption is sent as plain, tag-stripped text so the flow never breaks.
    """
    msg, chat_id, bot = _origin(target)
    if chat_id is None or bot is None:
        return
    try:
        # send_media picks send_photo vs send_animation, so a GIF/MP4 banner actually animates
        # (send_photo would freeze a .gif to one frame and reject an .mp4 outright).
        await send_media(
            bot,
            chat_id,
            photo,  # type: ignore[arg-type]
            caption=caption[:_CAPTION_LIMIT],
            reply_markup=markup,
            parse_mode="HTML",
        )
    except Exception:
        # Last resort: strip tags and send plain, so even a caption Telegram rejected for bad
        # HTML entities still delivers instead of raising (the real "never breaks" safety net).
        plain = re.sub(r"<[^>]+>", "", caption)
        await bot.send_message(chat_id, plain, reply_markup=markup)
    if msg is not None:
        with contextlib.suppress(Exception):
            await msg.delete()


async def show_media_screen(
    target: Target,
    photo: object | None,
    caption: str,
    markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Render a banner screen the smooth way.

    No banner (photo is None) or a caption too long for a photo -> a plain text screen. When
    the current message is already a photo, the image + caption + buttons are swapped in place
    (no flicker); otherwise a fresh photo is sent and the old message dropped.
    """
    if photo is None or len(caption) > _CAPTION_LIMIT:
        await show_screen(target, caption, markup)
        return
    msg, _chat_id, _bot = _origin(target)
    # Edit in place only when the current message already carries media (photo OR animation);
    # editMessageMedia can swap between the two, so a GIF banner edits cleanly over a photo.
    if msg is not None and (msg.photo or msg.animation):
        inp = media_input(photo)  # type: ignore[arg-type]
        media = (
            InputMediaAnimation(media=inp, caption=caption, parse_mode="HTML")
            if is_animated(photo)  # type: ignore[arg-type]
            else InputMediaPhoto(media=inp, caption=caption, parse_mode="HTML")
        )
        try:
            await msg.edit_media(media, reply_markup=markup)
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc):
                with contextlib.suppress(Exception):  # only the keyboard changed
                    await msg.edit_reply_markup(reply_markup=markup)
                return
            # otherwise fall through to a fresh send
        except Exception:
            pass  # network/other edit failure -> fresh send below (never a stuck spinner)
    await show_photo_screen(target, photo, caption, markup)


async def ack(target: Target, text: str | None = None, *, alert: bool = False) -> None:
    """Acknowledge the trigger: dismiss a callback's spinner (optionally a toast/alert); for a
    reply-keyboard/command Message, send ``text`` as a message when given, else do nothing."""
    with contextlib.suppress(Exception):
        if isinstance(target, CallbackQuery):
            await target.answer(text, show_alert=alert)
        elif text:
            await target.answer(text)


async def safe_answer(target: Target, text: str | None = None) -> None:
    """Answer a trigger that may already be answered (chained handlers). No-op for a Message."""
    await ack(target, text)
