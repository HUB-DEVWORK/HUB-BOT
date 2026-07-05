"""In-memory fake of the RemnawaveClient protocol — no network, deterministic."""

from __future__ import annotations

import dataclasses
import uuid

from src.application.dto.panel import (
    PanelNode,
    PanelSquad,
    PanelUser,
    PanelVersion,
    ProvisionSpec,
)


class FakeRemnawaveClient:
    """Records created users; satisfies application.common.panel.RemnawaveClient."""

    def __init__(self, *, version: tuple[int, int, int] = (2, 8, 0)) -> None:
        self._version = version
        self.users: dict[uuid.UUID, PanelUser] = {}
        self.deleted: list[uuid.UUID] = []

    async def get_version(self) -> PanelVersion:
        maj, minr, pat = self._version
        return PanelVersion(raw=".".join(map(str, self._version)), major=maj, minor=minr, patch=pat)

    def _make_user(self, spec: ProvisionSpec, panel_uuid: uuid.UUID) -> PanelUser:
        return PanelUser(
            uuid=panel_uuid,
            short_id=spec.short_id,
            username=spec.username,
            is_enabled=True,
            expire_at=spec.expire_at,
            traffic_limit_bytes=spec.traffic_limit_bytes,
            traffic_used_bytes=0,
            device_limit=spec.device_limit,
            subscription_url=f"https://panel.test/sub/{spec.short_id}",
            telegram_id=spec.telegram_id,
            internal_squads=spec.internal_squads,
            external_squad=spec.external_squad,
        )

    async def create_user(self, spec: ProvisionSpec) -> PanelUser:
        panel_uuid = uuid.uuid4()
        user = self._make_user(spec, panel_uuid)
        self.users[panel_uuid] = user
        return user

    async def update_user(self, panel_uuid: uuid.UUID, spec: ProvisionSpec) -> PanelUser:
        user = self._make_user(spec, panel_uuid)
        self.users[panel_uuid] = user
        return user

    async def get_user_by_uuid(self, panel_uuid: uuid.UUID) -> PanelUser | None:
        return self.users.get(panel_uuid)

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser | None:
        return next((u for u in self.users.values() if u.telegram_id == telegram_id), None)

    async def enable_user(self, panel_uuid: uuid.UUID) -> None: ...

    async def disable_user(self, panel_uuid: uuid.UUID) -> None: ...

    async def delete_user(self, panel_uuid: uuid.UUID) -> None:
        self.users.pop(panel_uuid, None)
        self.deleted.append(panel_uuid)

    async def reset_traffic(self, panel_uuid: uuid.UUID) -> None: ...

    async def revoke_subscription(self, panel_uuid: uuid.UUID) -> PanelUser:
        user = self.users[panel_uuid]
        rotated = dataclasses.replace(
            user, subscription_url=f"{user.subscription_url}?r={uuid.uuid4().hex[:6]}"
        )
        self.users[panel_uuid] = rotated
        return rotated

    async def drop_connections(self, panel_uuid: uuid.UUID) -> None: ...

    async def get_internal_squads(self) -> list[PanelSquad]:
        return [PanelSquad(uuid=uuid.uuid4(), name="test-squad")]

    async def get_nodes(self) -> list[PanelNode]:
        return [PanelNode(uuid=uuid.uuid4(), name="node-1", is_online=True)]

    # not part of the protocol, but handy in assertions
    def created_count(self) -> int:
        return len(self.users) + len(self.deleted)
