# Transitive tag application to member roles

**Date:** 2026-07-14
**Status:** Approved design — ready for implementation planning
**Branch:** `brynna/sox_tag_transitivity`

## Summary

Support configuring a tag so that it applies transitively to roles that are members of a
tagged group. When a tag carrying the new `apply_tag_to_member_roles` constraint is applied to a
group — directly or inherited from the group's app — that tag is also applied to any `RoleGroup`
that is an active **member** (not owner) of the group. If the role later loses that membership,
the transitively-applied tag is removed.

The transitive tag is a real, materialized `OktaGroupTagMap` row on the role, so it behaves
**exactly** like a tag applied directly to the role: its constraints are enforced (time limits,
reason requirements, self-add restrictions), retroactive time-limit capping fires, and it is
displayed in both the role view and the tag view. Enforcement is suppressed for disabled tags
by the same mechanism that already suppresses direct/app tags.

### Motivating example

A compliance tag (e.g. `https://access.discord.tools/tags/SOX`) is applied to an app. All of
that app's `AppGroup`s inherit it. Any role that is a member of one of those app groups should
also be treated as SOX-scoped — its own members must respect SOX time limits and self-add
restrictions — without an operator having to hand-tag every such role and remember to untag it
when the role's membership changes.

## Decisions (from brainstorming)

1. **Enforcement scope:** Display **and** enforce. A transitively-received tag governs the
   role's own membership exactly as a directly-applied tag would.
2. **Trigger relationship:** **Member mappings only** (`RoleGroupMap.is_owner == False`). A role
   that is an *owner* of a tagged group does not receive the tag.
3. **Retroactive capping:** Yes. Behavior is identical to a directly-applied tag — attaching a
   role to an already-time-limited group, or adding the `apply_tag_to_member_roles` constraint
   to a tag already on a group, retroactively caps the role's existing members' end-dates.
4. **Architecture:** Materialize derived rows (Approach A) — reuse `OktaGroupTagMap` with a new
   `source_role_group_map_id` column, rather than computing effective tags live at every call
   site.
5. **Disabled tags:** Derived rows exist independent of `Tag.enabled`, mirroring how
   direct/app tag-map rows exist for disabled tags today. `enabled` gates enforcement only, and
   that suppression is automatic because derived rows point at the same `Tag`.

## Background: how tagging works today

Facts established from the codebase (`main`), load-bearing for this design:

- **Group tags are stored, not computed on read.** `OktaGroupTagMap` is a join table
  (`tag_id`, `group_id`, `ended_at`, `created_at`). A group's effective tags are just
  `group.active_group_tags` — a single relationship containing every active row.
- **App inheritance is materialized by fan-out.** `api/operations/modify_app_tags.py`
  (`ModifyAppTags`) inserts one `AppTagMap` row and then one `OktaGroupTagMap` row per
  `AppGroup` of the app, each with `app_tag_map_id` set (the "inherited from app" provenance).
  Removal ends the `AppTagMap` row and every `OktaGroupTagMap` row referencing it.
  `api/operations/modify_group_tags.py` (`ModifyGroupTags`) is the direct-to-group analogue and
  only ever touches rows with `app_tag_map_id IS NULL`.
- **The "Direct or via App" distinction is purely `active_app_tag_mapping`.** In
  `src/pages/tags/Read.tsx`, a row is labeled with the app name if
  `tagMap.active_app_tag_mapping` is truthy, else "Direct". The remove button is hidden when
  `active_app_tag_mapping` is set.
- **Role pages and group pages are the same component.** `src/App.tsx` routes both
  `/groups/:id` and `/roles/:id` to `ReadGroup` (`src/pages/groups/Read.tsx`). A `RoleGroup`
  **is** an `OktaGroup`, already supports directly-applied tags, and renders them with the same
  chip logic as any group.
- **`enabled` gates enforcement, not row existence.** `active_group_tags` is unfiltered by
  `enabled`. Only `coalesce_constraints` (`api/models/tag.py:11`, `if tag.enabled and
  constraint_key in tag.constraints`) and the separate `enabled_active_tag` relationship
  consult `enabled`. Disabled tags' group mappings exist and display; only their constraints
  are suppressed.
