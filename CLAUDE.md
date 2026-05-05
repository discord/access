# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Access is a centralized employee portal for managing Okta group and role memberships. It has a **React + TypeScript SPA** frontend and a **FastAPI** backend. The backend is in the middle of a migration from Flask + Marshmallow to FastAPI + Pydantic v2 (see `POST_MIGRATION_TODO.md` for deferred follow-up work).

## Commands

```bash
# Setup
make dev                     # install Python deps into venv (idempotent)
npm install                  # install frontend deps

# Run
make run-backend             # uvicorn on port 6060 with --reload
make run-frontend            # Vite dev server on port 3000
make run                     # both together

# Database
make db-migrate              # alembic upgrade head (uses local SQLite)
make db-init email=you@…     # migrate + seed from Okta
make db-revision msg="…"     # autogenerate a new Alembic migration

# Tests
make pytest                  # pytest with in-memory SQLite
make pytest-postgres         # pytest against a disposable postgres:16 container
pytest tests/test_group.py::test_name -s   # run a single test

# Lint / typecheck
make ruff                    # ruff check + format --check
make mypy                    # mypy (strict, with per-module relaxations)
make test                    # ruff + mypy + pytest
```

The Makefile is a thin wrapper over `tox`; test env vars (`DATABASE_URI`, `FLASK_ENV`) are set in `tox.ini`.

## Architecture

### Backend layers

```
api/routers/      FastAPI route handlers — query-building and response shaping
api/operations/   Business logic — one class per mutation (e.g. CreateGroup, ApproveAccessRequest)
api/models/       SQLAlchemy ORM models (OktaUser, OktaGroup, AppGroup, RoleGroup, …)
api/schemas/      Pydantic v2 request/response models (replaced Marshmallow)
api/services/     External service clients (okta_service.py — all Okta API calls)
api/plugins/      Pluggy hook specifications (notifications, conditional_access, app_group_lifecycle)
migrations/       Alembic migrations
```

`api/app.py` is the `create_app()` factory; `api/asgi.py` is the ASGI entrypoint (`uvicorn api.asgi:app`).

### The `db` compatibility shim (`api/extensions.py`)

The codebase uses a `db` shim that preserves the Flask-SQLAlchemy surface on top of plain SQLAlchemy 2.0. This is intentional and **temporary** (deferred in `POST_MIGRATION_TODO.md` #1). Its key behaviors:

- `db.Model` → `DeclarativeBase`; models inherit from it and get `__tablename__` auto-generated
- `db.session` → request-scoped `Session` via a `ContextVar` (`_session_scope`)
- `Model.query` → a `_QueryProperty` descriptor returning a `Query` bound to the active session
- `Query.first_or_404()` / `.get_or_404()` raise `HTTPException(404)` (not Flask's abort)

Session scope is set per-request by `RequestIdMiddleware` and released on response teardown. The `get_db` dependency in `api/database.py` handles commit/rollback. Use the `DbSession` type alias in routers:

```python
from api.database import DbSession

def my_endpoint(db: DbSession) -> ...:
    ...
```

### Eager loading

All model relationships are declared `lazy="raise_on_sql"`. Every router that serializes relationships must declare a `DEFAULT_LOAD_OPTIONS` tuple of `selectinload`/`joinedload` calls and pass it to the query. Missing eager loads will raise `InvalidRequestError` at runtime.

### Pydantic schemas

Schemas in `api/schemas/core_schemas.py` use `ConfigDict(from_attributes=True)` to serialize ORM objects directly. Datetime fields use `RFC822Datetime` (Marshmallow-compatible format) rather than Pydantic's default ISO 8601 — this is a known divergence tracked in `POST_MIGRATION_TODO.md` #5.

### Plugin system

Plugins use [pluggy](https://pluggy.readthedocs.io/). Hook specs live in `api/plugins/`:
- `notifications.py` — events like `access_request_created`, `access_request_completed`
- `conditional_access.py` — auto-approve/deny logic returning `ConditionalAccessResponse`
- `app_group_lifecycle.py` — lifecycle hooks for AppGroups

Plugins are installed as Python packages into the container (see `examples/plugins/`).

### Frontend

The React SPA lives in `src/`. The backend auto-generates an OpenAPI spec at `/api/openapi.json` (when `DEBUG=True`). The TypeScript API client is generated via `npx openapi-codegen gen api` using `openapi-codegen.config.ts`.

## Testing

- Tests default to **in-memory SQLite**. Use `make pytest-postgres` or `TEST_DATABASE_URI=postgresql+pg8000://...` for Postgres-specific behavior.
- The `db` fixture (in `tests/conftest.py`) creates tables, seeds the bootstrap Access app and admin user, and tears down after each test.
- `mock_user` is a factory fixture that overrides `get_current_user_id` via `app.dependency_overrides`.
- Factories are in `tests/factories.py` using `factory_boy`.

## mypy

`mypy` runs with `strict = True` globally, but several modules have relaxed settings (see `.mypy.ini`). `api/routers/` and `api/schemas/` currently have many strict checks disabled — tightening these is tracked in `POST_MIGRATION_TODO.md` #14.

## Key deferred work

`POST_MIGRATION_TODO.md` tracks follow-up items from the Flask→FastAPI migration. The most load-bearing ones to be aware of:
- **#1** Remove the `db` shim (`api/extensions.py`) — don't add new usages of `db.Column`, `Model.query`, etc.
- **#2** Switch to async SQLAlchemy — routers are currently sync `def`, not `async def`
- **#5** Normalize datetimes to ISO 8601 (currently RFC822)
