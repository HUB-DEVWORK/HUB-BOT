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
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
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
