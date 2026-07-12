"""RemnawaveService — business-facing panel operations over the RemnawaveClient protocol.

Handles version capability probing, unit conversion (GB->bytes, unlimited modelling) and
username templating with the permanent ``short_id`` suffix. No httpx here — that's the
concrete client's job.
"""

from __future__ import annotations

import datetime as dt
import uuid

from src.application.common.panel import RemnawaveClient
from src.application.dto.panel import PanelUser, PanelVersion, ProvisionSpec
from src.core.constants import (
    BYTES_PER_GB,
    MIN_REMNAWAVE_VERSION,
    UNLIMITED_EXPIRE_DAYS,
    UNLIMITED_TRAFFIC_BYTES,
)
from src.core.exceptions import RemnawaveVersionError
from src.core.logging import get_logger

_USERNAME_MAX = 34
_UNKNOWN_VERSION = (0, 0, 0)
log = get_logger(__name__)


class RemnawaveService:
    def __init__(self, client: RemnawaveClient) -> None:
        self._client = client

    async def ensure_supported(self) -> PanelVersion:
        """Probe the panel version at startup.

        Only hard-fails when the version is *known* and below the minimum. Some panels do
        not expose their version via the API — in that case we proceed with a warning rather
        than block a healthy panel (verified against a live panel with no version field).
        """
        version = await self._client.get_version()
        if version.tuple == _UNKNOWN_VERSION:
            log.warning("panel_version_unknown", note="proceeding without a version gate")
            return version
        if version.tuple < MIN_REMNAWAVE_VERSION:
            raise RemnawaveVersionError(
                f"panel {version.raw} < required {'.'.join(map(str, MIN_REMNAWAVE_VERSION))}"
            )
        return version

    @staticmethod
    def gb_to_bytes(gb: int | None) -> int:
        """Convert a GB limit to bytes; None/0 -> unlimited (0)."""
        if not gb:
            return UNLIMITED_TRAFFIC_BYTES
        return gb * BYTES_PER_GB

    @staticmethod
    def username_for(short_id: str, *, prefix: str = "sub_") -> str:
        return f"{prefix}{short_id}"[:_USERNAME_MAX]

    def build_spec(
        self,
        *,
        short_id: str,
        telegram_id: int | None,
        expire_at: dt.datetime,
        traffic_limit_bytes: int,
        device_limit: int | None,
        internal_squads: tuple[str, ...] = (),
        external_squad: str | None = None,
        unlimited_expire: bool = False,
    ) -> ProvisionSpec:
        if unlimited_expire:
            expire_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=UNLIMITED_EXPIRE_DAYS)
        return ProvisionSpec(
            short_id=short_id,
            telegram_id=telegram_id,
            username=self.username_for(short_id),
            expire_at=expire_at,
            traffic_limit_bytes=traffic_limit_bytes,
            device_limit=device_limit,
            internal_squads=internal_squads,
            external_squad=external_squad,
            description=f"tg:{telegram_id}" if telegram_id else None,
        )

    async def provision(self, spec: ProvisionSpec) -> PanelUser:
        """Create the panel user for a subscription (panel-first, ADR-0005)."""
        return await self._client.create_user(spec)

    async def apply(self, panel_uuid: uuid.UUID, spec: ProvisionSpec) -> PanelUser:
        """Push a spec change (renew/change) to an existing panel user."""
        return await self._client.update_user(panel_uuid, spec)
