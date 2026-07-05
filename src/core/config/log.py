"""Logging settings."""

from __future__ import annotations

from pydantic import BaseModel


class LogSettings(BaseModel):
    level: str = "INFO"
    # env: LOG__USE_JSON. (Named ``use_json`` to avoid shadowing BaseModel.json.)
    use_json: bool = False
