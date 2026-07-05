#!/usr/bin/env bash
# Container entrypoint: apply DB migrations once, then exec the given command.
# Only the `web` service should run migrations; workers wait for the DB to be ready.
set -euo pipefail

if [[ "${RUN_MIGRATIONS:-false}" == "true" ]]; then
  echo "[entrypoint] applying migrations..."
  alembic upgrade head
fi

echo "[entrypoint] starting: $*"
exec "$@"
