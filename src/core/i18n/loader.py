"""JSON i18n loader + Translator.

Locale files live in ``locales/<code>.json`` with flat UPPER_SNAKE keys. Optional DB/user
overrides are deep-merged on top. Domain events carry ``(key, kwargs)`` and are rendered
in the recipient's locale via :meth:`Translator.gettext` — never pre-render text in events.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.enums import Locale

DEFAULT_LOCALES_DIR = Path(__file__).resolve().parents[3] / "locales"


class _SafeDict(dict[str, Any]):
    """Leaves unknown ``{placeholders}`` intact instead of raising KeyError."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class Translator:
    """In-memory translator over flat key->text maps per locale."""

    def __init__(self, catalogs: dict[Locale, dict[str, str]]) -> None:
        self._catalogs = catalogs

    def gettext(self, key: str, locale: Locale | None = None, /, **kwargs: object) -> str:
        """Return the localized string for ``key``, formatted with ``kwargs``.

        Falls back to the default locale, then to the key itself, so a missing
        translation is visible but never crashes a flow.
        """
        loc = locale or Locale.default()
        catalog = self._catalogs.get(loc) or self._catalogs.get(Locale.default(), {})
        template = catalog.get(key)
        if template is None and loc is not Locale.default():
            template = self._catalogs.get(Locale.default(), {}).get(key)
        if template is None:
            return key
        return template.format_map(_SafeDict(kwargs))

    def has(self, key: str, locale: Locale | None = None) -> bool:
        catalog = self._catalogs.get(locale or Locale.default(), {})
        return key in catalog


def _deep_merge(base: dict[str, str], override: dict[str, str]) -> dict[str, str]:
    merged = dict(base)
    merged.update(override)
    return merged


def load_translations(
    locales_dir: Path = DEFAULT_LOCALES_DIR,
    overrides: dict[Locale, dict[str, str]] | None = None,
) -> Translator:
    """Load all ``<locale>.json`` files and merge optional overrides on top."""
    catalogs: dict[Locale, dict[str, str]] = {}
    for locale in Locale:
        path = locales_dir / f"{locale.value}.json"
        data: dict[str, str] = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        if overrides and locale in overrides:
            data = _deep_merge(data, overrides[locale])
        catalogs[locale] = data
    return Translator(catalogs)
