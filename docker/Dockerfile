# syntax=docker/dockerfile:1
# Multi-stage build: admin SPA (node) + python deps (uv) -> slim runtime.
FROM node:20-alpine AS spa
WORKDIR /spa
COPY admin/package.json admin/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY admin/ .
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
# Install deps from the committed lockfile so prod images are reproducible (CI resolves off
# uv.lock too). The old `uv pip install -r pyproject.toml` ignored the lock and could pull a
# breaking major on a future rebuild. Only lock + metadata are copied, so this stays a cached
# layer until deps change; `package = false` means the app itself is never installed (deps only),
# and `--no-dev` keeps the dev extra out. Creates the venv at /app/.venv.
COPY uv.lock pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS runtime
# Git short-SHA of the build (install.sh/update.sh pass it). The update checker compares
# APP__BUILD_SHA to GitHub's latest commit to know if a newer version shipped.
ARG GIT_SHA=""
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP__BUILD_SHA=$GIT_SHA
WORKDIR /app
# pg_dump for the scheduled encrypted backups (run_backup). It must match the server major
# (compose pins postgres:16) — an older pg_dump refuses to dump a newer server — and Debian
# bookworm only ships 15, so pull postgresql-client-16 from the PGDG apt repo.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] \
http://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -r app && useradd -r -g app app
COPY --from=builder /app/.venv /app/.venv
COPY . .
# The cabinet SPA is built in the image — a fresh git clone has no admin/dist.
COPY --from=spa /spa/dist /app/admin/dist
RUN chmod +x docker/entrypoint.sh && chown -R app:app /app
USER app
ENTRYPOINT ["docker/entrypoint.sh"]
# Default: the web seam (payment + panel webhooks, health). Override for worker/scheduler.
CMD ["uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
