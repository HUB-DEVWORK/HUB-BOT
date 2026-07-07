"""DAOs for the admin-cabinet aggregates (thin CRUD + a few domain queries)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import func, select

from src.core.enums import BroadcastStatus, TicketStatus
from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.audit_log import AuditLog
from src.infrastructure.database.models.bot_config import BotConfigValue
from src.infrastructure.database.models.broadcast import Broadcast
from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken
from src.infrastructure.database.models.campaign import Campaign
from src.infrastructure.database.models.constructor import ConstructorPeriod, TrafficPack
from src.infrastructure.database.models.holiday import Holiday
from src.infrastructure.database.models.menu_node import MenuNode
from src.infrastructure.database.models.miniapp_config import MiniappConfig
from src.infrastructure.database.models.report_topic import ReportTopic
from src.infrastructure.database.models.server_node import ServerNode
from src.infrastructure.database.models.smart_reminder import SmartReminder
from src.infrastructure.database.models.ticket import Ticket, TicketMessage
from src.infrastructure.database.models.winback_step import WinbackStep
from src.infrastructure.database.models.withdrawal import WithdrawalRequest


class BotConfigValueDAO(BaseDAO[BotConfigValue]):
    model = BotConfigValue

    async def as_dict(self) -> dict[str, object]:
        rows = await self.list()
        return {r.key: r.value for r in rows}

    async def upsert(self, key: str, value: object) -> BotConfigValue:
        row = await self.find_one(key=key)
        if row is None:
            row = await self.add(BotConfigValue(key=key, value=value))
        else:
            row.value = value
            await self.session.flush()
        return row


class MenuNodeDAO(BaseDAO[MenuNode]):
    model = MenuNode

    async def tree(self) -> Sequence[MenuNode]:
        """All nodes ordered for tree assembly client-side."""
        result = await self.session.scalars(
            select(MenuNode).order_by(MenuNode.parent_id.nulls_first(), MenuNode.order_index)
        )
        return result.all()

    async def replace_all(self, nodes: list[MenuNode]) -> None:
        """Atomic «Сохранить меню»: wipe and reinsert the whole tree."""
        await self.delete_by()
        self.session.add_all(nodes)
        await self.session.flush()


class MiniappConfigDAO(BaseDAO[MiniappConfig]):
    model = MiniappConfig

    async def get_or_create(self) -> MiniappConfig:
        row = await self.find_one()
        if row is None:
            row = await self.add(MiniappConfig())
        return row


class BroadcastDAO(BaseDAO[Broadcast]):
    model = Broadcast

    async def running(self) -> Sequence[Broadcast]:
        result = await self.session.scalars(
            select(Broadcast).where(
                Broadcast.status.in_([BroadcastStatus.PENDING, BroadcastStatus.RUNNING])
            )
        )
        return result.all()

    async def recent(self, limit: int = 20) -> Sequence[Broadcast]:
        result = await self.session.scalars(
            select(Broadcast).order_by(Broadcast.id.desc()).limit(limit)
        )
        return result.all()


class SmartReminderDAO(BaseDAO[SmartReminder]):
    model = SmartReminder

    async def get_or_create(self) -> SmartReminder:
        row = await self.find_one()
        if row is None:
            row = await self.add(SmartReminder())
        return row


class HolidayDAO(BaseDAO[Holiday]):
    model = Holiday

    async def ordered(self) -> Sequence[Holiday]:
        result = await self.session.scalars(select(Holiday).order_by(Holiday.month, Holiday.day))
        return result.all()


class WinbackStepDAO(BaseDAO[WinbackStep]):
    model = WinbackStep

    async def ordered(self) -> Sequence[WinbackStep]:
        result = await self.session.scalars(select(WinbackStep).order_by(WinbackStep.offset_days))
        return result.all()


class CampaignDAO(BaseDAO[Campaign]):
    model = Campaign


class TicketDAO(BaseDAO[Ticket]):
    model = Ticket

    async def open_count(self) -> int:
        stmt = select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.OPEN)
        return int(await self.session.scalar(stmt) or 0)

    async def recent(self, limit: int = 50) -> Sequence[Ticket]:
        result = await self.session.scalars(
            select(Ticket).order_by(Ticket.updated_at.desc()).limit(limit)
        )
        return result.all()


class TicketMessageDAO(BaseDAO[TicketMessage]):
    model = TicketMessage


class ReportTopicDAO(BaseDAO[ReportTopic]):
    model = ReportTopic


class WithdrawalDAO(BaseDAO[WithdrawalRequest]):
    model = WithdrawalRequest


class CabinetRefreshTokenDAO(BaseDAO[CabinetRefreshToken]):
    model = CabinetRefreshToken


class ServerNodeDAO(BaseDAO[ServerNode]):
    model = ServerNode


class AuditLogDAO(BaseDAO[AuditLog]):
    model = AuditLog

    async def recent(self, limit: int = 30) -> Sequence[AuditLog]:
        result = await self.session.scalars(
            select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
        )
        return result.all()


class ConstructorPeriodDAO(BaseDAO[ConstructorPeriod]):
    model = ConstructorPeriod


class TrafficPackDAO(BaseDAO[TrafficPack]):
    model = TrafficPack
