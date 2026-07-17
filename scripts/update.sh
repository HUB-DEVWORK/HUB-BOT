#!/usr/bin/env bash
# VPN-HUB BOT — safe one-command update.
#
#   cd HUB-BOT && ./scripts/update.sh
#
# Order matters so a broken update never loses data:
#   1. dump the DB into ./backups/  (rollback insurance)
#   2. git pull --ff-only           (never rewrites local history)
#   3. rebuild images + restart     (web runs alembic migrations on start)
#   4. wait for /health             (fail loudly with rollback instructions)
set -euo pipefail

B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
ORANGE=$'\033[38;5;208m'; GREEN=$'\033[1;32m'; RED=$'\033[1;31m'
LINE="────────────────────────────────────────────────────────"

hr()   { printf "%s%s%s\n" "$DIM" "$LINE" "$R"; }
step() { printf "\n%s[%s/4]%s %s%s%s\n" "$ORANGE" "$1" "$R" "$B" "$2" "$R"; }
ok()   { printf "  %s✔%s %s\n" "$GREEN" "$R" "$*"; }
fail() { printf "\n  %s✗ %s%s\n" "$RED" "$*" "$R"; exit 1; }

run_spin() { # run_spin "подпись" cmd...
  local label=$1; shift
  # Template must END in X's: busybox mktemp (the updater sidecar is alpine-based) rejects a
  # suffix after them with "mktemp: : Invalid argument". That aborted step 3 on every
  # button-triggered update — the sidecar path — while host runs (GNU mktemp) worked fine.
  local log; log=$(mktemp /tmp/vpnhub-update.XXXXXX)
  printf "  %s…%s %s " "$DIM" "$R" "$label"
  if "$@" >"$log" 2>&1; then
    printf "\r  %s✔%s %s%s\n" "$GREEN" "$R" "$label" "          "
    rm -f "$log"
  else
    printf "\r  %s✗ %s — последние строки лога:%s\n" "$RED" "$label" "$R"
    tail -n 25 "$log" | sed 's/^/    /'
    printf "  %sполный лог: %s%s\n" "$DIM" "$log" "$R"
    exit 1
  fi
}

cd "$(dirname "$0")/.."
# --env-file .env: Compose resolves ${VAR:?} interpolation against the compose file's dir
# (docker/), not the CWD, so without this it can't find our repo-root .env.
COMPOSE="docker compose --env-file .env -f docker/compose.prod.yml"
[ -f .env ] || fail ".env не найден — сначала установка: ./scripts/install.sh"

bring_up() { # (re)create+start the stack; never recreate the updater if we ARE it
  if [ -n "${SKIP_UPDATER_RECREATE:-}" ]; then
    # Triggered from inside the updater container: recreate every service EXCEPT updater,
    # else `up -d` would kill this very process mid-update. The updater keeps the old code
    # (it's a tiny watch loop); to update it too, run ./scripts/update.sh on the host once.
    # Naming a service on the CLI activates it even if its profile is off — on a "behind an
    # existing proxy" install (caddy omitted from COMPOSE_PROFILES, :80/:443 already taken)
    # that would try to start caddy, fail to bind, and abort the whole update. So include
    # caddy only when its profile is actually enabled.
    local svc="postgres redis web bot worker scheduler"
    grep -qE '^COMPOSE_PROFILES=.*caddy' .env 2>/dev/null && svc="$svc caddy"
    $COMPOSE up -d --no-deps $svc
  else
    $COMPOSE up -d
  fi
}

# An update that dies half-way (dropped ssh, ^C, failed build, OOM) used to leave the stack
# down: `up -d` creates containers before starting them, so an interrupt strands bot/worker/
# scheduler in "Created" and the bot never comes back. Always try to put the stack back on
# its feet — with the new image if the build got that far, otherwise the old one.
_recovered=0
recover() {
  local code=$?
  [ "$code" -eq 0 ] && return 0
  [ "$_recovered" -eq 1 ] && return 0
  _recovered=1
  printf "\n  %s⚠ обновление прервано — возвращаю сервисы в строй%s\n" "$ORANGE" "$R"
  bring_up >/dev/null 2>&1 || true
  $COMPOSE ps 2>/dev/null | tail -n +2 | sed 's/^/    /' || true
}
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap recover EXIT

printf "\n"; hr
printf "   %sVPN%s%s-HUB%s %sBOT%s  %s· безопасное обновление%s\n" \
  "$B" "$R" "$ORANGE$B" "$R" "$B" "$R" "$DIM" "$R"
