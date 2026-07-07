# Post-Migration TODO

Follow-up work after the [Flask + Marshmallow → FastAPI + Pydantic v2 migration](https://github.com/discord/access/pull/425). Each item is intentionally **deferred** — the initial migration prioritized wire compatibility over idiomatic FastAPI, so several "make it nicer" changes were postponed to keep that diff focused and reviewable.

Items grouped roughly by surface area; ordering within each group is rough priority. Most items can be done independently of each other except where called out.

---

## Database / SQLAlchemy

### 4. Eager-loading hygiene

**Strict serialization landed.** `api/schemas/_serialize.py` exposes
`dump_orm`, which runs each adapter with `from_attributes=True` and lets
`InvalidRequestError` on unloaded relationships surface to the test
suite. The eager-load topology covering every field on
`OktaUserGroupMemberDetail`, `RoleGroupMapDetail`,
`OktaGroupTagMapDetail` is centralized in `api/routers/_eager.py` and
re-used across `apps.py`, `groups.py`, `tags.py`, `users.py`,
`role_requests.py`, and `audit.py`, so the loader stays in lockstep
with the schema. 281/281 tests green.

**Still to do under this item:**
- Per-route `DEFAULT_LOAD_OPTIONS` for redundant joins; some routes
  pre-load more than they emit.

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

### 11a. Okta SDK v3 upgrade

The async migration kept `okta==2.9.8` (aiohttp-based, async-native)
behind the `OktaService` facade. The SDK's v3 line (3.x, late 2025+) is
a ground-up openapi-generator rewrite: Pydantic models replace
`OktaObject`, API methods move to `okta.api.*` classes, and the
exception hierarchy changes. Still async, so nothing blocks on it — but
v2 is in maintenance mode. The facade contains the blast radius:
upgrading touches `api/services/okta_service.py` (retry/pagination/
custom group-owners endpoint plumbing), `tests/factories.py` (Okta SDK
model factories), and the okta service/retry tests. Verify rate-limit
and pagination behavior against a live org when upgrading.

### 11b. Shared pooled Okta client

`OktaService._okta_client()` creates a fresh SDK client (and aiohttp
session) per call so CLI invocations and per-test event loops stay
isolated. The FastAPI server loop could instead hold one client for
connection pooling — e.g. created in a lifespan hook and reused when
`asyncio.get_running_loop()` matches. Measure before bothering: Okta
API latency dominates connection setup for most operations.

(Note: the previous "Replace the in-process syncer.py loop with a
proper task runner" entry was removed — the syncer already runs as a
Kubernetes CronJob via `examples/kubernetes/cron-job-syncer.yaml`,
invoking `access sync` every 15 minutes. The Dockerfile's default
`gunicorn` CMD is overridable in the K8s manifest. README.md §
Kubernetes Deployment and CronJobs already documents this pattern.)

---

## Tooling

### 14. Strict type checking on routers + schemas

Tighten `ty` on `api/routers/`. It is currently listed in the relaxed
`[[tool.ty.overrides]]` block in [`pyproject.toml`](pyproject.toml); remove
it from the `include` list and resolve the resulting diagnostics.
(`api/schemas/` is already checked strictly — it's not in the override.)
Operations can stay loose initially since it's inherited from the Flask
era. Add a CI check that fails the build on new violations in those
directories.

---

## Test Ergonomics

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
hooks via `pluggy`. The application is now async, so every call site
bridges the sync hook — and the bridge differs by call site, which is
exactly the smell that motivates going natively async:

- `app_group_lifecycle` hooks (which receive `session=`) run through
  `AsyncSession.run_sync`, which hands them a working sync `Session` on
  the greenlet bridge (`create_group`, `delete_group`, `modify_group_*`).
- The syncer's `access_expiring_*` `notifications` hooks also run through
  `db.session.run_sync` — the syncer is a batch CLI job holding
  AsyncSession-bound ORM rows, so run_sync keeps those rows on the
  session's own thread instead of handing them to an `anyio` worker
  thread (which was the original cross-thread hazard). Every ORM
  relationship is `lazy="raise_on_sql"`, so a hook can only read what the
  syncer eager-loaded regardless — extend the `joinedload`s there if a
  notification plugin needs a wider graph.
- `notifications` / `conditional_access` hooks fired from request-path
  operations still run **inline on the event loop**, so a plugin doing
  slow network I/O blocks the request. Moving that slow tail off the hot
  path is item 10 (`BackgroundTasks`); `conditional_access` is harder
  because its return value gates the request.

Making the hooks natively async collapses all three bridges into one
`await`. Plugins should eventually follow the app.

Strategy mirrors the existing `DeprecationWarning` pattern in
`api/plugins/notifications.py:48-74`:

1. Duplicate every hook spec to add an `_async` variant —
   `access_request_created_async`, `notify_user_async`, etc.
2. Keep both for one major version. Sync hooks emit
   `DeprecationWarning` when called.
3. Remove the sync set at the next major bump.

Plugins authored in this window can pick either flavor; the application
prefers the async hook when both are registered.

Rules to document with the async hook specs:
- Hooks must not import `api.extensions.db` — they receive everything
  they need (the lifecycle hooks' `session` argument is a sync
  `Session` shim today and becomes an `AsyncSession` for the `_async`
  variants).
- **Plugin-contributed CLI commands** (the `access.commands` entry
  point) now run inside the CLI's `asyncio.run` boundary; commands
  written against the old sync `db.session` will break. They should
  declare `async def` bodies and await session calls — document this
  in the plugin guide alongside the async hook rollout.

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
