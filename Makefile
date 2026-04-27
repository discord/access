ACTIVATE := $(if $(wildcard venv/bin/activate),. venv/bin/activate &&,)

.DEFAULT_GOAL := help

# Local sqlite DB used for `make run` / `make db-*` targets
LOCAL_DB_URI ?= sqlite:///instance/access.db

.PHONY: help
help:
	@echo "Access dev targets:"
	@echo "  make run                Run backend (port 6060) and frontend together"
	@echo "  make run-backend        Run uvicorn on port 6060 with --reload"
	@echo "  make run-frontend       Run Vite dev server on port 3000"
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
	@echo ""
	@echo "Docker:"
	@echo "  make build              docker build"
	@echo "  make run-docker         docker compose up --build"
	@echo "  make compose-down       docker compose down"
	@echo ""
	@echo "Tests / lint:"
	@echo "  make pytest             pytest"
	@echo "  make ruff               tox -e ruff"
	@echo "  make mypy               tox -e mypy"
	@echo "  make test               ruff + mypy + pytest"
	@echo ""
	@echo "Other:"
	@echo "  make dev                Install deps into venv (idempotent)"
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
		.mypy_cache/ \
		.pytest_cache \
		.ruff_cache \
		**/__pycache__ \
		dist/ \
		build/ \
		*.egg-info/

.PHONY: dev
dev:
	$(ACTIVATE) pip install -r requirements.txt -r requirements-test.txt
	$(ACTIVATE) pip install -e .

# ----------------------------------------------------------------------
# Run targets
# ----------------------------------------------------------------------

.PHONY: run
run: .env dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) \
	uvicorn --reload --host 0.0.0.0 --port 6060 api.asgi:app & \
	cd . && npm install && npm start

.PHONY: run-frontend
run-frontend:
	npm install && npm start

.PHONY: run-backend
run-backend: .env dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) \
	uvicorn --reload --host 0.0.0.0 --port 6060 api.asgi:app

# ----------------------------------------------------------------------
# Database / migrations
# ----------------------------------------------------------------------

.PHONY: db-migrate
db-migrate: dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) alembic upgrade head

.PHONY: db-downgrade
db-downgrade: dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) alembic downgrade -1

.PHONY: db-current
db-current: dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) alembic current

.PHONY: db-history
db-history: dev
	$(ACTIVATE) alembic history

.PHONY: db-revision
db-revision: dev
	@if [ -z "$(msg)" ]; then echo "usage: make db-revision msg=\"<message>\""; exit 1; fi
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) alembic revision --autogenerate -m "$(msg)"

.PHONY: db-init
db-init: db-migrate
	@if [ -z "$(email)" ]; then echo "usage: make db-init email=<admin-okta-email>"; exit 1; fi
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) access init "$(email)"

# ----------------------------------------------------------------------
# Sync / management commands
# ----------------------------------------------------------------------

.PHONY: sync
sync: dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) access sync

.PHONY: notify
notify: dev
	$(ACTIVATE) DATABASE_URI=$(LOCAL_DB_URI) access notify

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
	$(ACTIVATE) pytest

.PHONY: mypy
mypy: dev
	$(ACTIVATE) tox -e mypy

.PHONY: ruff
ruff: dev
	$(ACTIVATE) tox -e ruff

.PHONY: test
test: ruff mypy pytest
