"""Shared fixtures. Tests run against in-memory aiosqlite via ``Base.metadata.create_all``
(no Postgres, no migrations) — see migrations/README.md.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.infrastructure.database.models import Base
from src.infrastructure.database.uow import UnitOfWork


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def uow(session_factory: async_sessionmaker) -> UnitOfWork:
    """A reusable UnitOfWork — each ``async with uow:`` opens a fresh session."""
    return UnitOfWork(session_factory)
