"""Fernet encryption for gateway credentials at rest (gotcha #15)."""

from __future__ import annotations

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
