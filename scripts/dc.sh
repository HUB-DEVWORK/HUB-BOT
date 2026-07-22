#!/usr/bin/env bash
# Convenience wrapper for `docker compose` against the production stack.
#
#   ./scripts/dc.sh logs -f web
#   ./scripts/dc.sh ps
#   ./scripts/dc.sh restart bot
#
# Why this exists: a bare `docker compose -f docker/compose.prod.yml <cmd>` looks for the
# .env used for ${VAR} interpolation in the compose FILE's directory (docker/), not the
# repo root — so it fails with «required variable DATABASE__PASSWORD is missing a value».
# This wrapper always runs from the repo root with `--env-file .env`, exactly like
# install.sh/update.sh do, so every compose command Just Works from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env не найден — сначала установка: ./scripts/install.sh" >&2; exit 1; }
exec docker compose --env-file .env -f docker/compose.prod.yml "$@"
