.DEFAULT_GOAL := help

# Local sqlite DB used for `make run` / `make db-*` targets
LOCAL_DB_URI ?= sqlite:///instance/access.db

# Ports — overridable via the PORT env var so Claude Code Desktop Preview's
# autoPort can pick a free port per worktree. See .claude/launch.json.
BACKEND_PORT  ?= $(if $(PORT),$(PORT),6060)
FRONTEND_PORT ?= $(if $(PORT),$(PORT),3000)

.PHONY: help
help:
	@echo "Access dev targets:"
	@echo "  make run                Run backend (port $(BACKEND_PORT)) and frontend (port $(FRONTEND_PORT)) together"
	@echo "  make run-backend        Run uvicorn on port $(BACKEND_PORT) with --reload"
	@echo "  make run-frontend       Run Vite dev server on port $(FRONTEND_PORT)"
	@echo ""
	@echo "Database:"
	@echo "  make db-migrate         alembic upgrade head"
	@echo "  make db-downgrade       alembic downgrade -1"
	@echo "  make db-current         alembic current"
	@echo "  make db-history         alembic history"
	@echo "  make db-revision msg=…  alembic revision --autogenerate"
	@echo "  make db-init email=…    alembic upgrade + access init <email>"
	@echo ""
	@echo "Management commands:"
	@echo "  make sync               access sync"
	@echo "  make notify             access notify"
	@echo "  make sync-app-groups    access sync-app-groups (loads .env)"
	@echo ""
	@echo "Docker:"
	@echo "  make build              docker build"
	@echo "  make run-docker         docker compose up --build"
	@echo "  make compose-down       docker compose down"
	@echo ""
	@echo "Tests / lint:"
	@echo "  make pytest             pytest (sqlite in-memory)"
	@echo "  make pytest-postgres    pytest against a disposable postgres:16 container"
	@echo "  make ruff               ruff check + ruff format --check"
	@echo "  make ty                 ty check"
	@echo "  make test               ruff + ty + pytest"
	@echo ""
	@echo "Other:"
	@echo "  make dev                Sync deps into .venv via uv (idempotent)"
	@echo "  make clean              Remove caches and build artifacts"

.env:
	@if [ ! -f .env ]; then \
	  echo "Create a .env file based on the README before running. Example:"; \
	  echo "  CURRENT_OKTA_USER_EMAIL=you@example.com"; \
	  echo "  OKTA_DOMAIN=mydomain.oktapreview.com"; \
	  echo "  OKTA_API_TOKEN=…"; \
	  echo "  DATABASE_URI=$(LOCAL_DB_URI)"; \
	  exit 1; \
	fi

.PHONY: clean
clean:
	rm -rf \
		.pytest_cache \
		.ruff_cache \
		**/__pycache__ \
		dist/ \
		build/ \
		*.egg-info/

.PHONY: dev
dev:
	uv sync

# ----------------------------------------------------------------------
# Run targets
# ----------------------------------------------------------------------

.PHONY: run
run: .env dev db-migrate
	@mkdir -p .claude
	@printf '%s\n' "$(BACKEND_PORT)" > .claude/.api-port
	DATABASE_URI=$(LOCAL_DB_URI) \
	uv run uvicorn --env-file .env --reload --host 0.0.0.0 --port $(BACKEND_PORT) api.asgi:app & \
	npm install && npx vite --host 0.0.0.0 --port $(FRONTEND_PORT)

.PHONY: run-frontend
run-frontend:
	npm install && npx vite --host 0.0.0.0 --port $(FRONTEND_PORT)

.PHONY: run-backend
run-backend: .env dev db-migrate
	@mkdir -p .claude
	@printf '%s\n' "$(BACKEND_PORT)" > .claude/.api-port
	DATABASE_URI=$(LOCAL_DB_URI) \
	uv run uvicorn --env-file .env --reload --host 0.0.0.0 --port $(BACKEND_PORT) api.asgi:app

# ----------------------------------------------------------------------
# Database / migrations
# ----------------------------------------------------------------------

.PHONY: db-migrate
db-migrate: dev
	DATABASE_URI=$(LOCAL_DB_URI) uv run alembic upgrade head

