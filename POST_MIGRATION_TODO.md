# Post-Migration TODO

Follow-up work after the [Flask + Marshmallow → FastAPI + Pydantic v2 migration](https://github.com/discord/access/pull/425). Each item is intentionally **deferred** — the initial migration prioritized wire compatibility over idiomatic FastAPI, so several "make it nicer" changes were postponed to keep that diff focused and reviewable.

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

### 4. Eager-loading hygiene + remove `safe_dump`

Replace `safe_dump`/`_SafeAttrProxy` (`api/schemas/_serialize.py`) with
strict `adapter.validate_python(obj, from_attributes=True)` calls so
unloaded relationships fail loud at the responsible route instead of
silently rendering as `null`.

The Section E commit on the migration PR tried the deletion and rolled
back when 51 tests across 9 files broke; every failure was an
`InvalidRequestError` on a relationship the route declined to
eager-load. The follow-up has to walk each of these routes, list the
schema fields it returns, and add the matching `selectinload` /
`joinedload` to the query. Routes / test files that need attention:

- `tests/test_user.py` — `GET /api/users` and `GET /api/users/{id}` —
  user-detail / membership / ownership graph
- `tests/test_tag.py` — `GET /api/tags` and `GET /api/tags/{id}` —
  `Tag.active_group_tags` chain
- `tests/test_app.py` — `GET /api/apps` list path
- `tests/test_app_group_lifecycle_plugin.py` — `GET /api/apps/{id}` /
  plugin metadata routes
- `tests/test_audit.py` — `GET /api/audit/users` /
  `GET /api/audit/groups` (the manual `_serialize_user_group_member` /
  `_serialize_role_group_map` helpers wrap `m`/`rgm` in
  `_SafeAttrProxy` directly; expand the audit query's
  `selectinload` / `joinedload` graph instead)
- `tests/test_group.py` / `tests/test_role.py` /
  `tests/test_role_request.py` — group + role + role-request routes
- `tests/test_time_limit_constraint.py` — group time-limit propagation

Also worth doing alongside:
- Audit `lazy="raise_on_sql"` usages on models — confirm each is still
  load-bearing now that `safe_dump` is the strict default.
- Per-route `DEFAULT_LOAD_OPTIONS` for redundant joins; some routes
  pre-load more than they emit.

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

### 11. Async HTTP for Okta calls

Replace synchronous `requests` calls in `api/services/okta_service.py` with
`httpx.AsyncClient` and connection pooling. Pairs naturally with #2 (async
SQLAlchemy).

**Important sequencing note.** The current `OktaService` sync wrappers
each call `asyncio.run(...)` internally on an inner async helper. That
works *today* because every router is `def` (sync) and runs on an
`anyio.to_thread.run_sync` worker thread that has no event loop. The
moment any router becomes `async def`, the same call breaks with
`asyncio.run() cannot be called from a running event loop`. So this
work is gated on #2 (async SQLAlchemy / async routers) and must land
together — converting `OktaService` to async without converting the
routers buys nothing and is risky.

(Note: the previous "Replace the in-process syncer.py loop with a
proper task runner" entry was removed — the syncer already runs as a
Kubernetes CronJob via `examples/kubernetes/cron-job-syncer.yaml`,
invoking `access sync` every 15 minutes. The Dockerfile's default
`gunicorn` CMD is overridable in the K8s manifest. README.md §
Kubernetes Deployment and CronJobs already documents this pattern.)

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

---

## Test Ergonomics

### 15. Async test client

Once routers are async (#2), switch from `fastapi.testclient.TestClient`
to `httpx.AsyncClient(transport=ASGITransport(app=app))` with
`pytest-asyncio`. Lets tests exercise true async paths (e.g. concurrent
DB calls in a single request) and avoids the sync-bridge in the current
TestClient.

### 16. Replace `factory_boy` with Pydantic-based builders

Either:
- Keep `factory_boy` but decouple it from the legacy SQLAlchemy session
  pattern, or
- Replace with [`polyfactory`](https://polyfactory.litestar.dev/) which
  generates fixtures from Pydantic models — keeps test data and request
  schemas in sync automatically.

### 17. Golden-file response snapshots

Add snapshot tests for the major endpoints (`GET /api/groups`,
`GET /api/groups/{id}`, `GET /api/users/{id}`, `GET /api/requests`,
`GET /api/audit/users`). Lets future changes to schemas surface as
diffable test failures rather than runtime regressions for clients.

Recommend `syrupy` for the snapshot framework — JSON-aware diffs and
clean `--snapshot-update` ergonomics.

---

## Plugin Interface Modernization

### 18. Make plugin interfaces async; deprecate the sync hooks

The four plugin types (`notifications`, `conditional_access`,
`app_group_lifecycle`, `metrics_reporter`) currently expose synchronous
hooks via `pluggy`. Once the application goes async (#2), plugins
should follow.

Strategy mirrors the existing `DeprecationWarning` pattern in
`api/plugins/notifications.py:48-74`:

1. Duplicate every hook spec to add an `_async` variant —
   `access_request_created_async`, `notify_user_async`, etc.
2. Keep both for one major version. Sync hooks emit
   `DeprecationWarning` when called.
3. Remove the sync set at the next major bump.

Plugins authored in this window can pick either flavor; the application
prefers the async hook when both are registered.

### 19. Pre-2.0 release checklist: drop deprecated plugin parameters

Several `access_expiring_*` hooks already carry `DeprecationWarning`
for legacy parameters: `groups`, `roles`, `users` on
`access_expiring_owner`; `groups` on `access_expiring_user`. Their
deprecation window ends at 2.0 — the parameters and their
backward-compat shims must be removed before tagging the release.

`api/plugins/notifications.py:48-74` is the canonical list.

---

## Security follow-ups (out of scope for the migration PR)

### 20. Nonce-based CSP

Drop `'unsafe-inline'` from `script-src` and `style-src` in
`api/middleware.py`. Generate a per-response nonce in
`SecurityHeadersMiddleware` and thread it through `build/index.html`
+ the React build pipeline so every inline `<script>` / `<style>`
carries the nonce. Touches the frontend; not a same-PR fix.

### 21. Trust proxy `X-Forwarded-*` only from an allowlist

`api/middleware.py:_client_ip` reads `X-Forwarded-For` / `X-Real-IP`
from any caller, so an attacker that can reach the FastAPI service
directly can forge `RequestContext.ip` (audit-log only — no
auth/rate-limit decision uses it). Configure
`uvicorn --forwarded-allow-ips=<LB CIDR>` in the production
deployment (or use Starlette's `ProxyHeadersMiddleware` with an
explicit `TRUSTED_PROXIES`). Document the setting in the K8s example
manifests.
