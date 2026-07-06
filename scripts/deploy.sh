#!/usr/bin/env bash
# Deploy to the test server (rsync working tree -> /opt/vpnshop/app, restart services).
# Usage: ./scripts/deploy.sh [host]   (default root@94.183.238.41)
set -euo pipefail

HOST="${1:-root@94.183.238.41}"
APP_DIR=/opt/vpnshop/app

echo "==> building admin SPA"
npm run build --prefix admin | tail -1

echo "==> rsync -> $HOST:$APP_DIR"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' --exclude '.env' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.mypy_cache' \
  --exclude 'backups' --exclude 'scripts/mock_panel_state.json' \
  ./ "$HOST:$APP_DIR/"

echo "==> sync deps + migrate + restart"
ssh "$HOST" "cd $APP_DIR \
  && ~/.local/bin/uv sync --frozen --no-dev >/dev/null \
  && .venv/bin/alembic upgrade head \
  && systemctl restart vpnshop-web vpnshop-worker vpnshop-scheduler vpnshop-bot vpnshop-mockpanel \
  && sleep 4 \
  && systemctl --no-pager --no-legend list-units 'vpnshop-*' | awk '{print \$1, \$3, \$4}'"

echo "==> health"
curl -s -o /dev/null -w 'admin SPA: %{http_code}\n' https://testbot.tvss-911.com/admin/
curl -s -o /dev/null -w 'miniapp:   %{http_code}\n' https://testbot.tvss-911.com/app/
