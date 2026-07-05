"""Structured logging setup (structlog over stdlib logging).

Call :func:`configure_logging` once at process start. Use ``structlog.get_logger()``
elsewhere. JSON output in production, human-readable in dev.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, level: str = "INFO", json: bool = False) -> None:
    """Configure structlog + stdlib logging.

    Args:
        level: root log level name (DEBUG/INFO/WARNING/ERROR).
        json: emit JSON lines (prod) instead of a console-friendly renderer.
    """
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (sqlalchemy, uvicorn, aiogram) through the same handler.
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=log_level)


def get_logger(name: str | None = None) -> structlog.typing.FilteringBoundLogger:
    """Return a bound logger. Prefer passing ``__name__``."""
    return structlog.get_logger(name)
