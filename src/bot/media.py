"""Resolve a stored image reference into something aiogram can send.

Images reach the bot three ways: a Telegram ``file_id`` (set from the bot via /setlogo),
a public URL, or a local ``uploads/…`` path uploaded from the cabinet (served at /uploads).
The first two are sent as-is; a local file is wrapped in ``FSInputFile`` so the bot streams
it straight from disk — Telegram cannot fetch a server-local path.

A banner/screen media can be a still image OR an animation (GIF/MP4) — the cabinet accepts
both. Animations MUST be sent with ``send_animation``: ``send_photo`` shows only a still frame
for a .gif and outright rejects an .mp4. ``is_animated`` picks the right send method.
"""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.types import (
    ForceReply,
    FSInputFile,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

# Any keyboard aiogram accepts on a media send (inline menus + the reply bottom-bar).
_Markup = InlineKeyboardMarkup | ReplyKeyboardMarkup | ReplyKeyboardRemove | ForceReply

# Extensions Telegram treats as animations (sent via send_animation, not send_photo).
_ANIMATED_EXT = (".gif", ".mp4", ".webm")
# A Telegram animation file_id (from /setbanner on a GIF/MP4) has no extension, so its
# animated-ness can't be read off the string — /setbanner stores it with this marker prefix.
_ANIM_PREFIX = "animation:"


def photo_input(ref: str) -> str | FSInputFile:
    ref = ref.strip()
    if ref and not ref.startswith(("http://", "https://")) and Path(ref).is_file():
        return FSInputFile(ref)
    return ref


def is_animated(ref: str | FSInputFile | None) -> bool:
    """True for a GIF/MP4/WebM reference — a marked animation file_id, or a URL / local path /
    FSInputFile with an animated extension."""
    if ref is None:
        return False
    if isinstance(ref, str) and ref.startswith(_ANIM_PREFIX):
        return True
    name = str(ref.path) if isinstance(ref, FSInputFile) else str(ref)
    return name.lower().rsplit("?", 1)[0].endswith(_ANIMATED_EXT)


def media_input(ref: str | FSInputFile) -> str | FSInputFile:
    """Turn a stored media reference into a sendable input: strip the animation marker and
    wrap a local path in FSInputFile (a file_id / URL passes through)."""
    if isinstance(ref, FSInputFile):
        return ref
    return photo_input(ref[len(_ANIM_PREFIX) :] if ref.startswith(_ANIM_PREFIX) else ref)


async def answer_media(
    msg: Message,
    media: str | FSInputFile,
    *,
    caption: str | None = None,
    reply_markup: _Markup | None = None,
    parse_mode: str | None = "HTML",
) -> Message:
    """Reply with a photo or an animation, whichever the media is."""
    inp = media_input(media)
    if is_animated(media):
        return await msg.answer_animation(
            inp, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode
        )
    return await msg.answer_photo(
        inp,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )


async def send_media(
    bot: Bot,
    chat_id: int,
    media: str | FSInputFile,
    *,
    caption: str | None = None,
    reply_markup: _Markup | None = None,
    parse_mode: str | None = "HTML",
) -> Message:
    """Send a photo or an animation to a chat, whichever the media is."""
    inp = media_input(media)
    if is_animated(media):
        return await bot.send_animation(
            chat_id, inp, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode
        )
    return await bot.send_photo(
        chat_id,
        inp,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
