"""Owner-configurable connection apps (CONNECTION_APPS) + deep-link filtering."""

from __future__ import annotations

from src.application.services.connection import (
    CLIENT_LABELS,
    CLIENT_STORES,
    connection_apps,
    parse_enabled_apps,
    store_links,
)

_URL = "https://sub.example/u/abc"


def test_parse_enabled_apps_orders_and_filters() -> None:
    assert parse_enabled_apps("hiddify,happ") == ["hiddify", "happ"]  # owner order preserved
    assert parse_enabled_apps("happ, bogus , v2raytun") == ["happ", "v2raytun"]  # unknown dropped
    assert parse_enabled_apps("HAPP") == ["happ"]  # case-insensitive


def test_parse_enabled_apps_empty_falls_back_to_all() -> None:
    # Never leave the Connect tab with nothing to import into.
    assert parse_enabled_apps("") == list(CLIENT_LABELS)
    assert parse_enabled_apps(None) == list(CLIENT_LABELS)
    assert parse_enabled_apps("nonsense,also-bad") == list(CLIENT_LABELS)


def test_connection_apps_only_enabled_in_order() -> None:
    apps = connection_apps(_URL, None, ["hiddify", "happ"])
    assert [a["key"] for a in apps] == ["hiddify", "happ"]
    assert apps[0] == {
        "key": "hiddify",
        "label": "Hiddify",
        "deep_link": f"hiddify://import/{_URL}",
        "stores": CLIENT_STORES["hiddify"],
    }
    assert apps[1]["deep_link"] == f"happ://add/{_URL}"


def test_connection_apps_happ_prefers_crypto_link() -> None:
    apps = connection_apps(_URL, "happ://crypto-token", ["happ"])
    assert apps[0]["deep_link"] == "happ://crypto-token"


def test_store_links_are_per_app_and_platform() -> None:
    # The reported bug: Windows must open the OWNER'S app, not a hardcoded Hiddify GitHub.
    happ = store_links("happ")
    assert "happ-desktop" in happ["windows"] and "hiddify" not in happ["windows"]
    assert happ["ios"].endswith("id6504287215")
    assert store_links("streisand")["default"].endswith("id6450534064")
    assert store_links("unknown-app") == {}
    # Every configurable client carries download links.
    assert set(CLIENT_STORES) == set(CLIENT_LABELS)


def test_connection_apps_windows_store_follows_owner_app() -> None:
    # Owner configured Happ first -> the download link for every platform is Happ's.
    apps = connection_apps(_URL, None, ["happ", "hiddify"])
    primary = apps[0]
    assert primary["key"] == "happ"
    assert "happ-desktop" in primary["stores"]["windows"]


def test_new_clients_deep_link_formats() -> None:
    """INCY takes the raw URL; Shadowrocket wants base64; query-style schemes must
    percent-encode the URL so its own ?query survives the outer link."""
    import base64
    from urllib.parse import quote

    from src.application.services.connection import build_deep_links

    url = "https://sub.example/u/abc?fmt=v2ray"
    links = build_deep_links(url)
    assert links["incy"] == f"incy://add/{url}"
    assert (
        links["shadowrocket"]
        == f"shadowrocket://add/sub://{base64.b64encode(url.encode()).decode()}"
    )
    enc = quote(url, safe="")
    assert links["v2box"] == f"v2box://install-sub?url={enc}"
    assert links["clash"] == f"clash://install-config?url={enc}"
    assert links["singbox"] == f"sing-box://import-remote-profile?url={enc}"
    # every advertised client has a working deep link + store entry
    assert set(links) == set(CLIENT_LABELS)


def test_incy_store_links() -> None:
    incy = store_links("incy")
    assert incy["ios"].endswith("id6756943388")
    assert "llc.itdev.incy" in incy["android"]
