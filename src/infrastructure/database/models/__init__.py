"""Import all models so ``Base.metadata`` is fully populated (for Alembic autogenerate
and ``create_all`` in tests). Import order is irrelevant — relationships use string refs.
"""

from __future__ import annotations

from src.infrastructure.database.base import Base
from src.infrastructure.database.models.payment_gateway import PaymentGateway
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice
from src.infrastructure.database.models.promo_group import PromoGroup, UserPromoGroup
from src.infrastructure.database.models.promocode import Promocode, PromocodeActivation
from src.infrastructure.database.models.referral import Referral, ReferralEarning
from src.infrastructure.database.models.server_squad import ServerSquad
from src.infrastructure.database.models.settings import Settings
from src.infrastructure.database.models.subscription import Subscription
from src.infrastructure.database.models.transaction import Transaction
from src.infrastructure.database.models.user import User

__all__ = [
    "Base",
    "PaymentGateway",
    "Plan",
    "PlanDuration",
    "PlanPrice",
    "PromoGroup",
    "Promocode",
    "PromocodeActivation",
    "Referral",
    "ReferralEarning",
    "ServerSquad",
    "Settings",
    "Subscription",
    "Transaction",
    "User",
    "UserPromoGroup",
]
