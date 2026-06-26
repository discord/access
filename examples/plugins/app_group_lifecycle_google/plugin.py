"""
App Group Lifecycle Google Group Management Plugin

Creates, updates, and deletes Google groups for Access groups and links them via
Okta group push. All create/update/sync paths run one idempotent reconcile.
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from api.models import App, AppGroup
from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginStatusProperty,
    get_config_value,
    get_status_value,
    hookimpl,
    set_config_value,
    set_status_value,
)
from api.services import okta  # used by the push-mapping/discovery helpers

PLUGIN_ID = "google_group_manager"

GOOGLE_GROUP_API_SCOPES = ["https://www.googleapis.com/auth/cloud-identity.groups"]

ENV_OKTA_APP_ID = "GOOGLE_WORKSPACE_OKTA_APP_ID"
ENV_DOMAIN = "GOOGLE_WORKSPACE_DOMAIN"
ENV_CUSTOMER_ID = "GOOGLE_WORKSPACE_CUSTOMER_ID"

# Required label marking a Cloud Identity group as a Google Workspace (discussion) group.
GROUP_DISCUSSION_FORUM_LABEL = "cloudidentity.googleapis.com/groups.discussion_forum"

# App config keys
CONFIG_ENABLED = "enabled"
CONFIG_EMAIL_PATTERN = "email_pattern"
# Group config keys
CONFIG_EMAIL = "email"
CONFIG_DISPLAY_NAME = "display_name"
# Group status keys
STATUS_PUSH_MAPPING_ID = "push_mapping_id"
STATUS_GOOGLE_GROUP_ID = "google_group_id"
STATUS_SYNC_STATUS = "sync_status"
STATUS_SYNC_ERROR = "sync_error"
STATUS_LAST_SYNCED_AT = "last_synced_at"
# sync_status values
SYNC_SYNCED = "synced"
SYNC_PENDING = "pending"
SYNC_ERROR = "error"

OKTA_GOOGLE_GROUP_PROFILE_FIELD_EMAIL = "googleGroupEmail"

# Conservative subset of Google group local-part rules: lowercase alphanumerics
# plus . _ - internally; must start and end alphanumeric.
GOOGLE_LOCAL_PART_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$")

logger = logging.getLogger(__name__)


def _is_group_absent_error(error: HttpError) -> bool:
    """Whether a Cloud Identity error means the group is not visible to us.

    The Groups API returns 403 (PERMISSION_DENIED, "...or it may not exist") rather than
    404 for a group the caller can't see -- including one that simply doesn't exist yet.
    Treating both as "absent" lets reconcile create the group instead of erroring; a
    genuine permission problem then surfaces on the subsequent create call."""
    return getattr(getattr(error, "resp", None), "status", None) in (403, 404)


class AmbiguousOktaTargetError(Exception):
    """More than one Okta target group matches a Google group email, so a push mapping cannot be
    created unambiguously. This is a misconfiguration (e.g. a stale + re-imported target sharing
    the same googleGroupEmail) that will not self-heal, so it is surfaced as a sync error rather
    than conflated with the not-yet-imported case (which simply defers)."""


class GoogleGroupManagerPlugin:
    """Manages the Google-group lifecycle for Access groups."""

    def __init__(self) -> None:
        okta_app_id = os.environ.get(ENV_OKTA_APP_ID)
        if not okta_app_id:
            raise ValueError(f"{ENV_OKTA_APP_ID} environment variable is required")
        self._okta_app_id = okta_app_id

        domain = os.environ.get(ENV_DOMAIN)
        if not domain:
            raise ValueError(f"{ENV_DOMAIN} environment variable is required")
        self._domain = domain

        customer_id = os.environ.get(ENV_CUSTOMER_ID)
        if not customer_id:
            raise ValueError(f"{ENV_CUSTOMER_ID} environment variable is required")
        self._customer_id = customer_id

        credentials, _ = default(scopes=GOOGLE_GROUP_API_SCOPES)
        self._groups_api = build("cloudidentity", "v1", credentials=credentials).groups()

    # ---- Helpers ----

    def _is_enabled(self, group: AppGroup) -> bool:
        return bool(get_config_value(group.app, CONFIG_ENABLED, PLUGIN_ID, False))

    def _full_email(self, prefix: str) -> str:
        return f"{prefix}@{self._domain}"

    def _prefix_from_email(self, email: str) -> str | None:
        suffix = f"@{self._domain}"
        if not email.endswith(suffix):
            return None
        return email[: -len(suffix)]

    def _validate_email_against_pattern(self, prefix: str, pattern: str | None) -> str | None:
        """Return an error message if the prefix violates the pattern, else None."""
        if not pattern:
            return None
        try:
            if re.search(pattern, prefix) is None:
                return f"The email prefix '{prefix}' does not match the required pattern '{pattern}'"
        except re.error:
            # A malformed pattern is reported at app-config validation; ignore here.
            return None
        return None

    def _group_config(self, group: AppGroup) -> tuple[str, str] | None:
        """(email_prefix, display_name) if both present, else None."""
        email = get_config_value(group, CONFIG_EMAIL, PLUGIN_ID)
        display_name = get_config_value(group, CONFIG_DISPLAY_NAME, PLUGIN_ID)
        if email and display_name:
            return email, display_name
        return None

    # ---- Metadata ----

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        return AppGroupLifecyclePluginMetadata(
            id=PLUGIN_ID,
            display_name="Google Group Management",
            description=f"Creates and manages Google groups in the domain {self._domain}.",
        )

    # ---- Config schema ----

    @hookimpl
    def get_plugin_app_config_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None
        return {
            CONFIG_ENABLED: AppGroupLifecyclePluginConfigProperty(
                display_name="Enabled?",
                help_text="Enable automatic Google group management for this app",
                type="boolean",
                default_value=True,
                required=True,
            ),
            CONFIG_EMAIL_PATTERN: AppGroupLifecyclePluginConfigProperty(
                display_name="Email Prefix Pattern",
                help_text=(
                    "Optional regex that each group's email prefix must match, "
                    f"e.g. ^gcp- to require addresses like gcp-security@{self._domain}"
                ),
                type="text",
                required=False,
            ),
        }

    @hookimpl
    def get_plugin_group_config_properties(
        self, plugin_id: str | None, app_config: dict[str, Any]
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        # The email prefix must satisfy the Google-safe charset and, if the app configures
        # one, the app's email_pattern. Surface both as client-side validation rules so the
        # UI can reject an out-of-pattern prefix before submitting; the backend remains
        # authoritative (it enforces the same in validate_plugin_group_config).
        email_patterns = [
            {
                "regex": GOOGLE_LOCAL_PART_RE.pattern,
                "message": "Only lowercase letters, digits, and . _ - ; must start and end with a letter or digit",
            }
        ]
        app_email_pattern = app_config.get(CONFIG_EMAIL_PATTERN) if isinstance(app_config, dict) else None
        if app_email_pattern:
            email_patterns.append(
                {"regex": app_email_pattern, "message": f"Must match this app's email pattern: {app_email_pattern}"}
            )

        return {
            CONFIG_EMAIL: AppGroupLifecyclePluginConfigProperty(
                display_name="Google Group Email Prefix",
                help_text=(
                    f"The local part of the address; the group will be prefix@{self._domain}. "
                    "Cannot be changed after the group is created."
                ),
                type="text",
                required=True,
                immutable=True,
                validation={"patterns": email_patterns},
            ),
            CONFIG_DISPLAY_NAME: AppGroupLifecyclePluginConfigProperty(
                display_name="Google Group Display Name",
                help_text="The display name of the linked Google group",
                type="text",
                required=True,
            ),
        }

    # ---- Status schema ----

    @hookimpl
    def get_plugin_app_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None
        # This plugin has no app-level status; sync state is tracked per group.
        return {}

    @hookimpl
    def get_plugin_group_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None
        return {
            STATUS_PUSH_MAPPING_ID: AppGroupLifecyclePluginStatusProperty(
                display_name="Okta Push Mapping ID", type="text"
            ),
            STATUS_GOOGLE_GROUP_ID: AppGroupLifecyclePluginStatusProperty(display_name="Google Group ID", type="text"),
            STATUS_SYNC_STATUS: AppGroupLifecyclePluginStatusProperty(
                display_name="Sync Status", help_text="synced, pending, or error", type="text"
            ),
            STATUS_SYNC_ERROR: AppGroupLifecyclePluginStatusProperty(display_name="Sync Error", type="text"),
            STATUS_LAST_SYNCED_AT: AppGroupLifecyclePluginStatusProperty(display_name="Last Synced", type="date"),
        }

    # ---- Validation ----

    @hookimpl
    def validate_plugin_app_config(self, config: dict[str, Any], plugin_id: str | None) -> dict[str, str] | None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None
        errors: dict[str, str] = {}
        if CONFIG_ENABLED not in config:
            errors[CONFIG_ENABLED] = "The 'enabled' field is required"
        elif not isinstance(config[CONFIG_ENABLED], bool):
            errors[CONFIG_ENABLED] = "The 'enabled' field must be a boolean"

        pattern = config.get(CONFIG_EMAIL_PATTERN)
        if pattern:
            if not isinstance(pattern, str):
                errors[CONFIG_EMAIL_PATTERN] = "The 'email_pattern' field must be a string"
            else:
                try:
                    re.compile(pattern)
                except re.error as e:
                    errors[CONFIG_EMAIL_PATTERN] = f"Invalid regex: {e}"
        return errors

    @hookimpl
    def validate_plugin_group_config(
        self, config: dict[str, Any], app_config: dict[str, Any], plugin_id: str | None
    ) -> dict[str, str] | None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None
        errors: dict[str, str] = {}

        display_name = config.get(CONFIG_DISPLAY_NAME)
        if CONFIG_DISPLAY_NAME not in config:
            errors[CONFIG_DISPLAY_NAME] = "The 'display_name' field is required"
        elif not isinstance(display_name, str) or not display_name:
            errors[CONFIG_DISPLAY_NAME] = "The 'display_name' field must be a non-empty string"

        email = config.get(CONFIG_EMAIL)
        if CONFIG_EMAIL not in config:
            errors[CONFIG_EMAIL] = "The 'email' field is required"
        elif not isinstance(email, str):
            errors[CONFIG_EMAIL] = "The 'email' field must be a string"
        elif not GOOGLE_LOCAL_PART_RE.match(email):
            errors[CONFIG_EMAIL] = (
                "The 'email' prefix may contain only lowercase letters, digits, and . _ - "
                "and must start and end with a letter or digit"
            )
        else:
            # The email is a valid prefix; also enforce the app's optional email_pattern
            # here so a violation is reported synchronously at create/update (a 400),
            # not only later during reconciliation. app_config is the app-level
            # configuration for this plugin (empty if the app has none).
            pattern_error = self._validate_email_against_pattern(
                email, app_config.get(CONFIG_EMAIL_PATTERN) if isinstance(app_config, dict) else None
            )
            if pattern_error:
                errors[CONFIG_EMAIL] = pattern_error

        return errors

    # ---- Google API wrappers (Cloud Identity Groups API) ----

    def _resource_name(self, google_group_id: str) -> str:
        return f"groups/{google_group_id}"

    def _create_google_group(self, prefix: str, display_name: str, description: str) -> str:
        body = {
            "parent": f"customers/{self._customer_id}",
            "groupKey": {"id": self._full_email(prefix)},
            "displayName": display_name,
            "description": description or "",
            "labels": {GROUP_DISCUSSION_FORUM_LABEL: ""},
        }
        # initialGroupConfig=EMPTY: the admin-role service account creates the group without
        # being added as an owner; Okta group push owns membership and ownership.
        operation = self._groups_api.create(body=body, initialGroupConfig="EMPTY").execute()
        created = operation.get("response") or {}
        name = created.get("name")
        if not name:
            raise ValueError(f"Google group creation returned no resource name: {operation}")
        return name.split("/", 1)[1]

    def _get_google_group(self, google_group_id: str) -> dict[str, Any]:
        return self._groups_api.get(name=self._resource_name(google_group_id)).execute()

    def _patch_google_group(
        self, google_group_id: str, *, display_name: str | None = None, description: str | None = None
    ) -> None:
        """Patch a Google group's mutable properties. groupKey (email) is immutable, so only
        displayName/description are patchable; pass only the fields to change. No-op if none."""
        body: dict[str, Any] = {}
        if display_name is not None:
            body["displayName"] = display_name
        if description is not None:
            body["description"] = description
        if not body:
            return
        update_mask = ",".join(sorted(body))
        self._groups_api.patch(name=self._resource_name(google_group_id), body=body, updateMask=update_mask).execute()

    def _delete_google_group(self, google_group_id: str) -> None:
        self._groups_api.delete(name=self._resource_name(google_group_id)).execute()

    def _lookup_google_group_id(self, email: str) -> str | None:
        """Resolve an email to its bare Cloud Identity group id, or None if no such group."""
        try:
            result = self._groups_api.lookup(groupKey_id=email).execute()
        except HttpError as e:
            if _is_group_absent_error(e):
                return None
            raise
        name = result.get("name")
        return name.split("/", 1)[1] if name else None

    # ---- Okta push-mapping + discovery ----

    def _okta_target_group_id(self, email: str) -> str | None:
        """Find the Okta-imported target group for a Google group email, if present.
        The email is the stable join key (the Directory numeric id Cloud Identity does
        not reproduce, but Okta's googleGroupEmail attribute is immutable)."""
        query = f'type eq "APP_GROUP" and profile.{OKTA_GOOGLE_GROUP_PROFILE_FIELD_EMAIL} eq "{email}"'
        matches = okta.list_groups(query_params={"search": query})
        if len(matches) > 1:
            raise AmbiguousOktaTargetError(
                f"{len(matches)} Okta target groups carry googleGroupEmail '{email}'; "
                "cannot create a push mapping unambiguously"
            )
        if not matches:
            return None
        return str(matches[0].group.id)

    def _create_push_mapping(self, group: AppGroup, email: str) -> bool:
        """Create the Okta push mapping. Returns False (defer) if Okta has not
        imported the target group yet."""
        target_group_id = self._okta_target_group_id(email)
        if target_group_id is None:
            return False
        result = okta.create_group_push_mapping(
            appId=self._okta_app_id, sourceGroupId=group.id, targetGroupId=target_group_id
        )
        mapping_id = result.get("id")
        if not mapping_id:
            raise ValueError(f"Okta push mapping creation returned no id: {result}")
        set_status_value(group, STATUS_PUSH_MAPPING_ID, mapping_id, PLUGIN_ID)
        return True

    def _discover_existing_link(self, group: AppGroup) -> dict[str, Any] | None:
        """Find an existing push mapping for this Access group and recover the linked
        Google group email from the Okta target group profile. Returns None if no link
        exists. The caller resolves the email to a Cloud Identity id via lookup."""
        mappings = okta.list_group_push_mappings(self._okta_app_id)
        mapping = next((m for m in mappings if m.get("sourceGroupId") == group.id), None)
        if mapping is None:
            logger.debug(f"No mapping found for group {group.name}.")
            return None

        target_group_id = mapping.get("targetGroupId")
        if not target_group_id:
            logger.debug(f"Failed to get target group ID mapped to {group.name}. Mapping:\n{mapping}")
            return None

        profile = okta.get_group(target_group_id).group.profile
        email = getattr(profile, OKTA_GOOGLE_GROUP_PROFILE_FIELD_EMAIL, None)
        if not email:
            logger.debug(
                f"Google group email could not be resolved for target group mapped to {group.name}.\n"
                f"Target group {target_group_id} has profile:\n{profile}"
            )
            return None

        return {"email": str(email), "push_mapping_id": mapping.get("id")}

    # ---- Status setters ----

    def _mark(self, session: Session, group: AppGroup, status: str, error: str | None = None) -> None:
        set_status_value(group, STATUS_SYNC_STATUS, status, PLUGIN_ID)
        set_status_value(group, STATUS_SYNC_ERROR, error, PLUGIN_ID)
        if status == SYNC_SYNCED:
            set_status_value(group, STATUS_LAST_SYNCED_AT, datetime.utcnow().isoformat(), PLUGIN_ID)
        session.add(group)
        session.commit()

    # ---- Reconcile ----

    def _owned_group_id(self, group: AppGroup) -> str | None:
        """The Google group id this Access group already owns (claimed on a prior reconcile),
        if it still exists. The recorded id is an ownership token -- it is written only after
        the ownership check passes (see _claim_group_id) -- so a live cached id needs no
        re-check. Clears the cached id and returns None if the group was deleted out of band,
        so the caller re-resolves/recreates. Returns None when nothing is cached."""
        cached = get_status_value(group, STATUS_GOOGLE_GROUP_ID, PLUGIN_ID)
        if not cached:
            return None
        try:
            self._get_google_group(cached)
            return cached
        except HttpError as e:
            if not _is_group_absent_error(e):
                raise
            logger.info(f"Cached Google group id {cached} for {group.name} is gone; clearing and re-resolving.")
            set_status_value(group, STATUS_GOOGLE_GROUP_ID, None, PLUGIN_ID)
            return None

    def _lock_claim(self, session: Session, candidate_id: str) -> None:
        """Serialize concurrent claims of the same Google group, closing the check-then-claim race
        in _claim_group_id. Takes a Postgres transaction-level advisory lock keyed on the candidate
        id (auto-released at commit/rollback), so a second reconcile claiming the same id blocks
        until the first commits and can then observe it as owned. A no-op on non-Postgres backends
        (e.g. the SQLite test DB), where the relevant sync paths are single-writer."""
        bind = session.get_bind()
        if bind is None or bind.dialect.name != "postgresql":
            return
        # hashtextextended maps the id to the bigint the advisory-lock functions take; key
        # collisions only cause extra (harmless) serialization.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": candidate_id})

    def _claim_group_id(
        self, session: Session, group: AppGroup, candidate_id: str, display_email: str | None = None
    ) -> str | None:
        """Record candidate_id as this group's owned Google group, but ONLY after confirming no
        other Access group already owns it -- refusing (and marking SYNC_ERROR) rather than
        clobbering / double-linking a group owned elsewhere. Returns the id on success, or None
        when refused. A no-op confirmation when we already hold this id.

        Persisting the id is gated on the ownership check (not the reverse) so a refused group
        never carries another group's id into its status, where group_deleted would later act on
        it. The check and the claim are serialized by an advisory lock on the candidate id (see
        _lock_claim) so two concurrent reconciles can't both pass the check for the same group."""
        if get_status_value(group, STATUS_GOOGLE_GROUP_ID, PLUGIN_ID) == candidate_id:
            return candidate_id
        self._lock_claim(session, candidate_id)
        owner = self._google_group_owner(session, group, candidate_id)
        if owner is not None:
            self._mark(
                session,
                group,
                SYNC_ERROR,
                f"Google group {display_email or candidate_id} is already managed by Access group "
                f"'{owner.name}'; refusing to adopt it.",
            )
            return None
        set_status_value(group, STATUS_GOOGLE_GROUP_ID, candidate_id, PLUGIN_ID)
        return candidate_id

    def _email_from_status(self, group: AppGroup) -> str | None:
        """Recover the group email from a cached id when the Access-side config is absent
        (adoption path). Returns the full email, or None when there is no cached id or the cached
        group was deleted out of band (mirrors _owned_group_id's absent-error handling, so a
        vanished group defers rather than hard-erroring reconcile)."""
        google_group_id = get_status_value(group, STATUS_GOOGLE_GROUP_ID, PLUGIN_ID)
        if not google_group_id:
            return None
        try:
            live = self._get_google_group(google_group_id)
        except HttpError as e:
            if not _is_group_absent_error(e):
                raise
            logger.info(f"Cached Google group id {google_group_id} for {group.name} is gone; cannot recover email.")
            return None
        return (live.get("groupKey") or {}).get("id")

    def _google_group_owner(self, session: Session, group: AppGroup, google_group_id: str) -> AppGroup | None:
        """Another active Access group that already owns this Google group -- i.e. records its id
        in status. Used to refuse adopting (and clobbering / double-linking) a Google group already
        owned elsewhere.

        Ownership keys on the recorded google_group_id ALONE, not on whether a push mapping exists
        yet: the id is recorded only after this same ownership check passes (see _claim_group_id),
        while the push mapping is created later and may defer until Okta imports the group. Were we
        to also require a push mapping, a second group reconciling during that window would not see
        the real owner and would double-claim the group.

        Access state is the source of truth here (not Okta's imported target groups, which lag
        behind and may not be queryable yet). The search spans every app configured with this
        plugin, not just this group's app: one Google Workspace can back several Access apps, and
        those apps all set this same plugin id, so a Google group can be owned by a group in any
        of them.

        The id predicate is pushed into SQL (a JSON path lookup on the stored status), so this
        stays a point lookup -- not a scan of every plugin-managed group on each reconcile. It also
        only runs for a group that isn't yet linked."""
        google_group_id_path = (PLUGIN_ID, "status", STATUS_GOOGLE_GROUP_ID)
        return session.scalars(
            select(AppGroup)
            .join(App, AppGroup.app_id == App.id)
            .where(App.app_group_lifecycle_plugin == PLUGIN_ID)
            .where(App.deleted_at.is_(None))
            .where(AppGroup.id != group.id)
            .where(AppGroup.deleted_at.is_(None))
            .where(AppGroup.plugin_data[google_group_id_path].as_string() == google_group_id)
            .limit(1)
        ).first()

    def _reconcile(self, session: Session, group: AppGroup) -> None:
        """Idempotent: resolve/adopt/create the Google group, enforce its properties,
        link via Okta push, and record sync status. Commits sync_status inside the hook
        so it survives the host's post-hook rollback on error."""
        if not self._is_enabled(group):
            return

        try:
            config = self._group_config(group)
            email = self._full_email(config[0]) if config is not None else None

            # A Google group id we already own (claimed on a prior reconcile), if still live.
            google_group_id = self._owned_group_id(group)

            if google_group_id is None:
                # Not yet owned. Find a candidate -- an existing Google group at our email, or one
                # behind an out-of-band Okta link -- then CLAIM it: record it only after confirming
                # no other Access group owns it. Refuse rather than adopt a group owned elsewhere.
                candidate = self._lookup_google_group_id(email) if email is not None else None
                link = None
                if candidate is None:
                    link = self._discover_existing_link(group)
                    if link is not None and link.get("email"):
                        logger.info(f"Backfilling group link for {group.name} that was added out-of-band...")
                        candidate = self._lookup_google_group_id(link["email"])
                if candidate is not None:
                    display_email = email or (link.get("email") if link else None)
                    google_group_id = self._claim_group_id(session, group, candidate, display_email)
                    if google_group_id is None:
                        return  # owned by another Access group; _claim_group_id marked the error
                    if link is not None and link.get("push_mapping_id"):
                        set_status_value(group, STATUS_PUSH_MAPPING_ID, link["push_mapping_id"], PLUGIN_ID)

            if google_group_id is None:
                # Nothing to adopt -> create from config (or skip when config is absent). Create is
                # self-guarding against duplicate prefixes: Cloud Identity rejects a second group at
                # the same email, so no ownership check is needed before recording the new id.
                if config is None:
                    logger.info(f"Skipping {group.name} due to missing required config.")
                    return
                logger.info(f"Adding and linking a new Google group for {group.name}...")
                prefix, display_name = config
                pattern = get_config_value(group.app, CONFIG_EMAIL_PATTERN, PLUGIN_ID)
                pattern_error = self._validate_email_against_pattern(prefix, pattern)
                if pattern_error:
                    self._mark(session, group, SYNC_ERROR, pattern_error)
                    return
                google_group_id = self._create_google_group(prefix, display_name, group.description or "")
                set_status_value(group, STATUS_GOOGLE_GROUP_ID, google_group_id, PLUGIN_ID)
            else:
                # We own this live Google group (cached or just claimed) -> enforce/adopt its props.
                logger.debug(f"Reconciling group properties for {group.name}...")
                live = self._get_google_group(google_group_id)
                reconcile_error = self._adopt_or_enforce(session, group, google_group_id, live)
                if reconcile_error is not None:
                    self._mark(session, group, SYNC_ERROR, reconcile_error)
                    return

            # Ensure the push mapping exists; may defer if Okta hasn't imported yet. An ambiguous
            # target (duplicate imports sharing the email) won't self-heal, so it errors rather
            # than deferring forever.
            if not get_status_value(group, STATUS_PUSH_MAPPING_ID, PLUGIN_ID):
                resolved_email = email or self._email_from_status(group)
                try:
                    linked = resolved_email is not None and self._create_push_mapping(group, resolved_email)
                except AmbiguousOktaTargetError as e:
                    self._mark(session, group, SYNC_ERROR, str(e))
                    return
                if not linked:
                    self._mark(session, group, SYNC_PENDING, "Awaiting Okta import of the Google group")
                    return

            self._mark(session, group, SYNC_SYNCED)
        except Exception as e:
            logger.exception(f"Reconcile failed for group {group.name}")
            try:
                self._mark(session, group, SYNC_ERROR, str(e))
            except Exception:
                logger.exception("Failed to persist error status")
            raise

    def _adopt_or_enforce(
        self, session: Session, group: AppGroup, google_group_id: str, live: dict[str, Any]
    ) -> str | None:
        """For an existing live Google group: adopt missing Access-side values from it,
        or enforce present values onto it. The email (groupKey) is immutable in the Cloud
        Identity API and host-blocked from changing, so it is never patched here. Returns
        an error string or None."""
        config = self._group_config(group)
        live_email = (live.get("groupKey") or {}).get("id", "") or ""

        if config is None:
            logger.info(f"Backfilling group properties from Google to Access for {group.name}...")
            inferred_prefix = self._prefix_from_email(live_email)
            if inferred_prefix is None:
                return f"Live Google group email '{live_email}' is not in domain {self._domain}"
            set_config_value(group, CONFIG_EMAIL, inferred_prefix, PLUGIN_ID)
            set_config_value(group, CONFIG_DISPLAY_NAME, live.get("displayName", "") or "", PLUGIN_ID)
        else:
            logger.debug(f"Pushing Access group config to Google for {group.name}...")
            _, display_name = config
            patch_display_name = display_name if (live.get("displayName") or "") != display_name else None
            # Description is handled below for both directions; only push it here when Access
            # has a (differing) non-empty description.
            access_desc = group.description or ""
            patch_description = access_desc if access_desc and (live.get("description") or "") != access_desc else None
            self._patch_google_group(google_group_id, display_name=patch_display_name, description=patch_description)

        # Description sync (both directions): clobber if Access has one, else backfill.
        access_desc = group.description or ""
        google_desc = live.get("description", "") or ""
        if not access_desc and google_desc:
            logger.info(f"Backfilling group description from Google to Access for {group.name}...")
            group.description = google_desc
            session.add(group)
            okta.update_group(group.id, group.name, google_desc)
        return None

    # ---- Lifecycle hooks ----

    @hookimpl
    def group_created(self, session: Session, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        self._reconcile(session, group)

    @hookimpl
    def group_updated(
        self, session: Session, group: AppGroup, old_name: str, old_description: str, plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        self._reconcile(session, group)

    @hookimpl
    def group_deleted(self, session: Session, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        if not self._is_enabled(group):
            return

        # Delete only a Google group this Access group provably owns: the recorded
        # google_group_id, written only after the reconcile ownership check passes. We
        # deliberately do NOT fall back to resolving the id by the (shared) email -- that could
        # resolve to, and destroy, a Google group owned by a different Access group that merely
        # collided on the prefix (e.g. one refused adoption, which therefore carries no id here).
        # The cost of being conservative is that a group we created but crashed before recording
        # is orphaned rather than cleaned up; the next reconcile re-resolves and records it.
        google_group_id = get_status_value(group, STATUS_GOOGLE_GROUP_ID, PLUGIN_ID)
        if not google_group_id:
            logger.info(f"Group {group.name} owns no linked Google group; nothing to delete")
            return

        mapping_id = get_status_value(group, STATUS_PUSH_MAPPING_ID, PLUGIN_ID)
        if mapping_id:
            # Best-effort unlink: a failure here must not block deleting the Google group, which is
            # the authoritative cleanup when the Access group is deleted. A leftover mapping points
            # at a now-deleted group, which is harmless and separately cleanable.
            try:
                okta.delete_group_push_mapping(appId=self._okta_app_id, mappingId=mapping_id, deleteTargetGroup=False)
                logger.info(f"Unlinked Okta push mapping {mapping_id} for Access group {group.name}")
            except Exception:
                logger.exception(
                    f"Failed to unlink Okta push mapping {mapping_id} for {group.name}; "
                    "deleting the Google group anyway"
                )
        self._delete_google_group(google_group_id)
        logger.info(f"Deleted Google group {google_group_id} for Access group {group.name}")

    @hookimpl
    def sync_all_groups(self, session: Session, app: App, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        groups = session.scalars(
            select(AppGroup).where(AppGroup.app_id == app.id).where(AppGroup.deleted_at.is_(None))
        ).all()
        for group in groups:
            try:
                self._reconcile(session, group)
            except Exception:
                logger.exception(f"Sync reconcile failed for group {group.name}")


google_group_manager_plugin = GoogleGroupManagerPlugin()
