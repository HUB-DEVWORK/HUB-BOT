"""Telemetry reporter: payload shape, dedupe, sanitization, resilience, opt-out."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from src.infrastructure.services.telemetry import (
    TelemetryReporter,
    _scrub,
    fingerprint,
    install_id_from_token,
)


def _boom() -> None:
    raise ValueError("secret /Users/nobody/project/src/x.py path in message")


def _make_exc() -> Exception:
    try:
        _boom()
    except ValueError as exc:
        return exc
    raise AssertionError


def _reporter(
    requests: list[dict[str, Any]], *, status: int = 200, enabled: bool = True
) -> TelemetryReporter:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(status)

    return TelemetryReporter(
        enabled=enabled,
        url="https://errors.test/ingest",
        app_version="0.1.0",
        install_id="cafe0123",
        transport=httpx.MockTransport(handle),
    )


async def test_report_flush_payload_and_dedupe() -> None:
    requests: list[dict[str, Any]] = []
    reporter = _reporter(requests)

    exc = _make_exc()
    error_id = reporter.report(exc, source="web", context={"path": "/x", "junk": "y" * 500})
    # Deterministic per bug: E<код класса>-<fingerprint>, same every occurrence.
    # ValueError не классифицирован -> 9001 (см. src/core/error_codes.py).
    assert error_id == f"E9001-{fingerprint(exc)[:8]}"
    # Same fingerprint twice more -> merged into count, not extra events.
    assert reporter.report(_make_exc(), source="web") == error_id
    reporter.report(_make_exc(), source="web")
    await reporter.flush()

    assert len(requests) == 1
    payload = requests[0]
    assert payload["install_id"] == "cafe0123"
    assert payload["version"] == "0.1.0"
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert event["count"] == 3
    assert event["exc_type"] == "ValueError"
    assert event["fingerprint"] == fingerprint(exc)
    # Absolute paths are relativized in the traceback (basename outside src/);
    # context values are capped.
    assert "/Users/" not in event["traceback"].split("ValueError")[0]
    assert 'File "test_telemetry_reporter.py"' in event["traceback"]
    assert len(event["context"]["junk"]) == 200
    await reporter.aclose()


async def test_send_failure_keeps_events_and_never_raises() -> None:
    requests: list[dict[str, Any]] = []
    reporter = _reporter(requests, status=503)
    reporter.report(_make_exc(), source="worker")
    await reporter.flush()  # 503 -> event goes back to the queue
    assert len(requests) == 1
    assert reporter._pending
    await reporter.aclose()  # final flush retries; still must not raise


async def test_disabled_is_noop_but_returns_error_id() -> None:
    requests: list[dict[str, Any]] = []
    reporter = _reporter(requests, enabled=False)
    error_id = reporter.report(_make_exc(), source="bot")
    assert error_id
    await reporter.flush()
    await reporter.aclose()
    assert requests == []


async def test_background_task_sends_without_explicit_flush() -> None:
    requests: list[dict[str, Any]] = []
    reporter = _reporter(requests)
    reporter.report(_make_exc(), source="web")
    # The sender wakes on report and flushes after the 3s settle delay.
    for _ in range(80):
        if requests:
            break
        await asyncio.sleep(0.1)
    assert requests, "background sender never flushed"
    await reporter.aclose()


def test_scrub_removes_pii_and_secrets() -> None:
    # SQLAlchemy appends bound parameters — the classic PII leak.
    sqla = (
        "(sqlite3.IntegrityError) UNIQUE constraint failed: users.telegram_id\n"
        "[SQL: INSERT INTO users (telegram_id, email) VALUES (?, ?)]\n"
        "[parameters: (123456789, 'user@example.com')]"
    )
    out = _scrub(sqla)
    assert "123456789" not in out and "user@example.com" not in out
    assert "[parameters" not in out and "[SQL" not in out
    assert "UNIQUE constraint failed" in out  # the useful bit survives

    # Bot token, email, telegram id, absolute path with OS username.
    assert "<token>" in _scrub("auth failed for 8123456789:AAHxyz_bot-Token_1234567890abcd")
    assert _scrub("mailto boss@corp.io denied") == "mailto <email> denied"
    assert "/Users/maksim/" not in _scrub('open("/Users/maksim/secret/x.py") failed')


async def test_message_pii_is_scrubbed_before_send() -> None:
    requests: list[dict[str, Any]] = []
    reporter = _reporter(requests)

    def raise_dbish() -> None:
        raise ValueError("dup [parameters: (777123456, 'a@b.com')]")

    try:
        raise_dbish()
    except ValueError as exc:
        reporter.report(exc, source="worker")
    await reporter.flush()

    event = requests[0]["events"][0]
    assert "777123456" not in event["message"] and "a@b.com" not in event["message"]
    await reporter.aclose()


def test_install_id_is_stable_and_anonymous() -> None:
    a = install_id_from_token("123:ABC")
    assert a == install_id_from_token("123:ABC")
    assert a != install_id_from_token("456:DEF")
    assert "123" not in a and len(a) == 16


def test_worker_transient_infra_not_reported() -> None:
    # Periodic worker tasks heal on the next tick; a transient DB/Redis/panel blip must be
    # skipped so it doesn't flood telemetry (the E1402/E1301 storms in the dashboard).
    import asyncio

    from src.infrastructure.taskiq.broker import _is_transient_infra

    assert _is_transient_infra(TimeoutError())
    assert _is_transient_infra(asyncio.TimeoutError())
    assert _is_transient_infra(ConnectionError("redis down"))
    assert _is_transient_infra(OSError(111, "Connection refused"))
    # a real bug is still reported
    assert not _is_transient_infra(ValueError("logic bug"))
    assert not _is_transient_infra(KeyError("missing"))
