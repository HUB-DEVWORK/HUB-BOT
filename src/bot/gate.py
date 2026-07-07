"""Channel-subscription gate: require joining channel(s) before key actions (#1).

Config: ``CHANNEL_SUB_REQUIRED`` (bool) + ``CHANNEL_SUB_CHANNELS`` (one channel per line,
``@channel | Title | link``; legacy ``CHANNEL_SUB_ID`` is merged in). The bot must be an
admin of each channel. If it cannot read a channel (not an admin / bad id), that channel
fails OPEN — a misconfiguration never locks users out of buying.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.core.logging import get_logger
from src.infrastructure.di import AppContainer

log = get_logger(__name__)

_OK_STATUSES = {"creator", "administrator", "member"}


@dataclass(frozen=True, slots=True)
class Channel:
    ref: str  # @username or -100… id used for get_chat_member
    title: str
    url: str


def _channel_url(ref: str) -> str:
    if ref.startswith("http"):
        return ref
    return f"https://t.me/{ref.lstrip('@')}"


def parse_channels(raw: str, legacy_id: str) -> list[Channel]:
    """Parse the «@channel | Title | link» lines (plus a legacy single id)."""
    out: list[Channel] = []
    seen: set[str] = set()
    lines = list(raw.splitlines())
    if legacy_id.strip():
        lines.insert(0, legacy_id.strip())
    for line in lines:
        parts = [p.strip() for p in line.split("|")]
        ref = parts[0]
        if not ref or ref in seen:
            continue
        seen.add(ref)
        title = parts[1] if len(parts) > 1 and parts[1] else "Канал"
        url = parts[2] if len(parts) > 2 and parts[2] else _channel_url(ref)
        out.append(Channel(ref=ref, title=title, url=url))
    return out


async def missing_channels(
    event: Message | CallbackQuery, container: AppContainer, *, scope: str
) -> list[Channel]:
    """Channels the user has NOT joined. Empty when the gate is off or scope-skipped."""
    async with container.uow() as uow:
        required = bool(await container.bot_config.value(uow, "CHANNEL_SUB_REQUIRED"))
        raw = str(await container.bot_config.value(uow, "CHANNEL_SUB_CHANNELS") or "")
        legacy = str(await container.bot_config.value(uow, "CHANNEL_SUB_ID") or "")
        gate_scope = str(await container.bot_config.value(uow, "CHANNEL_SUB_SCOPE") or "all")
    if not required or event.from_user is None or event.bot is None:
        return []
    if gate_scope not in ("all", scope):
        return []
    channels = parse_channels(raw, legacy)
    missing: list[Channel] = []
    for ch in channels:
        try:
            member = await event.bot.get_chat_member(ch.ref, event.from_user.id)
        except Exception:
            log.warning("channel_gate_check_failed", channel=ch.ref, exc_info=True)
            continue  # fail open per channel
        subscribed = member.status in _OK_STATUSES or (
            member.status == "restricted" and getattr(member, "is_member", False)
        )
        if not subscribed:
            missing.append(ch)
    return missing


async def ensure_channel(
    event: Message | CallbackQuery, container: AppContainer, *, scope: str = "all"
) -> bool:
    """True if the user may proceed; otherwise show the join screen and return False."""
    missing = await missing_channels(event, container, scope=scope)
    if not missing:
        return True

    rows = [[InlineKeyboardButton(text=f"📢 {ch.title}", url=ch.url)] for ch in missing[:8]]
    rows.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check:sub")])
    rows.append([InlineKeyboardButton(text="‹ Меню", callback_data="nav:root")])
    text = (
        "Чтобы продолжить, подпишись на наш канал 👇"
        if len(missing) == 1
        else "Чтобы продолжить, подпишись на наши каналы 👇"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if isinstance(event, CallbackQuery):
        if event.message is not None:
            try:
                await event.message.edit_text(text, reply_markup=markup)  # type: ignore[union-attr]
            except Exception:
                await event.message.answer(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)
    return False
