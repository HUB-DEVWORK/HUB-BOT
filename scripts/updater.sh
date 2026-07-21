#!/usr/bin/env bash
# Self-update runner (runs inside the `updater` sidecar container).
#
# Watches for the marker the bot's «Обновить» button drops into the shared volume and then
# runs scripts/update.sh (backup → git pull → rebuild → restart). The container has the host
# docker socket + the host repo bind-mounted at /repo, so update.sh operates on the real files
# and the host daemon. SKIP_UPDATER_RECREATE tells update.sh not to recreate US mid-update.
set -uo pipefail

REPO=/repo
MARKER="${UPDATE_REQUEST_FILE:-/repo/update-signals/request}"
LOG="$(dirname "$MARKER")/last-update.log"

mkdir -p "$(dirname "$MARKER")"
# The bind-mounted repo is owned by the host user; git refuses to run in a "dubious" dir as root.
git config --global --add safe.directory "$REPO" 2>/dev/null || true

COMPOSE="docker compose --env-file $REPO/.env -f $REPO/docker/compose.prod.yml"
_RESTARTABLE="bot web worker scheduler"

HEARTBEAT="$(dirname "$MARKER")/.alive"
echo "updater: watching $MARKER"
while true; do
  # Liveness beacon: the app checks this file's freshness before promising "update started" —
  # the signal volume exists in every container even when this sidecar isn't running.
  touch "$HEARTBEAT" 2>/dev/null || true
  if [ -f "$MARKER" ]; then
    req="$(head -n1 "$MARKER" 2>/dev/null)"
    rm -f "$MARKER"
    case "$req" in
      "restart "*)
        # Restart one service (or all) via the host daemon — no rebuild, no git pull.
        svc="${req#restart }"
        echo "updater: restart '$svc' at $(date -u +%FT%TZ)"
        if [ "$svc" = "all" ]; then
          (cd "$REPO" && $COMPOSE restart bot web worker scheduler) >>"$LOG" 2>&1 \
            && echo "updater: restart OK" || echo "updater: restart FAILED — see $LOG"
        elif printf '%s\n' $_RESTARTABLE | grep -qx "$svc"; then
          (cd "$REPO" && $COMPOSE restart "$svc") >>"$LOG" 2>&1 \
            && echo "updater: restart OK" || echo "updater: restart FAILED — see $LOG"
        else
          echo "updater: refused restart of unknown service '$svc'"
        fi
        ;;
      *)
        echo "updater: update at $(date -u +%FT%TZ) — running update.sh"
        if (cd "$REPO" && SKIP_UPDATER_RECREATE=1 bash scripts/update.sh) >>"$LOG" 2>&1; then
          echo "updater: update OK"
        else
          echo "updater: update FAILED — see $LOG"
        fi
        # git ran as root here; hand the repo back to its host owner so a later manual
        # ./scripts/update.sh (run as that user) isn't blocked by root-owned objects. PRUNE the
        # signal volume: it's bind-mounted inside the repo but written by the non-root app user,
        # and a recursive chown to root would make future markers unwritable — silently disabling
        # the updater after the first run.
        owner="$(stat -c '%u:%g' "$REPO" 2>/dev/null || echo 0:0)"
        find "$REPO" -path "$REPO/update-signals" -prune -o -exec chown "$owner" {} + 2>/dev/null \
          || true
        ;;
    esac
  fi
  sleep 10
done
