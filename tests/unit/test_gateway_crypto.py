"""Gateway settings encryption: the write and read paths agree on ONE secret predicate,
so every field encrypted on save is decrypted on read (webhook verification depends on it).
"""

from __future__ import annotations

from cryptography.fernet import Fernet

from src.infrastructure.payments.crypto import (
    SecretBox,
    decrypt_gateway_settings,
    is_secret_key,
)


def test_is_secret_key_hint_matching() -> None:
    for name in (
        "secret",
        "webhook_secret",
        "private_key",
        "public_key",
        "api_token",
        "signature_token",
        "signing_secret",
        "secret_id",
        "shop_password",
    ):
        assert is_secret_key(name), name
    for name in ("shop_id", "enabled_forms", "merchant_login", "project"):
        assert not is_secret_key(name), name


def test_encrypt_decrypt_roundtrips_any_hinted_field() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    # Field names that were encrypted on write but NOT in the old fixed decrypt set — the bug.
    stored = {
        "webhook_secret": box.encrypt("wh-s3cret"),
        "private_key": box.encrypt("-----KEY-----"),
        "secret_id": box.encrypt("sid-42"),
        "shop_id": "12345",  # non-secret, left untouched
    }
    out = decrypt_gateway_settings(box, stored)
    assert out["webhook_secret"] == "wh-s3cret"
    assert out["private_key"] == "-----KEY-----"
    assert out["secret_id"] == "sid-42"
    assert out["shop_id"] == "12345"


def test_decrypt_tolerates_plaintext_secret() -> None:
    box = SecretBox(Fernet.generate_key().decode())
    out = decrypt_gateway_settings(box, {"api_token": "plain-not-fernet"})
    assert out["api_token"] == "plain-not-fernet"


def test_none_box_passthrough() -> None:
    assert decrypt_gateway_settings(None, {"secret": "x"}) == {"secret": "x"}
