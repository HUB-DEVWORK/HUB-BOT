#!/usr/bin/env bash
# VPN-HUB BOT — one-command install.
#
#   git clone https://github.com/bini69-oi/HUB-BOT.git && cd HUB-BOT && ./scripts/install.sh
#
# Asks only for the bot token (and optionally a domain); generates every secret,
# starts the whole stack in Docker and prints the cabinet URL + admin password.
# Everything else is configured later through the web UI.
set -euo pipefail

# --- pretty output -------------------------------------------------------------
B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
ORANGE=$'\033[38;5;208m'; GREEN=$'\033[1;32m'; CYAN=$'\033[1;36m'; RED=$'\033[1;31m'
LINE="────────────────────────────────────────────────────────"

hr()    { printf "%s%s%s\n" "$DIM" "$LINE" "$R"; }
step()  { printf "\n%s[%s/4]%s %s%s%s\n" "$ORANGE" "$1" "$R" "$B" "$2" "$R"; }
ok()    { printf "  %s✔%s %s\n" "$GREEN" "$R" "$*"; }
note()  { printf "  %s·%s %s\n" "$DIM" "$R" "$*"; }
ask()   { printf "  %s?%s %s" "$CYAN" "$R" "$*"; }
fail()  { printf "\n  %s✗ %s%s\n" "$RED" "$*" "$R"; exit 1; }

banner() {
  printf "\n"
  hr
  printf "   %sVPN%s%s-HUB%s %sBOT%s  %s· установка одной командой%s\n" \
    "$B" "$R" "$ORANGE$B" "$R" "$B" "$R" "$DIM" "$R"
  hr
}

