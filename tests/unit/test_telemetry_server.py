"""Tests for the standalone telemetry ingest server.

``telemetry-server/`` has a hyphen, so the module is loaded via importlib, not import.
Env is read inside ``create_app()`` — set it first, then build the app.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVER_PATH = ROOT / "telemetry-server" / "server.py"


def _load(tmp_db: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("TS_DB_PATH", str(tmp_db))
    monkeypatch.setenv("TS_DASH_USER", "admin")
    monkeypatch.setenv("TS_DASH_PASS", "pw")
    monkeypatch.delenv("TS_INGEST_TOKEN", raising=False)
    monkeypatch.setenv("TS_ALLOW_ANONYMOUS", "1")  # tokenless tests opt into anonymous ingest
    monkeypatch.delenv("TS_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TS_TG_CHAT_ID", raising=False)
    spec = importlib.util.spec_from_file_location("telemetry_server", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # dataclass/pydantic resolve stringified annotations via sys.modules[__module__].
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ts")


def _batch(fingerprint: str, install_id: str, version: str, count: int) -> dict[str, Any]:
    return {
        "install_id": install_id,
        "version": version,
        "events": [
            {
                "error_id": f"err-{install_id}-{count}",
                "fingerprint": fingerprint,
                "source": "bot",
                "exc_type": "ValueError",
                "message": "boom in handler",
                "traceback": "Traceback (most recent call last): ...",
                "context": {"handler": "start"},
                "count": count,
                "ts": "2026-07-11T00:00:00+00:00",
            }
        ],
    }


async def test_ingest_aggregates_same_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.db"
    mod = _load(db, monkeypatch)
    async with _client(mod.create_app()) as client:
        r1 = await client.post("/ingest", json=_batch("fp-agg", "inst-1", "1.0.0", 2))
        r2 = await client.post("/ingest", json=_batch("fp-agg", "inst-2", "1.1.0", 3))
    assert r1.status_code == 200
    assert r1.json() == {"ok": True, "accepted": 1}
    assert r2.json() == {"ok": True, "accepted": 1}

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT total, installs, versions, resolved FROM issues WHERE fingerprint = 'fp-agg'"
        ).fetchone()
        n_issues = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        n_events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE fingerprint = 'fp-agg'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_issues == 1
    assert row is not None
    assert row[0] == 5
    assert "inst-1" in row[1] and "inst-2" in row[1]
    assert "1.0.0" in row[2] and "1.1.0" in row[2]
    assert row[3] == 0
    assert n_events == 2


async def test_dashboard_auth_toggle_and_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.db"
    mod = _load(db, monkeypatch)
    auth = ("admin", "pw")
    async with _client(mod.create_app()) as client:
        await client.post("/ingest", json=_batch("fp-dash", "inst-1", "1.0.0", 1))

        r = await client.get("/")
        assert r.status_code == 401

        r = await client.get("/", auth=auth)
        assert r.status_code == 200
        assert "ValueError" in r.text

        r = await client.post("/issues/fp-dash/toggle", auth=auth)
        assert r.status_code == 303

        r = await client.get("/", auth=auth)
        assert "ValueError" not in r.text
        r = await client.get("/?all=1", auth=auth)
        assert "ValueError" in r.text

        # Regression path: a resolved issue reopens on the next ingest.
        r = await client.post("/ingest", json=_batch("fp-dash", "inst-1", "1.0.0", 1))
        assert r.json()["accepted"] == 1
        r = await client.get("/", auth=auth)
        assert "ValueError" in r.text

    conn = sqlite3.connect(db)
    try:
        resolved = conn.execute(
            "SELECT resolved FROM issues WHERE fingerprint = 'fp-dash'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert resolved == 0


async def test_bad_event_never_strands_a_partial_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed event mid-batch rolls back only itself; good events still commit."""
    db = tmp_path / "t.db"
    mod = _load(db, monkeypatch)
    good = {
        "error_id": "e1",
        "fingerprint": "fp-good",
        "exc_type": "ValueError",
        "message": "ok",
        "count": 1,
        "ts": "2026-07-11T00:00:00+00:00",
    }
    # count over SQLite's INTEGER range would raise OverflowError inside the insert;
    # pydantic's le= now rejects it, and even a non-listed error must not 500 the batch.
    over = {**good, "fingerprint": "fp-over", "count": 10**19}
    async with _client(mod.create_app()) as client:
        r = await client.post(
            "/ingest", json={"install_id": "i", "version": "1", "events": [good, over]}
        )
    assert r.status_code == 200
    assert r.json()["accepted"] == 1  # good accepted, bad skipped

    conn = sqlite3.connect(db)
    try:
        fps = {row[0] for row in conn.execute("SELECT fingerprint FROM issues")}
    finally:
        conn.close()
    assert fps == {"fp-good"}  # the good event committed, the bad one left nothing


async def test_dashboard_escapes_malicious_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hostile install can't inject script into our own dashboard (stored XSS)."""
    db = tmp_path / "t.db"
    mod = _load(db, monkeypatch)
    evil = {
        "error_id": "x",
        "fingerprint": "fp-xss",
        "exc_type": "ValueError",
        "message": "<script>alert(1)</script>",
        "count": 1,
        "ts": "2026-07-11T00:00:00+00:00",
    }
    async with _client(mod.create_app()) as client:
        await client.post("/ingest", json={"install_id": "i", "version": "1", "events": [evil]})
        r = await client.get("/", auth=("admin", "pw"))
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


async def test_ingest_token_enforced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load(tmp_path / "t.db", monkeypatch)
    monkeypatch.setenv("TS_INGEST_TOKEN", "sekret")
    async with _client(mod.create_app()) as client:
        batch = _batch("fp-tok", "inst-1", "1.0.0", 1)

        r = await client.post("/ingest", json=batch)
        assert r.status_code in (401, 403)

        r = await client.post("/ingest", json=batch, headers={"X-Telemetry-Token": "wrong"})
        assert r.status_code in (401, 403)

        r = await client.post("/ingest", json=batch, headers={"X-Telemetry-Token": "sekret"})
        assert r.status_code == 200
        assert r.json() == {"ok": True, "accepted": 1}


async def test_error_code_stored_shown_and_not_wiped_by_legacy_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "t.db"
    mod = _load(db, monkeypatch)
    coded = _batch("fp-code", "inst-1", "1.0.0", 1)
    coded["events"][0]["code"] = 4003
    legacy = _batch("fp-code", "inst-2", "0.9.0", 1)  # старый клиент: поля code нет
    async with _client(mod.create_app()) as client:
        assert (await client.post("/ingest", json=coded)).status_code == 200
        assert (await client.post("/ingest", json=legacy)).status_code == 200
        dash = await client.get("/", auth=("admin", "pw"))
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT code FROM issues WHERE fingerprint = 'fp-code'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == 4003  # code=0 от legacy-клиента не затёр известный код
    assert "E4003" in dash.text
