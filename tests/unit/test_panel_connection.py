"""Connection profile + auth strategy (gotcha #3-4)."""

from __future__ import annotations

from src.core.config.remnawave import PanelAuthType, RemnawaveSettings
from src.infrastructure.remnawave.connection import build_profile


def test_external_panel_uses_https_and_verifies() -> None:
    cfg = RemnawaveSettings(
        base_url="https://panel.example.com", auth_type=PanelAuthType.API_KEY, token="secret-token"
    )
    profile = build_profile(cfg)
    assert profile.verify is True
    assert profile.headers["X-Api-Key"] == "secret-token"
    assert profile.headers["Authorization"] == "Bearer secret-token"  # both are sent
    assert "X-Forwarded-Proto" not in profile.headers


def test_local_panel_injects_forwarded_headers_and_skips_tls() -> None:
    cfg = RemnawaveSettings(
        base_url="http://remnawave:3000", auth_type=PanelAuthType.API_KEY, token="t"
    )
    assert cfg.is_local is True  # bare docker service name
    profile = build_profile(cfg)
    assert profile.verify is False
    assert profile.headers["X-Forwarded-Proto"] == "https"
    assert profile.headers["X-Real-IP"] == "127.0.0.1"
    assert profile.headers["Host"] == "localhost"


def test_private_ip_is_treated_as_local() -> None:
    cfg = RemnawaveSettings(base_url="http://10.0.0.5:8080", token="t")
    assert cfg.is_local is True
    assert build_profile(cfg).verify is False


def test_secret_key_cookie_is_parsed() -> None:
    cfg = RemnawaveSettings(
        base_url="https://panel.example.com", token="t", secret_key_cookie="access:abc123"
    )
    profile = build_profile(cfg)
    assert profile.cookies == {"access": "abc123"}
