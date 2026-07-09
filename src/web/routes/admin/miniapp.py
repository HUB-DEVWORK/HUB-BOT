"""Admin: mini-app customization (screen 06) — template choice + branding."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.infrastructure.di import AppContainer
from src.web.deps import get_container
from src.web.routes.admin._common import audit, iso
from src.web.routes.admin.deps import AdminIdentity, require_admin

router = APIRouter(prefix="/miniapp")

# Template ids must match miniapp/templates.json (extended with the 8 design themes).
KNOWN_TEMPLATES = (
    "minimal",
    "private",
    "buddy",
    "native",
    "terminal",
    "magazine",
    "neon",
    "pop",
)


UI_BUTTON_KEYS = ("renew", "share", "open_app", "get_link", "connect_proxy", "trial")
# Built-in home-screen sections + the "custom" bucket that holds admin blocks/buttons
# targeted at the home tab. All of them are reorderable AND hideable.
UI_SECTIONS = ("status", "plans", "referral", "proxy", "custom")
# Where an admin-defined block or button may be placed.
UI_SCREENS = ("home", "connect", "account")
_MAX_CUSTOM = 16  # per list (blocks, buttons_extra) — plenty, keeps the payload sane
# Schemes a custom link may use. Anything else (notably javascript:) is dropped.
_URL_SCHEMES = ("https://", "http://", "tg://", "mailto:")


def _clean_hex(color: Any) -> str | None:
    """Return a valid ``#RGB``/``#RRGGBB`` string or None."""
    s = str(color or "")
    return s if (s.startswith("#") and len(s) in (4, 7)) else None


def _clean_url(url: Any) -> str | None:
    """Return a safe outbound link (http/https/tg/mailto) or None."""
    s = str(url or "").strip()
    if len(s) > 512:
        return None
    return s if s.lower().startswith(_URL_SCHEMES) else None


def _clean_screen(screen: Any) -> str:
    s = str(screen or "home")
    return s if s in UI_SCREENS else "home"


def _clean_blocks(raw: Any) -> list[dict[str, Any]]:
    """Admin content cards: title + text + optional link-button, placed on a screen."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:_MAX_CUSTOM]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")[:64]
        text = str(item.get("text") or "")[:1000]
        label = str(item.get("button_label") or "")[:32]
        if not (title or text or label):
            continue  # empty block — skip
        out.append(
            {
                "id": str(item.get("id") or f"b{i}")[:40],
                "screen": _clean_screen(item.get("screen")),
                "title": title,
                "text": text,
                "icon": str(item.get("icon") or "")[:8],
                "url": _clean_url(item.get("url")),
                "button_label": label,
                "color": _clean_hex(item.get("color")),
            }
        )
    return out


def _clean_buttons_extra(raw: Any) -> list[dict[str, Any]]:
    """Admin standalone link-buttons: label + url, placed on a screen."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:_MAX_CUSTOM]):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")[:32]
        url = _clean_url(item.get("url"))
        if not label or not url:
            continue  # a link button without a label or a valid url is useless
        out.append(
            {
                "id": str(item.get("id") or f"x{i}")[:40],
                "screen": _clean_screen(item.get("screen")),
                "label": label,
                "url": url,
                "color": _clean_hex(item.get("color")),
                "style": "ghost" if item.get("style") == "ghost" else "primary",
            }
        )
    return out


def _clean_landing(raw: Any) -> dict[str, Any] | None:
    """Public marketing site (served at /) content — hero copy, feature cards, FAQ,
    and where the «Личный кабинет» CTA points (web auth window vs the Telegram bot)."""
    if not isinstance(raw, dict):
        return None
    features = []
    for i, f in enumerate((raw.get("features") or [])[:8]):
        if not isinstance(f, dict):
            continue
        title = str(f.get("title") or "")[:60]
        text = str(f.get("text") or "")[:200]
        if not (title or text):
            continue
        features.append(
            {
                "id": str(f.get("id") or f"f{i}")[:40],
                "icon": str(f.get("icon") or "")[:8],
                "title": title,
                "text": text,
            }
        )
    faq = []
    for i, q in enumerate((raw.get("faq") or [])[:8]):
        if not isinstance(q, dict):
            continue
        question = str(q.get("q") or "")[:160]
        answer = str(q.get("a") or "")[:600]
        if not (question and answer):
            continue
        faq.append({"id": str(q.get("id") or f"q{i}")[:40], "q": question, "a": answer})
    return {
        "enabled": bool(raw.get("enabled", True)),
        "headline": str(raw.get("headline") or "")[:120],
        "subheadline": str(raw.get("subheadline") or "")[:300],
        "cta_target": "bot" if raw.get("cta_target") == "bot" else "web",
        "features": features,
        "faq": faq,
    }


