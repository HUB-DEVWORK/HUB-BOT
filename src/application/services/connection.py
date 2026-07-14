"""VPN-client connection deep links (shared by the mini-app cabinet API and the bot)."""

from __future__ import annotations

# Import schemes for the popular clients. Keys are stable identifiers used by both surfaces.
CLIENT_LABELS: dict[str, str] = {
    "happ": "Happ",
    "v2raytun": "v2RayTun",
    "hiddify": "Hiddify",
    "streisand": "Streisand",
}


def build_deep_links(subscription_url: str, crypto_link: str | None = None) -> dict[str, str]:
    """One-tap import links per client from a Remnawave subscription URL.

    Happ prefers the panel-provided crypto (happ) link when present.
    """
    return {
        "happ": crypto_link or f"happ://add/{subscription_url}",
        "v2raytun": f"v2raytun://import/{subscription_url}",
        "hiddify": f"hiddify://import/{subscription_url}",
        "streisand": f"streisand://import/{subscription_url}",
    }


def parse_enabled_apps(raw: str | None) -> list[str]:
    """Owner setting CONNECTION_APPS ('happ,hiddify') -> ordered list of known client keys.

    Unknown/empty entries are dropped; an empty result falls back to all clients so the
    Connect tab is never left with nothing to import into.
    """
    keys = [k.strip().lower() for k in (raw or "").split(",") if k.strip()]
    enabled = [k for k in keys if k in CLIENT_LABELS]
    return enabled or list(CLIENT_LABELS)


def connection_apps(
    subscription_url: str, crypto_link: str | None, enabled: list[str]
) -> list[dict[str, str]]:
    """Per-app entries (key, label, deep_link) for only the enabled clients, in owner order."""
    links = build_deep_links(subscription_url, crypto_link)
    return [
        {"key": k, "label": CLIENT_LABELS[k], "deep_link": links[k]} for k in enabled if k in links
    ]
