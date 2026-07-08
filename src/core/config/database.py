"""PostgreSQL settings."""

from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel


class DatabaseSettings(BaseModel):
    host: str = "postgres"
    port: int = 5432
    user: str = "vpn"
    password: str = ""
    name: str = "vpn"
    pool_size: int = 10

    @property
    def url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver). Credentials are percent-encoded so a generated
        secret with URL-reserved chars (@ / : + = #) can't corrupt the DSN."""
        user, password = quote(self.user, safe=""), quote(self.password, safe="")
        return f"postgresql+asyncpg://{user}:{password}@{self.host}:{self.port}/{self.name}"
