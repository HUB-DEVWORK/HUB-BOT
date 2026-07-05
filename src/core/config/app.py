"""Application-wide settings (env, secrets, owner ids)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, field_validator


class Env(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class AppSettings(BaseModel):
    env: Env = Env.LOCAL
    debug: bool = True
    # Fernet key encrypting gateway credentials at rest. Validated in Settings.
    crypt_key: str = ""
    # Distinct secret for signing cabinet/mini-app JWTs (added with the web cabinet).
    jwt_secret: str = ""
    # Telegram ids granted OWNER on first contact.
    owner_ids: list[int] = []

    @field_validator("owner_ids", mode="before")
    @classmethod
    def _split_owner_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.replace(",", " ").split()]
        return v

    @property
    def is_production(self) -> bool:
        return self.env is Env.PRODUCTION
