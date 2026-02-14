"""
App Group Lifecycle Google Group Management Plugin

This plugin automatically creates and manages Google groups for Access groups,
linking them via Okta's group push feature.
"""

import logging
import os
import re
from typing import Any

from google.auth import default
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

# Import models
from api.models import AppGroup

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
from api.services import okta

# Plugin configuration
PLUGIN_ID = "google_group_manager"

# Google Admin SDK scopes needed to manage Google groups
GOOGLE_GROUP_API_SCOPES = ['https://www.googleapis.com/auth/admin.directory.group']

# Required environment variables
GOOGLE_OKTA_APP_ID = "GOOGLE_WORKSPACE_OKTA_APP_ID"
GOOGLE_DOMAIN = "GOOGLE_WORKSPACE_DOMAIN"

logger = logging.getLogger(__name__)


class GoogleGroupManagerPlugin:
    """Plugin that manages Google groups lifecycle for Access groups."""

    def __init__(self) -> None:
        """Initialize the plugin and Google Groups API client."""

        google_okta_app_id = os.environ.get(GOOGLE_OKTA_APP_ID)
        if not google_okta_app_id:
            raise ValueError(
                f"{GOOGLE_OKTA_APP_ID} environment variable not set, but required for Google groups management (group push mapping)."
            )
        self._google_okta_app_id = google_okta_app_id

        google_workspace_domain = os.environ.get(GOOGLE_DOMAIN)
        if not google_workspace_domain:
            raise ValueError(
                f"{GOOGLE_DOMAIN} environment variable not set, but required for Google groups management."
            )
        self._google_domain = google_workspace_domain

        # Initialize Google Admin SDK Directory API client
        google_credentials, _ = default(scopes=GOOGLE_GROUP_API_SCOPES)
        self._google_group_api_client = build('admin', 'directory_v1', credentials=google_credentials)

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        """Return metadata for this plugin."""
        return AppGroupLifecyclePluginMetadata(
            id=PLUGIN_ID,
            display_name="Google Group Management",
            description=f"Automatically creates and deletes corresponding Google groups in the domain {self._google_domain}.",
        )

    # Configuration hooks

    @hookimpl
    def get_plugin_app_config_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """Return app-level configuration schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        return {
            "enabled": AppGroupLifecyclePluginConfigProperty(
                display_name="Enable?",
                help_text="Enable or disable automatic Google group creation and management",
                type="boolean",
                default_value=True,
                required=True,
            ),
            "email_prefix": AppGroupLifecyclePluginConfigProperty(
                display_name="Email Prefix",
                help_text=(
                    "Optional prefix for Google group email addresses. "
                    f"If provided, emails will be {{prefix}}-{{group-name-without-app-prefix}}@{self._google_domain}."
                ),
                type="text",
                default_value="",
                required=False,
            ),
        }

    @hookimpl
    def validate_plugin_app_config(
        self, config: dict[str, Any], plugin_id: str | None
    ) -> dict[str, str] | None:
        """Validate app-level configuration."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        errors = {}

        # Validate enabled field
        if "enabled" not in config:
            errors["enabled"] = "The 'enabled' field is required"
        elif not isinstance(config["enabled"], bool):
            errors["enabled"] = "The 'enabled' field must be a boolean"

        # Validate email_prefix if provided
        if "email_prefix" in config and config["email_prefix"]:
            if not isinstance(config["email_prefix"], str):
                errors["email_prefix"] = "The 'email_prefix' field must be a string"
            elif not re.match(r"^[a-z0-9]+([a-z0-9-]+[a-z0-9]+)*$", config["email_prefix"]):
                errors["email_prefix"] = (
                    "The 'email_prefix' must contain only hyphen-delimited groups of lowercase letters and numbers (kebab-case)"
                )

        return errors if errors else {}

    @hookimpl
    def get_plugin_group_config_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        """Return group-level configuration schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        # No group-level configuration needed
        return {}

    @hookimpl
    def validate_plugin_group_config(
        self, config: dict[str, Any], plugin_id: str | None
    ) -> dict[str, str] | None:
        """Validate group-level configuration."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        # No group-level configuration to validate
        return {}

    # Status hooks

    @hookimpl
    def get_plugin_app_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """Return app-level status schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        # No app-level status needed
        return {}

    @hookimpl
    def get_plugin_group_status_properties(
        self, plugin_id: str | None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        """Return group-level status schema."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return None

        return {
            "name": AppGroupLifecyclePluginStatusProperty(
                display_name="Google Group Name",
                help_text="The display name of the linked Google group",
                type="text",
            ),
            "email": AppGroupLifecyclePluginStatusProperty(
                display_name="Google Group Email",
                help_text="The email address of the linked Google group",
                type="text",
            ),
            "push_mapping_id": AppGroupLifecyclePluginStatusProperty(
                display_name="Okta Group Push Mapping ID",
                help_text="The ID of the Okta group push mapping",
                type="text",
            ),
        }

    # Lifecycle hooks

    @hookimpl
    def group_created(
        self, session: Session, group: AppGroup, plugin_id: str | None
    ) -> None:
        """Handle group creation by creating a Google group and Okta group push."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        if not self._is_enabled(group):
            return

        # Create the Google group (this will also set the name and email status)
        google_group_name = self._create_google_group(group)

        # Add group to session to persist status updates
        session.add(group)
        session.commit()

        # Create Okta group push mapping (this will also set the push_mapping_id status)
        self._create_okta_group_push_mapping(group, google_group_name)

        # Add group to session to persist status updates
        session.add(group)
        session.commit()

    @hookimpl
    def group_deleted(
        self, session: Session, group: AppGroup, plugin_id: str | None
    ) -> None:
        """Handle group deletion by deleting the Google group and Okta group push."""
        if plugin_id is not None and plugin_id != PLUGIN_ID:
            return

        if not self._is_enabled(group):
            return
        
        # Get the group email from status
        group_email: str | None = get_status_value(group, "email", PLUGIN_ID)

        if not group_email:
            # Assume that this group is not managed by this plugin
            return

        logger.info(f"Deleting Okta push mapping and target Google group (email: {group_email}) for Access group {group.name}")

        self._delete_okta_group_push_mapping_and_google_group(group)

    # Helper methods

    def _is_enabled(self, group: AppGroup) -> bool:
        """Check if the plugin is enabled for this group's app."""
        return get_config_value(group.app, "enabled", PLUGIN_ID, False)

    def _get_group_name_without_app_prefix(self, group: AppGroup) -> str:
        """Get the group name without the app prefix, e.g. "App-MyApp-MyGroup" -> "MyGroup"."""
        return group.name.replace(f"{AppGroup.APP_GROUP_NAME_PREFIX}{group.app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}", "")

    def _generate_group_email(self, group: AppGroup) -> str:
        """Generate the email address for a Google group."""
        email_id = self._get_group_name_without_app_prefix(group).lower()

        prefix = get_config_value(group.app, "email_prefix", PLUGIN_ID, "")
        if prefix:
            email_id = f"{prefix}-{email_id}"

        return f"{email_id}@{self._google_domain}"

    def _generate_display_name(self, group: AppGroup) -> str:
        """Generate the display name for a Google group."""
        return f'{group.app.name.replace("-", " ")} - {self._get_group_name_without_app_prefix(group).replace("-", " ")}'

    def _create_google_group(self, group: AppGroup) -> str:
        """Create a Google group using the Admin SDK Directory API.

        Returns the Google group name.
        """
        # Generate group email and display name
        group_email = self._generate_group_email(group)
        display_name = self._generate_display_name(group)

        logger.info(f"Creating Google group: {group_email} ({display_name})")

        try:
            group_properties = {
                'email': group_email,
                'name': display_name,
                'description': f'Managed by the Access group {group.name}. {group.description}',
            }

            result = self._google_group_api_client.groups().insert(body=group_properties).execute()

            created_google_group_name = result.get('name')
            if not created_google_group_name:
                raise ValueError(f"Expected to get a Google group name from the Google group creation result, but got {result}")
            
            created_google_group_email = result.get('email')
            if not created_google_group_email:
                raise ValueError(f"Expected to get a Google group email from the Google group creation result, but got {result}")

            # Update status with group info
            set_status_value(group, "name", created_google_group_name, PLUGIN_ID)
            set_status_value(group, "email", created_google_group_email, PLUGIN_ID)

            logger.info(f"Successfully created Google group: {created_google_group_email} ({created_google_group_name})")

            return created_google_group_name

        except Exception as e:
            e.add_note(f"Failed to create Google group {group_email} for Access group {group.name}")
            raise

    def _create_okta_group_push_mapping(self, group: AppGroup, google_group_name: str) -> None:
        """Create an Okta group push mapping for the group."""
        try:
            # Create the group push mapping using OktaService
            result = okta.create_group_push_mapping(
                appId=self._google_okta_app_id,
                sourceGroupId=group.id,
                targetGroupName=google_group_name,
            )

            # Store the mapping ID in status for later deletion
            mapping_id = result.get("id")
            if mapping_id:
                set_status_value(group, "push_mapping_id", mapping_id, PLUGIN_ID)
            else:
                raise Exception(f"Expected to get a mapping ID from the Okta group push mapping creation result, but got {result}")

            logger.info(f"Successfully created Okta group push for {group.name} (mapping ID: {mapping_id})")

        except Exception as e:
            e.add_note(f"Failed to create Okta group push mapping for {group.name}")
            raise

    def _delete_okta_group_push_mapping_and_google_group(self, group: AppGroup) -> None:
        """Delete the Okta group push mapping for the group."""
        try:
            # Get the mapping ID from status
            mapping_id: str | None = get_status_value(group, "push_mapping_id", PLUGIN_ID)

            if not mapping_id:
                logger.warning(f"No push mapping ID found for {group.name}; cannot delete group push mapping and Google group")
                return

            # Delete the group push mapping and the target Google group
            okta.delete_group_push_mapping(
                appId=self._google_okta_app_id,
                mappingId=mapping_id,
                deleteTargetGroup=True,
            )

            logger.info(f"Successfully deleted Okta push mapping and target Google group for {group.name} (mapping ID: {mapping_id})")

        except Exception as e:
            e.add_note(f"Failed to delete Okta push mapping and target Google group for {group.name}")
            raise


# Create plugin instance
google_group_manager_plugin = GoogleGroupManagerPlugin()
