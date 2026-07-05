"""Identifier generation (permanent, unguessable). Uses ``secrets``, never the row id."""

from __future__ import annotations

import secrets

from src.core.constants import REFERRAL_CODE_LENGTH, SHORT_ID_LENGTH

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _token(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def generate_short_id() -> str:
    """Permanent per-subscription suffix (ADR-0003). Never derived from a mutable id."""
    return _token(SHORT_ID_LENGTH)


def generate_referral_code() -> str:
    return _token(REFERRAL_CODE_LENGTH)
