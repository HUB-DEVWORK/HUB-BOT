"""Module discovery and loading.

Lifecycle (driven from ``src/bot/main.py::run`` where the DI container exists):

1. :meth:`discover` — scan ``src/bot/modules/*/``, import each module's *light*
   ``manifest.py`` and read its :class:`~src.bot.modules.api.ModuleManifest`.
2. :meth:`resolve_enabled` — decide which modules are on, from
   ``MODULE_<NAME>_ENABLED`` config plus ``requires`` dependencies.
3. :meth:`load` — for each enabled module import the full package, run its
   ``register(reg)`` (or auto-wire a bare ``router``), import ``hooks.py`` so its
   ``@register_hook`` decorators fire, and collect routers/migrations/middlewares.
4. :meth:`routers` — priority-sorted routers, spliced into ``build_router``.
5. :meth:`run_migrations` / :meth:`apply_middlewares` — before polling starts.

Config params of modules are registered separately and earlier, at
``config_registry`` import time (see ``_load_module_config`` there), so they are
visible in *both* the bot and the web process without importing aiogram code.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING, Any

from src.bot import hooks as hook_bus
from src.bot.modules.api import ModuleManifest, Registrar

from src.core.logging import get_logger

if TYPE_CHECKING:
    from aiogram import Dispatcher, Router

log = get_logger("bot.modules")

_PKG = "src.bot.modules"
# Package members that are framework files, never modules.
_RESERVED = {"api", "loader", "hooks"}


class LoadedModule:
    __slots__ = ("manifest", "registrar", "package", "setup")

    def __init__(
        self,
        manifest: ModuleManifest,
        registrar: Registrar,
        package: Any,
        setup: Any = None,
    ):
        self.manifest = manifest
        self.registrar = registrar
        self.package = package
        self.setup = setup


class ModuleSystem:
    """Owns discovery + load state for the running bot process."""

    def __init__(self) -> None:
        self._manifests: dict[str, ModuleManifest] = {}
        self._enabled: set[str] = set()
        self._loaded: list[LoadedModule] = []

    # -- 1. discovery ----------------------------------------------------
    def discover(self) -> dict[str, ModuleManifest]:
        """Import every module's light ``manifest.py`` and cache manifests."""
        pkg = importlib.import_module(_PKG)
        for info in pkgutil.iter_modules(pkg.__path__):
            if not info.ispkg or info.name in _RESERVED or info.name.startswith("_"):
                continue
            name = info.name
            try:
                mod = importlib.import_module(f"{_PKG}.{name}.manifest")
                manifest = getattr(mod, "MANIFEST")
            except Exception as e:  # noqa: BLE001
                log.error("module_manifest_error", module=name, error=str(e), exc_info=True)
                continue
            if not isinstance(manifest, ModuleManifest):
                log.error("module_bad_manifest", module=name)
                continue
            if manifest.name != name:
                log.warning("module_name_mismatch", folder=name, declared=manifest.name)
            self._manifests[name] = manifest
        log.info("modules_discovered", count=len(self._manifests), names=sorted(self._manifests))
        return dict(self._manifests)

    # -- 2. enable resolution -------------------------------------------
    async def resolve_enabled(self, container: Any) -> set[str]:
        """Compute the enabled set from config + ``requires`` dependencies."""
        # Ext modules may have been spliced onto the package path AFTER
        # config_registry was first imported (import-order race), leaving their
        # enable keys unregistered in this process. Re-seed idempotently so the
        # bot_config.value() read below finds them instead of throwing.
        from src.core.config_registry import refresh_module_config
        refresh_module_config()
        raw: set[str] = set()
        async with container.uow() as uow:
            for name, manifest in self._manifests.items():
                try:
                    on = bool(await container.bot_config.value(uow, manifest.enable_key()))
                except Exception:  # noqa: BLE001 — fall back to manifest default
                    on = manifest.default_enabled
                if on:
                    raw.add(name)

        # Drop modules whose required dependencies are not enabled (iterate to
        # a fixpoint so a chain A->B->C collapses correctly).
        enabled = set(raw)
        changed = True
        while changed:
            changed = False
            for name in list(enabled):
                missing = [r for r in self._manifests[name].requires if r not in enabled]
                if missing:
                    log.warning("module_deps_unmet", module=name, missing=missing)
                    enabled.discard(name)
                    changed = True
        self._enabled = enabled
        hook_bus.set_enabled_checker(lambda n: n in self._enabled)
        log.info("modules_enabled", enabled=sorted(enabled), discovered=sorted(self._manifests))
        return set(enabled)

    # -- 3. load ---------------------------------------------------------
    def load(self) -> None:
        """Import + register every enabled module. Idempotent per instance."""
        self._loaded.clear()
        for name in sorted(self._enabled):
            manifest = self._manifests[name]
            try:
                pkg = importlib.import_module(f"{_PKG}.{name}")
                reg = Registrar(module=name)
                register = getattr(pkg, "register", None)
                if callable(register):
                    register(reg)
                else:
                    self._auto_wire(name, manifest, reg)
                # Importing hooks.py triggers its @register_hook side effects.
                self._import_optional(name, "hooks")
                setup = getattr(pkg, "setup", None)
                self._loaded.append(LoadedModule(manifest, reg, pkg, setup))
                log.info(
                    "module_loaded",
                    module=name,
                    version=manifest.version,
                    routers=len(reg.routers),
                    config=len(reg.config),
                    migrations=len(reg.migrations),
                    middlewares=len(reg.middlewares),
                )
            except Exception as e:  # noqa: BLE001 — one bad module must not kill the bot
                log.error("module_load_error", module=name, error=str(e), exc_info=True)
                self._enabled.discard(name)
        hook_bus.set_enabled_checker(lambda n: n in self._enabled)

    def _auto_wire(self, name: str, manifest: ModuleManifest, reg: Registrar) -> None:
        """Default wiring when a module has no ``register``: just its router."""
        router_mod = self._import_optional(name, "router")
        if router_mod is not None:
            router = getattr(router_mod, "router", None)
            if router is not None:
                reg.add_router(router, manifest.router_priority)

    def _import_optional(self, name: str, sub: str) -> Any:
        try:
            return importlib.import_module(f"{_PKG}.{name}.{sub}")
        except ModuleNotFoundError:
            return None

    # -- 4. consumption --------------------------------------------------
    def routers(self) -> list["Router"]:
        """All enabled-module routers, sorted by (priority, module name)."""
        pairs: list[tuple[int, str, "Router"]] = []
        for lm in self._loaded:
            for router, prio in lm.registrar.routers:
                pairs.append((prio, lm.manifest.name, router))
        pairs.sort(key=lambda t: (t[0], t[1]))
        return [r for (_p, _n, r) in pairs]

    async def run_migrations(self, session: Any) -> None:
        """Run every enabled module's idempotent migration(s)."""
        for lm in self._loaded:
            for fn in lm.registrar.migrations:
                try:
                    await fn(session)
                    log.info("module_migrated", module=lm.manifest.name, fn=fn.__name__)
                except Exception as e:  # noqa: BLE001
                    log.error(
                        "module_migration_error",
                        module=lm.manifest.name,
                        fn=getattr(fn, "__name__", str(fn)),
                        error=str(e),
                        exc_info=True,
                    )

    def apply_middlewares(self, dp: "Dispatcher") -> None:
        """Attach each module's middlewares to the dispatcher event buses."""
        for lm in self._loaded:
            for spec in lm.registrar.middlewares:
                bus = getattr(dp, spec.event, None)
                if bus is None:
                    log.warning("module_mw_bad_event", module=lm.manifest.name, event=spec.event)
                    continue
                bus.middleware(spec.middleware)
                log.info("module_mw_attached", module=lm.manifest.name, event=spec.event)

    async def apply_setup(self, bot: Any, dp: "Dispatcher", container: Any) -> None:
        """Run each enabled module's ``setup(bot, dp, container)`` lifecycle hook.

        The hook is where a middleware-only module wires itself into the live bot
        and dispatcher (e.g. bot.session middleware + dp.message.outer_middleware),
        the analogue of SoloBot's ``install()``. Sync or async; one failure is
        logged and skipped, never fatal.
        """
        for lm in self._loaded:
            fn = lm.setup
            if fn is None:
                continue
            try:
                result = fn(bot, dp, container)
                if inspect.isawaitable(result):
                    await result
                log.info("module_setup_done", module=lm.manifest.name)
            except Exception as e:  # noqa: BLE001
                log.error(
                    "module_setup_error",
                    module=lm.manifest.name,
                    error=str(e),
                    exc_info=True,
                )

    # -- introspection ---------------------------------------------------
    @property
    def enabled(self) -> set[str]:
        return set(self._enabled)

    @property
    def manifests(self) -> dict[str, ModuleManifest]:
        return dict(self._manifests)


__all__ = ("ModuleSystem", "LoadedModule")