hr

OLD_REV=$(git rev-parse --short HEAD)

# --- 1. backup ----------------------------------------------------------------
step 1 "Бэкап БД"
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="backups/pre-update-$STAMP.sql.gz"
DB_USER=$(grep '^DATABASE__USER=' .env | cut -d= -f2)
DB_NAME=$(grep '^DATABASE__NAME=' .env | cut -d= -f2)
$COMPOSE exec -T postgres pg_dump -U "${DB_USER:-vpn}" "${DB_NAME:-vpn}" | gzip > "$BACKUP" \
  || fail "бэкап не снялся — обновление отменено (стек запущен? $COMPOSE ps)"
[ -s "$BACKUP" ] || fail "бэкап пустой — обновление отменено"
ok "снят: $BACKUP ($(du -h "$BACKUP" | cut -f1))"

# --- 2. pull ------------------------------------------------------------------
step 2 "Обновления из git"
git pull --ff-only >/dev/null 2>&1 || fail "git pull не прошёл — есть локальные правки? (git stash, затем повторить)"
NEW_REV=$(git rev-parse --short HEAD)
if [ "$OLD_REV" = "$NEW_REV" ]; then
  ok "уже последняя версия ($NEW_REV) — пересобираю на всякий случай"
else
  ok "$OLD_REV → $NEW_REV"
  git log --oneline "$OLD_REV..$NEW_REV" | head -8 | sed "s/^/    ${DIM}·${R} /"
fi

# --- 3. rebuild + restart -------------------------------------------------------
step 3 "Пересборка и перезапуск"
# Re-bake the git SHA after the pull so the update checker reports the new revision.
export GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
run_spin "docker compose build" $COMPOSE build
if [ -n "${SKIP_UPDATER_RECREATE:-}" ]; then
  run_spin "docker compose up -d (без updater)" bring_up
else
  run_spin "docker compose up -d" bring_up
fi

# --- 3b. re-attach web to an external reverse-proxy network (optional) ----------
# `docker network connect` is imperative: it's lost every time compose recreates the
# container. A `web` fronted by an EXISTING proxy on another network (e.g. a shared Caddy
# that already owns :443) therefore drops off the proxy on each update and its domain
# starts 502-ing. Set WEB_PROXY_NETWORK=<external network name> in .env to have every
# update re-attach it. No-op when unset or when the network doesn't exist.
PROXY_NET=$(grep -E '^WEB_PROXY_NETWORK=' .env 2>/dev/null | cut -d= -f2- | xargs)
if [ -n "${PROXY_NET:-}" ]; then
  WEB_CID=$($COMPOSE ps -q web 2>/dev/null)
  if [ -n "$WEB_CID" ] && docker network inspect "$PROXY_NET" >/dev/null 2>&1; then
    docker network connect "$PROXY_NET" "$WEB_CID" 2>/dev/null \
      && ok "web подключён к сети прокси: $PROXY_NET" \
      || ok "web уже в сети прокси: $PROXY_NET"
  fi
fi

# --- 4. health-gate -------------------------------------------------------------
step 4 "Миграции и здоровье"
printf "  %s…%s жду /health " "$DIM" "$R"
for _ in $(seq 1 90); do
  if $COMPOSE exec -T web \
       python -c "import urllib.request as u; u.urlopen('http://localhost:8080/health', timeout=3)" \
       >/dev/null 2>&1; then
    printf "\n"
    ok "живой"
    printf "\n"; hr
    printf "   %s🎉 Обновлено до %s%s  %s(бэкап: %s)%s\n" "$GREEN$B" "$NEW_REV" "$R" "$DIM" "$BACKUP" "$R"
    hr
    exit 0
  fi
  printf "."
  sleep 2
done
printf "\n"

printf "\n  %s✗ web не поднялся после обновления.%s\n\n" "$RED" "$R"
printf "   %sЛоги%s    $COMPOSE logs --tail 100 web\n" "$DIM" "$R"
printf "   %sОткат%s   git checkout %s && $COMPOSE up -d --build\n" "$DIM" "$R" "$OLD_REV"
printf "   %sБД%s      gunzip -c %s | $COMPOSE exec -T postgres psql -U %s %s\n" \
  "$DIM" "$R" "$BACKUP" "${DB_USER:-vpn}" "${DB_NAME:-vpn}"
exit 1
