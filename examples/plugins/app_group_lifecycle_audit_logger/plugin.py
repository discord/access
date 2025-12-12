"""
App Group Lifecycle Audit Logger Plugin

This plugin demonstrates how to implement an app group lifecycle plugin.
It logs all group lifecycle events to provide a simple audit trail.
"""

import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

# Import models
from api.models import App, AppGroup, OktaUser

# Import the plugin spec and decorators
from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginStatusProperty,
    get_config_value,
    get_status_value,
    hookimpl,
    set_status_value,
)

# Plugin configuration
PLUGIN_ID = "audit_logger"
PLUGIN_DISPLAY_NAME = "Audit Logger"
PLUGIN_DESCRIPTION = "Logs all group lifecycle events for auditing purposes"

logger = logging.getLogger(__name__)


class AuditLoggerPlugin:
    """Example plugin that logs all group lifecycle events."""

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        """Return metadata for this plugin."""
        return AppGroupLifecyclePluginMetadata(
            id=PLUGIN_ID,
            display_name=PLUGIN_DISPLAY_NAME,
            description=PLUGIN_DESCRIPTION,
        )

    # Configuration hooks

    @hookimpl
    def get_plugin_app_config_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """Return app-level configuration schema."""
        # Only respond if this plugin is being queried
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        return {
            "enabled": AppGroupLifecyclePluginConfigProperty(
                display_name="Enable audit logging?",
                help_text="Enable or disable audit logging for all groups associated with this app",
                type="boolean",
                default_value=True,
                required=True,
            ),
            "log_level": AppGroupLifecyclePluginConfigProperty(
                display_name="Log Level",
                help_text="The log level to use (INFO, WARNING, ERROR)",
                type="text",
                default_value="INFO",
                required=False,
                validation={
                    "allowed_values": ["INFO", "WARNING", "ERROR"],
                },
            ),
        }

    @hookimpl
    def validate_plugin_app_config(self, config: dict[str, Any], plugin_id: str | None = None) -> dict[str, str] | None:
        """Validate app-level configuration."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        errors = {}

        # Validate enabled field
        if "enabled" not in config:
            errors["enabled"] = "The 'enabled' field is required"
        elif not isinstance(config["enabled"], bool):
            errors["enabled"] = "The 'enabled' field must be a boolean"

        # Validate log_level if provided
        if "log_level" in config:
            if not isinstance(config["log_level"], str):
                errors["log_level"] = "The 'log_level' field must be a string"
            elif config["log_level"] not in ["INFO", "WARNING", "ERROR"]:
                errors["log_level"] = "The 'log_level' must be one of: INFO, WARNING, ERROR"

        return errors if errors else {}

    @hookimpl
    def get_plugin_group_config_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """Return group-level configuration schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        return {
            "enabled": AppGroupLifecyclePluginConfigProperty(
                display_name="Enable audit logging?",
                help_text="Enable or disable audit logging for this specific group",
                type="boolean",
                default_value=True,
                required=True,
            ),
            "custom_tag": AppGroupLifecyclePluginConfigProperty(
                display_name="Custom Tag",
                help_text="Custom tag to include in log messages for this group",
                type="text",
                default_value="",
                required=False,
            ),
        }

    @hookimpl
    def validate_plugin_group_config(
        self, config: dict[str, Any], plugin_id: str | None = None
    ) -> dict[str, str] | None:
        """Validate group-level configuration."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        errors = {}

        # Validate enabled field
        if "enabled" not in config:
            errors["enabled"] = "The 'enabled' field is required"
        elif not isinstance(config["enabled"], bool):
            errors["enabled"] = "The 'enabled' field must be a boolean"

        # Validate custom_tag if provided
        if "custom_tag" in config and not isinstance(config["custom_tag"], str):
            errors["custom_tag"] = "The 'custom_tag' field must be a string"

        return errors if errors else {}

    # Status hooks

    @hookimpl
    def get_plugin_app_status_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """Return app-level status schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        return {
            "total_events_logged": AppGroupLifecyclePluginStatusProperty(
                display_name="Total Events Logged",
                help_text="Total number of events logged for this app",
                type="number",
            ),
            "last_sync_at": AppGroupLifecyclePluginStatusProperty(
                display_name="Last Synced",
                help_text="Timestamp of the last periodic sync of all group memberships for this app",
                type="date",
            ),
        }

    @hookimpl
    def get_plugin_group_status_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """Return group-level status schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        return {
            "events_logged": AppGroupLifecyclePluginStatusProperty(
                display_name="Events Logged",
                help_text="Number of events logged for this group",
                type="number",
            ),
            "last_event_at": AppGroupLifecyclePluginStatusProperty(
                display_name="Last Event Logged",
                help_text="Timestamp of the last event logged for this group",
                type="date",
            ),
        }

    # Lifecycle hooks

    @hookimpl
    def group_created(self, session: Session, group: AppGroup, plugin_id: str | None = None) -> None:
        """Handle group creation."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        if not self._is_enabled(group):
            return

        self._log(f"Group created: {group.name} (app: {group.app.name})", group=group)

        # Update status
        self._increment_event_count(session, group)

    @hookimpl
    def group_deleted(self, session: Session, group: AppGroup, plugin_id: str | None = None) -> None:
        """Handle group deletion."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        if not self._is_enabled(group):
            return

        self._log(f"Group deleted: {group.name} (app: {group.app.name})", group=group)

        # Update status
        self._increment_event_count(session, group)

    @hookimpl
    def group_members_added(
        self,
        session: Session,
        group: AppGroup,
        members: list[OktaUser],
        plugin_id: str | None = None,
    ) -> None:
        """Handle member addition."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        if not self._is_enabled(group):
            return

        member_emails = [m.email for m in members]
        self._log(f"Members added to {group.name}: {', '.join(member_emails)}", group=group)

        # Update status
        self._increment_event_count(session, group)

    @hookimpl
    def group_members_removed(
        self,
        session: Session,
        group: AppGroup,
        members: list[OktaUser],
        plugin_id: str | None = None,
    ) -> None:
        """Handle member removal."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        if not self._is_enabled(group):
            return

        member_emails = [m.email for m in members]
        self._log(f"Members removed from {group.name}: {', '.join(member_emails)}", group=group)

        # Update status
        self._increment_event_count(session, group)

    @hookimpl
    def sync_all_group_membership(self, session: Session, app: App, plugin_id: str | None = None) -> None:
        """Perform periodic sync of all group memberships."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        self._log(f"Periodic sync triggered for app: {app.name}", app)

        # Update app-level status
        set_status_value(app, "last_sync_at", datetime.utcnow().isoformat(), PLUGIN_ID)
        session.add(app)

    # Helper methods

    def _log(self, message: str, app: App | None = None, group: AppGroup | None = None) -> None:
        """Log a message at the level specified in the app's plugin configuration."""
        if app is None:
            if group is None:
                raise ValueError("Either app or group must be provided")
            else:
                app = group.app

        level_str: str = get_config_value(app, "log_level", PLUGIN_ID, "INFO")
        level: int = getattr(logging, level_str)
        custom_tag = get_config_value(group, "custom_tag", PLUGIN_ID) if group else ""
        logger.log(level, f"[AUDIT_LOGGER]{f'[{custom_tag}]' if custom_tag else ''} {message}")

    def _is_enabled(self, group: AppGroup) -> bool:
        """Check if the plugin is enabled for this group."""
        return get_config_value(group.app, "enabled", PLUGIN_ID, True) and get_config_value(
            group, "enabled", PLUGIN_ID, True
        )

    def _increment_event_count(self, session: Session, group: AppGroup) -> None:
        """Increment the event count in the status."""
        # Update group-level status
        current_count = get_status_value(group, "events_logged", PLUGIN_ID) or 0
        set_status_value(group, "events_logged", current_count + 1, PLUGIN_ID)
        set_status_value(group, "last_event_at", datetime.utcnow().isoformat(), PLUGIN_ID)
        session.add(group)

        # Update app-level status
        app_count = get_status_value(group.app, "total_events_logged", PLUGIN_ID) or 0
        set_status_value(group.app, "total_events_logged", app_count + 1, PLUGIN_ID)
        session.add(group.app)


# Create plugin instance
audit_logger_plugin = AuditLoggerPlugin()
