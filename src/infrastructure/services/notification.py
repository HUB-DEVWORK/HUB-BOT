"""Notifier implementations.

``LogNotifier`` is the no-op fallback (no bot token). ``TelegramNotifier`` delivers real
Telegram messages to users and admins via a lazily-created aiogram ``Bot`` on the bot token.
Both satisfy ``application.common.notifier.Notifier`` and are best-effort — a delivery failure
is logged, never raised.
"""

from __future__ import annotations

from collections.abc import Iterable

from aiogram import Bot

from src.core.logging import get_logger

log = get_logger(__name__)


class LogNotifier:
    """Satisfies the Notifier protocol by logging. Used when no bot token is configured."""

    async def notify_user(self, telegram_id: int, text: str) -> None:
        log.info("notify_user", telegram_id=telegram_id, text=text)

    async def notify_admins(self, text: str, *, topic: str | None = None) -> None:
        log.info("notify_admins", topic=topic, text=text)

    async def notify_admins_document(self, document: object, *, caption: str | None = None) -> None:
        log.info("notify_admins_document", caption=caption)

    async def aclose(self) -> None:  # symmetry with TelegramNotifier
        return None


class TelegramNotifier:
    """Real Telegram delivery. Best-effort: failures are logged, not raised."""

    def __init__(self, token: str, admin_ids: Iterable[int]) -> None:
        self._token = token
        self._admin_ids = tuple(admin_ids)
        self._bot: Bot | None = None

    def _get_bot(self) -> Bot:
        if self._bot is None:
            self._bot = Bot(token=self._token)
        return self._bot

    async def notify_user(self, telegram_id: int, text: str) -> None:
        try:
            await self._get_bot().send_message(telegram_id, text)
        except Exception:
            log.warning("notify_user_failed", telegram_id=telegram_id, exc_info=True)

    async def notify_admins(self, text: str, *, topic: str | None = None) -> None:
        prefix = f"[{topic}] " if topic else ""
        for admin_id in self._admin_ids:
            try:
                await self._get_bot().send_message(admin_id, prefix + text)
            except Exception:
                log.warning("notify_admin_failed", admin_id=admin_id, exc_info=True)

    async def notify_admins_document(self, document: object, *, caption: str | None = None) -> None:
        for admin_id in self._admin_ids:
            try:
                await self._get_bot().send_document(admin_id, document, caption=caption)  # type: ignore[arg-type]
            except Exception:
                log.warning("notify_admin_doc_failed", admin_id=admin_id, exc_info=True)

    async def aclose(self) -> None:
        if self._bot is not None:
            await self._bot.session.close()
            self._bot = None
