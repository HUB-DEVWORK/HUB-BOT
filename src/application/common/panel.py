"""RemnawaveClient protocol — the swappable seam to the VPN panel.

Services depend on this, never on httpx. The concrete client lives in
``src/infrastructure/remnawave/client.py``; tests inject a ``FakeRemnawaveClient``.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from src.application.dto.panel import (
    PanelDevice,
    PanelNode,
    PanelSquad,
    PanelUser,
    PanelVersion,
    ProvisionSpec,
)


@runtime_checkable
class RemnawaveClient(Protocol):
    """Thin, typed async wrapper over the Remnawave HTTP API."""

    async def get_version(self) -> PanelVersion: ...

    async def create_user(self, spec: ProvisionSpec) -> PanelUser: ...

    async def update_user(self, panel_uuid: uuid.UUID, spec: ProvisionSpec) -> PanelUser: ...

    async def get_user_by_uuid(self, panel_uuid: uuid.UUID) -> PanelUser | None: ...

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser | None: ...

    async def enable_user(self, panel_uuid: uuid.UUID) -> None: ...

    async def disable_user(self, panel_uuid: uuid.UUID) -> None: ...

    async def delete_user(self, panel_uuid: uuid.UUID) -> None: ...

    async def reset_traffic(self, panel_uuid: uuid.UUID) -> None: ...

    async def revoke_subscription(self, panel_uuid: uuid.UUID) -> PanelUser: ...

    async def drop_connections(self, panel_uuid: uuid.UUID) -> None: ...

    async def get_devices(self, panel_uuid: uuid.UUID) -> list[PanelDevice]: ...

    async def delete_device(self, panel_uuid: uuid.UUID, hwid: str) -> None: ...

    async def get_internal_squads(self) -> list[PanelSquad]: ...

    async def get_nodes(self) -> list[PanelNode]: ...
