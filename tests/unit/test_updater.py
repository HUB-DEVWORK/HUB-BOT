"""Update checker: compares our build SHA to GitHub's latest commit. Fail-soft everywhere."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from src.infrastructure.services import updater as updater_mod
from src.infrastructure.services.updater import check_for_update, request_update

_URL = "https://api.github.com/repos/acme/bot/commits/main"


def _commit(sha: str, msg: str = "feat: thing\n\nbody") -> dict[str, object]:
    return {"sha": sha, "commit": {"message": msg}}


@respx.mock
async def test_update_available_when_sha_differs() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_commit("b" * 40)))
    info = await check_for_update("acme/bot", "main", "a" * 12)
    assert info.available is True
    assert info.latest == "b" * 12
    assert info.current == "a" * 12
    assert info.message == "feat: thing"
    assert "compare" in info.url


@respx.mock
async def test_no_update_when_sha_matches() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_commit("a" * 40)))
    info = await check_for_update("acme/bot", "main", "a" * 12)
    assert info.available is False
    assert info.latest == "a" * 12


@respx.mock
async def test_no_update_when_short_build_sha_matches() -> None:
    # Real deploys bake a 7-char `git rev-parse --short HEAD`, but GitHub returns the full 40-char
    # sha (we keep its 12-char prefix). A width-naive `!=` would report an update forever even on
    # the identical commit; the checker must recognise the 7-char prefix as the same commit.
    full = "7f9d6d9ed44c22e57eb9f4d610376d704c0d38e4"
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_commit(full)))
    info = await check_for_update("acme/bot", "main", "7f9d6d9")
    assert info.available is False
    assert info.current == "7f9d6d9"


@respx.mock
async def test_update_available_when_short_build_sha_differs() -> None:
    full = "abcdef01234556789abcdef01234556789abcdef"
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_commit(full)))
    info = await check_for_update("acme/bot", "main", "7f9d6d9")
    assert info.available is True


@respx.mock
async def test_unknown_local_sha_surfaces_update() -> None:
    # An image built without the build-arg (build_sha="") should still surface an update.
    respx.get(_URL).mock(return_value=httpx.Response(200, json=_commit("c" * 40)))
    info = await check_for_update("acme/bot", "main", "")
    assert info.available is True
    assert "commit/" in info.url  # no compare base → link the commit


@respx.mock
async def test_network_error_is_soft() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("down"))
    info = await check_for_update("acme/bot", "main", "a" * 12)
    assert info.available is False and info.latest == ""


@respx.mock
async def test_non_200_is_soft() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(404))
    info = await check_for_update("acme/bot", "main", "a" * 12)
    assert info.available is False


async def test_bad_repo_is_soft() -> None:
    info = await check_for_update("", "main", "a" * 12)
    assert info.available is False and info.latest == ""


def test_request_update_writes_marker_when_sidecar_alive(
    tmp_path: Path, monkeypatch: object
) -> None:
    # AUTO_UPDATE_ENABLED path: with a FRESH sidecar heartbeat, the marker is written.
    marker = tmp_path / "update-signals" / "request"
    marker.parent.mkdir()
    heartbeat = marker.parent / ".alive"
    heartbeat.touch()  # fresh -> updater considered alive
    monkeypatch.setattr(updater_mod, "UPDATE_REQUEST_FILE", str(marker))  # type: ignore[attr-defined]
    monkeypatch.setattr(updater_mod, "_HEARTBEAT_FILE", str(heartbeat))  # type: ignore[attr-defined]
    assert request_update() is True
    assert marker.is_file()


def test_request_update_soft_when_sidecar_not_running(tmp_path: Path, monkeypatch: object) -> None:
    # Volume mounted but NO fresh heartbeat (updater profile off / crash-looped) -> honest False,
    # so the caller falls back to the manual notice instead of a fake "started".
    marker = tmp_path / "update-signals" / "request"
    marker.parent.mkdir()
    monkeypatch.setattr(updater_mod, "UPDATE_REQUEST_FILE", str(marker))  # type: ignore[attr-defined]
    monkeypatch.setattr(updater_mod, "_HEARTBEAT_FILE", str(marker.parent / ".alive"))  # type: ignore[attr-defined]
    assert request_update() is False
    assert not marker.exists()


def test_request_update_soft_when_volume_missing(tmp_path: Path, monkeypatch: object) -> None:
    # updater module not wired up (no signals volume) → no crash, caller falls back to notify.
    marker = tmp_path / "absent" / "request"
    monkeypatch.setattr(updater_mod, "UPDATE_REQUEST_FILE", str(marker))  # type: ignore[attr-defined]
    assert request_update() is False
    assert not marker.exists()


def test_auto_update_setting_registered() -> None:
    # Owner-facing toggle for automatic installation lives in the config registry (cabinet).
    from src.core.config_registry import REGISTRY
    from src.core.enums import ConfigParamType

    row = next((p for p in REGISTRY if p.key == "AUTO_UPDATE_ENABLED"), None)
    assert row is not None, "AUTO_UPDATE_ENABLED must be registered"
    assert row.type is ConfigParamType.BOOL
    assert row.default is False  # opt-in: never auto-install unless the owner turns it on
