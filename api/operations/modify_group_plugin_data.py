import copy
from typing import Any

from api.extensions import db
from api.models import AppGroup, OktaGroup
from api.plugins.app_group_lifecycle import (
    AppGroupLifecycleHook,
    get_app_group_lifecycle_plugin_to_invoke,
    invoke_app_group_lifecycle_hook,
    is_plugin_config_changed,
    merge_app_lifecycle_plugin_data,
)


class ModifyGroupPluginData:
    """Apply a (partial) plugin_data patch to a group and fire group_updated when the
    configured app group lifecycle plugin's *configuration* changes.

    Owns the plugin_data merge semantics so callers only supply the patch:
    - top-level plugin entries the patch omits are preserved;
    - for app group lifecycle plugins, per-plugin configuration/status keys the patch
      omits are preserved too, so a partial configuration edit does not wipe
      plugin-managed status (e.g. a linked external group id).

    group_updated fires only on configuration changes -- never on status-only changes,
    which the plugin itself writes and which would otherwise re-trigger reconciliation in
    a loop -- and only after the merge, so the hook observes the fully-merged plugin_data.
    """

    def __init__(self, *, group: OktaGroup, plugin_data: dict[str, Any], fire_lifecycle_hook: bool = True):
        self.group = group
        self.plugin_data = plugin_data
        # put_group sets this False when it also changed the name/description, so a single
        # group_updated fire covers both changes instead of reconciling twice. The merge still
        # runs; only the hook call is gated. `config_changed` is exposed so that caller can
        # decide whether to fire the consolidated hook itself.
        self.fire_lifecycle_hook = fire_lifecycle_hook
        self.config_changed = False

    async def execute(self) -> OktaGroup:
        old_plugin_data = self.group.plugin_data or {}
        old_name = self.group.name
        old_description = self.group.description or ""
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)

        # merge_app_lifecycle_plugin_data below mutates old_plugin_data in place (it shares the
        # configuration/status dicts), so snapshot the pre-merge config to compare against later.
        old_config_snapshot = copy.deepcopy(old_plugin_data)

        # Apply the patch: start from the incoming data, preserving top-level plugin
        # entries it didn't mention.
        merged_plugin_data = dict(self.plugin_data)
        changed = bool(old_plugin_data) and merged_plugin_data != old_plugin_data
        if changed:
            for key in old_plugin_data:
                if key not in merged_plugin_data:
                    merged_plugin_data[key] = old_plugin_data[key]

        self.group.plugin_data = merged_plugin_data

        # Preserve per-plugin configuration/status keys the patch omitted for app group
        # lifecycle plugins (partial-patch semantics).
        if changed and type(self.group) is AppGroup:
            merge_app_lifecycle_plugin_data(self.group, old_plugin_data)

        # Compare the *fully merged* effective config against the pre-merge snapshot -- not the
        # raw patch -- so a partial patch that merely omits an unchanged config key (e.g. the
        # immutable email, which the present-only immutable check now permits) is not misread as
        # a change and does not fire a redundant reconcile.
        config_changed = plugin_id is not None and is_plugin_config_changed(
            old_config_snapshot, self.group.plugin_data or {}, plugin_id
        )
        self.config_changed = config_changed

        await db.session.commit()

        # invoke_app_group_lifecycle_hook drives the async hook via run_hooks_to_completion
        # (asyncio.wait) and commits on success / rolls back on error, never propagating a
        # plugin failure. Fire it only on a real configuration change (checked above), and
        # only when the caller hasn't opted to fire a consolidated hook itself.
        if config_changed and self.fire_lifecycle_hook:
            await invoke_app_group_lifecycle_hook(
                AppGroupLifecycleHook.GROUP_UPDATED,
                group=self.group,
                old_name=old_name,
                old_description=old_description,
            )

        return self.group
