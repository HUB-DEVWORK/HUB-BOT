"""Promocode DAOs."""

from __future__ import annotations

from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation


class PromocodeDAO(BaseDAO[Promocode]):
    model = Promocode

    async def get_by_code(self, code: str) -> Promocode | None:
        return await self.find_one(code=code)


class PromocodeActivationDAO(BaseDAO[PromocodeActivation]):
    model = PromocodeActivation

    async def is_activated_by(self, promocode_id: int, user_id: int) -> bool:
        return await self.find_one(promocode_id=promocode_id, user_id=user_id) is not None
