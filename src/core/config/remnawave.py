"""Remnawave panel settings (auth strategy, connection profile, webhook secret).

See docs/context/01-remnawave-domain.md — panel auth is the #1 source of connection failures,
so this is deliberately explicit.
"""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import BaseModel


class PanelAuthType(StrEnum):
    API_KEY = "api_key"
    BEARER = "bearer"
    BASIC = "basic"
    CADDY = "caddy"


class RemnawaveSettings(BaseModel):
    base_url: str = ""
    auth_type: PanelAuthType = PanelAuthType.API_KEY
    token: str = ""  # sent as BOTH X-Api-Key and Authorization: Bearer
    basic_user: str = ""
    basic_password: str = ""
    caddy_api_key: str = ""
    cf_access_client_id: str = ""
    cf_access_client_secret: str = ""
    secret_key_cookie: str = ""  # "name:value"
    webhook_secret: str = ""
    # tri-state: "" -> auto-detect, "true"/"false" -> force
    force_local: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.base_url).hostname or ""

    @property
    def is_local(self) -> bool:
        """Whether to treat the panel as local (inject X-Forwarded-*, TLS verify off).

        Forced by ``force_local`` when set, otherwise auto-detected: bare host,
        ``*.local``, docker service name (no dots), or a private/loopback IP.
        """
        if self.force_local:
            return self.force_local.strip().lower() in {"1", "true", "yes"}
        host = self.host
        if not host or host in {"localhost"} or host.endswith(".local"):
            return True
        if "." not in host:  # bare docker service name, e.g. "remnawave"
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return ip.is_private or ip.is_loopback
