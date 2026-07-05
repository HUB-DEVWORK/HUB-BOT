"""PostgreSQL settings."""

from __future__ import annotations

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
        """Async SQLAlchemy URL (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )
