"""Domain events + EventBus protocol.

Events carry an ``i18n_key`` + ``kwargs`` (not rendered text) so notifications render in
each recipient's locale (remnashop pattern). Side-effects (referral, notifications,
analytics) subscribe to the bus and are best-effort — isolated from the atomic core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base domain event. Subclasses add typed payload fields."""

    # Optional user-facing message description, resolved later in the recipient's locale.
    i18n_key: str | None = None
    i18n_kwargs: dict[str, object] = field(default_factory=dict)


@runtime_checkable
class EventBus(Protocol):
    """Publishes domain events to registered async handlers. Publishing never raises."""

    async def publish(self, event: DomainEvent) -> None: ...
