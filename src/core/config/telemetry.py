"""Crash-telemetry settings (docs/TELEMETRY.md).

Unhandled errors are reported to the product's ingest server so bugs in the wild
get fixed before owners even write in. No user data travels — see the reporter's
module docstring for the exact payload (scrubbed traceback, E-code, app version,
anonymous install id). Opt out with ``TELEMETRY__ENABLED=false``; point
``TELEMETRY__URL`` at your own ingest (deploy one from ``telemetry-server/``)
to keep the reports to yourself.
"""

from __future__ import annotations

from pydantic import BaseModel

VENDOR_INGEST_URL = "https://docs.vpn-hub.pro/ingest"


class TelemetrySettings(BaseModel):
    enabled: bool = True
    url: str = VENDOR_INGEST_URL  # empty -> telemetry is a no-op
    token: str = ""  # optional shared secret, sent as X-Telemetry-Token
