"""Notifier implementations.

The base ships a ``LogNotifier`` (the bot isn't built yet). When the aiogram bot lands, a
``TelegramNotifier`` implements the same protocol and routes admin messages per topic.
"""

from __future__ import annotations

from src.core.logging import get_logger

log = get_logger(__name__)


class LogNotifier:
    """Satisfies the Notifier protocol by logging. Swapped for a Telegram notifier later."""

    async def notify_user(self, telegram_id: int, text: str) -> None:
        log.info("notify_user", telegram_id=telegram_id, text=text)

    async def notify_admins(self, text: str, *, topic: str | None = None) -> None:
        log.info("notify_admins", topic=topic, text=text)
