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

### Key UX/behavior changes

1. **Time-limit constraints can now be transitive** — the new capability, closing the one gap
   (access expiration) that did not previously extend to member-roles.
2. **Transitivity is on by default, opt-out per tag** — `apply_tag_to_member_roles` defaults to
   `true` for new and existing tags; set it `false` to exclude a tag. Not a breaking change for
   self-add/reason (which already propagate today); see the default-on decision.
3. **Transitive tags are explicit in the UI** — they render alongside direct tags on the role and
   in the tag's "Groups with Tag" list, with clear source/rationale (direct / via app / derived
   from membership in {groups}).

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
6. **Default-on, opt-out (KEY DECISION):** `apply_tag_to_member_roles` is the single switch that
   **gates all transitive application** — self-add, reason, **and** time limits — and it
   **defaults to `true`** consistently for **both new and existing tags** (existing tags via a
   one-time backfill of the key; new tags via a create-time default). An operator opts a specific
   tag *out* by setting it `false`, which suppresses **all** transitive application for that tag
   (the unified-gate reading — see decision 7). Because the default is on, this is **not a
   breaking change** for the four constraints that already propagate today (self-add ×2, reason
   ×2 — the defaults preserve current behavior); its new effect is to extend the two **time-limit**
   constraints to member-roles by default, closing the compliance gap. See "Existing role
   propagation and the default-on decision".
7. **Unified gate — opt-out is all-or-nothing (KEY DECISION, open to team pushback):** setting
   `apply_tag_to_member_roles: false` on a tag stops *every* transitive constraint for that tag,
   not just the new time-limit extension. This keeps a single code path (derived rows are the sole
   mechanism; the old live traversal is removed). The alternative — keep the live traversal so
   opt-out suppresses only time limits while self-add/reason stay unconditionally transitive — is
   documented as a one-edit variant in "Existing role propagation and the default-on decision" in
   case the team prefers it.
8. **Rollout staging:** the disruptive part of enabling time-limit transitivity is the
   **retroactive capping** it triggers, not flipping the flag. So the initial full-population
   capping reconcile is an **operator-triggered step**, decoupled from the key backfill, giving a
   window to set `false` on any tag whose capping should be deferred. See "Orphan backstop &
   backfill".

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

## Existing role propagation and the default-on decision

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

`apply_tag_to_member_roles` is the **single gate for all transitive application**, and it
**defaults to `true`** for both new and existing tags (decision 6). Concretely:

- The reconciler creates a derived `OktaGroupTagMap` row on a member-role **iff** the source
  group's tag has `apply_tag_to_member_roles` truthy. That row carries the tag's *entire*
  constraint set (it points at the `Tag`), so once it exists, self-add, reason, and time-limit
  constraints all apply to the role through the role's own `active_group_tags` — via the exact
  same code that enforces a directly-applied tag.
- **We remove** the existing associated-group live-traversal blocks in
  `CheckForSelfAdd.execute_for_group` (lines 89-115) and `CheckForReason.execute_for_group`
  (lines 71-95). Derived rows now carry self-add/reason to member-roles, so those blocks are
  redundant; keeping them would also make opt-out (`false`) ineffective for self-add/reason,
  because the live traversal ignores the flag. This is the **unified gate** (decision 7).

**Why default-on makes this non-breaking:** because every tag defaults to `true`, the four
constraints that already propagate today (self-add ×2, reason ×2) keep propagating — now through
derived rows instead of the live traversal, but with the same observable result. No operator
action is required to preserve current behavior. The genuinely new effect is that the two
**time-limit** constraints now extend to member-roles by default. That is a real enforcement
change (it can shorten access), so operators should be told — but it is the intended
gap-closure, not a regression. Whether it warrants a major version bump is an operator judgment;
it is *not* the "your tags silently stop enforcing" break the earlier opt-in framing carried.

### Opt-out semantics and the one-edit variant

Under the unified gate (decision 7), `apply_tag_to_member_roles: false` suppresses **all**
transitive application for that tag — self-add, reason, and time limits alike. That is the plain
meaning of "this tag does not apply to member roles," and it is the recommended reading.

If the team instead wants opt-out to suppress **only** the new time-limit extension while
self-add/reason stay unconditionally transitive (as they are today), that is a **single-edit
variant**: **keep** the associated-group live-traversal blocks rather than removing them. Then
self-add/reason propagate unconditionally regardless of the flag, derived rows redundantly
re-cover them for opted-in tags (idempotent), and the flag governs only time-limit propagation +
materialized visibility. Keep those two blocks factored/co-located so this stays a one-paragraph
change either way.

