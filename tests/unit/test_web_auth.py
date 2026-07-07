"""Web-cabinet auth building blocks: email lookup, verify-token lookup, refresh rotation."""

from __future__ import annotations

import datetime as dt
import hashlib

from src.core.enums import AuthType, Currency
from src.core.security import hash_password, jwt_decode, jwt_encode, verify_password
from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken
from src.infrastructure.database.models.user import User
from src.infrastructure.database.uow import UnitOfWork


async def _make_email_user(uow: UnitOfWork, email: str, password: str) -> User:
    from src.application.services.ids import generate_referral_code

    user = User(
        auth_type=AuthType.EMAIL,
        email=email,
        password_hash=hash_password(password),
        email_verified=True,
        referral_code=generate_referral_code(),
        currency=Currency.RUB,
    )
    await uow.users.add(user)
    return user


async def test_get_by_email_is_case_insensitive(uow: UnitOfWork) -> None:
    async with uow:
        await _make_email_user(uow, "Buyer@Example.com", "hunter2pass")
        await uow.commit()
        found = await uow.users.get_by_email("buyer@example.com")
        assert found is not None and found.auth_type is AuthType.EMAIL
        assert verify_password("hunter2pass", found.password_hash or "")
        assert not verify_password("wrong", found.password_hash or "")


async def test_find_by_verify_token(uow: UnitOfWork) -> None:
    async with uow:
        user = await _make_email_user(uow, "v@example.com", "password1")
        user.email_verified = False
        user.notification_settings = {"verify_token": "abc123deadbeef", "verify_expires": "x"}
        await uow.commit()
        found = await uow.users.find_by_verify_token("abc123deadbeef")
        assert found is not None and found.id == user.id
        assert await uow.users.find_by_verify_token("nope") is None


async def test_refresh_token_rotation(uow: UnitOfWork) -> None:
    async with uow:
        user = await _make_email_user(uow, "r@example.com", "password1")
        await uow.commit()

        def token_row(raw: str) -> CabinetRefreshToken:
            return CabinetRefreshToken(
                user_id=user.id,
                token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=7),
            )

        uow.session.add(token_row("refresh-1"))
        await uow.commit()

        row = await uow.cabinet_tokens.find_one(token_hash=hashlib.sha256(b"refresh-1").hexdigest())
        assert row is not None and row.revoked_at is None
        # rotate: spend the old one, issue a new one
        row.revoked_at = dt.datetime.now(dt.UTC)
        uow.session.add(token_row("refresh-2"))
        await uow.commit()

        spent = await uow.cabinet_tokens.find_one(
            token_hash=hashlib.sha256(b"refresh-1").hexdigest()
        )
        assert spent is not None and spent.revoked_at is not None  # cannot be reused
        fresh = await uow.cabinet_tokens.find_one(
            token_hash=hashlib.sha256(b"refresh-2").hexdigest()
        )
        assert fresh is not None and fresh.revoked_at is None


def test_access_jwt_round_trip() -> None:
    token = jwt_encode({"sub": 42, "type": "access", "web": True}, "secret-key", 900)
    payload = jwt_decode(token, "secret-key")
    assert payload is not None
    assert payload["sub"] == 42 and payload["web"] is True
    assert jwt_decode(token, "wrong-key") is None
