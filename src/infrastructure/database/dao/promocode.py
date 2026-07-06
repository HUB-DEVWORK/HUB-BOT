"""Promocode DAOs."""

from __future__ import annotations

from sqlalchemy import select

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation


class PromocodeDAO(BaseDAO[Promocode]):
    model = Promocode

    async def get_by_code(self, code: str) -> Promocode | None:
        return await self.find_one(code=code)

    async def get_by_code_for_update(self, code: str) -> Promocode | None:
        """Row-lock the promocode so count-then-insert of activations is atomic (no overshoot
        of max_activations under concurrency). ``FOR UPDATE`` is a no-op on SQLite."""
        stmt = select(Promocode).where(Promocode.code == code).with_for_update()
        return (await self.session.scalars(stmt)).first()


class PromocodeActivationDAO(BaseDAO[PromocodeActivation]):
    model = PromocodeActivation

    async def is_activated_by(self, promocode_id: int, user_id: int) -> bool:
        return await self.find_one(promocode_id=promocode_id, user_id=user_id) is not None
