"""DAOs for the admin-cabinet aggregates (thin CRUD + a few domain queries)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import func, select

from src.core.enums import BroadcastStatus, TicketStatus
from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.audit_log import AuditLog
from src.infrastructure.database.models.blacklist import BlacklistEntry
from src.infrastructure.database.models.bot_config import BotConfigValue
from src.infrastructure.database.models.broadcast import Broadcast
from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken
from src.infrastructure.database.models.campaign import Campaign
from src.infrastructure.database.models.constructor import ConstructorPeriod, TrafficPack
from src.infrastructure.database.models.holiday import Holiday
from src.infrastructure.database.models.menu_node import MenuNode
from src.infrastructure.database.models.miniapp_config import MiniappConfig
from src.infrastructure.database.models.notification_template import NotificationTemplate
from src.infrastructure.database.models.partner import Partner
from src.infrastructure.database.models.reminder_step import ReminderStep
from src.infrastructure.database.models.report_topic import ReportTopic
from src.infrastructure.database.models.sale_campaign import SaleCampaign
from src.infrastructure.database.models.server_node import ServerNode
from src.infrastructure.database.models.smart_reminder import SmartReminder
from src.infrastructure.database.models.ticket import Ticket, TicketMessage
from src.infrastructure.database.models.traffic_snapshot import TrafficSnapshot
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


class BlacklistDAO(BaseDAO[BlacklistEntry]):
    model = BlacklistEntry

    async def has(self, telegram_id: int) -> bool:
        return await self.find_one(telegram_id=telegram_id) is not None

    async def ordered(self) -> Sequence[BlacklistEntry]:
        result = await self.session.scalars(
            select(BlacklistEntry).order_by(BlacklistEntry.created_at.desc())
        )
        return result.all()


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


class ReminderStepDAO(BaseDAO[ReminderStep]):
    model = ReminderStep

    async def ordered(self) -> Sequence[ReminderStep]:
        """Furthest-out first: 24 h → 12 h → 1 h → 0 h (at expiry)."""
        result = await self.session.scalars(
            select(ReminderStep).order_by(ReminderStep.hours_before.desc())
        )
        return result.all()


class NotificationTemplateDAO(BaseDAO[NotificationTemplate]):
    model = NotificationTemplate

    async def by_event(self, event: str) -> NotificationTemplate | None:
        return await self.find_one(event=event)

    async def ordered(self) -> Sequence[NotificationTemplate]:
        result = await self.session.scalars(
            select(NotificationTemplate).order_by(NotificationTemplate.event)
        )
        return result.all()


class SaleCampaignDAO(BaseDAO[SaleCampaign]):
    model = SaleCampaign

    async def ordered(self) -> Sequence[SaleCampaign]:
        result = await self.session.scalars(
            select(SaleCampaign).order_by(SaleCampaign.discount_pct.desc())
        )
        return result.all()

    async def active_now(self, now: dt.datetime) -> SaleCampaign | None:
        """Best enabled sale whose day-window covers ``now`` and whose monthly quota is not
        yet spent; highest discount wins."""
        period, day = now.strftime("%Y-%m"), now.day
        rows = await self.session.scalars(
            select(SaleCampaign)
            .where(SaleCampaign.enabled.is_(True))
            .order_by(SaleCampaign.discount_pct.desc())
        )
        for c in rows:
            if not (c.start_day <= day <= c.end_day):
                continue
            used = c.used_count if c.used_period == period else 0
            if c.max_uses == 0 or used < c.max_uses:
                return c
        return None

    async def consume(self, campaign_id: int, now: dt.datetime) -> None:
        """Count one sale purchase against this month's quota (resets on month rollover)."""
        c = await self.get(campaign_id)
        if c is None:
            return
        period = now.strftime("%Y-%m")
        if c.used_period != period:
            c.used_count = 0
            c.used_period = period
        c.used_count += 1


class PartnerDAO(BaseDAO[Partner]):
    model = Partner

    async def by_code(self, code: str) -> Partner | None:
        return await self.find_one(code=code)

    async def ordered(self) -> Sequence[Partner]:
        result = await self.session.scalars(select(Partner).order_by(Partner.created_at.desc()))
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


class TrafficSnapshotDAO(BaseDAO[TrafficSnapshot]):
    model = TrafficSnapshot

    async def upsert(self, subscription_id: int, day: str, used_bytes: int) -> None:
        row = await self.find_one(subscription_id=subscription_id, day=day)
        if row is None:
            self.session.add(
                TrafficSnapshot(subscription_id=subscription_id, day=day, used_bytes=used_bytes)
            )
        else:
            row.used_bytes = used_bytes
        await self.session.flush()

    async def series(self, subscription_id: int, limit: int = 30) -> Sequence[TrafficSnapshot]:
        """Most-recent-first daily readings (client reverses + diffs for the graph)."""
        result = await self.session.scalars(
            select(TrafficSnapshot)
            .where(TrafficSnapshot.subscription_id == subscription_id)
            .order_by(TrafficSnapshot.day.desc())
            .limit(limit)
        )
        return result.all()


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
