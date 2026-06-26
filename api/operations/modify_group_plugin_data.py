import logging
from typing import Any

from api.extensions import db
from api.models import AppGroup, OktaGroup
from api.plugins.app_group_lifecycle import (
    get_app_group_lifecycle_hook,
    get_app_group_lifecycle_plugin_to_invoke,
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

    def __init__(self, *, group: OktaGroup, plugin_data: dict[str, Any]):
        self.group = group
        self.plugin_data = plugin_data

    def execute(self) -> OktaGroup:
        old_plugin_data = self.group.plugin_data or {}
        old_name = self.group.name
        old_description = self.group.description or ""
        plugin_id = get_app_group_lifecycle_plugin_to_invoke(self.group)

        # Apply the patch: start from the incoming data, preserving top-level plugin
        # entries it didn't mention.
        merged_plugin_data = dict(self.plugin_data)
        changed = bool(old_plugin_data) and merged_plugin_data != old_plugin_data
        if changed:
            for key in old_plugin_data:
                if key not in merged_plugin_data:
                    merged_plugin_data[key] = old_plugin_data[key]

        # Decide whether to fire the hook before the per-plugin merge below, which
        # mutates old_plugin_data in place (it shares the configuration/status dicts)
        # and would otherwise erase the difference we're testing for.
        config_changed = plugin_id is not None and is_plugin_config_changed(
            old_plugin_data, merged_plugin_data, plugin_id
        )

        self.group.plugin_data = merged_plugin_data

        # Preserve per-plugin configuration/status keys the patch omitted for app group
        # lifecycle plugins (partial-patch semantics).
        if changed and type(self.group) is AppGroup:
            merge_app_lifecycle_plugin_data(self.group, old_plugin_data)

        db.session.commit()

        if config_changed:
            try:
                hook = get_app_group_lifecycle_hook()
                hook.group_updated(
                    session=db.session,
                    group=self.group,
                    old_name=old_name,
                    old_description=old_description,
                    plugin_id=plugin_id,
                )
                db.session.commit()
            except Exception:
                logging.getLogger("api").exception(
                    f"Failed to invoke group_updated hook for group {self.group.id} with plugin '{plugin_id}'"
                )
                db.session.rollback()

        return self.group