# Long-running command with a spinner; full log lands in /tmp, tail shown on failure.
run_spin() { # run_spin "подпись" cmd...
  local label=$1; shift
  local log; log=$(mktemp /tmp/vpnhub-install.XXXXXX.log)
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
banner

# --- [1/4] prerequisites --------------------------------------------------------
step 1 "Docker"
if command -v docker >/dev/null 2>&1; then
  ok "docker уже установлен ($(docker --version | cut -d, -f1))"
else
  run_spin "ставлю Docker (get.docker.com)" sh -c "curl -fsSL https://get.docker.com | sh"
fi
docker compose version >/dev/null 2>&1 || fail "docker compose v2 не найден"
ok "docker compose v2 на месте"

# --- [2/4] questions (only what we can't invent) --------------------------------
step 2 "Пара вопросов"
if [ -f .env ]; then
  ok ".env уже существует — использую его (удалите файл для чистой установки)"
else
  ask "Токен бота из @BotFather: "; read -r BOT_TOKEN
  [ -n "$BOT_TOKEN" ] || fail "токен обязателен"
  ask "Домен для кабинета ${DIM}(Enter — пропустить, будет http://IP)${R}: "; read -r DOMAIN || true
  ACME_EMAIL=""
  if [ -n "${DOMAIN:-}" ]; then
    ask "E-mail для Let's Encrypt: "; read -r ACME_EMAIL
  fi
  ask "URL панели Remnawave ${DIM}(Enter — встроенная мок-панель для теста)${R}: "; read -r PANEL_URL || true
  PANEL_TOKEN=""
  if [ -n "${PANEL_URL:-}" ]; then
    ask "API-токен панели: "; read -r PANEL_TOKEN
  fi

  run_spin "генерирую секреты" docker pull python:3.12-slim
  gen() { docker run --rm python:3.12-slim python -c "$1"; }
  CRYPT=$(gen "from base64 import urlsafe_b64encode; import os; print(urlsafe_b64encode(os.urandom(32)).decode())")
  JWT=$(gen "import secrets; print(secrets.token_hex(32))")
  WHS=$(gen "import secrets; print(secrets.token_hex(24))")
  DBPW=$(gen "import secrets; print(secrets.token_urlsafe(18))")
  ADMPW=$(gen "import secrets; print(secrets.token_urlsafe(12))")

  cat > .env <<ENVEOF
APP__ENV=production
APP__DEBUG=false
APP__CRYPT_KEY=$CRYPT
APP__JWT_SECRET=$JWT
ADMIN__USERNAME=admin
ADMIN__PASSWORD=$ADMPW
BOT__TOKEN=$BOT_TOKEN
BOT__USE_WEBHOOK=false
BOT__WEBHOOK_SECRET=$WHS
DATABASE__HOST=postgres
DATABASE__PORT=5432
DATABASE__USER=vpn
DATABASE__PASSWORD=$DBPW
DATABASE__NAME=vpn
REDIS__HOST=redis
REDIS__PORT=6379
REMNAWAVE__BASE_URL=${PANEL_URL:-http://mockpanel:3010}
REMNAWAVE__AUTH_TYPE=api_key
REMNAWAVE__TOKEN=${PANEL_TOKEN:-mock-panel-token}
REMNAWAVE__WEBHOOK_SECRET=$WHS
WEB__HOST=0.0.0.0
WEB__PORT=8080
WEB__PUBLIC_URL=$([ -n "${DOMAIN:-}" ] && echo "https://$DOMAIN")
LOG__LEVEL=INFO
LOG__USE_JSON=true
DOMAIN=${DOMAIN:-:80}
ACME_EMAIL=${ACME_EMAIL:-}
$([ -z "${PANEL_URL:-}" ] && echo "COMPOSE_PROFILES=mock")
ENVEOF
  chmod 600 .env
  ok ".env создан, права 600"
  [ -z "${PANEL_URL:-}" ] && note "панель не указана — включаю встроенную мок-панель (профиль mock)"
fi

# --- [3/4] build + up ------------------------------------------------------------
step 3 "Сборка и запуск стека"
note "postgres · redis · web · bot · worker · scheduler · caddy"
run_spin "docker compose build (первый раз — несколько минут)" \
  docker compose -f docker/compose.prod.yml build
run_spin "docker compose up -d" \
  docker compose -f docker/compose.prod.yml up -d

# --- [4/4] health ---------------------------------------------------------------
step 4 "Миграции и здоровье"
printf "  %s…%s жду /health " "$DIM" "$R"
HEALTH_OK=""
for _ in $(seq 1 90); do
  if docker compose -f docker/compose.prod.yml exec -T web \
       python -c "import urllib.request as u; u.urlopen('http://localhost:8080/health', timeout=3)" \
       >/dev/null 2>&1; then
    HEALTH_OK=1; break
  fi
  printf "."
  sleep 2
done
printf "\n"
[ -n "$HEALTH_OK" ] || fail "web не поднялся за 3 минуты — смотри: docker compose -f docker/compose.prod.yml logs web"
ok "миграции применены, /health отвечает"

# --- summary ---------------------------------------------------------------------
ENV_DOMAIN=$(grep '^DOMAIN=' .env | cut -d= -f2)
IP=$(curl -fs4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
URL="http://$IP"
[ -n "$ENV_DOMAIN" ] && [ "$ENV_DOMAIN" != ":80" ] && URL="https://$ENV_DOMAIN"
ADMPW_OUT=$(grep '^ADMIN__PASSWORD=' .env | cut -d= -f2)

printf "\n"
hr
printf "   %s🎉 Готово!%s\n\n" "$GREEN$B" "$R"
printf "   %sКабинет%s      %s%s/admin/%s\n"  "$DIM" "$R" "$B" "$URL" "$R"
printf "   %sЛогин%s        admin\n"           "$DIM" "$R"
printf "   %sПароль%s       %s%s%s\n"          "$DIM" "$R" "$B" "$ADMPW_OUT" "$R"
printf "   %sМини-аппа%s    %s/app/\n"         "$DIM" "$R" "$URL"
printf "\n"
printf "   %sДальше всё в кабинете: тарифы, платёжки, меню бота, мини-аппа.%s\n" "$DIM" "$R"
printf "   %sОбновление в одну команду: ./scripts/update.sh%s\n" "$DIM" "$R"
hr
