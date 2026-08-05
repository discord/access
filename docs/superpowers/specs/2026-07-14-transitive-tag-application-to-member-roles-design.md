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

A compliance tag (e.g. `SOX`) is applied to an app. All of that app's `AppGroup`s inherit it.
Any role that is a member of one of those app groups should also be treated as SOX-scoped — its
own members must respect SOX time limits and self-add restrictions — without an operator having
to hand-tag every such role and remember to untag it when the role's membership changes.

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
6. **Gate vs. add (KEY DECISION — breaking, ships with Access 2.0):** `apply_tag_to_member_roles`
   is the single opt-in switch that **gates all transitive application** — self-add, reason,
   **and** time limits — replacing the existing *unconditional* propagation of self-add/reason
   to member-roles. This is a **breaking change** (see "Existing role propagation and the
   gate-vs-add decision" below) and is intended to land as part of the Access 2.0 major version
   bump. The design isolates this to a single keep/remove edit so it can be reverted to the
   non-breaking "add-only" variant if team feedback prefers it.

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

## Existing role propagation and the gate-vs-add decision

**Four of the six constraints already propagate to member-roles today — unconditionally.**
`CheckForSelfAdd.execute_for_group` (`check_for_self_add.py:89-115`) and
`CheckForReason.execute_for_group` (`check_for_reason.py:71-95`) run whenever a user is added to
a group; when that group **is a role**, they walk the role's
`active_role_associated_group_member_mappings` (the groups the role is a member of), pull each
group's `active_group_tags`, and coalesce the constraint. So `disallow_self_add_membership`,
`disallow_self_add_ownership`, `require_member_reason`, and `require_owner_reason` **already reach
role members** — with **no opt-in**; it happens for every tag carrying those constraints.

**The two time-limit constraints do *not* propagate in this direction.** `ModifyGroupsTimeLimit`'s
role traversal (`modify_groups_time_limit.py:74-101`) handles the *opposite* direction — a tag
applied directly *to* a role capping the downstream access that role grants — not "a role inherits
its member-group's time limit." Nothing caps a user's membership-in-role duration based on the
tags of groups the role belongs to.

This is because the two categories need structurally different propagation: self-add and reason are
**veto gates** (a live read-time "block or allow" check, nothing to store), while time limits are
**state mutations** (they must write `ended_at`, so they need materialization + retroactive
capping). That asymmetry is why the existing code propagates the first category but not the second.

### The decision

`apply_tag_to_member_roles` becomes the **single opt-in gate for all transitive application**
(option 2). Concretely:

- The reconciler creates a derived `OktaGroupTagMap` row on a member-role **iff** the source
  group's tag has `apply_tag_to_member_roles` truthy. That row carries the tag's *entire*
  constraint set (it points at the `Tag`), so once it exists, self-add, reason, and time-limit
  constraints all apply to the role through the role's own `active_group_tags` — via the exact
  same code that enforces a directly-applied tag.
- **We remove** the existing associated-group live-traversal blocks in
  `CheckForSelfAdd.execute_for_group` (lines 89-115) and `CheckForReason.execute_for_group`
  (lines 71-95). With derived rows present, those blocks are redundant for opted-in tags; kept,
  they would leak un-gated self-add/reason propagation for tags that did *not* opt in, defeating
  the gate.

**Why this is breaking:** existing tags that rely on the always-on self-add/reason propagation
will stop propagating to member-roles until an operator sets `apply_tag_to_member_roles` on them.
Hence it ships with the **Access 2.0** major version bump, and the PR must ship the admin
enablement snippet (see "Breaking change & operator enablement").

### Reverting to the non-breaking "add-only" variant (option 1)

If team feedback prefers backwards compatibility, the entire decision reverts via a **single
keep/remove edit**: **keep** the associated-group live-traversal blocks instead of removing them.
Then self-add/reason continue propagating unconditionally (as today, non-breaking), the derived
rows redundantly re-cover them for opted-in tags (idempotent — same answer), and
`apply_tag_to_member_roles` meaningfully controls only time-limit propagation + materialized
visibility. Nothing else in this design changes. This is the only behavioral knob between the two
variants; the implementation plan should keep those two blocks factored so the edit stays a
one-paragraph change.

> **Enforcement-path note (option 2):** removing the live traversal makes the derived rows the
> *sole* enforcement path for self-add/reason on member-roles. A missing derived row (an orphan or
> a trigger gap) would silently stop enforcement — which is the primary reason the orphan
> backstop below is not optional under option 2.

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
`okta_group_tag_map`. Additive and backwards-compatible: existing rows get `NULL`. Backfill is
performed by the full-population reconcile, not the migration — see "Orphan backstop & backfill".

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

**`is_managed` handling, mirroring "tags can't be applied to unmanaged groups":** the reconciler
does **not create** a derived row on a role `R` where `R.is_managed` is false. It does **not
proactively tear down** derived rows when a role flips to unmanaged, because `UnmanageGroup`
leaves a group's directly-applied tags in place (inert, since every constraint check gates on
`is_managed`) and derived rows should behave identically. Net effect: a derived row on an
unmanaged role is inert but retained, exactly like a direct tag on an unmanaged group.

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
5. **`UnmanageGroup`** — ends every `RoleGroupMap` where `group_id == G` via a direct bulk
   `update` (`unmanage_group.py:108`), **bypassing `ModifyRoleGroups`**. After that update,
   reconcile the roles whose member-mappings into `G` were just ended, so their derived rows are
   torn down. (Unmanaging a *role* preserves its outbound mappings and its tag rows per
   `unmanage_group.py:116-117`, so no teardown is needed there — see the `is_managed` rule in the
   reconciler invariant.)
