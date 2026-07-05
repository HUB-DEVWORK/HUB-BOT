"""Generic async CRUD DAO. Per-aggregate DAOs subclass this and add domain queries."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.base import Base


class BaseDAO[ModelT: Base]:
    """CRUD over a single mapped model, bound to one AsyncSession.

    DAOs never commit — the :class:`~src.infrastructure.database.uow.UnitOfWork` owns the
    transaction boundary. ``flush`` is used to populate generated PKs within the transaction.
    """

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id_: int) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def find_one(self, **filters: Any) -> ModelT | None:
        result = await self.session.scalars(select(self.model).filter_by(**filters).limit(1))
        return result.first()

    async def list(
        self, *, limit: int | None = None, offset: int = 0, **filters: Any
    ) -> Sequence[ModelT]:
        stmt = select(self.model).filter_by(**filters).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.scalars(stmt)
        return result.all()

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).filter_by(**filters)
        return int(await self.session.scalar(stmt) or 0)

    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)

    async def delete_by(self, **filters: Any) -> int:
        result = await self.session.execute(delete(self.model).filter_by(**filters))
        return cast("CursorResult[Any]", result).rowcount or 0