.PHONY: db-downgrade
db-downgrade: dev
	DATABASE_URI=$(LOCAL_DB_URI) uv run alembic downgrade -1

.PHONY: db-current
db-current: dev
	DATABASE_URI=$(LOCAL_DB_URI) uv run alembic current

.PHONY: db-history
db-history: dev
	uv run alembic history

.PHONY: db-revision
db-revision: dev
	@if [ -z "$(msg)" ]; then echo "usage: make db-revision msg=\"<message>\""; exit 1; fi
	DATABASE_URI=$(LOCAL_DB_URI) uv run alembic revision --autogenerate -m "$(msg)"

.PHONY: db-init
db-init: db-migrate
	@if [ -z "$(email)" ]; then echo "usage: make db-init email=<admin-okta-email>"; exit 1; fi
	DATABASE_URI=$(LOCAL_DB_URI) uv run access init "$(email)"

# ----------------------------------------------------------------------
# Sync / management commands
# ----------------------------------------------------------------------

.PHONY: sync
sync: dev
	DATABASE_URI=$(LOCAL_DB_URI) uv run access sync

.PHONY: notify
notify: dev
	DATABASE_URI=$(LOCAL_DB_URI) uv run access notify

# Unlike `sync`/`notify`, this loads the full .env so the app-group lifecycle
# plugins get the credentials they need (e.g. Google/Okta). `access` has no
# --env-file flag, so we source .env into the environment ourselves: `set -a`
# auto-exports every var defined while sourcing. We then force
# DATABASE_URI=$(LOCAL_DB_URI) -- exactly like `sync`/`notify` -- so this points
# at the same migrated instance/access.db the dev server uses, rather than
# whatever DATABASE_URI .env happens to set.
.PHONY: sync-app-groups
sync-app-groups: .env dev
	set -a && . ./.env && set +a && \
	DATABASE_URI=$(LOCAL_DB_URI) uv run access sync-app-groups

# ----------------------------------------------------------------------
# Docker
# ----------------------------------------------------------------------

.PHONY: build
build:
	docker build -t access .

.PHONY: run-docker
run-docker:
	docker compose up --build --remove-orphans

.PHONY: compose-down
compose-down:
	docker compose down || :

# ----------------------------------------------------------------------
# Tests / linting
# ----------------------------------------------------------------------

.PHONY: pytest
pytest: dev
	uv run pytest

# ----------------------------------------------------------------------
# Postgres integration test target
# ----------------------------------------------------------------------
# Boots a disposable postgres:16 container on port 5433, runs pytest with
# TEST_DATABASE_URI pointed at it, then stops the container. The default
# `make pytest` keeps using the in-memory sqlite the test fixtures fall
# back to.
PG_TEST_CONTAINER ?= access-test-pg
PG_TEST_PORT ?= 5433
PG_TEST_URI := postgresql+asyncpg://postgres:postgres@localhost:$(PG_TEST_PORT)/access_test

.PHONY: pytest-postgres
pytest-postgres: dev pytest-postgres-up
	TEST_DATABASE_URI='$(PG_TEST_URI)' uv run pytest tests/; \
	  status=$$?; \
	  $(MAKE) pytest-postgres-down; \
	  exit $$status

.PHONY: pytest-postgres-up
pytest-postgres-up:
	@if [ -z "$$(docker ps -q -f name=^$(PG_TEST_CONTAINER)$$)" ]; then \
	  docker run -d --rm --name $(PG_TEST_CONTAINER) \
	    -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=access_test \
	    -p $(PG_TEST_PORT):5432 postgres:16 >/dev/null; \
	  echo "Waiting for postgres on :$(PG_TEST_PORT)..."; \
	  until docker exec $(PG_TEST_CONTAINER) pg_isready -U postgres >/dev/null 2>&1; do sleep 0.5; done; \
	  echo "postgres ready."; \
	fi

.PHONY: pytest-postgres-down
pytest-postgres-down:
	@docker rm -f $(PG_TEST_CONTAINER) >/dev/null 2>&1 || :

.PHONY: ty
ty: dev
	uv run ty check .

.PHONY: ruff
ruff: dev
	uv run ruff check .
	uv run ruff format --check .

.PHONY: test
test: ruff ty pytest
