"""Subscription DAO."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import or_, select

from src.core.enums import SubscriptionStatus
from src.infrastructure.database.dao.base import BaseDAO
from src.infrastructure.database.models.subscription import Subscription

_USABLE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIAL, SubscriptionStatus.LIMITED)
# Grace entry looks at paid subs that just crossed expiry — ACTIVE/LIMITED, or already flipped
# EXPIRED by the panel/resync in the same window. Bounded by a look-back so enabling the feature
# never sweeps the whole historical backlog of long-dead subs.
_GRACE_CANDIDATE_STATUS = (
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.LIMITED,
    SubscriptionStatus.EXPIRED,
)


class SubscriptionDAO(BaseDAO[Subscription]):
    model = Subscription

    async def get_by_short_id(self, short_id: str) -> Subscription | None:
        return await self.find_one(short_id=short_id)

    async def get_by_remnawave_uuid(self, remnawave_uuid: uuid.UUID) -> Subscription | None:
        return await self.find_one(remnawave_uuid=remnawave_uuid)

    async def get_by_remnawave_id(self, remnawave_id: int) -> Subscription | None:
        """Resolve by the numeric user id Remnawave >=3.0 panels use instead of the uuid."""
        return await self.find_one(remnawave_id=remnawave_id)

    async def active_for_user(self, user_id: int) -> Sequence[Subscription]:
        stmt = select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.status.in_(_USABLE),
        )
        return (await self.session.scalars(stmt)).all()

    async def live_with_panel(self, limit: int) -> Sequence[Subscription]:
        """Usable subs that are provisioned on the panel — the resync / device-guard working set.
        Filter is pushed to SQL (hits the partial remnawave indexes) instead of loading the whole
        table and filtering in Python, which is O(all subs) and unbounded at scale."""
        stmt = (
            select(Subscription)
            .where(
                Subscription.status.in_(_USABLE),
                or_(
                    Subscription.remnawave_uuid.is_not(None),
                    Subscription.remnawave_id.is_not(None),
                ),
            )
            .order_by(Subscription.id)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def disabled_with_panel(self, limit: int) -> Sequence[Subscription]:
        """Locally-DISABLED subs still provisioned on the panel — the refund/revoke backstop.
        A refund disables locally + best-effort on the panel; if the panel was down the retry
        can't recover, and live_with_panel never re-checks these (they're not usable). This sweep
        re-asserts the disable so a refunded user can't keep connecting."""
        stmt = (
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.DISABLED,
                Subscription.remnawave_uuid.is_not(None),
            )
            .order_by(Subscription.id)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def grace_candidates(
        self, now: dt.datetime, *, lookback: dt.timedelta, cooldown_cutoff: dt.datetime, limit: int
    ) -> Sequence[Subscription]:
        """Paid subs that just expired and are eligible to enter the grace window.

        Not trials; provisioned on the panel; expired within ``lookback`` (so enabling grace
        never resurrects the whole backlog); not already in grace; and past the cooldown since
        their last grace (``grace_started_at`` NULL = never graced)."""
        stmt = (
            select(Subscription)
            .where(
                Subscription.status.in_(_GRACE_CANDIDATE_STATUS),
                Subscription.is_trial.is_(False),
                Subscription.remnawave_uuid.is_not(None),
                Subscription.grace_until.is_(None),
                Subscription.expire_at.is_not(None),
                Subscription.expire_at <= now,
                Subscription.expire_at > now - lookback,
                or_(
                    Subscription.grace_started_at.is_(None),
                    Subscription.grace_started_at <= cooldown_cutoff,
                ),
            )
            .order_by(Subscription.id)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def grace_expired(self, now: dt.datetime, *, limit: int) -> Sequence[Subscription]:
        """Subs whose grace window has now closed — ready to be fully turned off."""
        stmt = (
            select(Subscription)
            .where(
                Subscription.grace_until.is_not(None),
                Subscription.grace_until <= now,
            )
            .order_by(Subscription.id)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()
