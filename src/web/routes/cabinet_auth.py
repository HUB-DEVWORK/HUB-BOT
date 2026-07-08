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
from src.core.enums import AuthType, Currency, UserStatus
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


def _ensure_active(user: User) -> None:
    """A BLOCKED user must not get (or keep) a web session — the bot + tma paths already
    enforce this; the email/OAuth/JWT path has to match, or a blocked user keeps full access."""
    if user.status is UserStatus.BLOCKED:
        raise HTTPException(403, "account blocked")


async def _auth_response(
    container: AppContainer, user: User, *, device: str | None
) -> dict[str, Any]:
    _ensure_active(user)  # single choke point: never mint a session for a blocked account
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
        user = await uow.users.get(int(payload["sub"]))
    if user is None or user.status is UserStatus.BLOCKED:
        return None  # blocked -> cabinet_user() raises 401, same as the bot/tma block
    return user


# --- guest purchase (buy without any prior registration) --------------------


def _auto_login_token(container: AppContainer, user_id: int) -> str:
    return jwt_encode(
        {"sub": user_id, "type": "auto_login", "web": True},
        _jwt_secret(container),
        72 * 3600,
    )


class GuestPurchaseIn(BaseModel):
    email: str = Field(..., max_length=255)
    plan_id: int
    days: int = Field(..., ge=1, le=3650)  # same cap as PurchaseIn (#12)
    method: str = Field(..., min_length=2, max_length=32)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("invalid email")
        return v


@router.post("/guest/purchase")
async def guest_purchase(
    body: GuestPurchaseIn, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    """Buy a subscription with only an e-mail — no registration step.

    Auto-provisions a verified e-mail account (safer than a phantom user), starts a
    hosted gateway payment, and returns the redirect + an auto-login token so the
    success page can drop the buyer straight into the cabinet. The subscription link
    is also e-mailed on fulfilment (see ``_notify_paid``).
    """
    await _require_web_enabled(container)
    from src.application.dto.pricing import PurchaseRequest
    from src.core.enums import PurchaseType
    from src.web.routes.cabinet import _pay_with_gateway

    if body.method in ("balance", "stars"):
        raise HTTPException(400, "guest purchases require an online payment method")

    async with container.uow() as uow:
        user = await uow.users.get_by_email(body.email)
        created = False
        if user is None:
            user = User(
                auth_type=AuthType.EMAIL,
                email=body.email,
                password_hash=hash_password(secrets.token_urlsafe(12)),
                email_verified=True,
                referral_code=generate_referral_code(),
                currency=Currency.RUB,
            )
            await uow.users.add(user)
            created = True
        await uow.commit()
        user_id = user.id
        ptype, sub_id = await container.purchase.resolve_purchase_type(uow, user_id, body.plan_id)

    req = PurchaseRequest(
        user_id=user_id,
        plan_id=body.plan_id,
        duration_days=body.days,
        currency=Currency.RUB,
        purchase_type=ptype if not created else PurchaseType.NEW,
        subscription_id=sub_id if not created else None,
    )
    async with container.uow() as uow:
        fresh = await uow.users.get(user_id)
    if fresh is None:
        raise HTTPException(500, "guest user vanished")
    result = await _pay_with_gateway(container, fresh, req, body.method)
    # Auto-login ONLY for an account THIS call just created. Reusing a pre-existing email must
    # never hand out a session for it — that would be account takeover by anyone who knows the
    # address (the guard mirrors register()'s 409 and oauth_callback()'s unverified-bind refusal).
    return {
        "redirect_url": result.get("redirect_url"),
        "auto_login_token": _auto_login_token(container, user_id) if created else None,
        "email": body.email,
    }


class AutoLoginIn(BaseModel):
    token: str = Field(..., min_length=8)


@router.post("/login/auto")
async def login_auto(
    body: AutoLoginIn, request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    """Exchange a guest auto-login token for a full session (post-purchase convenience)."""
    payload = jwt_decode(body.token, _jwt_secret(container))
    if payload is None or payload.get("type") != "auto_login":
        raise HTTPException(401, "invalid token")
    async with container.uow() as uow:
        user = await uow.users.get(int(payload["sub"]))
        if user is None:
            raise HTTPException(401, "user gone")
        # never let an auto-login token (weak proof) unlock a staff account
        if user.role.is_staff:
            raise HTTPException(403, "not allowed for staff accounts")
    return await _auth_response(container, user, device=request.headers.get("user-agent"))


# --- OAuth (Google / Yandex) ------------------------------------------------


def _redirect_uri(cabinet_url: str) -> str:
    # The web SPA (this same page) reads ?code&state on load — no separate route.
    return cabinet_url.rstrip("/")


async def _oauth_provider(container: AppContainer, name: str):  # type: ignore[no-untyped-def]
    from src.infrastructure.services.oauth import build_provider

    async with container.uow() as uow:
        cfg = container.bot_config
        cid = str(await cfg.value(uow, f"OAUTH_{name.upper()}_CLIENT_ID") or "")
        sec = str(await cfg.value(uow, f"OAUTH_{name.upper()}_CLIENT_SECRET") or "")
        cabinet_url = str(await cfg.value(uow, "CABINET_URL") or "")
    return build_provider(name, cid, sec), cabinet_url


@router.get("/oauth/{provider}/authorize")
async def oauth_authorize(
    provider: str, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    await _require_web_enabled(container)
    from src.infrastructure.services.oauth import save_state

    prov, cabinet_url = await _oauth_provider(container, provider)
    if prov is None or not cabinet_url:
        raise HTTPException(400, "provider not configured")
    state = await save_state(container.redis, provider)
    return {"authorize_url": prov.authorize_url(_redirect_uri(cabinet_url), state), "state": state}


class OAuthCallbackIn(BaseModel):
    provider: str = Field(..., min_length=2, max_length=32)
    code: str = Field(..., min_length=4, max_length=2048)
    state: str = Field(..., min_length=8, max_length=128)


@router.post("/oauth/callback")
async def oauth_callback(
    body: OAuthCallbackIn, request: Request, container: AppContainer = Depends(get_container)
) -> dict[str, Any]:
    await _require_web_enabled(container)
    from src.infrastructure.services.oauth import consume_state

    saved = await consume_state(container.redis, body.state)  # single-use (anti-replay)
    if saved != body.provider:
        raise HTTPException(401, "invalid or expired state")
    prov, cabinet_url = await _oauth_provider(container, body.provider)
    if prov is None or not cabinet_url:
        raise HTTPException(400, "provider not configured")
    try:
        info = await prov.fetch_user(body.code, _redirect_uri(cabinet_url))
    except Exception as exc:
        raise HTTPException(400, f"oauth exchange failed: {exc}") from exc
    if not info.email_verified:
        raise HTTPException(403, "email not verified by the provider")

    async with container.uow() as uow:
        user = await uow.users.get_by_email(info.email)
        if user is None:
            user = User(
                auth_type=AuthType.OAUTH,
                email=info.email,
                email_verified=True,
                first_name=(info.name or "")[:128] or None,
                referral_code=generate_referral_code(),
                currency=Currency.RUB,
            )
            await uow.users.add(user)
            await uow.commit()
        elif not user.email_verified:
            # a never-confirmed local account must not be taken over via OAuth
            raise HTTPException(409, "email exists but is unverified — log in and link instead")
        user_id = user.id
        fresh = await uow.users.get(user_id)
    if fresh is None:
        raise HTTPException(500, "oauth login failed")
    return await _auth_response(container, fresh, device=request.headers.get("user-agent"))
