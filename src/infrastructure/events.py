"""In-process EventBus. Best-effort: a failing handler never breaks the publisher.

Side-effects (referral, notifications, analytics) register handlers here. For multi-process
fan-out later, swap this for a Redis/broker-backed bus behind the same protocol.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.application.common.events import DomainEvent
from src.core.logging import get_logger

log = get_logger(__name__)

Handler = Callable[[DomainEvent], Awaitable[None]]


class InProcessEventBus:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def publish(self, event: DomainEvent) -> None:
        for handler in self._handlers:
            try:
                await handler(event)
            except Exception:
                log.warning("event_handler_failed", event_type=type(event).__name__, exc_info=True)
