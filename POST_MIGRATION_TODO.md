# Post-Migration TODO

Follow-up work after the [Flask + Marshmallow → FastAPI + Pydantic v2 migration](https://github.com/discord/access/pull/REPLACE-ME). Each item is intentionally **deferred** — the initial migration prioritized wire compatibility over idiomatic FastAPI, so several "make it nicer" changes were postponed to keep that diff focused and reviewable.

Items grouped roughly by surface area; ordering within each group is rough priority. Most items can be done independently of each other except where called out.

---

## Database / SQLAlchemy

### 1. Remove the `db` compatibility shim

The shim in `api/extensions.py` keeps the legacy `db.Model` / `db.session` /
`Model.query` API alive on top of plain SQLAlchemy 2.0. It was added so the
migration could touch models and operations as little as possible.

**What's involved:**
- `db.Model` → `Base` (a `DeclarativeBase`)
- `db.Column` → `mapped_column`
- `db.relationship` → `relationship`
- `db.func` / `db.or_` / `db.not_` / `db.and_` → direct `sqlalchemy` imports
- `Model.query.filter(...)` → `db.session.query(Model).filter(...)` or
  `session.execute(select(Model).filter(...))`

**Net change:** removes ~150 lines of shim, makes the model layer indistinguishable
from any other SQLAlchemy 2.0 project. Mostly mechanical.

### 2. Switch to async SQLAlchemy

The migration kept SQLAlchemy synchronous. To go async:
- Engine: `create_engine` → `create_async_engine`
- Sessionmaker: `sessionmaker` → `async_sessionmaker`
- `get_db`: `Generator[Session, …]` → `AsyncGenerator[AsyncSession, …]`
- Operations: every `db.session.query(...).first()` → `await session.execute(select(...))`
- Routers: `def` → `async def`

Major lift — every operation file changes — but unlocks concurrent I/O and
removes the sync-thread-pool overhead per request. Should be done **after**
#1 (removing the shim) since the shim's `Query` class assumes sync.

### 3. Replace the `_session_scope` ContextVar dance with `async_scoped_session`

Once #2 lands, SQLAlchemy's built-in `async_scoped_session` replaces the
manual ContextVar plumbing in `api/extensions.py`.

### 4. Eager-loading hygiene

Audit:
- `lazy="raise_on_sql"` usages on models — confirm each is still load-bearing
- Per-route `DEFAULT_LOAD_OPTIONS` for redundant joins (some routes load
  more than they use)
- `safe_dump` swallowing `InvalidRequestError` — consider making it strict
  and instead fixing the missing eager loads

The migration preserved the existing eager-loading topology as-is.

---

## Schemas / Wire Shape

These are **breaking** for clients. Coordinate frontend changes.

### 5. Normalize datetime format to ISO 8601

Marshmallow defaulted to RFC822 (`"Sun, 26 Apr 2026 13:45:00 -0000"`); we
preserved that with `RFC822Datetime`. Pydantic's default is ISO 8601 which
is more universally parseable. Frontend needs to update its parsers.

### 6. Stop emitting absent-as-`null`

Pydantic emits `None` fields as `"key": null` by default. Switch responses
to `model_dump(exclude_none=True)` and update the frontend to handle missing
keys as None.

### 7. Adopt RFC 9457 problem-detail error format

Today: `{"message": "..."}` (matches the legacy Flask envelope).
Future: `{"type": "...", "title": "...", "status": 400, "detail": "..."}`
(FastAPI / RFC 9457 standard). Update `api/exception_handlers.py` and the
frontend.

### 8. Replace `paginate()` with `fastapi-pagination`

Drop the hand-rolled `{total, pages, next, prev, results}` envelope in
`api/pagination.py`; use [`fastapi-pagination`](https://uriyyo-fastapi-pagination.netlify.app/)'s
`Page[T]` + `Params`. Gets cursor-based pagination, OpenAPI integration,
and consistent shape across endpoints for free. Wire shape changes; coordinate
with frontend.

---

## Auth

### 9. Cache JWKS lookups smarter

Today: `cachetools.TTLCache(maxsize=1, ttl=3600)` per process. Production
scale may want:
- Redis-backed cache shared across replicas, or
- A sidecar that pre-fetches and refreshes
- Move CF / OIDC verification entirely into middleware so dependencies don't
  re-verify on each call

---

## Background Work / Long-Running Operations

### 10. FastAPI `BackgroundTasks` for multi-Okta-call operations

`CreateApp`, `ApproveAccessRequest`, `ModifyGroupUsers`, and
`ModifyRoleGroups` each make several Okta API calls per HTTP request, often
serially. Move the slow tail (Okta sync, plugin lifecycle hooks, downstream
audit log emission) into `BackgroundTasks` so the HTTP response returns as
soon as the local DB state commits.

Care needed:
- Errors after the response are silent — consider a retry queue with a
  dead-letter table
- Audit log ordering may shift slightly
- Tests that assert call-count of mocked Okta methods may need to await
  background tasks before asserting

### 11. Replace the in-process `syncer.py` loop with a proper task runner

`api/syncer.py` runs as a long-lived process or one-shot CLI invocation
today. Migrate to:
- APScheduler (in-process, single replica)
- Celery + Redis broker (multi-replica)
- Kubernetes `CronJob` (declarative, no in-process scheduler) — preferred

### 12. Async HTTP for Okta calls

Replace synchronous `requests` calls in `api/services/okta_service.py` with
`httpx.AsyncClient` and connection pooling. Pairs naturally with #2 (async
SQLAlchemy). Today, the OktaService uses an asyncio loop internally for
parallel calls but the entry points are sync.

---

## Tooling

### 13. OpenAPI client codegen

FastAPI auto-publishes `/api/openapi.json` (when `DEBUG=true`). Run
`npx openapi-codegen gen api` against the new spec and replace any
hand-written API client code in the frontend with the generated types
and React-Query hooks. The codegen config in `openapi-codegen.config.ts`
is already pointed at the new endpoint.

### 14. Strict type checking on routers + schemas

Enforce `pyright` / `mypy` strict mode on `api/routers/` and `api/schemas/`.
Operations and models can stay loose initially since they're inherited
from the Flask era. Add a CI check that fails the build on new strict-mode
violations in those directories.

### 15. Drop `werkzeug` (and other Flask-transitive deps)

Now that Flask is gone, `werkzeug` is no longer required transitively.
Audit `pip freeze` output for stragglers and remove direct imports if any
slipped through.

---

## Test Ergonomics

### 16. Async test client

Once routers are async (#2), switch from `fastapi.testclient.TestClient`
to `httpx.AsyncClient(transport=ASGITransport(app=app))` with
`pytest-asyncio`. Lets tests exercise true async paths (e.g. concurrent
DB calls in a single request) and avoids the sync-bridge in the current
TestClient.

### 17. Replace `factory_boy` with Pydantic-based builders

Either:
- Keep `factory_boy` but decouple it from the legacy SQLAlchemy session
  pattern, or
- Replace with [`polyfactory`](https://polyfactory.litestar.dev/) which
  generates fixtures from Pydantic models — keeps test data and request
  schemas in sync automatically.

### 18. Golden-file response snapshots

Add snapshot tests for the major endpoints (`GET /api/groups`,
`GET /api/groups/{id}`, `GET /api/users/{id}`, `GET /api/requests`,
`GET /api/audit/users`). Lets future changes to schemas surface as
diffable test failures rather than runtime regressions for clients.

Recommend `syrupy` for the snapshot framework — JSON-aware diffs and
clean `--snapshot-update` ergonomics.