- **The one existing "does a constraint reach a role via membership" precedent** is
  `CheckForSelfAdd` / `CheckForReason` in `api/operations/constraints/`, whose `execute_for_role`
  walks `RoleGroupMap → active_group.active_group_tags` **live**, as a validation gate only —
  it never materializes or displays a derived association. This design materializes that
  relationship so it can also be enforced uniformly and displayed.
- **Transitivity is one level deep.** A `@validates("group")` hook on `RoleGroupMap` forbids a
  role from being a member of another role, so a role can *receive* a transitive tag but can
  never re-propagate it. No recursion, no cycle detection.

## Data model

### New constraint key

Add to `Tag.CONSTRAINTS` in `api/models/core_models.py`, following the existing boolean pattern
(`disallow_self_add_*`):

```python
APPLY_TAG_TO_MEMBER_ROLES_CONSTRAINT_KEY = "apply_tag_to_member_roles"

# in CONSTRAINTS:
APPLY_TAG_TO_MEMBER_ROLES_CONSTRAINT_KEY: TagConstraint(
    name="Apply Tag to Member Roles",
    description="Apply this tag to any role that is a member of a group carrying this tag.",
    validator=lambda v: isinstance(v, bool),
    coalesce=lambda a, b: a or b,
),
```

Stored in the existing `Tag.constraints` JSON column — no new column on `Tag`.

### New column on `OktaGroupTagMap`

Parallel to the existing `app_tag_map_id`:

```python
source_role_group_map_id: Mapped[Optional[str]] = mapped_column(
    ForeignKey("role_group_map.id"), nullable=True
)
source_role_group_map: Mapped[Optional["RoleGroupMap"]] = relationship(...)
```

For any `OktaGroupTagMap` row, **at most one** of `{app_tag_map_id, source_role_group_map_id}`
is set:

- both `None` → the tag is applied directly to the group/role;
- `app_tag_map_id` set → inherited from the group's app (existing);
- `source_role_group_map_id` set → derived from the role's membership in a group (new).

They are mutually exclusive by construction: app fan-out only targets `AppGroup`s (never a
`RoleGroup`), and a role's derived row is sourced from exactly one `RoleGroupMap`. This is
enforced by convention in the write path, consistent with how the codebase treats the analogous
`app_tag_map_id` invariant (no DB-level CHECK constraint).

The FK points at `RoleGroupMap`, not at the source group's own tag row, because the source of
truth for "does this role still qualify" is the membership (`RoleGroupMap.ended_at`), not the
particular tag row on the group (which may itself be direct or app-derived — irrelevant to the
role).

### Migration

Alembic migration adding the nullable `source_role_group_map_id` column and its FK to
`okta_group_tag_map`. Additive and backwards-compatible: existing rows get `NULL`. No data
backfill on migration — see rollout note below.

## Reconciler: `SyncMemberRoleTags`

