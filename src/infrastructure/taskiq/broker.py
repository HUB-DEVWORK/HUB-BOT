"""taskiq broker + scheduler, plus a worker-lifetime AppContainer.

The webhook route enqueues work here and returns 200 immediately (gotcha #6). The worker
holds one :class:`AppContainer` so jobs share the same singletons (engine, panel client).
"""

from __future__ import annotations

from taskiq import SimpleRetryMiddleware, TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker

from src.core.config import get_settings
from src.core.logging import configure_logging
from src.infrastructure.di import AppContainer

_settings = get_settings()
# ListQueueBroker acks on pickup — without retries a task that raises is simply lost.
# Tasks opt in with `retry_on_error=True`; the payment reconciler covers longer outages.
broker = ListQueueBroker(_settings.redis.url).with_middlewares(
    SimpleRetryMiddleware(default_retry_count=5)
)
scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])

_container: AppContainer | None = None


def get_container() -> AppContainer:
    if _container is None:
        raise RuntimeError("AppContainer is not initialised (worker not started)")
    return _container


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def _on_startup(state: TaskiqState) -> None:
    global _container
    configure_logging(level=_settings.log.level, json=_settings.log.use_json)
    _container = AppContainer(_settings)


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def _on_shutdown(state: TaskiqState) -> None:
    if _container is not None:
        await _container.aclose()
