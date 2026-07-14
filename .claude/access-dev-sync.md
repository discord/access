# Access — Sync and notifications

Companion to `.claude/CLAUDE.md`. Read this when working on `syncer.py` (the Okta sync cronjob)
or on notification-plugin code.

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
