"""Fernet encryption for gateway credentials at rest (gotcha #15)."""

from __future__ import annotations

import contextlib

from cryptography.fernet import Fernet, InvalidToken

from src.core.exceptions import ConfigError


class SecretBox:
    """Encrypts/decrypts short secret strings with the app Fernet key."""

    def __init__(self, crypt_key: str) -> None:
        try:
            self._fernet = Fernet(crypt_key.encode())
        except Exception as exc:
            raise ConfigError(f"invalid APP__CRYPT_KEY: {exc}") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ConfigError("gateway secret failed to decrypt (wrong crypt key?)") from exc


# A gateway settings field holds a secret when its name hints at one. The SAME predicate
# ENCRYPTS (admin write path) and DECRYPTS (here), so the two can never disagree. The old
# code encrypted any *key/secret/token/password*-named field on save but decrypted only a
# fixed subset on read, leaving fields like ``webhook_secret`` / ``private_key`` / ``secret_id``
# encrypted forever — which silently broke webhook signature verification on those gateways.
_SECRET_HINTS = ("key", "secret", "token", "password")


def is_secret_key(name: str) -> bool:
    """True when a payment-gateway settings field name denotes a secret to encrypt at rest."""
    lowered = name.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def decrypt_gateway_settings(
    box: SecretBox | None, settings: dict[str, object]
) -> dict[str, object]:
    """Decrypt every secret-looking value; tolerate plaintext (dev seeds)."""
    if box is None:
        return dict(settings)
    out = dict(settings)
    for key, value in list(out.items()):
        if is_secret_key(key) and isinstance(value, str) and value:
            with contextlib.suppress(ConfigError):  # plaintext dev seeds pass through
                out[key] = box.decrypt(value)
    return out
