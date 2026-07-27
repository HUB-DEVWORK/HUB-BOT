"""Light, aiogram-free module discovery shared by the web process.

The bot's :mod:`src.bot.modules.loader` owns the full lifecycle (it imports
aiogram-heavy code). The web process only needs to *list* modules - name,
version, whether a module is built-in or uploaded, whether it declares config -
without importing any router/middleware code. This module provides exactly that
by reading each module's pure-data ``manifest.py`` (the same contract the loader
and ``config_registry`` already rely on).
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from dataclasses import dataclass

from src.bot.modules import EXTERNAL_MODULES_DIR

_PKG = "src.bot.modules"
# Framework files that live in the package but are never modules.
_RESERVED = {"api", "loader", "hooks", "discovery"}


@dataclass(frozen=True)
class DiscoveredModule:
    name: str
    title_ru: str
    title_en: str
    version: str
    default_enabled: bool
    requires: tuple[str, ...]
    enable_key: str
    external: bool
    has_config: bool
    config_keys: tuple[str, ...]


def _is_external(finder_path: str) -> bool:
    if not finder_path:
        return False
    try:
        return os.path.abspath(finder_path) == os.path.abspath(EXTERNAL_MODULES_DIR)
    except Exception:
        return False


def _config_keys(name: str, enable_key: str) -> tuple[str, ...]:
    """Keys this module contributes to the settings registry: its enable toggle
    first, then any params declared in its ``config.py`` ``CONFIG`` tuple. Used to
    deep-link the settings screen to just this module's parameters."""
    keys = [enable_key]
    try:
        cfg = importlib.import_module(f"{_PKG}.{name}.config")
        for sp in getattr(cfg, "CONFIG", ()):
            k = getattr(sp, "key", None)
            if isinstance(k, str) and k not in keys:
                keys.append(k)
    except Exception:
        pass
    return tuple(keys)


def discover_manifests() -> list[DiscoveredModule]:
    """Return every discoverable module's manifest data, built-ins first.

    Never raises: a module whose ``manifest.py`` is broken is skipped. A name
    collision between a built-in and an uploaded module resolves to the built-in
    (its directory is earlier on ``__path__``), so uploads can't shadow core.
    """
    try:
        pkg = importlib.import_module(_PKG)
    except Exception:
        return []

    out: list[DiscoveredModule] = []
    seen: set[str] = set()
    for info in pkgutil.iter_modules(pkg.__path__):
        if not info.ispkg or info.name in _RESERVED or info.name.startswith("_"):
            continue
        name = info.name
        if name in seen:
            continue
        seen.add(name)
        try:
            manifest = importlib.import_module(f"{_PKG}.{name}.manifest").MANIFEST
        except Exception:
            continue
        finder_path = getattr(info.module_finder, "path", "")
        has_config = bool(finder_path) and os.path.isfile(
            os.path.join(finder_path, name, "config.py")
        )
        out.append(
            DiscoveredModule(
                name=name,
                title_ru=getattr(manifest, "title_ru", name),
                title_en=getattr(manifest, "title_en", name),
                version=getattr(manifest, "version", "?"),
                default_enabled=bool(getattr(manifest, "default_enabled", False)),
                requires=tuple(getattr(manifest, "requires", ()) or ()),
                enable_key=manifest.enable_key(),
                external=_is_external(finder_path),
                has_config=has_config,
                config_keys=_config_keys(name, manifest.enable_key()),
            )
        )
    out.sort(key=lambda m: (m.external, m.name))
    return out


__all__ = ("EXTERNAL_MODULES_DIR", "DiscoveredModule", "discover_manifests")
