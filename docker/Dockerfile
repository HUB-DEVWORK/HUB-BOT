# syntax=docker/dockerfile:1
# Multi-stage build using uv.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
# Install deps first (cached layer) — only project metadata needed.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv && uv pip install --python /app/.venv -r pyproject.toml

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd -r app && useradd -r -g app app
COPY --from=builder /app/.venv /app/.venv
COPY . .
RUN chmod +x docker/entrypoint.sh && chown -R app:app /app
USER app
ENTRYPOINT ["docker/entrypoint.sh"]
# Default: the web seam (payment + panel webhooks, health). Override for worker/scheduler.
CMD ["uvicorn", "src.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
