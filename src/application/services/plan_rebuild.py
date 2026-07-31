"""Rebuild the tariff catalog after migrating from another bot.

Operators kept reporting «перенеслись только пользователи, тарифы не перенеслись» — and that was
exactly right: the importers carried users, subscriptions, payments and referrals, but the tariff
catalog stayed empty and every imported subscription had no plan (``plan_id`` NULL).

Source bots rarely expose a usable tariff table (id + name at best), but every imported
subscription carries a plan snapshot — so the catalog is derived from those: one tariff per
distinct name, limits from the snapshot, duration from the actual subscription period.

The source never gives us a price, so each tariff is created DISABLED with a price of 0: selling
at a made-up price would be worse than not selling. The operator fills the prices in and switches
them on. Imported subscriptions are linked to the new tariffs, so renewals work right away (and
extend the same panel account instead of minting a duplicate).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from src.core.enums import Currency
from src.infrastructure.database.models.plan import Plan, PlanDuration, PlanPrice

if TYPE_CHECKING:
    from src.infrastructure.database.models.subscription import Subscription
    from src.infrastructure.database.uow import UnitOfWork

_DEFAULT_NAME = "Импортированный тариф"  # shown to the operator
_DEFAULT_DAYS = 30
_MAX_PLANS = 50  # junk-data guard: hundreds of "tariffs" from broken snapshots help nobody


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "imported"
    code = base
    n = 2
    while code in taken:
        code = f"{base}-{n}"[:64]
        n += 1
    taken.add(code)
    return code


def _period_days(sub: Subscription) -> int | None:
    """The period actually sold, taken from the subscription itself."""
    if sub.start_at is None or sub.expire_at is None:
        return None
    days = round((sub.expire_at - sub.start_at) / dt.timedelta(days=1))
    return days if 1 <= days <= 3650 else None


async def rebuild_plans(uow: UnitOfWork, *, source: str, summary: dict[str, Any]) -> int:
    """Build tariffs from this source's imported subscriptions and link the subs to them.

    Idempotent: a tariff is matched by name, so re-running an import never duplicates it.
    """
    summary.setdefault("plans", 0)  # the key is always present, even with nothing to create
    subs = [
        s
        for s in await uow.subscriptions.list()
        if s.plan_id is None
        and isinstance(s.plan_snapshot, dict)
        and str(s.plan_snapshot.get("source") or "") == source
    ]
    if not subs:
        return 0

    groups: dict[str, list[Subscription]] = {}
    for sub in subs:
        name = str(sub.plan_snapshot.get("name") or "").strip()
        if not name or name.lower() == "imported":  # importers' placeholder name
            name = _DEFAULT_NAME
        groups.setdefault(name[:128], []).append(sub)

    taken_codes = {p.public_code for p in await uow.plans.list()}
    created = 0
    for name, members in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:_MAX_PLANS]:
        plan = await uow.plans.find_one(name=name)
        if plan is None:
            snapshot = members[0].plan_snapshot or {}
            days_seen = [d for d in (_period_days(s) for s in members) if d]
            days = Counter(days_seen).most_common(1)[0][0] if days_seen else _DEFAULT_DAYS
            plan = Plan(
                public_code=_slug(name, taken_codes),
                name=name,
                description="Создан при переезде — проверьте цену и включите",
                traffic_limit_bytes=members[0].traffic_limit_bytes or None,
                device_limit=members[0].device_limit,
                internal_squads=list(snapshot.get("internal_squads") or []),
                external_squad=snapshot.get("external_squad"),
                # No price from the source: keep it off the shelf until the operator sets one.
                is_active=False,
            )
            await uow.plans.add(plan)
            await uow.flush()
            duration = PlanDuration(plan_id=plan.id, days=days)
            uow.session.add(duration)
            await uow.flush()
            uow.session.add(
                PlanPrice(plan_duration_id=duration.id, currency=Currency.RUB, price_minor=0)
            )
            created += 1
        # `uq_active_sub` allows at most ONE LIVE subscription per (user, plan): a user who
        # brought several live subs of the same tariff gets only their current one linked, the
        # rest stay plan-less rather than exploding the import with a constraint error.
        linked_live = {
            s.user_id for s in await uow.subscriptions.list(plan_id=plan.id) if s.status.is_usable
        }
        for sub in members:
            if sub.status.is_usable:
                if sub.user_id in linked_live:
                    continue
                linked_live.add(sub.user_id)
            sub.plan_id = plan.id
    await uow.flush()

    # `plans` is a plain counter — `skipped` lists what could NOT be imported, not notices.
    summary["plans"] += created
    return created
