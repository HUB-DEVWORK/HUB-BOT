#!/usr/bin/env bash
# VPN-HUB BOT — one-command install.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/bini69-oi/HUB-BOT/main/scripts/install.sh)
#
# (или из клона: git clone https://github.com/bini69-oi/HUB-BOT.git && cd HUB-BOT && ./scripts/install.sh)
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
step()  { printf "\n%s[%s/5]%s %s%s%s\n" "$ORANGE" "$1" "$R" "$B" "$2" "$R"; }
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

# --- bootstrap: запуск через `bash <(curl …)` вне клона — клонируем репо и перезапускаемся
SCRIPT_DIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd -P || true)
if [ -z "$SCRIPT_DIR" ] || [ ! -f "$SCRIPT_DIR/../docker/compose.prod.yml" ]; then
  banner
  note "запуск вне клона репозитория — забираю код"
  if ! command -v git >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || fail "нужен git: установите его и запустите ещё раз"
    run_spin "ставлю git" sh -c "apt-get update -qq && apt-get install -y -qq git"
  fi
  if [ -d HUB-BOT/.git ]; then
    ok "клон HUB-BOT уже есть — использую его"
  else
    run_spin "git clone bini69-oi/HUB-BOT" git clone --depth 1 https://github.com/bini69-oi/HUB-BOT.git HUB-BOT
  fi
  cd HUB-BOT
  exec env VPNHUB_BOOTSTRAPPED=1 bash scripts/install.sh
fi

cd "$SCRIPT_DIR/.."
[ -n "${VPNHUB_BOOTSTRAPPED:-}" ] || banner
note "Требования: 1 vCPU / 1–2 GB RAM (создаём swap автоматически)"

# --- [1/5] prerequisites --------------------------------------------------------
step 1 "Docker"
if command -v docker >/dev/null 2>&1; then
  ok "docker уже установлен ($(docker --version | cut -d, -f1))"
else
  run_spin "ставлю Docker (get.docker.com)" sh -c "curl -fsSL https://get.docker.com | sh"
fi
docker compose version >/dev/null 2>&1 || fail "docker compose v2 не найден"
ok "docker compose v2 на месте"

# --- [2/5] memory / swap guard --------------------------------------------------
# На 1 GB VPS `docker compose build` (SPA-сборка + Python-образ) может уронить
# машину по OOM. Заранее поднимаем swapfile, чтобы сборка пережила пик памяти.
# Всё best-effort и идемпотентно: не создаём второй swap, не дублируем fstab,
# а при любой невозможности (контейнер / нет root / нет места) — предупреждаем и идём дальше.
step 2 "Память и swap"
ensure_swap() {
  local swapfile=/swapfile
  local mem_kb swap_kb mem_mb swap_mb

  if [ ! -r /proc/meminfo ]; then
    note "не Linux или нет /proc/meminfo — пропускаю проверку памяти"
    return 0
  fi

  mem_kb=$(awk '/^MemTotal:/{print $2; exit}'  /proc/meminfo 2>/dev/null || echo 0)
  swap_kb=$(awk '/^SwapTotal:/{print $2; exit}' /proc/meminfo 2>/dev/null || echo 0)
  [ -n "$mem_kb" ]  || mem_kb=0
  [ -n "$swap_kb" ] || swap_kb=0
  mem_mb=$(( mem_kb / 1024 ))
  swap_mb=$(( swap_kb / 1024 ))
  note "RAM: ${mem_mb} MB · swap: ${swap_mb} MB"

  # Достаточно памяти → swap не нужен.
  if [ "$mem_kb" -ge 1843200 ]; then          # ~1.8 GB
    ok "памяти достаточно (${mem_mb} MB) — swap не требуется"
    return 0
  fi

  # Swap уже есть (любой источник) → второй не добавляем.
  if [ "$swap_kb" -gt 0 ]; then
    ok "swap уже активен (${swap_mb} MB) — хватит для сборки"
    return 0
  fi

  note "мало RAM (${mem_mb} MB) и нет swap — поднимаю 2 GB ${swapfile} под сборку"

  if [ "$(id -u 2>/dev/null || echo 1)" -ne 0 ]; then
    note "нет root — не могу создать swap; продолжаю (сборка может упасть по OOM)"
    return 0
  fi

  # Файл остался с прошлого запуска — не пересоздаём, пробуем просто включить.
  if [ -e "$swapfile" ]; then
    if swapon "$swapfile" 2>/dev/null; then
      ok "включил существующий ${swapfile}"
    else
      note "${swapfile} уже существует — пропускаю создание"
    fi
    return 0
  fi

  # Выделяем: сначала fallocate, при неудаче — dd.
  if ! fallocate -l 2G "$swapfile" 2>/dev/null; then
    if ! dd if=/dev/zero of="$swapfile" bs=1M count=2048 status=none 2>/dev/null; then
      note "не удалось выделить ${swapfile} (нет места/прав?) — продолжаю без swap"
      rm -f "$swapfile" 2>/dev/null || true
      return 0
    fi
  fi

  chmod 600 "$swapfile" 2>/dev/null || true

  if ! mkswap "$swapfile" >/dev/null 2>&1; then
    note "mkswap не отработал — продолжаю без swap"
    rm -f "$swapfile" 2>/dev/null || true
    return 0
  fi

  if ! swapon "$swapfile" 2>/dev/null; then
    note "swapon не отработал (нет прав в контейнере?) — продолжаю без swap"
    rm -f "$swapfile" 2>/dev/null || true
    return 0
  fi

  # Переживём перезагрузку — но не дублируем строку в /etc/fstab.
  if ! grep -qsE "^${swapfile}[[:space:]]" /etc/fstab 2>/dev/null; then
    if printf '%s none swap sw 0 0\n' "$swapfile" >> /etc/fstab 2>/dev/null; then
      note "добавил запись в /etc/fstab"
    else
      note "не смог записать /etc/fstab — swap активен только до перезагрузки"
    fi
  fi

  ok "swap 2 GB подключён (${swapfile})"
  return 0
}
ensure_swap

