"""Thin per-aggregate DAOs for catalogue / config tables."""

from __future__ import annotations

from src.core.enums import PaymentGatewayType
from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.payment_gateway import PaymentGateway
from src.infrastructure.database.models.plan import Plan
from src.infrastructure.database.models.promo_group import PromoGroup
from src.infrastructure.database.models.server_squad import ServerSquad
from src.infrastructure.database.models.settings import Settings


class PlanDAO(BaseDAO[Plan]):
    model = Plan

    async def get_by_code(self, public_code: str) -> Plan | None:
        return await self.find_one(public_code=public_code)


class ServerSquadDAO(BaseDAO[ServerSquad]):
    model = ServerSquad


class PromoGroupDAO(BaseDAO[PromoGroup]):
    model = PromoGroup

    async def get_default(self) -> PromoGroup | None:
        return await self.find_one(is_default=True)


class PaymentGatewayDAO(BaseDAO[PaymentGateway]):
    model = PaymentGateway

    async def get_active(self, gateway_type: PaymentGatewayType) -> PaymentGateway | None:
        return await self.find_one(type=gateway_type, is_active=True)


class SettingsDAO(BaseDAO[Settings]):
    model = Settings

    async def get_singleton(self) -> Settings | None:
        """Return the single settings row (id=1 by convention), or None if unseeded."""
        return await self.find_one()
