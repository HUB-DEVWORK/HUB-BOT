"""Application-wide settings (env, secrets, owner ids)."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, field_validator
from pydantic_settings import NoDecode


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
    # Telegram ids granted OWNER on first contact. NoDecode: take the raw env string as-is
    # (pydantic-settings would otherwise JSON-decode APP__OWNER_IDS, so a plain `898...` becomes
    # an int and fails "not a list"). The validator below accepts "898", "898,123", "898 123".
    owner_ids: Annotated[list[int], NoDecode] = []

    @field_validator("owner_ids", mode="before")
    @classmethod
    def _split_owner_ids(cls, v: object) -> object:
        if v is None:
            return []
        if isinstance(v, int):  # a lone id passed programmatically
            return [v]
        if isinstance(v, str):
            # NoDecode gives the raw string — accept ANY shape an env/.env/tool emits:
            # "898", "898,123", "898 123", "[898, 123]", "" → pull out every integer.
            return [int(x) for x in re.findall(r"-?\d+", v)]
        return v

    @property
    def is_production(self) -> bool:
        return self.env is Env.PRODUCTION
