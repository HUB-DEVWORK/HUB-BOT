#!/usr/bin/env bash
# VPN-HUB BOT — one-command install.
#
#   git clone https://github.com/bini69-oi/HUB-BOT.git && cd HUB-BOT && ./scripts/install.sh
#
# Asks only for the bot token (and optionally a domain); generates every secret,
# starts the whole stack in Docker and prints the cabinet URL + admin password.
# Everything else is configured later through the web UI.
set -euo pipefail

say()  { printf "\033[1;32m==>\033[0m %s\n" "$*"; }
ask()  { printf "\033[1;36m ?\033[0m %s" "$*"; }
fail() { printf "\033[1;31m ✗ %s\033[0m\n" "$*"; exit 1; }

cd "$(dirname "$0")/.."

# --- prerequisites -----------------------------------------------------------
command -v docker >/dev/null 2>&1 || {
  say "Ставлю Docker…"
  curl -fsSL https://get.docker.com | sh >/dev/null
}
docker compose version >/dev/null 2>&1 || fail "docker compose v2 не найден"

# --- questions (only what we can't invent) ------------------------------------
if [ -f .env ]; then
  say ".env уже существует — использую его (удалите файл для чистой установки)"
else
  ask "Токен бота из @BotFather: "; read -r BOT_TOKEN
  [ -n "$BOT_TOKEN" ] || fail "токен обязателен"
  ask "Домен для кабинета (Enter — пропустить, будет http://IP): "; read -r DOMAIN || true
  ACME_EMAIL=""
  if [ -n "${DOMAIN:-}" ]; then
    ask "E-mail для Let's Encrypt: "; read -r ACME_EMAIL
  fi
  ask "URL панели Remnawave (Enter — встроенная мок-панель для теста): "; read -r PANEL_URL || true
  PANEL_TOKEN=""
  if [ -n "${PANEL_URL:-}" ]; then
    ask "API-токен панели: "; read -r PANEL_TOKEN
  fi

  say "Генерирую секреты…"
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
REMNAWAVE__BASE_URL=${PANEL_URL:-http://web:8080/__no_panel__}
REMNAWAVE__AUTH_TYPE=api_key
REMNAWAVE__TOKEN=${PANEL_TOKEN:-mock-panel-token}
REMNAWAVE__WEBHOOK_SECRET=$WHS
WEB__HOST=0.0.0.0
WEB__PORT=8080
LOG__LEVEL=INFO
LOG__USE_JSON=true
DOMAIN=${DOMAIN:-:80}
ACME_EMAIL=${ACME_EMAIL:-}
ENVEOF
  chmod 600 .env
fi

# --- up ------------------------------------------------------------------------
say "Собираю и запускаю стек (postgres, redis, web, bot, worker, scheduler, caddy)…"
docker compose -f docker/compose.prod.yml up -d --build

say "Жду миграции и старт веба…"
for _ in $(seq 1 60); do
  if docker compose -f docker/compose.prod.yml exec -T web python -c "print('ok')" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

IP=$(curl -fs4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
URL="http://$IP"
[ -n "${DOMAIN:-}" ] && [ "${DOMAIN:-:80}" != ":80" ] && URL="https://$DOMAIN"

ADMPW_OUT=$(grep '^ADMIN__PASSWORD=' .env | cut -d= -f2)
say "Готово! 🎉"
echo
echo "  Кабинет:    $URL/admin/"
echo "  Логин:      admin"
echo "  Пароль:     $ADMPW_OUT"
echo "  Мини-аппа:  $URL/app/"
echo
echo "  Дальше всё настраивается в кабинете: тарифы, платёжки, меню бота,"
echo "  мини-аппа, Remnawave (Настройки → или .env REMNAWAVE__*)."
