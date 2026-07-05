"""Reusable config validation helpers (safety rails, see docs/context/07 #15)."""

from __future__ import annotations

from src.core.exceptions import ConfigError

# Values that indicate a secret was never filled in. Rejected outright.
PLACEHOLDER_VALUES = {"", "change_me", "changeme", "todo", "xxx", "your_token_here"}


def is_placeholder(value: str | None) -> bool:
    return value is None or value.strip().lower() in PLACEHOLDER_VALUES


def ensure_filled(value: str | None, field: str) -> None:
    """Raise if a required secret is empty or a known placeholder."""
    if is_placeholder(value):
        raise ConfigError(f"{field} is required and must not be a placeholder value")


def ensure_fernet_key(value: str, field: str) -> None:
    """Validate a Fernet key: 44-char urlsafe base64 that Fernet accepts."""
    if len(value) != 44:
        raise ConfigError(f"{field} must be a 44-char urlsafe-base64 Fernet key (got {len(value)})")
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode())
    except Exception as exc:
        raise ConfigError(f"{field} is not a valid Fernet key: {exc}") from exc
