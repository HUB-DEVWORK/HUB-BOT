"""Web-cabinet email auth: register / verify / login / refresh / logout.

Lets a person register and buy a subscription from a website WITHOUT Telegram.
Sessions are short-lived access JWTs (15 min) + rotating refresh tokens (7 days,
SHA-256-hashed in the DB). The access JWT is signed with APP__JWT_SECRET (never the
bot token — that would let a leaked bot token forge cabinet sessions). Once logged
in, the same ``/api/cabinet/*`` endpoints (purchase, me, devices…) work over the
``Authorization: Bearer <access>`` header.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re as _re
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from src.application.services.ids import generate_referral_code
from src.core.enums import AuthType, Currency
from src.core.security import hash_password, jwt_decode, jwt_encode, verify_password
from src.infrastructure.database.models.cabinet_token import CabinetRefreshToken
from src.infrastructure.database.models.user import User
from src.infrastructure.di import AppContainer
from src.web.deps import get_container

router = APIRouter(prefix="/api/cabinet/auth", tags=["cabinet-auth"])

_ACCESS_TTL = 15 * 60
_REFRESH_TTL_DAYS = 7
_VERIFY_TTL_HOURS = 24


def _jwt_secret(container: AppContainer) -> str:
    return container.settings.app.jwt_secret


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _access_token(container: AppContainer, user: User) -> str:
    return jwt_encode(
        {"sub": user.id, "type": "access", "web": True},
        _jwt_secret(container),
        _ACCESS_TTL,
    )


async def _issue_refresh(container: AppContainer, user_id: int, *, device: str | None) -> str:
    token = secrets.token_urlsafe(32)
    async with container.uow() as uow:
        uow.session.add(
            CabinetRefreshToken(
                user_id=user_id,
                token_hash=_hash(token),
                device_info=(device or "")[:256] or None,
                expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=_REFRESH_TTL_DAYS),
            )
        )
        await uow.commit()
    return token


async def _auth_response(
    container: AppContainer, user: User, *, device: str | None
) -> dict[str, Any]:
    refresh = await _issue_refresh(container, user.id, device=device)
    return {
        "access_token": _access_token(container, user),
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": _ACCESS_TTL,
        "user": {"id": user.id, "email": user.email, "email_verified": user.email_verified},
    }


async def _require_web_enabled(container: AppContainer) -> None:
    async with container.uow() as uow:
        if not bool(await container.bot_config.value(uow, "WEB_CABINET_ENABLED")):
            raise HTTPException(403, "web cabinet is disabled")


class RegisterIn(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("invalid email")
        return v


@router.post("/register")
async def register(
    body: RegisterIn, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    await _require_web_enabled(container)
    email = str(body.email).strip().lower()
    async with container.uow() as uow:
        if await uow.users.get_by_email(email) is not None:
            raise HTTPException(409, "email already registered")
        verify = bool(await container.bot_config.value(uow, "CABINET_EMAIL_VERIFICATION"))
        cabinet_url = str(await container.bot_config.value(uow, "CABINET_URL") or "")
        user = User(
            auth_type=AuthType.EMAIL,
            email=email,
            password_hash=hash_password(body.password),
            email_verified=not verify,
            referral_code=generate_referral_code(),
            currency=Currency.RUB,
        )
        await uow.users.add(user)
        token = ""
        if verify:
            token = secrets.token_hex(32)
            user.notification_settings = {
                **(user.notification_settings or {}),
                "verify_token": token,
                "verify_expires": (
                    dt.datetime.now(dt.UTC) + dt.timedelta(hours=_VERIFY_TTL_HOURS)
                ).isoformat(),
            }
        await uow.commit()
        user_id = user.id

    if not verify:
        async with container.uow() as uow:
            fresh = await uow.users.get(user_id)
        if fresh is None:
            raise HTTPException(500, "registration failed")
        return await _auth_response(container, fresh, device=None)

    link = f"{cabinet_url.rstrip('/')}/verify-email?token={token}" if cabinet_url else token
    mailer = await container.build_mailer()
    await mailer.send(
        email,
        "Подтверждение регистрации",
        f"Подтвердите e-mail, перейдя по ссылке:\n{link}\n\nСсылка действует 24 часа.",  # noqa: RUF001
    )
    return {"ok": True, "requires_verification": True, "email": email}


class VerifyIn(BaseModel):
    token: str = Field(..., min_length=8, max_length=128)


@router.post("/verify")
async def verify_email(
    body: VerifyIn, request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    async with container.uow() as uow:
        user = await uow.users.find_by_verify_token(body.token)
        if user is None:
            raise HTTPException(400, "invalid or expired token")
        settings_ = user.notification_settings or {}
        expires = settings_.get("verify_expires")
        if expires and dt.datetime.fromisoformat(expires) < dt.datetime.now(dt.UTC):
            raise HTTPException(400, "verification link expired")
        user.email_verified = True
        user.notification_settings = {
            k: v for k, v in settings_.items() if k not in ("verify_token", "verify_expires")
        }
        await uow.commit()
        user_id = user.id
        fresh = await uow.users.get(user_id)
    if fresh is None:
        raise HTTPException(400, "user gone")
    return await _auth_response(container, fresh, device=request.headers.get("user-agent"))


class LoginIn(BaseModel):
    email: str = Field(..., max_length=255)
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return v.strip().lower()


@router.post("/login")
async def login(
    body: LoginIn, request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    await _require_web_enabled(container)
    async with container.uow() as uow:
        user = await uow.users.get_by_email(str(body.email).strip().lower())
        stored = user.password_hash if (user and user.password_hash) else "scrypt$1$1$1$x$x"
        # verify even on miss (constant-ish time) to avoid user enumeration
        if not verify_password(body.password, stored):
            raise HTTPException(401, "invalid credentials")
        if user is None or not user.email_verified:
            raise HTTPException(403, "email not verified")
        verified_user = user
    return await _auth_response(container, verified_user, device=request.headers.get("user-agent"))


class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=8)


@router.post("/refresh")
async def refresh(
    body: RefreshIn, request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    token_hash = _hash(body.refresh_token)
    now = dt.datetime.now(dt.UTC)
    async with container.uow() as uow:
        row = await uow.cabinet_tokens.find_one(token_hash=token_hash)
        if row is None or row.revoked_at is not None or row.expires_at < now:
            raise HTTPException(401, "invalid refresh token")
        user = await uow.users.get(row.user_id)
        if user is None:
            raise HTTPException(401, "user gone")
        row.revoked_at = now  # rotate: this refresh is now spent
        await uow.commit()
    return await _auth_response(container, user, device=request.headers.get("user-agent"))


@router.post("/logout")
async def logout(
    body: RefreshIn, container: AppContainer = Depends(get_container)
) -> dict[str, bool]:
    async with container.uow() as uow:
        row = await uow.cabinet_tokens.find_one(token_hash=_hash(body.refresh_token))
        if row is not None and row.revoked_at is None:
            row.revoked_at = dt.datetime.now(dt.UTC)
            await uow.commit()
    return {"ok": True}


async def web_user_from_bearer(request: Request, container: AppContainer) -> User | None:
    """Resolve a web-cabinet user from an ``Authorization: Bearer <access>`` header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    payload = jwt_decode(auth.removeprefix("Bearer "), _jwt_secret(container))
    if payload is None or payload.get("type") != "access" or not payload.get("web"):
        return None
    async with container.uow() as uow:
        return await uow.users.get(int(payload["sub"]))
