"""RemnawaveClient protocol — the swappable seam to the VPN panel.

Services depend on this, never on httpx. The concrete client lives in
``src/infrastructure/remnawave/client.py``; tests inject a ``FakeRemnawaveClient``.

Panel users are addressed by :data:`~src.application.dto.panel.PanelRef`: a bare 2.x
uuid or a :class:`~src.application.dto.panel.PanelUserRef` that also carries the numeric
id Remnawave 3.0 switched to. Callers pass ``subscription.panel_ref``; the client picks
the key its probed panel version understands.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.application.dto.panel import (
    PanelDevice,
    PanelNode,
    PanelRef,
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

    async def update_user(self, ref: PanelRef, spec: ProvisionSpec) -> PanelUser: ...

    async def get_user(self, ref: PanelRef) -> PanelUser | None: ...

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser | None: ...

    async def enable_user(self, ref: PanelRef) -> None: ...

    async def disable_user(self, ref: PanelRef) -> None: ...

    async def delete_user(self, ref: PanelRef) -> None: ...

    async def reset_traffic(self, ref: PanelRef) -> None: ...

    async def revoke_subscription(self, ref: PanelRef) -> PanelUser: ...

    async def drop_connections(self, ref: PanelRef) -> None: ...

    async def get_devices(self, ref: PanelRef) -> list[PanelDevice]: ...

    async def start_users_ips_job(self, node_uuid: str) -> str: ...

    async def get_users_ips_result(self, job_id: str) -> list[tuple[str, list[str]]] | None: ...

    async def delete_device(self, ref: PanelRef, hwid: str) -> None: ...

    async def get_internal_squads(self) -> list[PanelSquad]: ...

    async def get_nodes(self) -> list[PanelNode]: ...
