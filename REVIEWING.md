# Reviewing Access changes

This is the review policy for Access pull requests. The AI review gate
(`.github/workflows/ai-review.yml`) appends it to its trusted prompt, and human reviewers can use
it as a checklist. It distills `.claude/CLAUDE.md`, which remains the authoritative description of
the codebase; when the two disagree, `.claude/CLAUDE.md` wins and this file should be updated.

Access is an open-source Okta access-control portal: FastAPI and async SQLAlchemy 2.0 on the
backend, React and TypeScript on the frontend, and pluggy plugins for operator-specific behavior.
Many organizations run it, so a defect here ships to all of them.

## What a finding must be

Report a defect only when the change introduced it, the impact is concrete, and you can trace a
path from the cited line to that impact. Read the surrounding code before reporting: many
invariants below are enforced one layer away (a dependency, an operation, a model relationship),
and a finding that ignores that layer is wrong.

## Rules to check

### Authorization tier (`authz_tier`)

- Every POST, PUT, PATCH, and DELETE route requires `CurrentUserId`. There is no exception.
- Authentication is not authorization. Each mutating route must be gated on the right tier:
  Access admin (`require_access_admin`), app owner (`require_app_owner_or_access_admin_for_app` /
  `AppForOwner`), or group/role owner, checked inline with `can_manage_group(db, user, group)`.
- A permission check that falls back to a more permissive tier for convenience is a defect.
- Attaching a role to a group (`PUT /api/roles/{R}/members` with `groups_to_add: [G]`) is
  authorized by ownership of the *group* G, not the role R. That is deliberate; do not report the
  missing role-owner check.

### Resolved versus requested target (`resolved_target_authz`)

Resolve and approve endpoints that accept `resolved_*` fields in the body (for example
`PUT /api/group-requests/{id}`) must authorize against the *resolved* type and target, not only the
requested ones, and the operation class must repeat the check so callers that bypass the route are
also covered. Otherwise an app owner authorized for `requested_app_id` can flip
`resolved_group_type` and create a role group or plain group they could never create directly.

### Tag-constraint bypass (`tag_constraint_bypass`)

- Constraints live in `api/operations/constraints/`; new restrictions belong there, not inline.
- Only enabled tags apply. A query that reads tag constraints without filtering for enabled tags
  applies constraints that an operator has turned off.
- Reason constraints (`require_member_reason`, `require_owner_reason`) apply to direct adds and
  role adds as well as access requests.
- `disallow_self_add_*` blocks owners adding themselves (including through a role they belong to)
  and blocks approval by an approver who is a member of the role. It does not block submitting a
  role request.
- Tags cannot be applied to externally managed groups (`is_managed=False`).

### Approver routing (`approver_routing`)

Approvals and notifications route to the group or role owner first, then (for app groups only) the
app owner, then Access admins as a last resort. Role groups and plain groups have no app-owner
tier. Routing everything to admins, skipping a tier, or giving a non-app group an app-owner tier is
a defect. The helpers are `get_group_managers`, `get_app_managers`, and `get_access_owners`.

### Active-record filters (`missing_active_filter`)

- `OktaUser`, `OktaGroup`, `App`, and `Tag` are soft-deleted: active rows need
  `deleted_at.is_(None)`.
- `OktaUserGroupMember`, `RoleGroupMap`, `OktaGroupTagMap`, and `AppTagMap` are time-bounded:
  active rows need `ended_at IS NULL OR ended_at > now()`.
- A query missing either filter silently returns stale access. The exception is a view whose
  purpose is history (audit pages, expired-access listings).
- Operations re-load their target at the top of `execute()` with these filters and then key every
  later query off the loaded instance (`group.id`), never the raw constructor input
  (`self.group_id`).

### Async ORM (`async_orm`)

- Handlers, dependencies, permission helpers, and `execute()` are `async`; DB calls are awaited.
- Most relationships are `lazy="raise_on_sql"`. Touching one that was not eagerly loaded raises at
  runtime, and any other implicit lazy load on the `AsyncSession` raises `MissingGreenlet`.
  Every relationship a response serializes must be loaded with `selectinload` (to-many) or
  `joinedload` (to-one); polymorphic group queries need `with_polymorphic` or
  `selectin_polymorphic`.
