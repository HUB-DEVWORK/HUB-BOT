"""Connection profile + auth strategy for the panel (the #1 source of failures, gotcha #3-4).

Builds the headers/cookies/verify flag once from settings. For a local panel it injects the
``X-Forwarded-*`` / ``Host`` headers and disables TLS verification; for an external panel it
uses HTTPS with verification on. Auth sends BOTH ``X-Api-Key`` and ``Authorization: Bearer``
by default because different panel deployments trust different ones.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from src.core.config.remnawave import PanelAuthType, RemnawaveSettings


@dataclass(frozen=True, slots=True)
class ConnectionProfile:
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    verify: bool = True


def build_profile(cfg: RemnawaveSettings) -> ConnectionProfile:
    headers: dict[str, str] = {"Accept": "application/json"}
    cookies: dict[str, str] = {}

    # --- auth ---------------------------------------------------------------
    if cfg.auth_type in (PanelAuthType.API_KEY, PanelAuthType.BEARER) and cfg.token:
        headers["X-Api-Key"] = cfg.token
        headers["Authorization"] = f"Bearer {cfg.token}"
    elif cfg.auth_type is PanelAuthType.CADDY and cfg.caddy_api_key:
        headers["X-Api-Key"] = cfg.caddy_api_key
    elif cfg.auth_type is PanelAuthType.BASIC and cfg.basic_user:
        raw = f"{cfg.basic_user}:{cfg.basic_password}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()

    if cfg.cf_access_client_id and cfg.cf_access_client_secret:
        headers["CF-Access-Client-Id"] = cfg.cf_access_client_id
        headers["CF-Access-Client-Secret"] = cfg.cf_access_client_secret

    if cfg.secret_key_cookie and ":" in cfg.secret_key_cookie:
        name, _, value = cfg.secret_key_cookie.partition(":")
        cookies[name] = value

    # --- local vs external --------------------------------------------------
    base_url = cfg.base_url.rstrip("/")
    verify = True
    if cfg.is_local:
        # Reach the panel over plain http inside the network and satisfy its trust logic.
        headers.update(
            {
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
                "Host": "localhost",
            }
        )
        verify = False

    return ConnectionProfile(base_url=base_url, headers=headers, cookies=cookies, verify=verify)
