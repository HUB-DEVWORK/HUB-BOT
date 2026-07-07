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


# Keys that hold provider secrets inside payment_gateways.settings.
GATEWAY_SECRET_KEYS = {
    "secret",
    "secret_key",
    "api_key",
    "api_token",
    "token",
    "password",
    "shop_secret",
    "api_secret",
    "secret1",
    "secret2",
}


def decrypt_gateway_settings(
    box: SecretBox | None, settings: dict[str, object]
) -> dict[str, object]:
    """Decrypt secret-looking values; tolerate plaintext (dev seeds)."""
    if box is None:
        return dict(settings)
    out = dict(settings)
    for key in GATEWAY_SECRET_KEYS & out.keys():
        value = out[key]
        if isinstance(value, str) and value:
            # Tolerate plaintext values (dev seeds).
            with contextlib.suppress(ConfigError):
                out[key] = box.decrypt(value)
    return out