6. **`DeleteGroup`** — ends `RoleGroupMap` where `group_id == G` via a direct bulk `update`
   (`delete_group.py:144`), also bypassing `ModifyRoleGroups`. Reconcile the affected member
   roles. (When the *deleted* group is itself a role `R`, `delete_group.py:252/304` already ends
   `R`'s outbound mappings and `R`'s own `OktaGroupTagMap` rows — including its derived rows —
   so that direction needs no extra handling.)

**Trigger-completeness is the correctness boundary of this design.** The rule is: *every
operation that ends a qualifying `RoleGroupMap`, or changes a group/app tag set, or edits the
constraint, must reconcile the affected roles.* An audit of all writers that end `RoleGroupMap`
outside `ModifyRoleGroups` yields exactly `UnmanageGroup` and `DeleteGroup` (the member-group
path); `ModifyGroupsTimeLimit` only *caps* `ended_at` to a future value and never ends a mapping,
so it is not a trigger. This set is complete as of the maintenance watermark, but bulk-write
paths are easy to add later without remembering to reconcile — which is why the orphan backstop
below is a required safety net, not a nicety.

## Enforcement parity

Because each derived row is a real `OktaGroupTagMap` on the role, enforcement rides on the code
that already exists:

- `coalesce_constraints`, and the *direct-group* branches of `CheckForSelfAdd` /
  `CheckForReason` (lines 64-85 / 54-67), already read the role's own `active_group_tags` and
  therefore see derived rows with **zero changes** — this is how self-add and reason constraints
  reach the role under option 2.
- Retroactive time-limit capping fires via the reconciler's `ModifyGroupsTimeLimit` call — the
  new enforcement this feature adds, since time limits did not previously propagate in this
  direction.
- **Disabled-tag suppression is free:** a derived row points at the same `Tag`. When `T` is
  disabled, `coalesce_constraints` skips it (its `tag.enabled` check) and `enabled_active_tag`
  excludes it, so `T`'s constraints do not coalesce onto the role. The tag still displays on the
  role, inert — exactly like a disabled direct tag on a group.

