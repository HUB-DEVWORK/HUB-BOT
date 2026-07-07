"""BotConfigService — merged view + typed access to hot-reload bot parameters.

The registry (``core/config_registry``) declares every parameter; ``bot_config_values``
stores admin overrides. Reads go through a small in-process cache (per-service instance)
that is invalidated on every write, so PATCHing from the cabinet applies without a
restart. Secret values are Fernet-encrypted at rest and masked in listings.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from src.core import config_registry as registry
from src.core.exceptions import ConfigError as CryptError
from src.core.exceptions import DomainError

if TYPE_CHECKING:
    from src.infrastructure.database.uow import UnitOfWork
    from src.infrastructure.payments.crypto import SecretBox

_MASK = "••••••••"


class BotConfigError(DomainError):
    """Unknown key or value not coercible to the declared type."""


class BotConfigService:
    # Web/bot/worker are separate processes: a same-process invalidate() cannot reach
    # them, so the cache self-expires. 15s keeps "hot-reload" honest at negligible cost.
    CACHE_TTL = 15.0

    def __init__(self, secret_box: SecretBox | None = None) -> None:
        self._box = secret_box
        self._cache: dict[str, Any] | None = None
        self._cache_at = 0.0

    # --- reads ---------------------------------------------------------------

    async def value(self, uow: UnitOfWork, key: str) -> Any:
        """Effective (typed, decrypted) value of one parameter."""
        if not registry.has(key):
            raise BotConfigError(f"unknown config key: {key}")
        values = await self._effective(uow)
        return values[key]

    async def snapshot(self, uow: UnitOfWork) -> dict[str, Any]:
        """Effective values for every registered key (secrets decrypted)."""
        return dict(await self._effective(uow))

    async def listing(self, uow: UnitOfWork, lang: str = "ru") -> list[dict[str, Any]]:
        """Rows for the settings screen: registry metadata + value (secrets masked)."""
        values = await self._effective(uow)
        overrides = await uow.bot_config.as_dict()
        ru = lang == "ru"
        rows: list[dict[str, Any]] = []
        for spec in registry.REGISTRY:
            val = values[spec.key]
            if spec.secret and val:
                val = _MASK
            rows.append(
                {
                    "key": spec.key,
                    "category": spec.category.value,
                    "type": spec.type.value,
                    "name": spec.name_ru if ru else spec.name_en,
                    "description": spec.desc_ru if ru else spec.desc_en,
                    "value": val,
                    "default": None if spec.secret else spec.default,
                    "is_overridden": spec.key in overrides,
                }
            )
        return rows

    # --- writes --------------------------------------------------------------

    async def set_values(self, uow: UnitOfWork, changes: dict[str, Any]) -> list[str]:
        """Apply a batch of changes; returns the list of keys actually written."""
        written: list[str] = []
        for key, raw in changes.items():
            if not registry.has(key):
                raise BotConfigError(f"unknown config key: {key}")
            spec = registry.spec(key)
            if spec.secret and raw == _MASK:
                continue  # masked round-trip from the UI — no change
            try:
                value = registry.coerce(key, raw)
            except (TypeError, ValueError) as exc:
                raise BotConfigError(f"{key}: {exc}") from exc
            if spec.secret and value and self._box is not None:
                value = self._box.encrypt(str(value))
            await uow.bot_config.upsert(key, value)
            written.append(key)
        self._cache = None
        return written

    async def reset(self, uow: UnitOfWork, keys: list[str]) -> None:
        """Drop overrides (revert to registry defaults)."""
        for key in keys:
            await uow.bot_config.delete_by(key=key)
        self._cache = None

    def invalidate(self) -> None:
        self._cache = None

    # --- internals -------------------------------------------------------------

    async def _effective(self, uow: UnitOfWork) -> dict[str, Any]:
        if self._cache is not None and time.monotonic() - self._cache_at > self.CACHE_TTL:
            self._cache = None
        if self._cache is None:
            overrides = await uow.bot_config.as_dict()
            merged: dict[str, Any] = {}
            for spec in registry.REGISTRY:
                if spec.key in overrides:
                    val = overrides[spec.key]
                    if spec.secret and isinstance(val, str) and val and self._box is not None:
                        try:
                            val = self._box.decrypt(val)
                        except CryptError:  # undecryptable (rotated key) -> default
                            val = spec.default
                    merged[spec.key] = val
                else:
                    merged[spec.key] = spec.default
            self._cache = merged
            self._cache_at = time.monotonic()
        return self._cache


def category_sections(lang: str = "ru") -> list[dict[str, str]]:
    """Ordered category metadata for the settings screen."""
    return [
        {"id": cat.value, "name": registry.CATEGORY_NAMES[cat]["ru" if lang == "ru" else "en"]}
        for cat in registry.CATEGORY_ORDER
    ]
