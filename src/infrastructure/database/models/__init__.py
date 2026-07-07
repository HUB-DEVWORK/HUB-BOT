"""Import all models so ``Base.metadata`` is fully populated (for Alembic autogenerate
and ``create_all`` in tests). Import order is irrelevant — relationships use string refs.
"""

from __future__ import annotations

from src.infrastructure.database.base import Base
from src.infrastructure.database.models.audit_log import AuditLog
from src.infrastructure.database.models.bot_config import BotConfigValue
from src.infrastructure.database.models.broadcast import Broadcast
from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken
from src.infrastructure.database.models.campaign import Campaign
from src.infrastructure.database.models.constructor import ConstructorPeriod, TrafficPack
from src.infrastructure.database.models.holiday import Holiday
from src.infrastructure.database.models.menu_node import MenuNode
from src.infrastructure.database.models.miniapp_config import MiniappConfig
from src.infrastructure.database.models.payment_gateway import PaymentGateway
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.database.models.promo_group import PromoGroup, UserPromoGroup
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.referral import Referral, ReferralEarning
from src.infrastructure.database.models.reminder_step import ReminderStep
from src.infrastructure.database.models.report_topic import ReportTopic
from src.infrastructure.database.models.server_node import ServerNode
from src.infrastructure.database.models.server_squad import ServerSquad
from src.infrastructure.database.models.settings import Settings
from src.infrastructure.database.models.smart_reminder import SmartReminder
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.ticket import Ticket, TicketMessage
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User
from src.infrastructure.database.models.winback_step import WinbackStep
from src.infrastructure.database.models.withdrawal import WithdrawalRequest

__all__ = [
    "AuditLog",
    "Base",
    "BotConfigValue",
    "Broadcast",
    "CabinetRefreshToken",
    "Campaign",
    "ConstructorPeriod",
    "Holiday",
    "MenuNode",
    "MiniappConfig",
    "PaymentGateway",
    "Plan",
    "PlanDuration",
    "PlanPrice",
    "PromoGroup",
    "Promocode",
    "PromocodeActivation",
    "Referral",
    "ReferralEarning",
    "ReminderStep",
    "ReportTopic",
    "ServerNode",
    "ServerSquad",
    "Settings",
    "SmartReminder",
    "Subscription",
    "Ticket",
    "TicketMessage",
    "TrafficPack",
    "Transaction",
    "User",
    "UserPromoGroup",
    "WinbackStep",
    "WithdrawalRequest",
]