- Request-path notification hooks run after the response through `defer_notification`, against
  objects that were expunged from the session. A hook can read only what the operation
  eager-loaded; widening what a notifier reads without widening the eager load silently drops
  the notification. New request-path notifications must use `defer_notification`, never
  `send_notification` directly.
- App-group lifecycle hooks are deferred with `defer_or_invoke_lifecycle_hook` after the commit,
  carrying plain data, not ORM objects. `group_deleted` is the exception and must stay inline.

### Plugin and API compatibility (`plugin_api_compat`)

- Hook implementations for notifications, conditional access, metrics, and lifecycle hooks are
  `async def`; metadata, config, status, and validation hooks stay sync.
- Plugins integrate only through the plugin interface: they never import `api.operations` or
  `api.services`, build queries, or touch a session, and hooks never commit or roll back.
- Changes to hook signatures, the REST API, or the data model affect every operator. Prefer
  additive changes and deprecation; an unannounced breaking change is a finding.
- Errors use the RFC 9457 problem-detail envelope. Operations raise `AccessException` subclasses
  (`ConflictError`, `ResourceGoneError`, `InvalidRequestError`), not `HTTPException` or bare
  `{"message": ...}` JSON.

### Migration safety (`migration_safety`)

Alembic migrations run against live operator databases. Look for data loss, migrations that
cannot be downgraded when the code claims they can, data migrations that assume one operator's
data, backfills that ignore soft-deleted or ended rows, and schema changes the code at the
previous release cannot run against during a rolling deploy.

### Audit-log contract (`audit_event_contract`)

Every operation's `execute()` emits a structured audit log through `AuditLogSchema` with an
`EventType` from `api/schemas/audit_logs.py`, guarding `get_request_context()` for `None` outside
a request. Renaming or removing an `EventType`, or changing which fields an event carries, breaks
operators' downstream detection tooling (SIEM schemas); call it out so the PR description can warn
them. A missing audit log on a new operation is enforced by an automated test, so report only a
log that is present but wrong (wrong actor, wrong entity, misleading event type).

### Operator-specific leaks (`operator_leak`)

The repository is public under Apache 2.0. Hardcoded Okta attributes, email formats, internal
URLs, private-repository references, or any behavior that only works for one operator belong in a
plugin or that operator's private repository. `.github/workflows/docker-image.yml` is the single
deliberate exception, and only for release-pipeline plumbing that is a no-op without its secret.

### Frontend authorization drift (`frontend_authz_drift`)

`src/authorization.tsx` mirrors the backend tiers. A change to who may perform an action on the
backend that is not reflected there (or the reverse) shows users controls that fail, or hides
controls they are entitled to.

### Everything else

Use `spec_mismatch` when the change does not do what its linked issue or description asks,
`runtime_correctness` for other concrete behavior bugs, and `runtime_security` for security
defects that fit none of the categories above.

## Never report

- **Open read visibility.** Any authenticated user may read every user, group, role, app, tag,
  membership and ownership history, audit record, and access/role/group request, including
  reason and justification text. That is a design goal, not an IDOR or a data leak. Reads still
  require authentication.
- **Predictable or guessable IDs.** IDs are not secrets and protect nothing.
- **Access admins bypassing tag constraints.** The exemption is intentional. Report only a flow
  that bypasses it *silently* where the admin should have been shown the constraint.
- Pre-existing defects the change did not touch, style, naming, and formatting.

## Enforced by other checks (do not re-report)

Separate CI jobs already enforce the following, so findings about them are noise:

- Lint, format, and types: `ruff` (including the security, bugbear, async, and logging rules
  such as f-strings in log calls), `ty`, `prettier`, `tsc`, and ESLint.
- Architecture tests: mutating routes lacking authentication, and operations missing an audit log.
- Semgrep rules, including direct database writes from routers.
- Plugins importing internal modules (ruff import bans).
- Changed-line test coverage.
- Dependency vulnerabilities and licenses.
- Workflow hygiene: unpinned actions or images, missing `permissions:`, and actionlint/zizmor
  findings.
- The operator-string leak scanner (literal operator names and domains). Report an operator
  leak only when it is behavioral, which a string scan cannot see.
- Migration linting (squawk) for lock-heavy DDL.
- Generated-client drift in `src/api/`.