# --- [3/5] questions (only what we can't invent) --------------------------------
step 3 "Пара вопросов"
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
  # Your own Telegram id → you receive failure alerts (панель упала, бэкап, споры) in DM.
  ask "Ваш Telegram ID ${DIM}(для уведомлений; узнать — @userinfobot; Enter — пропустить)${R}: "
  read -r OWNER_ID || true

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
APP__OWNER_IDS=${OWNER_ID:-}
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

# --- [4/5] build + up ------------------------------------------------------------
step 4 "Сборка и запуск стека"
note "postgres · redis · web · bot · worker · scheduler · caddy"
# --env-file .env is REQUIRED: with `-f docker/compose.prod.yml` Compose resolves ${VAR:?}
# interpolation (e.g. DATABASE__PASSWORD) against the compose file's dir, not the CWD, so it
# wouldn't find our repo-root .env and the build would fail "required variable ... is missing".
COMPOSE="docker compose --env-file .env -f docker/compose.prod.yml"
run_spin "docker compose build (первый раз — несколько минут)" $COMPOSE build
run_spin "docker compose up -d" $COMPOSE up -d

# --- [5/5] health ---------------------------------------------------------------
step 5 "Миграции и здоровье"
printf "  %s…%s жду /health " "$DIM" "$R"
HEALTH_OK=""
for _ in $(seq 1 90); do
  if $COMPOSE exec -T web \
       python -c "import urllib.request as u; u.urlopen('http://localhost:8080/health', timeout=3)" \
       >/dev/null 2>&1; then
    HEALTH_OK=1; break
  fi
  printf "."
  sleep 2
done
printf "\n"
[ -n "$HEALTH_OK" ] || fail "web не поднялся за 3 минуты — смотри: $COMPOSE logs web"
ok "миграции применены, /health отвечает"

# --- summary ---------------------------------------------------------------------
ENV_DOMAIN=$(grep '^DOMAIN=' .env | cut -d= -f2)
IP=$(curl -fs4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
URL="http://$IP"
[ -n "$ENV_DOMAIN" ] && [ "$ENV_DOMAIN" != ":80" ] && URL="https://$ENV_DOMAIN"
ADMPW_OUT=$(grep '^ADMIN__PASSWORD=' .env | cut -d= -f2)
JWT_OUT=$(grep '^APP__JWT_SECRET=' .env | cut -d= -f2)
BACKUP_PW="${JWT_OUT:0:16}"   # the password DB backups are encrypted with (until you set one in the cabinet)

printf "\n"
hr
printf "   %s🎉 Готово!%s\n\n" "$GREEN$B" "$R"
printf "   %sКабинет%s      %s%s/admin/%s\n"  "$DIM" "$R" "$B" "$URL" "$R"
printf "   %sЛогин%s        admin\n"           "$DIM" "$R"
printf "   %sПароль%s       %s%s%s\n"          "$DIM" "$R" "$B" "$ADMPW_OUT" "$R"
printf "   %sМини-аппа%s    %s/app/\n"         "$DIM" "$R" "$URL"
printf "\n"
printf "   %s⚠ Пароль шифрования бэкапов%s  %s%s%s\n" "$ORANGE" "$R" "$B" "$BACKUP_PW" "$R"
printf "   %sСохраните его ОТДЕЛЬНО от сервера — без него бэкап БД не расшифровать.%s\n" "$DIM" "$R"
printf "   %s(в кабинете → Обслуживание можно задать свой пароль бэкапов)%s\n" "$DIM" "$R"
printf "\n"
printf "   %sДальше всё в кабинете: тарифы, платёжки, меню бота, мини-аппа.%s\n" "$DIM" "$R"
printf "   %sОбновление в одну команду: ./scripts/update.sh%s\n" "$DIM" "$R"
hr
