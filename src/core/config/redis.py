"""Redis settings (FSM, cache, locks, pending-referral, taskiq broker)."""

from __future__ import annotations

from pydantic import BaseModel


class RedisSettings(BaseModel):
    host: str = "redis"
    port: int = 6379
    db: int = 0
    password: str = ""

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"
