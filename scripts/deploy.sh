#!/usr/bin/env bash
# Deploy to a VPS provisioned per docs/deploy-vps.md (rsync working tree -> /opt/vpnshop/app, restart services).
# Usage: ./scripts/deploy.sh <user@host> [https://your-domain]
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <user@host> [https://your-domain]" >&2
  exit 1
fi

HOST="$1"
BASE_URL="${2:-}"
APP_DIR=/opt/vpnshop/app

echo "==> building admin SPA"
npm run build --prefix admin | tail -1

echo "==> rsync -> $HOST:$APP_DIR"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '.env' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.mypy_cache' \
  --exclude 'backups' --exclude 'uploads' --exclude 'scripts/mock_panel_state.json' \
  ./ "$HOST:$APP_DIR/"

SHA="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "==> sync deps + migrate + restart (build $SHA)"
# Stamp the deployed revision into .env so the version check reports the true current version;
# without it build_sha="" and the bot forever shows "update available / версия неизвестна".
ssh "$HOST" "cd $APP_DIR \
  && (grep -q '^APP__BUILD_SHA=' .env && sed -i 's/^APP__BUILD_SHA=.*/APP__BUILD_SHA=$SHA/' .env || echo 'APP__BUILD_SHA=$SHA' >> .env) \
  && ~/.local/bin/uv sync --frozen --no-dev >/dev/null \
  && .venv/bin/alembic upgrade head \
  && systemctl restart vpnshop-web vpnshop-worker vpnshop-scheduler vpnshop-bot vpnshop-mockpanel \
  && systemctl --no-pager --no-legend list-units 'vpnshop-*' | awk '{print \$1, \$3, \$4}'"

if [ -z "$BASE_URL" ]; then
  echo "==> done (no domain passed — skipping health check)"
  exit 0
fi

echo "==> health (uvicorn поднимается ~30с, ждём до 90с)"
deadline=$((SECONDS + 90))
while :; do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_URL/admin/" || true)
  [ "$code" = "200" ] && break
  if (( SECONDS >= deadline )); then
    echo "admin SPA: $code — не поднялось за 90с" >&2
    exit 1
  fi
  sleep 4
done
echo "admin SPA: $code"
curl -s -o /dev/null -w 'miniapp:   %{http_code}\n' "$BASE_URL/app/"