> **Enforcement-path note (unified gate):** removing the live traversal makes derived rows the
> *sole* enforcement path for self-add/reason on member-roles. A missing derived row (an orphan or
> a trigger gap) would silently stop enforcement — which is the primary reason the orphan backstop
> below is required, not optional.

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

**Default value.** Per decisions 6–7 the constraint defaults to `true`. New tags are created
with `apply_tag_to_member_roles: true` in their constraints unless the operator sets it `false`;
existing tags receive the key via a one-time backfill (see "Orphan backstop & backfill"). The
reconciler and `coalesce_constraints` keep the standard `absent → off` convention every other
constraint follows — the default-on behavior comes from the key being *present and true*, never
from special-casing a missing key. (A tag that predates the backfill and hasn't been touched
therefore reads as off until backfilled, which is why the backfill is part of rollout rather than
optional.)

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

Two rollout steps, kept separate on purpose:

1. **Schema migration** — Alembic adds the nullable `source_role_group_map_id` column and its FK
   to `okta_group_tag_map`. Additive and backwards-compatible: existing rows get `NULL`.
2. **Constraint-key backfill** — a data step sets `apply_tag_to_member_roles: true` on existing
   tags' `constraints` (the default-on decision). This is *enablement only*; it materializes no
   rows and caps nothing by itself.

The disruptive **derived-row materialization + retroactive capping** is a *third*, separately
operator-triggered step (the full-population reconcile), so operators get a window between the
backfill and the capping to set `false` on any tag whose capping should be deferred. See "Orphan
backstop & backfill".

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
  reach the role under the unified gate.
- Retroactive time-limit capping fires via the reconciler's `ModifyGroupsTimeLimit` call — the
  new enforcement this feature adds, since time limits did not previously propagate in this
  direction.
- **Disabled-tag suppression is free:** a derived row points at the same `Tag`. When `T` is
  disabled, `coalesce_constraints` skips it (its `tag.enabled` check) and `enabled_active_tag`
  excludes it, so `T`'s constraints do not coalesce onto the role. The tag still displays on the
  role, inert — exactly like a disabled direct tag on a group.

The one enforcement-code change is the **removal** (unified gate) of the associated-group
live-traversal blocks in `CheckForSelfAdd.execute_for_group` (89-115) and
`CheckForReason.execute_for_group` (71-95), as described in the default-on decision. Keep those
blocks factored/co-located so the keep-live-traversal variant stays a one-paragraph change.
`execute_for_role` in both classes is a *different* direction (checking target-group tags when a
role is attached to groups) and is **not** touched by this feature.

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

The eager triggers are the fast path; a **full-population reconcile** is the safety net, the
initial materialization, and the re-manage coverage — all the *same idempotent operation* run
over "all roles" instead of a scoped subset.

- **What it does:** for every managed role, end derived rows whose sourcing `RoleGroupMap` is no
  longer active (or whose source group's tag no longer opts in / no longer exists), and create
  any missing derived rows the invariant requires (retroactively capping via
  `ModifyGroupsTimeLimit` on each insert). Being delta-based, it is safe to run repeatedly.
- **Backstop:** run it periodically (the syncer is the natural host — a batch CLI job that
  already runs on a schedule and drains hooks inline). This self-heals any orphan a future
  bulk-write `RoleGroupMap`-ender introduces without wiring in a trigger, and — under the unified
  gate, where derived rows are the sole self-add/reason enforcement path — closes the window in
  which a missed trigger would silently drop enforcement.
- **Initial materialization is operator-triggered (staging lever).** The default-on key backfill
  (migration step 2) only sets the flag; it is this reconcile that first materializes derived
  rows and performs the org-wide retroactive capping. Running it as a deliberate operator step —
  *after* the key backfill — gives the staging window: set `apply_tag_to_member_roles: false` on
  any tag whose capping should be deferred, run the reconcile, then flip deferred tags back to
  `true` later (each flip caps that tag's member-roles via the `ModifyTag` trigger, spreading the
  load instead of one org-wide event). This is what closes the compliance gap at rollout on the
  operator's schedule rather than in a single surprise mutation.
- **Re-manage coverage:** the same reconcile covers the unmanaged→managed transition, which has
  no operation to hook (see the "Role re-managed" edge case). Wiring the *periodic* reconcile as a
  phase of the syncer pass — after group sync commits (`syncer.py:260`) — keeps that window to a
  single cycle.

**Decision — backstop only for re-manage.** We do **not** add an eager reconcile at the
re-manage site. Since the unmanaged→managed transition happens only in the syncer, and the full
reconcile runs in that same pass, the backstop covers it with a bounded (one-cycle) window and no
extra call site. To keep that guarantee real, the implementation must **order the full-population
reconcile after group sync commits** (`syncer.py:260`) within the syncer entrypoint.

## Operator rollout & opt-out

Because the constraint defaults to `true` (decision 6), operators need **no action to preserve**
today's self-add/reason propagation — it carries over automatically once the key backfill runs.
The operator's levers are all about the *new* time-limit extension and its capping:

- **Defer or exclude a tag:** set `apply_tag_to_member_roles: false` on it. This is the precise,
  per-tag lever — it suppresses transitivity for that tag only and leaves its direct/app-group
  enforcement fully intact. **Do not** disable the tag to achieve this: `enabled` is a blunt
  switch that turns off *all* of the tag's enforcement everywhere (including on the directly
  tagged groups), and toggling it is not wired to the reconciler anyway (trigger #4 reacts to the
  constraint value, not `enabled`).
