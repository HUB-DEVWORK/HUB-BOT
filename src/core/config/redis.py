"""Redis settings (FSM, cache, locks, pending-referral, taskiq broker)."""

from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel


class RedisSettings(BaseModel):
    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: str = ""

    @property
    def url(self) -> str:
        # Percent-encode the password so URL-reserved chars in a secret don't corrupt the DSN.
        auth = f":{quote(self.password, safe='')}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"
