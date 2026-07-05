# VPN-shop base — dev tasks. Requires `uv` (https://docs.astral.sh/uv/).
.DEFAULT_GOAL := help
COMPOSE := docker compose -f docker/compose.local.yml

.PHONY: help install fmt lint typecheck test check up down logs migrate revision smoke

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install deps into a local venv
	uv sync --extra dev

fmt: ## Auto-format & fix
	uv run ruff format src tests scripts
	uv run ruff check --fix src tests scripts

lint: ## Lint (no changes)
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

typecheck: ## Static type check
	uv run mypy

test: ## Run tests
	uv run pytest

check: lint typecheck test ## Run the full gate (lint + types + tests)

up: ## Start the local stack (postgres, redis, app, worker)
	$(COMPOSE) up --build

down: ## Stop the stack and remove volumes
	$(COMPOSE) down -v

logs: ## Tail stack logs
	$(COMPOSE) logs -f

migrate: ## Apply DB migrations
	uv run alembic upgrade head

revision: ## Autogenerate a migration:  make revision m="add x"
	uv run alembic revision --autogenerate -m "$(m)"

smoke: ## E2E smoke-test against the configured Remnawave panel
	uv run python scripts/smoke.py
