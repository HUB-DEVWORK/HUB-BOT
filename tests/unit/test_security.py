"""core/security: scrypt hashes, HS256 JWT, Telegram initData validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse

from src.core.security import (
    hash_password,
    jwt_decode,
    jwt_encode,
    validate_init_data,
    verify_password,
)

SECRET = "test-jwt-secret"
BOT_TOKEN = "12345:TESTTOKEN"


class TestPasswords:
    def test_roundtrip(self) -> None:
        stored = hash_password("s3cret!")
        assert stored.startswith("scrypt$")
        assert verify_password("s3cret!", stored)

    def test_wrong_password(self) -> None:
        assert not verify_password("nope", hash_password("s3cret!"))

    def test_salts_differ(self) -> None:
        assert hash_password("x") != hash_password("x")

    def test_garbage_stored_value(self) -> None:
        assert not verify_password("x", "not-a-hash")
        assert not verify_password("x", "scrypt$bad$fields")


class TestJwt:
    def test_roundtrip(self) -> None:
        token = jwt_encode({"sub": 42, "scope": "admin"}, SECRET, ttl_seconds=60)
        payload = jwt_decode(token, SECRET)
        assert payload is not None
        assert payload["sub"] == 42
        assert payload["scope"] == "admin"

    def test_expired(self) -> None:
        token = jwt_encode({"sub": 1}, SECRET, ttl_seconds=-10)
        assert jwt_decode(token, SECRET) is None

    def test_tampered_payload(self) -> None:
        token = jwt_encode({"sub": 1, "role": "USER"}, SECRET, ttl_seconds=60)
        head, _body, sig = token.split(".")
        import base64

        forged_body = (
            base64.urlsafe_b64encode(
                json.dumps({"sub": 1, "role": "OWNER", "exp": int(time.time()) + 60}).encode()
            )
            .decode()
            .rstrip("=")
        )
        assert jwt_decode(f"{head}.{forged_body}.{sig}", SECRET) is None

    def test_wrong_secret(self) -> None:
        token = jwt_encode({"sub": 1}, SECRET, ttl_seconds=60)
        assert jwt_decode(token, "other-secret") is None


def _signed_init_data(user: dict, *, auth_date: int | None = None, token: str = BOT_TOKEN) -> str:
    pairs = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAE-test",
        "user": json.dumps(user, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


class TestInitData:
    def test_valid(self) -> None:
        init = _signed_init_data({"id": 777, "first_name": "T"})
        data = validate_init_data(init, BOT_TOKEN)
        assert data is not None
        assert data["user_parsed"]["id"] == 777

    def test_bad_signature(self) -> None:
        init = _signed_init_data({"id": 777}, token="999:WRONG")
        assert validate_init_data(init, BOT_TOKEN) is None

    def test_stale_auth_date(self) -> None:
        init = _signed_init_data({"id": 777}, auth_date=int(time.time()) - 999_999)
        assert validate_init_data(init, BOT_TOKEN) is None

    def test_missing_hash(self) -> None:
        assert validate_init_data("auth_date=1&user=%7B%7D", BOT_TOKEN) is None