The one enforcement-code change is the **removal** (option 2) of the associated-group
live-traversal blocks in `CheckForSelfAdd.execute_for_group` (89-115) and
`CheckForReason.execute_for_group` (71-95), as described in the gate-vs-add decision. Keep those
blocks factored/co-located so the option-1 revert is a one-paragraph change. `execute_for_role`
in both classes is a *different* direction (checking target-group tags when a role is attached to
groups) and is **not** touched by this feature.

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
- **Source group unmanaged (`UnmanageGroup` on `G`):** `G`'s member `RoleGroupMap`s are bulk-ended,
  so the reconciler (trigger #5) tears down the affected roles' derived rows.
- **Source group deleted (`DeleteGroup` on `G`):** same as unmanage via trigger #6.
- **Role itself unmanaged:** outbound mappings and tag rows are preserved; derived rows stay but
  are inert (all checks gate on `is_managed`). Reconciler will not *create* new derived rows on an
  unmanaged role.
- **Role re-managed (unmanaged → managed):** there is **no `ManageGroup` operation** to hook — the
  transition is a bare attribute flip in `group.update_okta_group(...)` inside the syncer
  (`syncer.py:246`), unlike the managed→unmanaged direction which the syncer routes through
  `UnmanageGroup` (`syncer.py:248-249`, trigger #5). A re-managed role may qualify for derived
  rows that were skipped while it was unmanaged, so it needs a reconcile. This is handled by the
  **syncer-hosted full-population reconcile** (see "Orphan backstop & backfill"): because the
  reconcile runs in the same syncer pass that performs the re-manage, the enforcement-gap window
  is at most one syncer cycle. (Decided: backstop only, no eager re-manage hook — see "Orphan
  backstop & backfill".)
- **Externally-managed groups (`is_managed == False`) as targets:** the reconciler never creates
  derived rows on an unmanaged role, mirroring "tags can't be applied to unmanaged groups."
- **Role carrying a tag both directly and transitively:** two rows with distinct provenance;
  coalescing de-dups by value for enforcement, the UI de-dups by tag id for display, and ending
  the membership ends only the derived row.
- **Same tag from two member groups:** two derived rows (one per `RoleGroupMap`); one merged
  role-view chip; one tag-view row with two "Group: …" chips.

## Orphan backstop & backfill (one mechanism)

The eager triggers are the fast path; a **full-population reconcile** is the safety net and the
initial backfill, and they are the *same idempotent operation* run over "all roles" instead of a
scoped subset.

- **What it does:** for every managed role, end derived rows whose sourcing `RoleGroupMap` is no
  longer active (or whose source group's tag no longer opts in / no longer exists), and create
  any missing derived rows the invariant requires. Being delta-based, it is safe to run
  repeatedly.
- **Backstop:** run it periodically (the syncer is the natural host — a batch CLI job that
  already runs on a schedule and drains hooks inline). This self-heals any orphan a future
  bulk-write `RoleGroupMap`-ender introduces without wiring in a trigger, and — under option 2,
  where derived rows are the sole self-add/reason enforcement path — closes the window in which a
  missed trigger would silently drop enforcement.
- **Backfill:** the migration is additive (nullable column) and does **not** backfill inline.
  Instead, the first full-population reconcile after deploy materializes derived rows for all
  pre-existing role-in-tagged-group memberships. This is what makes a role already sitting in a
  SOX-tagged group become SOX-scoped at rollout rather than only on its next unrelated edit.
- **Re-manage coverage:** the backstop is also what covers the unmanaged→managed transition,
  which has no operation to hook (see the "Role re-managed" edge case). Wiring the reconcile as a
  phase of the syncer pass — after group sync commits (`syncer.py:260`) — keeps that window to a
  single cycle.

**Decision — backstop only for re-manage.** We do **not** add an eager reconcile at the
re-manage site. Since the unmanaged→managed transition happens only in the syncer, and the full
reconcile runs in that same pass, the backstop covers it with a bounded (one-cycle) window and no
extra call site. To keep that guarantee real, the implementation must **order the full-population
reconcile after group sync commits** (`syncer.py:260`) within the syncer entrypoint.

The migration adds the nullable `source_role_group_map_id` column and FK; existing rows get
`NULL`; backfill is the reconcile pass, not a data migration.

## Breaking change & operator enablement

Under the chosen option 2, existing tags stop propagating self-add/reason to member-roles until
`apply_tag_to_member_roles` is set on them. To preserve pre-2.0 behavior exactly, an operator
enables the constraint on the impacted tags at upgrade time. The **PR description must include a
runnable snippet** an Access admin can execute once, post-deploy, to set
`apply_tag_to_member_roles: true` on all tags that previously relied on the always-on propagation
(conservatively: every tag carrying any self-add or reason constraint). Sketch — final form
verified against the ORM in the PR:

```python
# One-off: preserve pre-2.0 transitive self-add/reason propagation to member-roles.
# Run inside the app context (async session). Merges the new key into each tag's
# JSON constraints without disturbing existing keys, then reconciles derived rows.
from sqlalchemy import select
from api.models import Tag  # constraint-key constants live on this class

BOOL_KEYS = {
    Tag.DISALLOW_SELF_ADD_MEMBERSHIP_CONSTRAINT_KEY,
    Tag.DISALLOW_SELF_ADD_OWNERSHIP_CONSTRAINT_KEY,
    Tag.REQUIRE_MEMBER_REASON_CONSTRAINT_KEY,
    Tag.REQUIRE_OWNER_REASON_CONSTRAINT_KEY,
}

async def enable_transitivity_for_impacted_tags(session):
    tags = (await session.scalars(select(Tag).where(Tag.deleted_at.is_(None)))).all()
    for tag in tags:
        if BOOL_KEYS & set(tag.constraints):
            tag.constraints = {**tag.constraints, Tag.APPLY_TAG_TO_MEMBER_ROLES_CONSTRAINT_KEY: True}
    await session.commit()
    # then run the full-population reconcile (backfill) to materialize derived rows
```

> Note: because `Tag.constraints` is a JSON column, reassign a new dict (as above) rather than
> mutating in place, so SQLAlchemy detects the change. If a `MutableDict` type is later adopted
> this caveat can drop.

## Testing

Tests live in the same commit as the code they validate (repo convention).

**Backend (pytest + Factory Boy):**

- Reconciler unit tests for each trigger and each cleanup path — including the bulk-ender
  triggers `UnmanageGroup` (#5) and `DeleteGroup` (#6) tearing down derived rows — plus
  idempotency (re-running produces no writes once converged).
- Parity tests: a transitively-tagged role enforces time-limit, self-add, and reason
  constraints identically to a directly-tagged role, **including** retroactive capping on
  attach and on constraint-add.
- **Gate test (option 2 behavior):** a tag *without* `apply_tag_to_member_roles` produces no
  derived rows and (after removing the live traversal) does *not* enforce self-add/reason on
  member-roles; adding the constraint makes both appear. This test is the tripwire that documents
  the breaking change — if the design reverts to option 1, this test's expectations change.
- Disabled-tag test: derived rows exist and display but constraints do not coalesce onto the
  role.
- Owner-mapping test: an owner `RoleGroupMap` does not produce derived rows.
- Unmanaged-role test: reconciler does not create derived rows on an unmanaged role.
- Re-manage test: a role that qualified while unmanaged (rows skipped) gets its derived rows
  materialized by the full-population reconcile once managed again.
- Migration test for the new column.
- Backstop/backfill test: a full-population reconcile materializes rows for pre-existing
  memberships and self-heals a deliberately-orphaned derived row.

**Frontend (vitest + React Testing Library):**

- Role view: deduped chip per tag; merged provenance tooltip across direct/app/role sources.
- Tag view: row-per-entity grouping; "Tag Source" column with the three chip types; remove
  button visible only when a Direct chip is present and removing only the direct assignment.

## Non-goals

- Propagating tags to roles that are *owners* (not members) of a tagged group.
- Multi-level transitivity (impossible — roles can't be members of roles).
- Changing how direct or app-inherited tags behave.
- Any operator-specific tag configuration (belongs in an operator's private repo).
