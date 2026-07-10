"""RemnawaveResyncService — nightly self-healing sweep of bot <-> panel drift.

For every locally-active subscription we fetch its panel user and reconcile:
  * panel user vanished        -> local sub DISABLED (someone deleted it in the panel)
  * panel disabled / expired   -> re-apply our authoritative spec (what the customer
    actually paid for), panel-first — corrects manual panel edits
  * expiry drifted > 1 day     -> same re-apply

We are the source of truth for what was PAID; the panel is only a projection. This
keeps subscribers working after admins poke the panel by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.core.enums import SubscriptionStatus
from src.core.logging import get_logger

if TYPE_CHECKING:
    from src.application.common.panel import RemnawaveClient
    from src.application.dto.panel import PanelUser
    from src.application.services.subscription import SubscriptionService
    from src.infrastructure.database.uow import UnitOfWork

log = get_logger(__name__)

_DRIFT_DAYS = 1


def _traffic_exhausted(panel: PanelUser) -> bool:
    """The panel turned this user LIMITED for exhausting the traffic cap we sold — its own
    correct enforcement of the plan, not drift. 0 == unlimited, so it never exhausts."""
    return panel.traffic_limit_bytes > 0 and panel.traffic_used_bytes >= panel.traffic_limit_bytes


@dataclass
class ResyncReport:
    checked: int = 0
    healed: int = 0
    orphaned_local: int = 0  # sub gone from the panel
    notes: list[str] = field(default_factory=list)


class RemnawaveResyncService:
    def __init__(self, client: RemnawaveClient, subscriptions: SubscriptionService) -> None:
        self._client = client
        self._subscriptions = subscriptions

    async def resync(self, uow: UnitOfWork, *, limit: int = 500) -> ResyncReport:
        report = ResyncReport()
        subs = await uow.subscriptions.live_with_panel(limit)
        for sub in subs:
            report.checked += 1
            assert sub.remnawave_uuid is not None
            try:
                panel = await self._client.get_user_by_uuid(sub.remnawave_uuid)
            except Exception as exc:
                log.warning("resync fetch failed", sub=sub.id, error=str(exc))
                continue

            if panel is None:
                sub.status = SubscriptionStatus.DISABLED
                report.orphaned_local += 1
                report.notes.append(f"#{sub.id}: пропал из панели → DISABLED")
                continue

            expire_drift = (
                sub.expire_at is not None
                and panel.expire_at is not None
                and abs((panel.expire_at - sub.expire_at).total_seconds()) > _DRIFT_DAYS * 86400
            )
            # A user the panel turned LIMITED for hitting the traffic cap we sold is the panel
            # enforcing that cap correctly — not drift. Re-enabling would flip them ACTIVE only
            # for the next panel check to re-limit them: a pointless flap that briefly overrides
            # real enforcement (B7). Re-enable only genuinely disabled/expired users.
            needs_enable = not panel.is_enabled and not _traffic_exhausted(panel)
            if needs_enable or expire_drift:
                try:
                    user = await uow.users.get(sub.user_id)
                    await self._subscriptions.push_limits(
                        uow, sub, telegram_id=user.telegram_id if user else None
                    )
                    if needs_enable:
                        # push_limits PATCHes limits but never flips status; a DISABLED user
                        # needs the explicit enable action, else self-heal is a no-op (#2).
                        await self._client.enable_user(sub.remnawave_uuid)
                    report.healed += 1
                    report.notes.append(
                        f"#{sub.id}: панель разошлась (enabled={panel.is_enabled}) → восстановлено"
                    )
                except Exception as exc:
                    log.warning("resync heal failed", sub=sub.id, error=str(exc))
        log.info(
            "resync done",
            checked=report.checked,
            healed=report.healed,
            orphaned=report.orphaned_local,
        )
        report.notes = report.notes[:50]
        return report
