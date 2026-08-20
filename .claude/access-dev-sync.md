# Access — Sync and notifications

Companion to `.claude/CLAUDE.md`. Read this when working on `syncer.py` (the Okta sync cronjob),
the `sync-app-groups` cronjob, or notification-plugin code.

## Sync and authority

`syncer.py` is written to run as a cronjob. It is fully `async` like the rest of
the app (`async def sync_groups(act_as_authority)`, etc.), and uses `db.session.run_sync(...)`
to run sync-only ORM work on the session's own greenlet. Behavior depends on `act_as_authority`
and whether a group `is_managed` (`act_authoritatively = act_as_authority and is_managed`):

- **Managed group + `act_as_authority=True`**: Access DB is authoritative. Members/owners in
  Okta but not in the DB are removed from Okta. Members/owners in the DB but not in Okta are
  pushed to Okta.
- **Unmanaged group, or `act_as_authority=False`**: Okta is authoritative. Changes in Okta
  are reflected into the DB; DB-only records are removed.

## App group lifecycle sync (`access sync-app-groups`)

A separate cronjob from `syncer.py`, driving the app-group-lifecycle plugin hooks rather than Okta
membership. `_sync_all_app_groups` in `api/cli.py` invokes the `sync_group` hook once per active app
group, and **each group is its own unit of work** — loaded, handed to its plugin, then committed or
rolled back by `invoke_app_group_lifecycle_hook`.

Keep that boundary per-group. Batching it back up for throughput would put failure isolation on the
plugins, where a plugin's own loop swallowing failures lets the job exit 0 having reconciled nothing,
and would let advisory locks accumulate across a batch, which overlapping runs iterating in different
orders can deadlock on.

`examples/kubernetes/cron-job-sync-app-groups.yaml` is the reference deployment, and its comments
carry the operator-facing version of the reasoning below (why `concurrencyPolicy: Forbid`, why
`backoffLimit: 0`, why the sweep is hourly). Keep the two in step when this behavior changes.

Note the loop holds plain column values and re-loads each group inside the iteration. That is
deliberate, not incidental: a rolled-back group expires the entire identity map, so anything held
across an iteration boundary is unusable — the same hazard described under `lazy="raise_on_sql"` in
`.claude/CLAUDE.md`.

This cronjob is also the recovery path for the request path's post-response lifecycle drain
(`api/operations/_lifecycle_fan_out.py`), which can lose a fire — a worker killed between a response
and its drain, or a request that fails after queuing one. How much it actually recovers is a
property of the plugin, not of the host, so be careful not to overstate it:

- `sync_group` is **optional**. `verify_async_impls` only checks that hooks a plugin *does*
  implement are `async def`, so a plugin omitting it loads clean and `_sync_all_app_groups` prints
  ✓ for every group and exits 0. Nothing in the hookspec makes `sync_group` subsume the event
  hooks; the Google example plugin routes it to the same `_reconcile()`, which is where
  "re-converges it" comes from, and that is a property of that implementation.
- **Deletes are structurally out of reach.** The sweep filters `AppGroup.deleted_at.is_(None)`
  (`api/cli.py`), so a soft-deleted group is invisible to it. A dropped `group_deleted` leaves the
  external group and its members alive with nothing scanning for them. This predates deferral — an
  inline hook that raised was already unrecoverable — but the drain widens the window.
- **Membership removals are deltas.** `group_members_removed` carries who lost access; `sync_group`
  sees only who currently has it. A plugin whose external system takes revoke calls rather than a
  desired-state write cannot recover a lost removal from a sweep.

So: if this job is disabled or failing, plugins that reconcile full state stop converging, and
plugins that don't were never covered. The contract this places on plugin authors is on
`AppGroupLifecyclePluginSpec`.

## Notification cadence

Relevant when working on notification plugin code. Cadence is controlled by the cronjob
schedules in the operator's deployment config — check there for the authoritative schedule, as
these are subject to change. The cadence below is an illustrative example of a typical
configuration; the notification bot DMs:

- **Members:** 1 week and 1 day before direct membership expires (rounded to Friday before if
  expiry falls on a weekend). Users are **not** notified about role-granted access expiring.
- **Group owners:** 1 week and 2 weeks before member/role access in their group expires.
  Sent on Mondays only.
- **Role owners:** 1 day and 1 week before a role-to-group mapping expires.
