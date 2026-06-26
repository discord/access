"""
App Group Lifecycle Google Group Management Plugin

Creates, updates, and deletes Google groups for Access groups and links them via
Okta group push. All create/update/sync paths run one idempotent reconcile.
"""

import logging
import os
import re
from typing import Any

from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from api.models import AppGroup
from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginStatusProperty,
    get_config_value,
    hookimpl,
)

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
            STATUS_GOOGLE_GROUP_ID: AppGroupLifecyclePluginStatusProperty(
                display_name="Google Group ID", type="text"
            ),
            STATUS_SYNC_STATUS: AppGroupLifecyclePluginStatusProperty(
                display_name="Sync Status", help_text="synced, pending, or error", type="text"
            ),
            STATUS_SYNC_ERROR: AppGroupLifecyclePluginStatusProperty(
                display_name="Sync Error", type="text"
            ),
            STATUS_LAST_SYNCED_AT: AppGroupLifecyclePluginStatusProperty(
                display_name="Last Synced", type="date"
            ),
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
        self._groups_api.patch(
            name=self._resource_name(google_group_id), body=body, updateMask=update_mask
        ).execute()

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