def _serialize(cfg: Any) -> dict[str, Any]:
    return {
        "template": cfg.template,
        "title": cfg.title,
        "greeting": cfg.greeting,
        "accent_color": cfg.accent_color,
        "photo_scale_pct": cfg.photo_scale_pct,
        "cover_path": cfg.cover_path,
        "ui": cfg.ui or {},
        "published_at": iso(cfg.published_at),
        "templates": list(KNOWN_TEMPLATES),
        "ui_button_keys": list(UI_BUTTON_KEYS),
        "ui_sections": list(UI_SECTIONS),
        "ui_screens": list(UI_SCREENS),
    }


@router.get("")
async def get_miniapp(container: AppContainer = Depends(get_container)) -> dict[str, Any]:
    async with container.uow() as uow:
        cfg = await uow.miniapp.get_or_create()
        await uow.commit()
    return _serialize(cfg)


class MiniappPatch(BaseModel):
    template: str | None = None
    title: str | None = Field(None, max_length=64)
    greeting: str | None = Field(None, max_length=256)
    accent_color: str | None = Field(None, max_length=9)
    photo_scale_pct: int | None = Field(None, ge=70, le=130)
    ui: dict[str, Any] | None = None

    @field_validator("ui")
    @classmethod
    def _ui_shape(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is None:
            return None
        out: dict[str, Any] = {}
        scale = v.get("scale")
        if scale is not None:
            out["scale"] = max(85, min(115, int(scale)))
        sections = v.get("sections")
        if isinstance(sections, list):
            out["sections"] = [s for s in sections if s in UI_SECTIONS]
        hidden = v.get("hidden")
        if isinstance(hidden, list):
            out["hidden"] = [s for s in hidden if s in UI_SECTIONS]
        buttons = v.get("buttons")
        if isinstance(buttons, dict):
            clean: dict[str, Any] = {}
            for key, spec in buttons.items():
                if key not in UI_BUTTON_KEYS or not isinstance(spec, dict):
                    continue
                text = str(spec.get("text") or "")[:32]
                clean[key] = {"text": text, "color": _clean_hex(spec.get("color"))}
            out["buttons"] = clean
        blocks = _clean_blocks(v.get("blocks"))
        if blocks:
            out["blocks"] = blocks
        extra = _clean_buttons_extra(v.get("buttons_extra"))
        if extra:
            out["buttons_extra"] = extra
        landing = _clean_landing(v.get("landing"))
        if landing is not None:
            out["landing"] = landing
        return out

    @field_validator("template")
    @classmethod
    def _known(cls, v: str | None) -> str | None:
        if v is not None and v not in KNOWN_TEMPLATES:
            raise ValueError(f"unknown template: {v}")
        return v

    @field_validator("accent_color")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not (v.startswith("#") and len(v) in (4, 7)):
            raise ValueError("accent must be #RGB or #RRGGBB")
        return v


@router.patch("")
async def patch_miniapp(
    body: MiniappPatch,
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(400, "no changes")
    async with container.uow() as uow:
        cfg = await uow.miniapp.get_or_create()
        for key, value in data.items():
            setattr(cfg, key, value)
        await audit(uow, identity, "miniapp.patch", None, **data)
        await uow.commit()
        return _serialize(cfg)


@router.post("/publish")
async def publish_miniapp(
    identity: AdminIdentity = Depends(require_admin),
    container: AppContainer = Depends(get_container),
) -> dict[str, Any]:
    async with container.uow() as uow:
        cfg = await uow.miniapp.get_or_create()
        cfg.published_at = dt.datetime.now(dt.UTC)
        await audit(uow, identity, "miniapp.publish", None, template=cfg.template)
        await uow.commit()
        return _serialize(cfg)
