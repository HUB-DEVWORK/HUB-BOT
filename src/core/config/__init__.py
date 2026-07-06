"""Composed application settings.

Loads from environment / ``.env`` with ``__`` as the nesting delimiter, e.g.
``DATABASE__PASSWORD`` -> ``settings.database.password``. Safety rails
(docs/context/07 #15) are enforced in :meth:`Settings._validate_safety`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.config.app import AppSettings, Env
from src.core.config.bot import BotSettings
from src.core.config.database import DatabaseSettings
from src.core.config.log import LogSettings
from src.core.config.redis import RedisSettings
from src.core.config.remnawave import RemnawaveSettings
from src.core.config.validators import ensure_fernet_key, ensure_filled
from src.core.config.web import WebSettings

__all__ = ["Env", "Settings", "get_settings"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppSettings = AppSettings()
    bot: BotSettings = BotSettings()
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    remnawave: RemnawaveSettings = RemnawaveSettings()
    web: WebSettings = WebSettings()
    log: LogSettings = LogSettings()

    @model_validator(mode="after")
    def _validate_safety(self) -> Settings:
        # Crypt key: validate format whenever provided; JWT must differ from it.
        if self.app.crypt_key:
            ensure_fernet_key(self.app.crypt_key, "APP__CRYPT_KEY")
            if self.app.crypt_key == self.app.jwt_secret:
                raise ValueError("APP__CRYPT_KEY and APP__JWT_SECRET must be distinct")

        # In production, all critical secrets must be present and non-placeholder.
        if self.app.env is Env.PRODUCTION:
            ensure_filled(self.app.crypt_key, "APP__CRYPT_KEY")
            ensure_fernet_key(self.app.crypt_key, "APP__CRYPT_KEY")
            ensure_filled(self.app.jwt_secret, "APP__JWT_SECRET")
            ensure_filled(self.bot.token, "BOT__TOKEN")
            ensure_filled(self.database.password, "DATABASE__PASSWORD")
            ensure_filled(self.remnawave.base_url, "REMNAWAVE__BASE_URL")
            ensure_filled(self.remnawave.token, "REMNAWAVE__TOKEN")
            ensure_filled(self.remnawave.webhook_secret, "REMNAWAVE__WEBHOOK_SECRET")
            if "*" in self.web.cors_origins:
                raise ValueError("WEB__CORS_ORIGINS must be explicit in production (not '*')")
            # Fail closed: never run with debug on in production (echo=True leaks SQL + params).
            self.app.debug = False
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings()
