"""Lightweight hook bus for the module system.

A thin hook bus for HUB-BOT:

* module ownership is derived from ``func.__module__`` — a hook defined in
  ``src.bot.modules.<name>.*`` is owned by ``<name>``; core-defined hooks have
  no owner and always run.
* whether a module is enabled is decided by a checker the loader installs via
  :func:`set_enabled_checker` (backed by ``MODULE_<NAME>_ENABLED`` config).
* one broken/slow hook never takes down the caller — every hook is time-boxed
  and its exceptions are logged, not propagated.

Core call sites use :func:`run_hooks` at extension points (see the module-system
design doc). Modules register with :func:`register_hook` from their ``hooks.py``.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.logging import get_logger

log = get_logger("bot.hooks")

# Per-hook timeout, seconds. Kept generous — hooks decorate UI/render paths but
# must never hang the event loop.
DEFAULT_HOOK_TIMEOUT = 5.0

# name -> list of (func, owner_module_or_None)
_hooks: dict[str, list[tuple[Callable[..., Any], str | None]]] = {}

# Installed by the loader once module enable-state is known. Default: everything
# on (so hooks work even before the loader wires the config-backed checker).
_is_enabled: Callable[[str], bool] = lambda _name: True


def set_enabled_checker(fn: Callable[[str], bool]) -> None:
    """Install the predicate used to skip hooks owned by disabled modules."""
    global _is_enabled
    _is_enabled = fn


def owner(func: Callable[..., Any]) -> str | None:
    """Return the owning module name for ``func`` (or ``None`` for core hooks)."""
    m = getattr(func, "__module__", "") or ""
    prefix = "src.bot.modules."
    if m.startswith(prefix):
        rest = m[len(prefix):]
        return rest.split(".", 1)[0] or None
    return None


def register_hook(name: str, func: Callable[..., Any] | None = None):
    """Register ``func`` for hook ``name``. Usable as decorator or direct call."""
    if func is None:

        def deco(f: Callable[..., Any]):
            _hooks.setdefault(name, []).append((f, owner(f)))
            log.info("hook_registered", hook=name, func=f.__name__, owner=owner(f))
            return f

        return deco

    _hooks.setdefault(name, []).append((func, owner(func)))
    log.info("hook_registered", hook=name, func=func.__name__, owner=owner(func))
    return func


def unregister_module_hooks(module_name: str) -> None:
    """Drop every hook owned by ``module_name`` (used on disable/unload)."""
    for key, lst in list(_hooks.items()):
        kept = [(f, own) for (f, own) in lst if own != module_name]
        if kept:
            _hooks[key] = kept
        else:
            _hooks.pop(key, None)


def registered_hooks() -> dict[str, list[str]]:
    """Introspection helper: ``{hook_name: [owner_or_'core', ...]}``."""
    return {k: [own or "core" for (_f, own) in lst] for k, lst in _hooks.items()}


async def run_hooks(name: str, *, require_enabled: bool = True, **kwargs) -> list[Any]:
    """Invoke every hook registered for ``name``; collect truthy results.

    Hooks owned by a disabled module are skipped when ``require_enabled`` is set.
    Both sync and async hook functions are supported; each is time-boxed by
    ``DEFAULT_HOOK_TIMEOUT`` and its failures are logged, never raised.
    """
    results: list[Any] = []
    for func, own in _hooks.get(name, []):
        if require_enabled and own and not _is_enabled(own):
            continue
        try:
            res: Any = func(**kwargs)
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(res, timeout=DEFAULT_HOOK_TIMEOUT)
            if res:
                results.append(res)
        except (TimeoutError, asyncio.TimeoutError):
            log.error("hook_timeout", hook=name, func=getattr(func, "__name__", str(func)))
        except Exception as e:  # noqa: BLE001 — isolate a bad hook from the caller
            log.error(
                "hook_error",
                hook=name,
                func=getattr(func, "__name__", str(func)),
                error=str(e),
                exc_info=True,
            )
    return results


__all__ = (
    "register_hook",
    "unregister_module_hooks",
    "run_hooks",
    "set_enabled_checker",
    "registered_hooks",
    "owner",
    "DEFAULT_HOOK_TIMEOUT",
)


# Re-exported type alias for module authors.
HookFunc = Callable[..., Awaitable[Any] | Any]