A single shared operation in `api/operations/` reconciles the derived rows so the logic is not
duplicated across trigger sites (per the repo's "don't duplicate logic across call sites" rule).

### Invariant

A role `R` has a derived `OktaGroupTagMap` row for tag `T` **iff** all hold:

- there is an active non-owner `RoleGroupMap(role=R, group=G)` (`is_owner == False`,
  `ended_at` null or future), **and**
- there is an active `OktaGroupTagMap(group=G, tag=T)`, **and**
- `apply_tag_to_member_roles` is present and truthy in `T.constraints`.

`Tag.enabled` is **not** consulted. Enforcement suppression for disabled tags happens
automatically downstream (see Enforcement parity).

### Behavior

Given a scope, the reconciler computes desired-vs-actual derived rows and writes only the delta:

- **Insert** a derived `OktaGroupTagMap` on `R` (with `source_role_group_map_id` = the
  qualifying mapping's id, `app_tag_map_id` null) for each desired `(R, T)` not already present.
- **End** (set `ended_at`) any existing derived row on `R` whose qualifying condition no longer
  holds — i.e. the sourcing `RoleGroupMap` ended, the group's tag row ended, or the constraint
  was removed from `T`.
- **Idempotent:** repeated runs over the same scope produce no writes once converged, so
  overlapping triggers never create duplicates.

The reconciler only ever touches rows with `source_role_group_map_id` set. It never ends or
mutates a direct row (`app_tag_map_id` null and `source_role_group_map_id` null) or an
app-derived row (`app_tag_map_id` set) — those remain owned by `ModifyGroupTags` /
`ModifyAppTags` respectively.

### Time-limit parity

When the reconciler inserts a derived row for a tag that carries a time-limit constraint, it
invokes the existing `ModifyGroupsTimeLimit` on the role — the same call
`ModifyGroupTags`/`ModifyAppTags` already make — so the role's existing members are retroactively
capped identically to a directly-tagged group.

## Trigger points

Each is an existing operation; the reconciler is invoked from within it after the operation's
own DB writes:

1. **`ModifyRoleGroups`** — a role is attached to or detached from a group as a member.
   Reconcile that `(role, group)` pair. Attach inserts derived rows for the group's qualifying
   tags; detach (membership ended) ends the derived rows sourced by that mapping. This is the
   "role loses access → transitive tag removed" path.
2. **`ModifyGroupTags`** — a tag is added to or removed from a group directly. Reconcile the
   member roles of that group for the affected tag.
3. **`ModifyAppTags`** — a tag is added to or removed from an app. After the existing
   app-group fan-out, reconcile the member roles of each affected `AppGroup`. This is the
   "inherited from app" source reaching member roles.
4. **`ModifyTag`** — the `apply_tag_to_member_roles` constraint value is **added to or removed
   from** `T.constraints`. Reconcile the member roles of every group currently carrying `T`.
   This trigger reacts **only** to the constraint value changing — **not** to the `enabled`
   toggle. Toggling `enabled` preserves derived rows, mirroring how it preserves group/app tag
   rows today.

## Enforcement parity (no new enforcement code)

Because each derived row is a real `OktaGroupTagMap` on the role:

- `coalesce_constraints`, `CheckForSelfAdd`, and `CheckForReason` already read
  `active_group_tags` and therefore see derived rows with **zero changes**.
- Retroactive time-limit capping fires via the reconciler's `ModifyGroupsTimeLimit` call.
- **Disabled-tag suppression is free:** a derived row points at the same `Tag`. When `T` is
  disabled, `coalesce_constraints` skips it (its `tag.enabled` check) and `enabled_active_tag`
  excludes it, so `T`'s constraints do not coalesce onto the role. The tag still displays on the
  role, inert — exactly like a disabled direct tag on a group.

## API & schema changes

- **`OktaGroupTagMapDetail`** (`api/schemas/core_schemas.py`) gains a field parallel to
  `active_app_tag_mapping`:

  ```python
  active_source_role_group_mapping: Optional["_RoleGroupMapRefForTag"] = None
  ```

  where `_RoleGroupMapRefForTag` carries the source group's id / name / type (and app summary,
  if the source group is an app group) — enough to render "derived from membership in {group}"
  and the "Group: {name}" chip.

- **Eager loading** (`api/routers/_eager.py`): extend the shared `group_tag_map_options()` (or
  add a sibling) to hydrate `source_role_group_map → group` (and its app). Required under
  `lazy="raise_on_sql"` — omitting it raises at serialization time, not silently.

- **Tag view backend** (`get_tag`, `api/routers/tags.py`): `tag.active_group_tags` already
  includes the derived rows on roles (they *are* `OktaGroupTagMap` rows for that tag), so roles
  appear in "Groups with Tag" for free. The only change is widening the eager-load options to
  hydrate the new relationship.

- **Role view backend:** served by the same `ReadGroup` path; derived rows appear automatically
  once the eager-load hydrates the relationship. No query change.

## Frontend

### Role / group view — tag chips (`src/pages/groups/Read.tsx`)

1. **Dedupe by `active_tag.id`** — collapse multiple provenance rows for the same tag into one
   chip (a role can carry a tag directly *and* transitively, or from two groups).
2. **Provenance tooltip** (MUI `Tooltip`) merged from all of that tag's rows, concatenating
   whichever sources are present:
   - "Applied directly to this group"
   - "Inherited from app {AppName}"
   - "Derived from membership in {GroupA, GroupB}"
3. Chip `variant` stays `filled` when any directly-owned source is present, `outlined` when the
   tag is only app- or role-derived (matching today's convention).

### Tag view — "Groups with Tag" table (`src/pages/tags/Read.tsx`)

This changes from row-per-tag-map to **row-per-entity**:

- **Group rows by `active_group.id`** — one row per group/role, even when it receives the tag
  from multiple sources. (Today the table renders one row per `OktaGroupTagMap` row.)
- **Rename the column "Direct or via App" → "Tag Source"**, holding one or more chips per row:
  - **"Direct"** — a row with neither app nor role source.
  - **"App: {name}"** — a row with `active_app_tag_mapping` (relabeled from today's bare app
    name).
  - **"Group: {name}"** — a row with `active_source_role_group_mapping`; one chip per distinct
    source group (a role fed by two groups shows two "Group: …" chips).
- **Remove button** shows **iff** that row has a **Direct** chip, and removing ends **only** the
  direct `OktaGroupTagMap` assignment. App- and group-derived rows are untouched — they are
  removed by removing the app tag or ending the role membership / constraint, respectively.

## Audit logging

The reconciler emits structured audit logs per the `AuditLogSchema` / `EventType` pattern in
`api/schemas/audit_logs.py`. Add two `EventType` values (e.g. `tag_applied_to_member_role`,
`tag_removed_from_member_role`) and log, for each derived-row insert/end: the acting user (the
actor of the triggering operation), the tag, the role, and the source group.
`get_request_context()` is null-guarded per the standard pattern (these triggers run inside HTTP
requests today, but the guard is cheap and correct).

> If new `EventType` values or logged fields are added, downstream detection tooling that parses
> these audit logs (SIEM / Panther schemas, typically in an operator's private repo) must be
> updated. Out of scope for this repo but worth flagging in the PR.

## Edge cases

- **Disabled tag:** derived rows created/kept regardless of `enabled`; enforcement suppressed
  automatically (above).
- **Constraint removed / flipped false:** `ModifyTag` trigger ends the derived rows.
- **Role added to already-tagged group / tag added to group that already has member roles:**
  covered by triggers 1 and 2/3/4 respectively.
- **Owner mappings ignored:** reconciler filters `is_owner == False`.
- **Externally-managed groups (`is_managed == False`):** tags can't be applied to them at all,
  so no special handling.
- **Role carrying a tag both directly and transitively:** two rows with distinct provenance;
  coalescing de-dups by value for enforcement, the UI de-dups by tag id for display, and ending
  the membership ends only the derived row.
- **Same tag from two member groups:** two derived rows (one per `RoleGroupMap`); one merged
  role-view chip; one tag-view row with two "Group: …" chips.

## Rollout / backfill

The migration is additive (nullable column) and does not backfill. Derived rows are created
going forward by the triggers. To materialize transitive tags for pre-existing
role-in-tagged-group memberships, run the reconciler over the existing population once — either
via a management/CLI command or a one-off invocation. **Open item for the implementation plan:**
decide whether to ship a backfill command or rely on the next membership/tag/constraint edit to
converge each role. Recommendation: ship a small idempotent backfill command, since without it a
role already sitting in a SOX-tagged group would not become SOX-scoped until unrelated activity
touches it.

## Testing

Tests live in the same commit as the code they validate (repo convention).

**Backend (pytest + Factory Boy):**

- Reconciler unit tests for each trigger and each cleanup path (membership end, group-tag
  removal, app-tag removal, constraint removal), including idempotency.
- Parity tests: a transitively-tagged role enforces time-limit, self-add, and reason
  constraints identically to a directly-tagged role, **including** retroactive capping on
  attach and on constraint-add.
- Disabled-tag test: derived rows exist and display but constraints do not coalesce onto the
  role.
- Owner-mapping test: an owner `RoleGroupMap` does not produce derived rows.
- Migration test for the new column.
- Backfill command test (if shipped).

**Frontend (vitest + React Testing Library):**

- Role view: deduped chip per tag; merged provenance tooltip across direct/app/role sources.
- Tag view: row-per-entity grouping; "Tag Source" column with the three chip types; remove
  button visible only when a Direct chip is present and removing only the direct assignment.

## Non-goals

- Propagating tags to roles that are *owners* (not members) of a tagged group.
- Multi-level transitivity (impossible — roles can't be members of roles).
- Changing how direct or app-inherited tags behave.
- Any operator-specific tag configuration (belongs in an operator's private repo).
