"""Web seam settings (FastAPI: webhooks + health; cabinet API later)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class WebSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    # Public https base URL the whole thing is reachable at (e.g. https://vpn.example.com).
    # On first boot this auto-wires the bot <-> mini-app link: SUBSCRIPTION_MINI_APP_URL=<url>/app
    # and CABINET_URL=<url>, so the bot shows the mini-app button without any manual config.
    public_url: str = ""
    # Explicit CORS origins for the future cabinet. NEVER "*" with cookie auth (gotcha #20).
    cors_origins: list[str] = []

    @field_validator("public_url", mode="before")
    @classmethod
    def _strip_url(cls, v: object) -> object:
        return v.strip().rstrip("/") if isinstance(v, str) else v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v
