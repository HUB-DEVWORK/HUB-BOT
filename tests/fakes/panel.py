"""In-memory fake of the RemnawaveClient protocol — no network, deterministic."""

from __future__ import annotations

import dataclasses
import uuid

from src.application.dto.panel import (
    PanelDevice,
    PanelNode,
    PanelRef,
    PanelSquad,
    PanelUser,
    PanelUserRef,
    PanelVersion,
    ProvisionSpec,
)


def _key(ref: PanelRef) -> uuid.UUID | None:
    """The fake stores users by uuid, like a 2.x panel; refs may also carry one."""
    if isinstance(ref, PanelUserRef):
        return ref.uuid
    return ref


class FakeRemnawaveClient:
    """Records created users; satisfies application.common.panel.RemnawaveClient."""

    def __init__(self, *, version: tuple[int, int, int] = (2, 8, 0)) -> None:
        self._version = version
        self.users: dict[uuid.UUID | None, PanelUser] = {}
        self.deleted: list[uuid.UUID | None] = []
        self.devices: dict[uuid.UUID | None, list[PanelDevice]] = {}
        self.users_ips: dict[str, list[tuple[str, list[str]]]] = {}

    async def get_version(self) -> PanelVersion:
        maj, minr, pat = self._version
        caps = frozenset({"v3_api"}) if maj >= 3 else frozenset()
        return PanelVersion(
            raw=".".join(map(str, self._version)),
            major=maj,
            minor=minr,
            patch=pat,
            capabilities=caps,
        )

    def _make_user(self, spec: ProvisionSpec, panel_uuid: uuid.UUID | None) -> PanelUser:
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

    async def update_user(self, ref: PanelRef, spec: ProvisionSpec) -> PanelUser:
        key = _key(ref)
        user = self._make_user(spec, key)
        self.users[key] = user
        return user

    async def get_user(self, ref: PanelRef) -> PanelUser | None:
        return self.users.get(_key(ref))

    async def get_user_by_telegram_id(self, telegram_id: int) -> PanelUser | None:
        return next((u for u in self.users.values() if u.telegram_id == telegram_id), None)

    async def enable_user(self, ref: PanelRef) -> None:
        key = _key(ref)
        user = self.users.get(key)
        if user is not None:
            self.users[key] = dataclasses.replace(user, is_enabled=True)

    async def disable_user(self, ref: PanelRef) -> None:
        key = _key(ref)
        user = self.users.get(key)
        if user is not None:
            self.users[key] = dataclasses.replace(user, is_enabled=False)

    async def delete_user(self, ref: PanelRef) -> None:
        key = _key(ref)
        self.users.pop(key, None)
        self.deleted.append(key)

    async def reset_traffic(self, ref: PanelRef) -> None: ...

    async def revoke_subscription(self, ref: PanelRef) -> PanelUser:
        key = _key(ref)
        user = self.users[key]
        rotated = dataclasses.replace(
            user, subscription_url=f"{user.subscription_url}?r={uuid.uuid4().hex[:6]}"
        )
        self.users[key] = rotated
        return rotated

    async def drop_connections(self, ref: PanelRef) -> None: ...

    async def start_users_ips_job(self, node_uuid: str) -> str:
        return f"job-{node_uuid}"

    async def get_users_ips_result(self, job_id: str) -> list[tuple[str, list[str]]] | None:
        return self.users_ips.get(job_id, [])

    async def get_devices(self, ref: PanelRef) -> list[PanelDevice]:
        return list(self.devices.get(_key(ref), []))

    async def delete_device(self, ref: PanelRef, hwid: str) -> None:
        key = _key(ref)
        self.devices[key] = [d for d in self.devices.get(key, []) if d.hwid != hwid]

    async def get_internal_squads(self) -> list[PanelSquad]:
        return [PanelSquad(uuid=uuid.uuid4(), name="test-squad")]

    async def get_nodes(self) -> list[PanelNode]:
        return [PanelNode(uuid=uuid.uuid4(), name="node-1", is_online=True)]

    # not part of the protocol, but handy in assertions
    def created_count(self) -> int:
        return len(self.users) + len(self.deleted)
