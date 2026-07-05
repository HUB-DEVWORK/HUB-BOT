"""Web seam settings (FastAPI: webhooks + health; cabinet API later)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


class WebSettings(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    # Explicit CORS origins for the future cabinet. NEVER "*" with cookie auth (gotcha #20).
    cors_origins: list[str] = []

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v
