"""Notifier + Translator protocols."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.core.enums import Locale


@runtime_checkable
class Translator(Protocol):
    """Resolves an i18n key to text in a locale (implemented by core.i18n.Translator)."""

    def gettext(self, key: str, locale: Locale | None = None, /, **kwargs: object) -> str: ...


@runtime_checkable
class Notifier(Protocol):
    """Sends a message to a user (Telegram) or an admin topic. Best-effort."""

    async def notify_user(self, telegram_id: int, text: str) -> None: ...

    async def notify_admins(self, text: str, *, topic: str | None = None) -> None: ...
