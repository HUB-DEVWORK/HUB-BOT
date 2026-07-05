"""Telegram bot settings. The bot itself is built later; the base only needs config + FSM."""

from __future__ import annotations

from pydantic import BaseModel


class BotSettings(BaseModel):
    token: str = ""
    use_webhook: bool = False
    webhook_base: str = ""  # https://your.domain
    webhook_secret: str = ""  # secret-token header (verified constant-time)

    @property
    def webhook_url(self) -> str:
        return f"{self.webhook_base.rstrip('/')}/webhook/telegram"
