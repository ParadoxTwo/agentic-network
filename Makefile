# Developer entrypoints. `make dev` is the one-command spin-up.
#
# Loads .env (if present) so the app commands see the same POSTGRES_* values
# that docker-compose uses — no drift between server and client.
-include .env
export

.DEFAULT_GOAL := help
SHELL := bash

# Use Docker if a daemon is reachable; otherwise fall back to native servers
# (scripts/dev-services.sh) for daemonless environments like the web sandbox.
DOCKER_OK := $(shell docker info >/dev/null 2>&1 && echo yes || echo no)

.PHONY: help dev install env up wait schema status down test lint typecheck fmt check clean

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(firstword $(MAKEFILE_LIST)) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

dev: install env up wait schema status ## One-command spin-up (deps, services, schema)
	@echo ""
	@echo "dev environment ready — try: make check"

install: ## Install Python dependencies
	uv sync

env: ## Create .env from the example if missing
	@test -f .env || { cp .env.example .env; echo "created .env"; }

up: ## Start Postgres + Redis (Docker if available, else native)
ifeq ($(DOCKER_OK),yes)
	docker compose up -d
else
	@echo "no Docker daemon — using native dev-services"
	@scripts/dev-services.sh up
endif

wait: ## Block until the store is reachable
	@uv run python scripts/wait_for_store.py

schema: ## Apply the store schema to the dev database (idempotent)
	@uv run python -c "from store import connect_pg, apply_schema; \
c = connect_pg(); apply_schema(c); print('schema applied')"

status: ## Show service readiness
ifeq ($(DOCKER_OK),yes)
	docker compose ps
else
	@scripts/dev-services.sh status
endif

down: ## Stop services (keep data)
ifeq ($(DOCKER_OK),yes)
	docker compose down
else
	@scripts/dev-services.sh down
endif

serve: ## Run the orchestrator + worker service (needs GITHUB_TOKEN, ANTHROPIC_API_KEY)
	uv run python -m app.service

api: ## Run the trigger/webhook API on :8000
	uv run uvicorn app.api:app --host 0.0.0.0 --port 8000

test: ## Run the test suite
	uv run pytest -q

lint: ## Lint
	uv run ruff check .

typecheck: ## Typecheck (strict)
	uv run mypy store schemas

fmt: ## Format
	uv run ruff format .

check: lint typecheck test ## Lint + typecheck + test

clean: ## Stop services and delete local data
ifeq ($(DOCKER_OK),yes)
	-docker compose down -v
else
	-scripts/dev-services.sh nuke
endif
