"""Recording EventBus for assertions."""

from __future__ import annotations

from src.application.common.events import DomainEvent


class RecordingEventBus:
    """Satisfies application.common.events.EventBus; keeps published events."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    async def publish(self, event: DomainEvent) -> None:
        self.events.append(event)

    def of_type(self, cls: type[DomainEvent]) -> list[DomainEvent]:
        return [e for e in self.events if isinstance(e, cls)]
