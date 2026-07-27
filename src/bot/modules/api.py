"""Public contract for HUB-BOT modules.

A module is a package under ``src.bot.modules.<name>`` that exposes a
``MANIFEST`` (:class:`ModuleManifest`) and, optionally, a ``register(reg)``
entry point. During ``register`` the module hands the core its aiogram router,
config params, DB migration and middlewares through a :class:`Registrar`.

Everything except ``MANIFEST`` is optional - the smallest useful module is a
``router.py`` plus a ``MANIFEST`` (the loader auto-wires a bare ``router`` if
no ``register`` is defined; see loader).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid importing aiogram / config at contract-load time
    from aiogram import Router

    from src.core.config_registry import ParamSpec


# Router priority slots. Core routers occupy the ends; modules live in-between.
# Lower runs earlier (aiogram tries routers in include order).
ROUTER_PRIORITY_MIN = 20
ROUTER_PRIORITY_MAX = 80
DEFAULT_ROUTER_PRIORITY = 50


@dataclass(frozen=True)
class ModuleManifest:
    """Declarative identity of a module. Pure data, no imports of heavy deps."""

    name: str  # unique code == package folder name, e.g. "message_cleaner"
    title_ru: str
    title_en: str
    version: str = "1.0.0"
    router_priority: int = DEFAULT_ROUTER_PRIORITY  # slot in [20..80]
    default_enabled: bool = False  # off until the owner flips MODULE_<NAME>_ENABLED
    requires: tuple[str, ...] = ()  # module codes this one depends on

    def enable_key(self) -> str:
        """Config key that gates this module: ``MODULE_<NAME>_ENABLED``."""
        return f"MODULE_{self.name.upper()}_ENABLED"


@dataclass
class MiddlewareSpec:
    """A middleware to attach to the dispatcher for one event type."""

    middleware: Any
    event: str = "callback_query"  # "message" | "callback_query" | "pre_checkout_query"


@dataclass
class Registrar:
    """Collector passed to ``register(reg)``. Modules push their pieces here.

    The loader reads these back to assemble routers, config and migrations. All
    lists default-empty so a module fills in only what it needs.
    """

    module: str
    routers: list[tuple[Router, int]] = field(default_factory=list)
    config: list[ParamSpec] = field(default_factory=list)
    migrations: list[Callable[..., Any]] = field(default_factory=list)
    middlewares: list[MiddlewareSpec] = field(default_factory=list)

    def add_router(self, router: Router, priority: int | None = None) -> None:
        self.routers.append((router, priority if priority is not None else DEFAULT_ROUTER_PRIORITY))

    def add_config(self, specs: Sequence[ParamSpec] | ParamSpec) -> None:
        try:
            self.config.extend(specs)  # type: ignore[arg-type]
        except TypeError:
            self.config.append(specs)  # type: ignore[arg-type]

    def add_migration(self, fn: Callable[..., Any]) -> None:
        """Register an idempotent migration ``async fn(session) -> None``."""
        self.migrations.append(fn)

    def add_middleware(self, middleware: Any, event: str = "callback_query") -> None:
        self.middlewares.append(MiddlewareSpec(middleware=middleware, event=event))


__all__ = (
    "DEFAULT_ROUTER_PRIORITY",
    "ROUTER_PRIORITY_MAX",
    "ROUTER_PRIORITY_MIN",
    "MiddlewareSpec",
    "ModuleManifest",
    "Registrar",
)
