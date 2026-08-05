"""
App Group Lifecycle Google Group Management Plugin

Creates, updates, and deletes Google groups for Access groups and links them via
Okta group push. All create/update/sync paths run one idempotent reconcile.
"""

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from google.auth import default
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from api.models import App, AppGroup
from api.plugins.app_group_lifecycle import (
    AmbiguousOktaTargetError,
    AppGroupLifecycleContext,
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginStatusProperty,
    MissingOktaTargetError,
    hookimpl,
)

PLUGIN_ID = "google_group_manager"

GOOGLE_GROUP_API_SCOPES = ["https://www.googleapis.com/auth/cloud-identity.groups"]

ENV_OKTA_APP_ID = "GOOGLE_WORKSPACE_OKTA_APP_ID"
ENV_DOMAIN = "GOOGLE_WORKSPACE_DOMAIN"

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

        credentials, _ = default(scopes=GOOGLE_GROUP_API_SCOPES)
        self._groups_api = build("cloudidentity", "v1", credentials=credentials).groups()

    # ---- Helpers ----

    def _is_enabled(self, ctx: AppGroupLifecycleContext, group: AppGroup) -> bool:
        return bool(ctx.get_config(group.app, CONFIG_ENABLED, False))

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

    def _get_configured_email_prefix(self, ctx: AppGroupLifecycleContext, group: AppGroup) -> str | None:
        return ctx.get_config(group, CONFIG_EMAIL)

    def _get_configured_display_name(self, ctx: AppGroupLifecycleContext, group: AppGroup) -> str | None:
        return ctx.get_config(group, CONFIG_DISPLAY_NAME)

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
                # Show the domain inline after the input so the operator sees the full address.
                suffix=f"@{self._domain}",
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

    # The google-api-python-client is a synchronous, blocking HTTP client. Under the async
    # plugin interface these wrappers are coroutines that offload each blocking `.execute()`
    # to a worker thread (asyncio.to_thread) so they never stall the event loop.
    async def _get_google_group(self, google_group_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            lambda: self._groups_api.get(name=self._resource_name(google_group_id)).execute()
        )

    async def _patch_google_group(
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
        await asyncio.to_thread(
            lambda: self._groups_api.patch(
                name=self._resource_name(google_group_id), body=body, updateMask=update_mask
            ).execute()
        )

    async def _delete_google_group(self, google_group_id: str) -> None:
        try:
            await asyncio.to_thread(
                lambda: self._groups_api.delete(name=self._resource_name(google_group_id)).execute()
            )
        except HttpError as e:
            if _is_group_absent_error(e):
                logger.warning(
                    f"Failed to delete the Google group {google_group_id}, "
                    "possibly because it does not exist or Access lacks permissions"
                )
                return None
            raise

    async def _look_up_google_group_id(self, email: str) -> str | None:
        """Resolve an email to its bare Cloud Identity group id, or None if no such group."""
        try:
            result = await asyncio.to_thread(lambda: self._groups_api.lookup(groupKey_id=email).execute())
        except HttpError as e:
            if _is_group_absent_error(e):
                return None
            raise
        name = result.get("name")
        return name.split("/", 1)[1] if name else None

    # ---- Status setters ----

    def _mark(self, ctx: AppGroupLifecycleContext, group: AppGroup, status: str, error: str | None = None) -> None:
        """Record this reconcile's outcome. Synchronous: the context mutates plugin_data in memory
        and marks the group for persistence, and the host commits after the hook returns.

        These are diagnostics, so they opt into `durable_on_failure`: the host re-applies them in a
        fresh transaction after a failed hook, which is what lets an operator see *why* reconcile
        failed rather than a group stuck with no explanation. Contrast the ownership tokens written
        elsewhere in this plugin, which deliberately do not -- see _claim_group_id.
        """
        if error:
            logger.error(f"Google group reconciliation failed for group {group.name}: {error}")

        ctx.set_status(group, STATUS_SYNC_STATUS, status, durable_on_failure=True)
        ctx.set_status(group, STATUS_SYNC_ERROR, error, durable_on_failure=True)
        if status == SYNC_SYNCED:
            ctx.set_status(
                group, STATUS_LAST_SYNCED_AT, datetime.now(timezone.utc).isoformat(), durable_on_failure=True
            )

    # ---- Reconcile ----

    async def _get_owned_group_id(self, ctx: AppGroupLifecycleContext, group: AppGroup) -> str | None:
        """The Google group id this Access group already owns (claimed on a prior reconcile),
        if it still exists. The recorded id is an ownership token -- it is written only after
        the ownership check passes (see _claim_group_id) -- so a live cached id needs no
        re-check. Clears the cached id and returns None if the group was deleted out of band,
        so the caller re-resolves/recreates. Returns None when nothing is cached."""
        cached = ctx.get_status(group, STATUS_GOOGLE_GROUP_ID)
        if not cached:
            return None
        try:
            await self._get_google_group(cached)
            return cached
        except HttpError as e:
            if not _is_group_absent_error(e):
                raise
            logger.info(f"Cached Google group id {cached} for {group.name} is gone; clearing and re-resolving.")
            ctx.set_status(group, STATUS_GOOGLE_GROUP_ID, None)
            return None

    async def _claim_group_id(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, candidate_id: str, email: str | None = None
    ) -> str | None:
        """Record candidate_id as this group's owned Google group, but ONLY after confirming no
        other Access group already owns it -- refusing (and marking SYNC_ERROR) rather than
        clobbering / double-linking a group owned elsewhere. Returns the id on success, or None
        when refused. A no-op confirmation when we already hold this id.

        Persisting the id is gated on the ownership check (not the reverse) so a refused group
        never carries another group's id into its status, where group_deleted would later act on
        it. `ctx.lock` serializes the check against the claim, so two concurrent reconciles can't
        both pass the check for the same Google group -- the lock is held until the host commits,
        which is what makes the pair atomic.

        Ownership keys on the recorded google_group_id ALONE, not on whether a push mapping exists
        yet: the id is recorded only after this check passes, while the push mapping is created
        later and may defer until Okta imports the group. Were we to also require a push mapping, a
        second group reconciling during that window would not see the real owner and would
        double-claim the group.

        The claimed id is an ownership token, so it is written WITHOUT `durable_on_failure`: it is
        only sound when committed in the transaction that held the lock during the check. Letting
        the host replay it after a rollback would reopen exactly the race this method closes."""
        if ctx.get_status(group, STATUS_GOOGLE_GROUP_ID) == candidate_id:
            return candidate_id
        await ctx.lock(candidate_id)
        owners = await ctx.find_groups_by_status(STATUS_GOOGLE_GROUP_ID, candidate_id, exclude_group=group, limit=1)
        if owners:
            self._mark(
                ctx,
                group,
                SYNC_ERROR,
                f"Google group {email or candidate_id} is already managed by Access group "
                f"'{owners[0].name}'; refusing to link it to this one.",
            )
            return None
        ctx.set_status(group, STATUS_GOOGLE_GROUP_ID, candidate_id)
        return candidate_id

    async def _get_email_from_status(self, ctx: AppGroupLifecycleContext, group: AppGroup) -> str | None:
        """Recover the group email from a cached id when the Access-side config is absent
        (adoption path). Returns the full email, or None when there is no cached id or the cached
        group was deleted out of band (mirrors _owned_group_id's absent-error handling, so a
        vanished group defers rather than hard-erroring reconcile)."""
        google_group_id = ctx.get_status(group, STATUS_GOOGLE_GROUP_ID)
        if not google_group_id:
            return None
        try:
            live = await self._get_google_group(google_group_id)
        except HttpError as e:
            if not _is_group_absent_error(e):
                raise
            logger.info(f"Cached Google group id {google_group_id} for {group.name} is gone; cannot recover email.")
            return None
        return (live.get("groupKey") or {}).get("id")

    async def _reconcile(self, ctx: AppGroupLifecycleContext, group: AppGroup) -> None:
        """Idempotent: resolve/adopt/create the Google group, enforce its properties,
        link via Okta push, and record sync status. Commits sync_status inside the hook
        so it survives the host's post-hook rollback on error."""
        if not self._is_enabled(ctx, group):
            return

        try:
            configured_email_prefix = self._get_configured_email_prefix(ctx, group)
            configured_email = self._full_email(configured_email_prefix) if configured_email_prefix else None

            # Case 1: Retrieve an existing Google group already claimed by this Access group
            # This should generally be the case in all but the group's first or second reconcile.
            claimed_google_group_id = await self._get_owned_group_id(ctx, group)

            if claimed_google_group_id is None:
                # Case 2: Retrieve an existing Google group matching the configured email (created out-of-band)
                candidate_group_id = (
                    await self._look_up_google_group_id(configured_email) if configured_email is not None else None
                )
                mapping_id = None
                resolved_email = None

                if candidate_group_id is None:
                    # Case 3: Retrieve an existing mapped Google group (linked in Okta out-of-band)
                    link = await ctx.discover_existing_push_mapping_and_target_group_external_id(
                        group, self._okta_app_id, OKTA_GOOGLE_GROUP_PROFILE_FIELD_EMAIL
                    )
                    if link:
                        mapping_id, resolved_email = link
                    if resolved_email:
                        # If an email was configured but we didn't hit Case 2, that implies that the
                        # email of the linked group doesn't match. This is a conflict that won't self-heal,
                        # so we surface it as an error rather than silently adopting the wrong group.
                        if configured_email is not None and resolved_email != configured_email:
                            self._mark(
                                ctx,
                                group,
                                SYNC_ERROR,
                                f"Existing Okta push mapping targets Google group '{resolved_email}', but this "
                                f"group is configured for '{configured_email}'. Resolve the conflict in Okta or update the "
                                "group's configured email.",
                            )
                            return

                        logger.info(f"Backfilling group link for {group.name} that was added out-of-band...")
                        candidate_group_id = await self._look_up_google_group_id(resolved_email)

                if candidate_group_id is not None:  # group created or mapped out-of-band; try to claim it
                    claimed_google_group_id = await self._claim_group_id(
                        ctx, group, candidate_group_id, configured_email or resolved_email
                    )
                    if claimed_google_group_id is None:
                        return  # owned by another Access group; _claim_group_id marked the error
                    if mapping_id:
                        ctx.set_status(group, STATUS_PUSH_MAPPING_ID, mapping_id)

            if claimed_google_group_id is None:
                # Case 4: Nothing to adopt -> create via Okta group push. Okta creates its target group AND
                # the downstream Google group (named by the email prefix) and links them in one
                # step. This avoids waiting for Okta to import a made in Google first (which involves
                # a manually-triggered fetch). Config is required to create a new group.
                if not configured_email_prefix or not configured_email:
                    logger.info(f"Skipping {group.name} due to missing required config.")
                    return

                pattern = ctx.get_config(group.app, CONFIG_EMAIL_PATTERN)
                pattern_error = self._validate_email_against_pattern(configured_email_prefix, pattern)
                if pattern_error:
                    self._mark(ctx, group, SYNC_ERROR, pattern_error)
                    return

                if not ctx.get_status(group, STATUS_PUSH_MAPPING_ID):
                    logger.info(f"Creating and linking a new Google group for {group.name} via Okta group push...")
                    # Create a push mapping with a new target group name (the email prefix).
                    # You can't specify all the group config (description, email) when creating it via
                    # Okta, so we give the email prefix which is immutable and then reconcile the other
                    # properties later.
                    mapping_id = await ctx.create_push_mapping_and_new_group(
                        group, self._okta_app_id, configured_email_prefix
                    )
                    ctx.set_status(group, STATUS_PUSH_MAPPING_ID, mapping_id)

                # Retrieve Google's ID for the group in order to claim it.
                google_group_id_to_claim = await self._look_up_google_group_id(configured_email)
                # Okta may not have pushed the new group to Google yet: resolve it by email and
                # defer if it isn't visible, adopting its Cloud Identity id and patching its
                # metadata on a later reconcile once it appears.
                if google_group_id_to_claim is None:
                    self._mark(ctx, group, SYNC_PENDING, "Awaiting Google group creation via Okta push")
                    return

                claimed_google_group_id = await self._claim_group_id(
                    ctx, group, google_group_id_to_claim, configured_email
                )
                if claimed_google_group_id is None:
                    return  # owned by another Access group; _claim_group_id marked the error

            # We hold a live Google group (cached, adopted, or freshly created) -> enforce Access's
            # properties onto it (or backfill from it during adoption). A group Okta just created is
            # named after the email prefix and has no description, so this is what applies the real
            # display name and description.
            logger.debug(f"Reconciling group properties for {group.name}...")
            google_group = await self._get_google_group(claimed_google_group_id)
            reconcile_error = await self._adopt_or_enforce(ctx, group, claimed_google_group_id, google_group)
            if reconcile_error is not None:
                self._mark(ctx, group, SYNC_ERROR, reconcile_error)
                return

            # Ensure the push mapping exists; may defer if Okta hasn't imported yet. An ambiguous
            # target (duplicate imports sharing the email) won't self-heal, so it errors rather
            # than deferring forever. The create-via-push path above already recorded a mapping, so
            # this only runs when adopting an existing group that isn't linked yet.
            if not ctx.get_status(group, STATUS_PUSH_MAPPING_ID):
                resolved_email = configured_email or await self._get_email_from_status(ctx, group)
                if not resolved_email:
                    logger.info(f"Skipping {group.name} due to missing required config.")
                    return
                try:
                    mapping_id = await ctx.create_push_mapping_for_existing_group(
                        group, self._okta_app_id, OKTA_GOOGLE_GROUP_PROFILE_FIELD_EMAIL, resolved_email
                    )
                    ctx.set_status(group, STATUS_PUSH_MAPPING_ID, mapping_id)
                except AmbiguousOktaTargetError as e:
                    self._mark(ctx, group, SYNC_ERROR, str(e))
                    return
                except MissingOktaTargetError:
                    self._mark(ctx, group, SYNC_PENDING, "Awaiting Okta import of the Google group")
                    return

            self._mark(ctx, group, SYNC_SYNCED)
        except Exception as e:
            logger.exception(f"Reconcile failed for group {group.name}")
            try:
                self._mark(ctx, group, SYNC_ERROR, str(e))
            except Exception:
                logger.exception("Failed to persist error status")
            raise

    async def _adopt_or_enforce(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, google_group_id: str, google_group: dict[str, Any]
    ) -> str | None:
        """For an existing live Google group: adopt missing Access-side values from it,
        or enforce present values onto it. The email (groupKey) is immutable in the Cloud
        Identity API and host-blocked from changing, so it is never patched here. Returns
        an error string or None."""
        configured_email_prefix = self._get_configured_email_prefix(ctx, group)
        configured_display_name = self._get_configured_display_name(ctx, group)
        google_email = (google_group.get("groupKey") or {}).get("id", "") or ""
        google_description = google_group.get("description", "") or ""

        if not configured_email_prefix and not configured_display_name:  # adopt Google -> Access
            logger.info(f"Backfilling group properties from Google to Access for {group.name}...")
            inferred_email_prefix = self._prefix_from_email(google_email)
            if inferred_email_prefix is None:
                return f"Live Google group email '{google_email}' is not in domain {self._domain}"
            ctx.set_config(group, CONFIG_EMAIL, inferred_email_prefix)
            ctx.set_config(group, CONFIG_DISPLAY_NAME, google_group.get("displayName", "") or "")
            if not (group.description or "") and google_description:
                logger.info(f"Backfilling group description from Google to Access for {group.name}...")
                # Route the Access-side description change through the plugin interface, which
                # updates the ORM and syncs to Okta without committing or re-firing this hook.
                await ctx.set_group_description(group, google_description)

        else:  # enforce Access -> Google
            logger.debug(f"Pushing Access group config to Google for {group.name}...")
            patch_display_name = (
                configured_display_name if (google_group.get("displayName") or "") != configured_display_name else None
            )
            access_description = group.description or ""
            patch_description = access_description if google_description != access_description else None
            await self._patch_google_group(
                google_group_id, display_name=patch_display_name, description=patch_description
            )
        return None

    # ---- Lifecycle hooks ----

    @hookimpl
    async def group_created(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        await self._reconcile(ctx, group)

    @hookimpl
    async def group_updated(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, old_name: str, old_description: str, plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        await self._reconcile(ctx, group)

    @hookimpl
    async def group_deleted(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return
        if not self._is_enabled(ctx, group):
            return

        # Delete only a Google group this Access group provably owns: the recorded
        # google_group_id, written only after the reconcile ownership check passes. We
        # deliberately do NOT fall back to resolving the id by the (shared) email -- that could
        # resolve to, and destroy, a Google group owned by a different Access group that merely
        # collided on the prefix (e.g. one refused adoption, which therefore carries no id here).
        # The cost of being conservative is that a group we created but crashed before recording
        # is orphaned rather than cleaned up; the next reconcile re-resolves and records it.
        google_group_id = ctx.get_status(group, STATUS_GOOGLE_GROUP_ID)
        if not google_group_id:
            logger.info(f"Group {group.name} owns no linked Google group; nothing to delete")
            return

        mapping_id = ctx.get_status(group, STATUS_PUSH_MAPPING_ID)
        if mapping_id:
            # Best-effort unlink: a failure here must not block deleting the Google group, which is
            # the authoritative cleanup when the Access group is deleted. A leftover mapping points
            # at a now-deleted group, which is harmless and separately cleanable.
            try:
                await ctx.delete_push_mapping(self._okta_app_id, mapping_id)
                logger.info(f"Unlinked Okta push mapping {mapping_id} for Access group {group.name}")
            except Exception:
                logger.exception(
                    f"Failed to unlink Okta push mapping {mapping_id} for {group.name}; "
                    "deleting the Google group anyway"
                )
        await self._delete_google_group(google_group_id)
        logger.info(f"Deleted Google group {google_group_id} for Access group {group.name}")

    @hookimpl
    async def sync_all_groups(self, ctx: AppGroupLifecycleContext, app: App, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        # Swallow per-group failures so one bad group doesn't abort the batch, but count them and
        # emit an aggregate signal at the end -- otherwise a systemic outage (e.g. Google API down)
        # leaves the periodic sync exiting cleanly with no top-level indication anything failed.
        failures = 0
        groups = app.active_app_groups
        for group in groups:
            try:
                await self._reconcile(ctx, group)
            except Exception:
                failures += 1
                logger.exception(f"Sync reconcile failed for group {group.name}")
        if failures:
            logger.error(
                f"Google group sync for app {app.name}: {failures} of {len(groups)} groups failed to reconcile"
            )


google_group_manager_plugin = GoogleGroupManagerPlugin()