- **Stage the capping:** set `false` on defer-tags *before* triggering the initial reconcile,
  then flip them to `true` on your own schedule.

The **PR description must include a runnable snippet** an Access admin can adapt — both the
default-on backfill (idempotent) and an opt-out helper for chosen tags. Sketch — final form
verified against the ORM in the PR:

```python
# Default-on key backfill (migration step 2): set apply_tag_to_member_roles=true on every
# existing tag. Enablement only — materializes no rows and caps nothing on its own. Idempotent.
# Run inside the app context (async session).
from sqlalchemy import select
from api.models import Tag  # constraint-key constant lives on this class

KEY = Tag.APPLY_TAG_TO_MEMBER_ROLES_CONSTRAINT_KEY

async def backfill_default_on(session):
    tags = (await session.scalars(select(Tag).where(Tag.deleted_at.is_(None)))).all()
    for tag in tags:
        if KEY not in tag.constraints:  # respect any operator-set value already present
            tag.constraints = {**tag.constraints, KEY: True}
    await session.commit()

# Opt-out helper (staging): set apply_tag_to_member_roles=false on chosen tags BEFORE triggering
# the initial full-population reconcile, to defer their capping. Reassigns the dict so SQLAlchemy
# detects the change (Tag.constraints is a plain JSON column, not a MutableDict).
async def opt_out(session, tag_names: set[str]):
    tags = (await session.scalars(
        select(Tag).where(Tag.deleted_at.is_(None)).where(Tag.name.in_(tag_names))
    )).all()
    for tag in tags:
        tag.constraints = {**tag.constraints, KEY: False}
    await session.commit()

# Then, as a deliberate step, run the full-population reconcile to materialize derived rows
# and retroactively cap. Flip any deferred tags back to True later to cap them on your schedule.
```

> Note: because `Tag.constraints` is a plain JSON column, reassign a new dict (as above) rather
> than mutating in place, so SQLAlchemy detects the change. If a `MutableDict` type is later
> adopted this caveat can drop.

## Testing

Tests live in the same commit as the code they validate (repo convention).

**Backend (pytest + Factory Boy):**

- Reconciler unit tests for each trigger and each cleanup path — including the bulk-ender
  triggers `UnmanageGroup` (#5) and `DeleteGroup` (#6) tearing down derived rows — plus
  idempotency (re-running produces no writes once converged).
- Parity tests: a transitively-tagged role enforces time-limit, self-add, and reason
  constraints identically to a directly-tagged role, **including** retroactive capping on
  attach and on constraint-add.
- **Default-on test:** a newly created tag has `apply_tag_to_member_roles: true` by default and
  propagates to member-roles without operator action; the backfill sets it on existing tags.
- **Opt-out / unified-gate test:** a tag with `apply_tag_to_member_roles: false` produces no
  derived rows and (with the live traversal removed) enforces *neither* time limits *nor*
  self-add/reason on member-roles. This is the tripwire for the unified-gate decision (decision
  7); if the team chooses the keep-live-traversal variant, this test's self-add/reason
  expectations change while the time-limit ones stay.
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
