#!/usr/bin/env bash
# Container entrypoint: apply DB migrations once, then exec the given command.
# Only the `web` service should run migrations; workers wait for the DB to be ready.
#
# Migrations run BEFORE the app starts, so the app's crash telemetry (which lives in the
# FastAPI/aiogram/taskiq handlers) can't see a boot failure. We report it here instead — a
# one-shot POST to the ingest server — so install-time failures surface on the dashboard
# alongside runtime errors, not only in the operator's `docker logs`.
set -euo pipefail

# Same anonymous-install id the runtime reporter uses, so boot and runtime errors from one
# install group together. Vendor ingest URL matches TelemetrySettings' default (installs
# usually don't set TELEMETRY__URL). Opt out with TELEMETRY__ENABLED=false.
report_boot_failure() { # $1 = phase, $2 = logfile with the captured output
  [[ "${TELEMETRY__ENABLED:-true}" == "true" ]] || return 0
  local url="${TELEMETRY__URL:-https://docs.vpn-hub.pro/ingest}"
  [[ -n "$url" ]] || return 0
  TS_URL="$url" TS_PHASE="$1" TS_LOG="$2" python - <<'PY' || true
import datetime, hashlib, json, os, re, sys, urllib.request

url, phase, logfile = os.environ["TS_URL"], os.environ["TS_PHASE"], os.environ["TS_LOG"]
try:
    tail = open(logfile, encoding="utf-8", errors="replace").read()[-4000:]
except Exception:
    tail = ""


def scrub(t: str) -> str:
    # Defense in depth: strip a DSN password, a bot token, emails before anything leaves.
    t = re.sub(r"(://[^:@/\s]+:)[^@/\s]+(@)", r"\1<redacted>\2", t)  # user:pass@ in any URL
    t = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "<token>", t)  # telegram bot token shape
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "<email>", t)
    return t


tail = scrub(tail)
lines = [ln for ln in tail.splitlines() if ln.strip()]
exc_line = lines[-1] if lines else f"{phase} failed"
token = os.environ.get("BOT__TOKEN", "")
install_id = hashlib.sha256(f"hubbot:{token or 'tokenless'}".encode()).hexdigest()[:16]
version = os.environ.get("APP__BUILD_SHA", "") or "boot"
fp = "boot" + hashlib.sha1(f"{phase}|{exc_line}".encode()).hexdigest()[:12]
event = {
    "error_id": f"E1201-{fp[4:12]}",  # 1201 = система: ошибка БД (см. error_codes.py)
    "code": 1201,
    "fingerprint": fp,
    "source": "install",
    "exc_type": "MigrationBootFailure",
    "message": exc_line[:500],
    "traceback": tail,
    "context": {"phase": phase},
    "count": 1,
    "ts": datetime.datetime.now(datetime.UTC).isoformat(),
}
payload = {"install_id": install_id, "version": version, "events": [event]}
headers = {"Content-Type": "application/json"}
tok = os.environ.get("TELEMETRY__TOKEN", "")
if tok:
    headers["X-Telemetry-Token"] = tok
req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
try:
    urllib.request.urlopen(req, timeout=5).read()
    print("[entrypoint] boot failure reported to telemetry", file=sys.stderr)
except Exception:
    pass  # telemetry must never mask the real failure
PY
}

if [[ "${RUN_MIGRATIONS:-false}" == "true" ]]; then
  echo "[entrypoint] applying migrations..."
  logf="$(mktemp)"
  if ! alembic upgrade head 2>&1 | tee "$logf"; then
    echo "[entrypoint] migrations FAILED — reporting to telemetry" >&2
    report_boot_failure migrations "$logf"
    rm -f "$logf"
    exit 1
  fi
  rm -f "$logf"
fi

echo "[entrypoint] starting: $*"
exec "$@"
