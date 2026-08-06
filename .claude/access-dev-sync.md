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
membership. `_sync_all_app_groups` in `api/cli.py` invokes the **`sync_group`** hook once per active
app group of every app with a plugin configured — not once per app, which is what the pre-2.0
`sync_all_groups` hook did.

Each group is **its own unit of work**: loaded, handed to its plugin, then committed or rolled back
by `invoke_app_group_lifecycle_hook`. Two reasons that boundary is per-group and should stay there:

- **Failure isolation belongs to the host.** A plugin's `sync_group` should let exceptions
  propagate; the caller counts the failure and the command exits non-zero. A plugin implementing its
  own batch loop would have to reproduce this, and the Google plugin's old loop swallowed per-group
  failures — so the job could exit 0 having reconciled nothing.
- **Advisory locks must not accumulate.** With one transaction per app, locks a plugin took via
  `ctx.lock` piled up until the app finished, so two overlapping runs iterating in different orders
  could deadlock on lock pairs. Don't batch these commits back up for throughput.

The loop holds plain column values and re-loads each group inside the iteration
(`joinedload(AppGroup.app)` + `populate_existing`). That is not incidental: a rolled-back group
expires the entire identity map, and the durable-status replay commits its own transaction, so
anything held across an iteration boundary is either unusable or stale.

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
