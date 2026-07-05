"""Unit of Work — one transaction boundary bundling all per-aggregate DAOs.

Usage::

    async with uow:
        user = await uow.users.get(1)
        ...
        await uow.commit()

On exit without a commit (or on exception) the transaction is rolled back. DAOs never
commit on their own — the UoW owns the boundary (see application/common protocol).
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.database.dao.catalog import (
    PaymentGatewayDAO,
    PlanDAO,
    PromoGroupDAO,
    ServerSquadDAO,
    SettingsDAO,
)
from src.infrastructure.database.dao.promocode import (
    PromocodeActivationDAO,
    PromocodeDAO,
)
from src.infrastructure.database.dao.referral import ReferralDAO, ReferralEarningDAO
from src.infrastructure.database.dao.subscription import SubscriptionDAO
from src.infrastructure.database.dao.transaction import TransactionDAO
from src.infrastructure.database.dao.user import UserDAO


class UnitOfWork:
    """Single-use async transaction context aggregating the DAOs."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> UnitOfWork:
        self._session = self._session_factory()
        session = self._session
        self.users = UserDAO(session)
        self.subscriptions = SubscriptionDAO(session)
        self.transactions = TransactionDAO(session)
        self.plans = PlanDAO(session)
        self.server_squads = ServerSquadDAO(session)
        self.promo_groups = PromoGroupDAO(session)
        self.payment_gateways = PaymentGatewayDAO(session)
        self.settings = SettingsDAO(session)
        self.promocodes = PromocodeDAO(session)
        self.promocode_activations = PromocodeActivationDAO(session)
        self.referrals = ReferralDAO(session)
        self.referral_earnings = ReferralEarningDAO(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is not None:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of an 'async with' block")
        return self._session

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def flush(self) -> None:
        await self.session.flush()
