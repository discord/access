You are helping implement features and bug fixes in the **Access** codebase — a company-internal
Okta access control portal. This document covers how the code is structured, key patterns,
gotchas, and security requirements for development.

If you notice that anything in this file conflicts with or is missing from the current codebase,
flag it to the user and suggest updating the relevant section.

> **Maintenance watermark:** this document was last reconciled against the codebase at `main`
> commit **`fb260a2`** (latest merged PR #495; includes the end-to-end async-SQLAlchemy
> migration #480/#481 and the `uv` + `ty` toolchain from #507). When refreshing it, review
> commits merged after this point and update the anchor.

## Companion docs — load on demand

This file holds the always-relevant rules. Deeper, area-specific material lives in sibling docs
under `.claude/` that are **not** auto-loaded — read the matching one when your task touches
that area:

- **Frontend / UI work** → `.claude/access-dev-frontend.md` — generated `src/api/` client,
  React Query v5, layout & design principles.
- **Running tests or writing migrations** → `.claude/access-dev-testing.md` — the `uv`/`ruff`/`ty`
  toolchain, Alembic migrations and CI, factories, the Okta sandbox.
- **Syncer or notification-plugin work** → `.claude/access-dev-sync.md` — the sync authority
  model and notification cadence.

## Stack

**Backend:** FastAPI + Pydantic v2 + **async** SQLAlchemy 2.0 (asyncio) + Alembic
**Frontend:** React 18 + TypeScript, Vite, MUI, React Query
**Auth:** Authlib (OIDC or Cloudflare Access JWT — operator's choice)
**Plugins:** pluggy
**Tooling:** `uv` (deps + venv, pinned to Python 3.13), `ruff` (lint/format), `ty` (type check)
**Tests:** pytest + Factory Boy (backend), vitest + React Testing Library (frontend)

## Open source constraints — read before making changes

The Access repo is open source under the Apache 2.0 License. Any change merged to `main` becomes
public. This means:

- **No operator-specific logic in the Access repo.** Don't hardcode one organization's Okta
  attributes, email formats, internal URLs, private-repo references, or anything tied to how a
  single operator runs Access. A change that only works for one operator's infrastructure does
  not belong upstream.
- **Changes should serve all operators.** Features and fixes should be general-purpose.
  Operator-specific behavior belongs in a plugin or in that operator's own private repo, not
  upstream.
- **No references to any operator's private code.** The Access repo must be self-contained.
- **Maintain Apache 2.0 License compliance.** Any dependency or code introduced must be compatible.
- **Prefer backwards compatibility for incremental changes.** Many organizations run Access.
  Breaking changes to the API, data model, or plugin interface affect them too — prefer additive
  changes and deprecation over breaking ones. That said, some changes (major framework migrations,
  significant redesigns) intentionally break backwards compatibility and that's acceptable. Use
  judgment: routine bug fixes and features should be additive; larger architectural changes can
  set a higher bar.

Operator-specific code (plugin implementations, container definitions, Kubernetes configs, and
deployment config) lives in the operator's own private repo, not here.

## Security and design goals

Access exists to facilitate a company-wide Role-Based Access Control (RBAC) strategy. These
are aspirational principles that should guide feature and bug fix decisions — goals we strive
to move toward, not all fully realized today.

**Prefer roles over direct group membership.** Roles represent job functions, not teams. When
building features or suggesting solutions, prefer paths that encourage the user to add a role
to a group over paths that add individual users to groups directly. Access is designed to make the role-based path
the natural default.

**Principle of least privilege.** When implementing permission checks or approval flows, be
opinionated about who is permitted to do what. Do not default to a more permissive tier for
convenience.

**Delegate to the right owner.** Features that route approvals or notifications should send
them to the group/role owner first, then Access admins as a last resort. For app groups
specifically, the app owner is an intermediate fallback between the group owner and Access
admins — but only app groups have an associated app and app owner. Role groups and plain
OktaGroups have no app owner tier. Groups and apps may also be unowned, in which case the
chain falls through to the next tier.
Centralizing approval in security is explicitly a failure mode of this strategy.

**Time-bound non-day-to-day access.** Indefinite access should be reserved for day-to-day
needs. Users may request or be granted time-bounded access to any group, including untagged
ones — tags enforce ownership and membership time limits but are not the only path to
temporary access. When working on access grant flows, encourage users to request short-term
access for temporary needs and keep the time-bounded grant path intact.

**Don't mix access models.** Features should not make it easier to bypass the role model or
grant access in ways that circumvent ownership and routing.

**Transparency by default.** Company-wide read visibility is a deliberate design decision, not
a leak: any authenticated user may read the full state of the system — membership and ownership
history, request/justification text, and all listings — across both the API and UI. This
openness serves the RBAC model. It applies to **reads only**; mutations and approvals remain
gated by the tiers below. See "Read visibility is intentionally open to all authenticated
users" under Authorization.

## Backend structure

```
api/
  app.py                 # App factory, exception handlers, and the SPA static-file catch-all
  routers/               # FastAPI routers — one file per domain
  routers/_eager.py      # Shared eager-load option builders reused across router queries
  schemas/               # Pydantic request/response models
  models/                # SQLAlchemy ORM models (core_models.py holds almost everything)
  operations/            # Business logic — all writes go through here
  operations/constraints/  # Validation rules (self-add, reason requirements)
  auth/                  # Dependencies, permission checks, OIDC/Cloudflare verification
  plugins/               # pluggy hook specs and implementations
  middleware.py          # Request ID, session scoping, security headers, cache control
  syncer.py              # Batch sync logic between Access DB and Okta
  database.py            # Session management via ContextVar
```

## Serving the built frontend (SPA catch-all)

`api/app.py` defines a `serve_spa` catch-all that serves the Vite build (`BUILD_DIR`). It is
separate from `CacheControlMiddleware` in `middleware.py` (which forces `no-store` on `/api/*`
and `/mcp` responses). Three cache invariants live here and are easy to break with a
well-meaning "simplification" — preserve them:

- **`assets/*` that exist on disk** are served `Cache-Control: public, max-age=31536000,
  immutable` — filenames are content-hashed, so a URL can never resolve to different bytes.
- **`assets/*` that are missing** must return a `404` with `Cache-Control: no-store`, **not**
  fall back to `index.html`. A missing hashed asset is genuinely gone (e.g. a request hitting a
  pod on a different build mid-rolling-deploy), not a client-side route; serving the SPA shell
  there would cache a broken page and mask the 404.
- **Everything else** falls back to `index.html` with `Cache-Control: no-cache,
  must-revalidate` — the shell names which asset hashes are currently valid, so a cached copy
  could point at hashes a later deploy has removed. Never cache `index.html`.

## Routers and dependency injection

Routers live in `api/routers/`. The codebase runs **async SQLAlchemy end-to-end** (the
`[async N/7]` migration landed in #480/#481), so handlers are **`async def`**, `DbSession` is
an `AsyncSession`, and DB access is awaited — `await db.scalars(select(...))`,
`await db.execute(...)`, and `await operation.execute()`. Auth dependencies and the model
helper functions (`get_group_managers`, `can_manage_group`, the `is_*` checks) are async too.
Match the surrounding async signatures. All dependencies are injected via FastAPI's `Depends`:

```python
@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(
    group_id: str,
    db: DbSession,                   # AsyncSession
    current_user_id: CurrentUserId,  # auth — omit only on public endpoints
):
```

**All POST, PUT, PATCH, and DELETE endpoints must require authentication — no exceptions.**
`CurrentUserId` is never optional on a mutating route. Beyond authentication, be deliberate
and opinionated about which tier of user is permitted to perform each action. An authenticated
user is not the same as an authorized one. This must never be bypassed, loosened without
explicit justification, or left as a follow-up.

Authorization checks are FastAPI dependencies:
```python
def create_app(
    _admin: Annotated[str, Depends(require_access_admin)],
    db: DbSession,
):
```

**Don't use Flask patterns:** no `g`, no `request` globals, no `abort()` — raise
`HTTPException` instead.

**Error response format:** The API emits RFC 9457 problem-detail envelopes via custom
exception handlers in `api/exception_handlers.py`:
`{"type": "...", "title": "...", "status": <code>, "detail": "<message>"}` with
`Content-Type: application/problem+json`. Validation errors add a non-standard `errors` list.
The human-readable message lives in `detail`. Operations raise `AccessException` subclasses
(`ConflictError` → 409, `ResourceGoneError` → 410, `InvalidRequestError` → 400) rather than
`HTTPException`, and the handler maps them to this envelope. Keep new error paths on this
shape — don't return bare `{"message": "..."}` or FastAPI's default `{"detail": "..."}` JSON.

## Business logic lives in operations, not routers

All writes go through operation classes in `api/operations/`. A router handler should call an
operation, not directly modify the DB. Operations follow a two-method pattern:

```python
class CreateGroup:
    def __init__(self, ...):         # setup and validation (sync)
        ...
    async def execute(self):         # DB writes + plugin hooks (awaited by the caller)
        ...
```

Plugin hooks fire from inside `execute()`. The plugin interface is **async** (see the Plugin
system section): each hook call returns coroutines that Access awaits via `run_hooks_to_completion`
in `api/plugins/_async_dispatch.py`, which uses `asyncio.wait` (not `gather`, so one hook failing
or a cancelled request can't tear down its siblings' in-flight I/O) and logs a failing hook at
**ERROR** — a plugin's failure is surfaced, not swallowed, yet never breaks the committed
operation; plugins are expected to catch their own expected/noisy errors. Separately,
side-effecting Okta/notification work is spawned as `asyncio` tasks after the local DB state
commits and drained via `drain_fan_out_tasks` in `api/operations/_fan_out.py` — which surfaces
task failures (logged at **ERROR**, non-propagating) instead of dropping them, and uses
`asyncio.wait` (not `gather`) so a cancelled request doesn't tear down in-flight Okta calls. On the
HTTP request path the drain is **deferred to a post-response FastAPI `BackgroundTask`** (via the
`defer_fan_out` router dependency in `api/routers/_fan_out.py`), so the response returns as soon as
the local DB state commits; outside a request (CLI, syncer, MCP, direct `execute()`) it drains
inline. These fan-out tasks do network I/O only, never `db.session` access — and because the
deferred drain runs after the request session is torn down, the operations `expunge` the
notification payload before deferring so the hooks read detached, already-loaded state (see the
Plugin system section). Operations fall into two broad groups — request
operations (create/approve/reject for access, group, and role requests) and resource operations
(create/delete/modify for apps, groups, tags, users, and roles). The exact set changes as
features land, so read `api/operations/` for the current, authoritative list — one class per
file, named for what it does — rather than relying on an enumeration here.

## Audit logging

Every operation's `execute()` method should emit a structured audit log. `AuditLogSchema` and
`EventType` live in `api/schemas/audit_logs.py`. The pattern:

```python
import logging
from api.context import get_request_context
from api.schemas import AuditLogSchema, EventType

_ctx = get_request_context()
logging.getLogger("access.audit").info(
    AuditLogSchema().dumps({
        "event_type": EventType.your_event,
        "user_agent": _ctx.user_agent if _ctx else None,
        "ip": _ctx.ip if _ctx else None,
        "current_user_id": self.actor_id,
        "current_user_email": self.actor_email,
        # ... relevant entities
    })
)
```

`get_request_context()` returns `None` outside an HTTP request (e.g. the syncer or CLI
commands), so always guard `.user_agent` and `.ip` with an `if _ctx` check.

When adding a new operation:
1. Add a new value to the `EventType` enum in `api/schemas/audit_logs.py`
2. Emit a log in `execute()` following the pattern above — include the event type, actor, and
   relevant entities (look at an existing operation for the pattern)

If you change what fields are logged or add a new `EventType`, any downstream detection tooling
that parses these audit logs (e.g. a SIEM or a Panther detection schema) also needs to be
updated. Those schemas typically live in the operator's own private repo, outside Access.

## Follow existing patterns before inventing new ones

Before implementing anything, look at existing code that does something similar:

- **New endpoint:** find an existing router in `api/routers/` for the same domain and follow
  its structure for dependency injection, response models, and error handling.
- **New query:** find an existing query in the same router or operation that touches the same
  model and copy its eager loading strategy — don't guess which relationships to load.
- **New constraint:** look at `api/operations/constraints/` before adding validation inline.
- **New auth check:** check `api/auth/permissions.py` for existing dependency functions before
  writing a new one.

When you do need a new pattern, make it consistent with the closest existing one. Watch for
writing near-duplicate logic across multiple call sites — if the same behavior is needed in
several places, extract and reuse it rather than repeating it.

## Check core_models and helper files before writing queries or auth logic

**Before writing a new query**, check `api/models/core_models.py` for pre-defined
relationships on the model you're querying. Many relationships already exist with the correct
eager loading strategy and active-record filters baked in (e.g. `active_group_memberships`,
`active_group_ownerships`, `active_role_associated_group_mappings`). Using a pre-defined
relationship via `joinedload` or `selectinload` is almost always preferable to writing a
separate query. Prefer `joinedload` for many-to-one / one-to-one relationships and
`selectinload` for one-to-many / many-to-many — `joinedload` on a to-many collection multiplies
rows via the JOIN, while `selectinload` fetches the collection in a separate `IN` query.

**Before writing auth or permission logic**, check these files for existing helpers:

- `api/auth/permissions.py` — the admin/app-owner `Depends` factories plus the
  `can_manage_group` group-owner helper (see Authorization below)
- `api/auth/dependencies.py` — `CurrentUserId`, `require_authenticated`, and other injected
  dependencies (`DbSession` is defined in `api/database.py` and re-exported here)
- `api/models/okta_group.py` — `get_group_managers(group_id)` returns active direct owners of a
  group only; it does not traverse app ownership
- `api/models/app_group.py` — `get_app_managers(app_id)` and `get_access_owners()` for the
  approver routing chain

## Data model

There are three concrete group types using SQLAlchemy **joined-table** inheritance on
`OktaGroup` — `AppGroup` and `RoleGroup` each have their own table (`app_group`, `role_group`)
with a PK that is an FK back to `okta_group.id`, discriminated by the `type` column. (Note: a
few in-code comments loosely call this "single-table"; the mapping is joined-table.):

- **`OktaGroup`** (vanilla) — plain group, no reserved prefix
- **`AppGroup`** — prefix `App-{AppName}-`, has `app_id` and `is_owner` fields
- **`RoleGroup`** — prefix `Role-`, ideally named by job function, not by team. Maps to other groups via `RoleGroupMap`

These prefixes and suffixes are defined as **class constants** on the models — currently
hardcoded string literals (`"App-"`, `"-"`, `"Owners"`, `"Role-"`), not read from config. Treat
the constants as the single source of truth; never re-type the literals inline:

- **Backend:** use the class constants `AppGroup.APP_GROUP_NAME_PREFIX`,
  `AppGroup.APP_NAME_GROUP_NAME_SEPARATOR`, `AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX`, and
  `RoleGroup.ROLE_GROUP_NAME_PREFIX`, defined in the class bodies in
  `api/models/core_models.py`.
- **Frontend:** the group-name prefixes are **not** exposed on `ACCESS_CONFIG` (which carries
  `NAME_VALIDATION_PATTERN`/`NAME_VALIDATION_ERROR`, the `IDP_*` link templates, and
  access-time settings — see `src/config/accessConfig.ts`). The frontend distinguishes group
  types by the API-provided `type` field rather than by parsing name prefixes, and validates
  names against `ACCESS_CONFIG.NAME_VALIDATION_PATTERN`.

When querying polymorphically, always use `with_polymorphic` or `selectin_polymorphic` to
eager-load the subtype. Without this, subtype fields will be missing or lazy load will raise.
The same to-one/to-many split applies here: `with_polymorphic` for many-to-one / one-to-one
relationships (a single JOIN), `selectin_polymorphic` for one-to-many / many-to-many (a separate
query per subtype) — for query efficiency.

**`App`** is a logical grouping in Access's DB only — apps do not correspond to Okta apps and
are not Okta groups.

**`App-Access-Owners`** is the owner group for the Access app itself and functions differently
from all other owner groups: its members are Access admins with system-wide permissions.

**Relationship tables:**
- `OktaUserGroupMember` — user ↔ group/role (roles are a type of group in the DB)
- `RoleGroupMap` — role → group (the groups a role grants access to, app groups OR vanilla)

Both carry: `created_at`, `ended_at`, `created_actor_id`, `ended_actor_id`, `created_reason`,
`is_owner`, `should_expire`. A role cannot contain other roles as members — enforced at the
model level.

## `lazy="raise_on_sql"` — this will bite you

Most relationships on ORM models use `lazy="raise_on_sql"`. Accessing a relationship
attribute without having eagerly loaded it raises a runtime exception — it does not silently
fire a query. Always explicitly `joinedload` or `selectinload` every relationship you access.

Under async SQLAlchemy this bites harder: a `raise_on_sql` relationship surfaces as
`InvalidRequestError`, and any *other* unexpected lazy load or expired-attribute access on the
`AsyncSession` raises **`MissingGreenlet`** (there's no greenlet to run the implicit IO on).
Several call sites carry comments about this — eager-load exactly what a response serializes.

Reuse the shared option builders in `api/routers/_eager.py` rather than re-listing loader
options at each call site. One gotcha lives there: when you reach `RoleGroupMap`s **via a
group's own relationships** (`OktaGroup.active_role_member_mappings` /
`active_role_owner_mappings`), each mapping's `.group`/`.active_group` is the very group you
already loaded, so eagerly re-selecting them re-fetches that same row once per mapping. Use
`role_group_map_options_for_own_group()` (which `noload`s those two) and then call
`bind_role_group_map_own_groups(group)` after load to stamp them from the group you have.
Use the plain `role_group_map_options()` everywhere else.

## Soft deletes and temporal membership — filter everything

- `OktaUser`, `OktaGroup`, `App`, `Tag`: soft-deleted via `deleted_at`. Always filter
  `Model.deleted_at.is_(None)` for active records.
- `OktaUserGroupMember`, `RoleGroupMap`, `OktaGroupTagMap`, `AppTagMap`: time-bounded via
  `ended_at`. Active records: `ended_at.is_(None) OR ended_at > now()`.

Never query these tables without both filters. Missing one silently returns stale data.
Exception: views whose intent is to show historical data — e.g. audit pages, or pages that
list past/expired access alongside current — deliberately omit these filters, since surfacing
ended or superseded records is the point there. Outside that history-oriented case, always
apply both filters.

Every membership and ownership change has `created_actor_id`, `ended_actor_id`, and
`created_reason` fields for audit purposes. These are not enforced — `created_actor_id` and
`ended_actor_id` are nullable, and `created_reason` defaults to an empty string — but they
should be populated whenever the information is available.

## Order of operations matters in multi-field updates

When applying multiple changes to a group in a single request, the order matters. Example:
if converting a group from `OktaGroup` to `AppGroup`, the type must be updated before the
name — otherwise name validation rejects the new `App-` prefix because the group still has
the wrong type. Be deliberate about operation ordering in any handler that touches multiple
fields at once.

## Tag constraints

Tags are named collections of constraints applicable to an `App` (all of its `AppGroup`s
inherit the tag — not all app groups in the system, just that app's groups) or directly to a
group. Tags cannot be applied to externally managed groups (`is_managed=False`).

**The six constraint types** (in `Tag.CONSTRAINTS` in `core_models.py`):

| Key | Type | Coalesced by |
|-----|------|--------------|
| `MEMBER_TIME_LIMIT_CONSTRAINT_KEY` | int (seconds) | min across tags |
| `OWNER_TIME_LIMIT_CONSTRAINT_KEY` | int (seconds) | min across tags |
| `REQUIRE_MEMBER_REASON_CONSTRAINT_KEY` | bool | OR across tags |
| `REQUIRE_OWNER_REASON_CONSTRAINT_KEY` | bool | OR across tags |
| `DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY` | bool | OR across tags |
| `DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY` | bool | OR across tags |

Time limits are stored in seconds but surfaced to users in days. Reason constraints apply to
direct adds and role adds, not only to access requests. **Only enabled tags apply** —
disabled tags still exist in the DB but their constraints are short-circuited.

Operators typically define named tags (e.g. SOX, Quarterly Renewal) with specific constraint
configurations. Read the tag definitions directly for current details — they change and any
summary here would become stale.

**`disallow_self_add_membership`** prevents owners from directly adding themselves as members
to any group carrying this constraint — not just roles. Common scenarios where this surfaces:
1. A group owner who is also a member of a role cannot add that role to the group (or renew
   the role's membership to the group).
2. A role owner cannot renew their own membership in that role — a different owner or Access
   admin must do it.

`disallow_self_add_*` does NOT prevent a role owner from submitting a role request. It can
block approval when all of the following are true: the approver is also a member of the role,
and the target group has a tag with this constraint enabled, and the approver is an owner of
the target group (or, for app groups, an app owner of the associated app). If the user is not
an owner of the target group or app, they cannot respond to the request at all — unless they
are an Access admin, in which case tag constraints do not apply.

Constraint validation lives in `api/operations/constraints/` — add new restrictions there,
not inline in a router or operation.

## Access grant precedence

When a new direct grant is made, it supersedes the existing equivalent access — the old
record is ended and replaced. Granting 90-day access to a user with indefinite access will
shorten their access.

When a user has concurrent access via both a direct grant and a role, the **longer** duration
takes precedence. Unlike the direct-over-direct case above, both DB entries co-exist — the
role membership and the direct grant are not collapsed into one record.

## Approver routing

For all request types, approval routing follows this chain — this affects notification plugin
code and any logic that determines who can approve:

1. **Group/role owner** — designated owner(s) of the target group or role; groups may be unowned
2. **App owner** — if the target is an `AppGroup` with no group owners, falls back to app owner; apps may also be unowned
3. **Access admin** — final fallback if no owners exist at any prior tier

Implemented in `api/models/okta_group.py` (`get_group_managers`) and
`api/models/app_group.py` (`get_app_managers`, `get_access_owners`).

## Authorization

**Authentication and authorization on mutating endpoints is non-negotiable.** Every write
operation must be gated on both who the user is and whether they are permitted to take that
specific action. When in doubt, be more restrictive — it is much easier to loosen a
permission check later than to remediate unauthorized access.

Three tiers, in `api/auth/permissions.py` (all `async def`, awaiting the async `is_*` helpers).
The first two are FastAPI `Depends` dependencies; the third is an inline helper (there is no
per-group `Depends` factory):

1. **Access admin** — member of `App-Access-Owners`. `require_access_admin`. Exempt from all
   tag constraints. Can approve any request. When building flows where an admin overrides a
   tag constraint, prefer surfacing the constraint to them first rather than silently bypassing
   it — the exemption is intentional, but the admin should know what they're overriding.
   (`require_access_admin_or_app_creator` is a related variant that also admits configured app
   creators.)
2. **App owner** — member or owner of an app's owner group.
   `require_app_owner_or_access_admin_for_app` (exposed as the `AppForOwner` type alias).
3. **Group/role owner** — designated owner of a specific group or role. Checked **inline** in
   the handler via `can_manage_group(db, current_user_id, group)`, which returns true for a
   group owner, the app owner of an app group's app, or an Access admin — not a `Depends`
   factory.

The frontend mirrors this logic in `src/authorization.tsx`. When you change backend
authorization rules, check whether the frontend needs updating too.

### Read visibility is intentionally open to all authenticated users

The tiered authorization above gates **mutations and approvals** — who can create, modify,
approve, or reject. It does **not** gate reads. Access is deliberately transparent: any
authenticated user may read the full state of the system, including:

- complete user and group **membership and ownership histories** (`/api/audit/users`,
  `/api/audit/groups`), including the `created_reason` text on each record;
- **access, role, and group request detail** for any request (`GET /api/requests/{id}` and
  the role/group analogues), including `request_reason` and `resolution_reason`;
- group, role, app, tag, and user listings.

This is a design choice, not an oversight. Company-wide visibility into who has access to what
— and the stated reason for each grant — is a goal of the RBAC model, not a leak. Endpoints
that expose this data to any authenticated user are **working as intended**.

Concretely, when reviewing or building read endpoints:

- **Do not** flag an unauthenticated-but-authenticated read of requests, memberships, ownership
  history, or audit data as a vulnerability or IDOR. There is no per-record read ACL to enforce,
  and reason/justification text is in scope for this visibility by design.
- The "when in doubt, be more restrictive" principle and the least-privilege goals elsewhere in
  this document apply to **writes and approvals**, not to read visibility.
- Resource IDs are not secrets and are not a security boundary — nothing is protected by an ID
  being hard to guess, because the underlying records are readable by any authenticated user
  regardless. ID-predictability is not a finding.
- Reads still require **authentication** (the app-wide `require_authenticated` dependency); open
  visibility means open to *authenticated* users, not to the public.

## Request types

**`AccessRequest`** — a user requests membership OR ownership in a group or role for
themselves. Users cannot submit access requests on behalf of others.

**`RoleRequest`** — a role owner requests that a role be granted access to a group (app group
OR vanilla group). The requester must be an owner of the role. Approver is the target group's
owner, app owner (if app group), or Access admin.

> **Who consents when attaching a role to a group.** Role owners decide which *users* are in
> their role; group owners (tool owners / domain experts) decide which *users and roles* are
> members/owners of their group. A `RoleGroupMap(role=R, group=G)` enrolls R's members into G
> — i.e. the role is a member of the group. So the direct endpoint
> `PUT /api/roles/{R}/members {groups_to_add: [G]}` is authorized by **group ownership of G**
> (`can_manage_group(G)`), *not* `can_manage_group(R)` — a group owner may attach a role to
> their group **without role-owner consent**, by design (see `api/routers/roles.py`). Do not
> add a `can_manage_group(role)` requirement there. The `RoleRequest` flow above is the
> *complementary* path: a role owner wanting access to a group they don't own initiates the
> request (`can_manage_group(role)`) and the group owner approves (`can_manage_group(group)`).

**`GroupRequest`** — a user requests creation of a new group, app group, or role. A new `App`
cannot be requested via this flow. On approval, the requester is added as an owner. App group
requests require selecting a parent app and route to the app's owners if they exist, otherwise
to Access admins; vanilla group and role requests route to Access admins.

## Plugin system

Hook specifications and invocations live in the Access repo (`api/plugins/`); operator-specific
implementations live in that operator's own private repo. Each operator writes their own plugin
implementations (for notifications, conditional access, etc.); operator-specific behavior
belongs in one of those plugins, not upstream.

**The plugin interface is async (Access 2.0).** `pluggy` never awaits, so a hook call returns a
list of coroutines that Access drives to completion via `run_hooks_to_completion`
(`api/plugins/_async_dispatch.py`). Consequently hook implementations across all four surfaces —
notifications, conditional access, `metrics_reporter` (`record_*` etc.; `batch_metrics` is an
async context manager), and the app-group-lifecycle *lifecycle* hooks — must be `async def`, and
`verify_async_impls` fails fast at hook load if one is registered as a plain `def`. Do blocking
I/O off the event loop: prefer a native async client (`httpx`, an SDK's async client) and fall
back to `await asyncio.to_thread(...)` only for sync-only dependencies. App-group-lifecycle hooks
that mutate state receive an `AsyncSession` (await ORM calls on it; `session.add(...)` is sync);
the pure metadata/config/status/validation hooks stay **sync**. Plugin-contributed
`access.commands` CLI commands run as ordinary Click commands and must drive their own
`asyncio.run(...)`. The README's plugin section documents this in full.

**Notification completion hooks receive read-only, detached ORM snapshots.**
`access_request_completed` / `access_role_request_completed` fire from the post-response fan-out
drain — after the request session has committed and been torn down — so the operations
(`ModifyGroupUsers` / `ModifyRoleGroups`) `expunge` the payload (the request, its `group`, the
`requester`, the `approvers`) from the session *before* deferring, leaving their already-loaded
attributes readable. A hook may therefore read only what the operation eager-loaded; touching an
unloaded relationship or otherwise triggering a lazy load raises (`lazy="raise_on_sql"` /
`DetachedInstanceError`), and the failure is logged then swallowed — silently dropping that
notification. So if a notifier needs a wider object graph, the operation must eager-load it before
dispatch (don't reach for a lazy load in the hook). This contract is documented for plugin authors
in `NotificationPluginSpec` and the README.

For hook signatures, adding a new plugin type, and implementing an operator override, read the
hookspecs in `api/plugins/`.

## Deployment and infrastructure

Deployment config lives entirely in the operator's own private repo — nothing
deployment-specific belongs in the Access repo. Cronjob schedules, cronjobs/secrets, and
infrastructure details live there too.

## Common mistakes

- **Editing `apiComponents.ts` or `apiSchemas.ts`** — generated; edits are overwritten
- **Missing eager loads** — `lazy="raise_on_sql"` raises at runtime, not at query time
- **Forgetting `deleted_at` or `ended_at` filters** — queries silently return stale records
- **Putting business logic in a router** — it goes in an operation class
- **Wrong order in multi-field group updates** — type before name when type determines valid prefixes
- **Authorizing against the requested target when the body can override it** — on resolve /
  approve endpoints (e.g. `PUT /api/group-requests/{id}`) that let the approver supply
  `resolved_*` fields, the authz check must consider the *resolved* type and target, not just
  the originally requested ones. Otherwise an app owner authorized against `requested_app_id`
  can flip `resolved_group_type` to mint a `role_group`/`okta_group` they could never create
  directly. Mirror the check in the corresponding operation class so it holds for callers
  that bypass the route.
- **Changing backend auth without updating `authorization.tsx`**
- **Using Flask patterns** — no `abort()`, no `g`, no `@app.before_request`
- **Returning bare `{"message": "..."}` or `{"detail": "..."}` errors** — the contract is the
  RFC 9457 problem-detail envelope (`application/problem+json`); raise `AccessException`
  subclasses from operations rather than hand-rolling error JSON
- **Assuming Okta is always source of truth** — only true for unmanaged groups or
  non-authoritative sync mode
- **Assuming `disallow_self_add_*` blocks role request submission** — it doesn't; it affects
  who can approve or add directly
- **Assuming reason constraints only apply to access requests** — they apply to direct adds
  and role adds too
- **Querying tags without filtering for enabled** — disabled tags still exist in the DB
- **Putting operator-specific logic in the Access repo** — it belongs in an operator's own plugin
