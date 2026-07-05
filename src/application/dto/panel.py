"""Typed DTOs returned by the Remnawave client (never raw dicts — gotcha-free consumption)."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PanelUser:
    """A user as it exists on the Remnawave panel."""

    uuid: uuid.UUID
    short_id: str
    username: str
    is_enabled: bool
    expire_at: dt.datetime | None
    traffic_limit_bytes: int
    traffic_used_bytes: int
    device_limit: int | None
    subscription_url: str | None
    telegram_id: int | None = None
    internal_squads: tuple[str, ...] = ()
    external_squad: str | None = None
    tag: str | None = None  # e.g. "IMPORTED" — ignore user.created for these (gotcha #19)


@dataclass(frozen=True, slots=True)
class PanelSquad:
    """A Remnawave internal squad (sellable server/location)."""

    uuid: uuid.UUID
    name: str
    members_count: int = 0


@dataclass(frozen=True, slots=True)
class PanelNode:
    uuid: uuid.UUID
    name: str
    is_online: bool


@dataclass(frozen=True, slots=True)
class PanelVersion:
    """Panel version + derived capability flags (probed at startup, not hardcoded)."""

    raw: str
    major: int
    minor: int
    patch: int
    capabilities: frozenset[str] = frozenset()

    @property
    def tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class ProvisionSpec:
    """What to create/update on the panel for one subscription."""

    short_id: str
    telegram_id: int | None
    username: str
    expire_at: dt.datetime
    traffic_limit_bytes: int  # 0 -> unlimited
    device_limit: int | None
    internal_squads: tuple[str, ...] = ()
    external_squad: str | None = None
    description: str | None = None
    extra: dict[str, object] = field(default_factory=dict)
