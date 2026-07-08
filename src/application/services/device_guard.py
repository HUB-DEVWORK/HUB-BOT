"""DeviceGuard — subscription-sharing detection over the panel's ip-control API.

The panel collects online IPs per user on each node (POST fetch-users-ips -> job ->
result). We aggregate unique IPs per subscription across nodes and flag the ones that
exceed their device limit plus a tolerance. Actions escalate by config:
``alert`` (admins only) -> ``drop`` (+kill live connections) -> ``disable``
(+turn the panel user off). A Redis cooldown keeps one alert per subscription per day.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.application.services.remnawave import RemnawaveService
from src.core.enums import SubscriptionStatus
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.application.common.panel import RemnawaveClient
    from src.infrastructure.database.models.subscription import Subscription
    from src.infrastructure.database.uow import UnitOfWork

log = get_logger(__name__)

_POLL_ATTEMPTS = 10
_POLL_DELAY = 2.0
# Safety bound on one scan's working set — far above any realistic live+panel base. If a scan ever
# hits it we log rather than silently skip the rest (a shop that large should raise this knowingly).
_SCAN_CAP = 100_000


@dataclass(frozen=True, slots=True)
class Violation:
    subscription_id: int
    user_id: int
    telegram_id: int | None
    plan_name: str
    limit: int
    ips: tuple[str, ...]
    action: str  # what was actually applied: alert | drop | disable


@dataclass
class GuardConfig:
    max_ips: int = 0  # fallback when the subscription has no device_limit; 0 -> skip
    tolerance: int = 1  # NAT/mobile switching slack
    action: str = "alert"  # alert | drop | disable


@dataclass
class _Usage:
    ips: set[str] = field(default_factory=set)


class DeviceGuardService:
    def __init__(self, client: RemnawaveClient) -> None:
        self._client = client

    async def collect_ips(self, node_uuids: list[str]) -> dict[str, set[str]]:
        """userId (panel username or uuid) -> unique online IPs across the given nodes."""
        usage: dict[str, _Usage] = {}
        for node_uuid in node_uuids:
            try:
                job_id = await self._client.start_users_ips_job(node_uuid)
                users = await self._poll_job(job_id)
            except Exception as exc:
                log.warning("device guard node skipped", node=node_uuid, error=str(exc))
                continue
            for user_id, ips in users:
                bucket = usage.setdefault(user_id, _Usage())
                bucket.ips.update(ips)
        return {uid: u.ips for uid, u in usage.items()}

    async def _poll_job(self, job_id: str) -> list[tuple[str, list[str]]]:
        for _ in range(_POLL_ATTEMPTS):
            result = await self._client.get_users_ips_result(job_id)
            if result is not None:
                return result
            await asyncio.sleep(_POLL_DELAY)
        raise TimeoutError(f"ip-control job {job_id} did not complete")

    async def scan(
        self, uow: UnitOfWork, usage: dict[str, set[str]], cfg: GuardConfig
    ) -> list[Violation]:
        """Match collected usage to our live subscriptions and apply the configured action."""
        violations: list[Violation] = []
        subs = await uow.subscriptions.live_with_panel(_SCAN_CAP)
        if len(subs) >= _SCAN_CAP:
            log.warning("device-guard scan hit the subscription cap", cap=_SCAN_CAP)
        for sub in subs:
            ips = self._ips_for(sub, usage)
            limit = sub.device_limit or cfg.max_ips
            if limit <= 0 or len(ips) <= limit + cfg.tolerance:
                continue
            user = await uow.users.get(sub.user_id)
            action = await self._apply_action(sub, cfg.action)
            violations.append(
                Violation(
                    subscription_id=sub.id,
                    user_id=sub.user_id,
                    telegram_id=user.telegram_id if user else None,
                    plan_name=str((sub.plan_snapshot or {}).get("name") or "—"),
                    limit=limit,
                    ips=tuple(sorted(ips)[:10]),
                    action=action,
                )
            )
        return violations

    @staticmethod
    def _ips_for(sub: Subscription, usage: dict[str, set[str]]) -> set[str]:
        """The panel reports userId as the username (sub_<short_id>) or the uuid."""
        for key in (
            RemnawaveService.username_for(sub.short_id),
            str(sub.remnawave_uuid),
            sub.short_id,
        ):
            if key in usage:
                return usage[key]
        return set()

    async def _apply_action(self, sub: Subscription, action: str) -> str:
        if sub.remnawave_uuid is None:
            return "alert"
        try:
            if action == "drop":
                await self._client.drop_connections(sub.remnawave_uuid)
                return "drop"
            if action == "disable":
                await self._client.disable_user(sub.remnawave_uuid)
                sub.status = SubscriptionStatus.DISABLED
                return "disable"
        except Exception as exc:
            log.warning("device guard action failed", sub=sub.id, action=action, error=str(exc))
        return "alert"
