"""
Tests for the App Group Lifecycle Plugin functionality.

This includes tests for:
- Plugin registration and discovery
- Plugin configuration and validation
- API endpoints for plugin configuration
- Authorization checks for plugin configuration (positive and negative cases)
- Plugin lifecycle hooks
"""

from typing import Any, Generator

import pytest
from flask import Flask, url_for
from flask.testing import FlaskClient
from flask_sqlalchemy import SQLAlchemy
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from api.models import AppGroup, OktaUser, OktaUserGroupMember
from api.plugins.app_group_lifecycle import (
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginStatusProperty,
    get_app_group_lifecycle_plugin_app_config_properties,
    get_app_group_lifecycle_plugin_app_status_properties,
    get_app_group_lifecycle_plugin_group_config_properties,
    get_app_group_lifecycle_plugin_group_status_properties,
    get_app_group_lifecycle_plugins,
    get_config_value,
    get_status_value,
    hookimpl,
    merge_app_lifecycle_plugin_data,
    set_status_value,
    validate_app_group_lifecycle_plugin_app_config,
    validate_app_group_lifecycle_plugin_group_config,
)
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory, OktaUserFactory, RoleGroupFactory


class DummyPlugin:
    """A simple test plugin for unit testing."""

    ID: str = "test_plugin"

    def __init__(self) -> None:
        self.group_created_calls: list[str] = []
        self.group_deleted_calls: list[str] = []
        self.members_added_calls: list[tuple[str, list[str]]] = []
        self.members_removed_calls: list[tuple[str, list[str]]] = []

    @hookimpl
    def get_plugin_metadata(self) -> AppGroupLifecyclePluginMetadata | None:
        return AppGroupLifecyclePluginMetadata(
            id=self.ID,
            display_name="Test Plugin",
            description="A test plugin for unit testing",
        )

    @hookimpl
    def get_plugin_app_config_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        return {
            "enabled": AppGroupLifecyclePluginConfigProperty(
                display_name="Enabled",
                help_text="Enable or disable the plugin",
                type="boolean",
                default_value=True,
                required=True,
            ),
            "category": AppGroupLifecyclePluginConfigProperty(
                display_name="Category",
                help_text="Group category for the external service",
                type="text",
                required=False,
            ),
        }

    @hookimpl
    def validate_plugin_app_config(self, config: dict[str, Any], plugin_id: str | None = None) -> dict[str, str] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        errors: dict[str, str] = {}
        if "enabled" not in config:
            errors["enabled"] = "The 'enabled' field is required"
        elif not isinstance(config["enabled"], bool):
            errors["enabled"] = "The 'enabled' field must be a boolean"

        if "category" in config and not isinstance(config["category"], str):
            errors["category"] = "The 'category' field must be a string"

        return errors

    @hookimpl
    def get_plugin_group_config_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginConfigProperty] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        return {
            "group_id": AppGroupLifecyclePluginConfigProperty(
                display_name="External Group ID",
                help_text="The ID of the group in the external system",
                type="text",
                required=True,
            ),
        }

    @hookimpl
    def validate_plugin_group_config(
        self, config: dict[str, Any], plugin_id: str | None = None
    ) -> dict[str, str] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        errors: dict[str, str] = {}
        if "group_id" not in config:
            errors["group_id"] = "The 'group_id' field is required"
        elif not isinstance(config["group_id"], str):
            errors["group_id"] = "The 'group_id' field must be a string"

        return errors

    @hookimpl
    def get_plugin_app_status_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        return {
            "last_sync": AppGroupLifecyclePluginStatusProperty(
                display_name="Last Sync",
                help_text="When the last sync occurred",
                type="date",
            ),
        }

    @hookimpl
    def get_plugin_group_status_properties(
        self, plugin_id: str | None = None
    ) -> dict[str, AppGroupLifecyclePluginStatusProperty] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        return {
            "member_count": AppGroupLifecyclePluginStatusProperty(
                display_name="Member Count",
                help_text="Number of members in the external group",
                type="number",
            ),
        }

    @hookimpl
    def group_created(self, session: Session, group: AppGroup, plugin_id: str | None = None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        # Track that this hook was called
        self.group_created_calls.append(group.id)

    @hookimpl
    def group_deleted(self, session: Session, group: AppGroup, plugin_id: str | None = None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        self.group_deleted_calls.append(group.id)

    @hookimpl
    def group_members_added(
        self, session: Session, group: AppGroup, members: list[OktaUser], plugin_id: str | None = None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        self.members_added_calls.append((group.id, [m.id for m in members]))

    @hookimpl
    def group_members_removed(
        self, session: Session, group: AppGroup, members: list[OktaUser], plugin_id: str | None = None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        self.members_removed_calls.append((group.id, [m.id for m in members]))


@pytest.fixture
def test_plugin(app: Flask, mocker: MockerFixture) -> Generator[DummyPlugin, None, None]:
    """Register the test plugin for testing."""
    import pluggy

    import api.plugins.app_group_lifecycle as plugin_module
    from api.plugins.app_group_lifecycle import AppGroupLifecyclePluginSpec

    # Create a new PluginManager with our test plugin
    test_plugin_instance = DummyPlugin()
    pm = pluggy.PluginManager(plugin_module.app_group_lifecycle_plugin_name)
    pm.add_hookspecs(AppGroupLifecyclePluginSpec)
    pm.register(plugin_module)  # Register the hook wrappers
    pm.register(test_plugin_instance, name=DummyPlugin.ID)

    # Mock the hook getter to return our test PluginManager's hook
    mocker.patch.object(plugin_module, "_cached_app_group_lifecycle_hook", pm.hook)
    mocker.patch.object(plugin_module, "_cached_plugin_registry", None)

    yield test_plugin_instance

    # Reset caches
    plugin_module._cached_app_group_lifecycle_hook = None
    plugin_module._cached_plugin_registry = None


class TestPluginRegistration:
    """Tests for plugin registration and discovery."""

    def test_plugin_metadata(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test that plugin metadata is correctly retrieved."""
        plugins = get_app_group_lifecycle_plugins()
        test_plugin_meta = next((p for p in plugins if p.id == DummyPlugin.ID), None)
        assert test_plugin_meta is not None
        assert test_plugin_meta.display_name == "Test Plugin"
        assert test_plugin_meta.description == "A test plugin for unit testing"

    def test_get_plugin_config_properties(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test retrieving plugin configuration properties."""
        app_props = get_app_group_lifecycle_plugin_app_config_properties(DummyPlugin.ID)
        assert "enabled" in app_props
        assert "category" in app_props
        assert app_props["enabled"].required is True
        assert app_props["category"].required is False


class TestPluginAPIEndpoints:
    """Tests for plugin-related API endpoints."""

    def test_list_plugins(self, client: FlaskClient, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test GET /api/plugins/app-group-lifecycle returns all plugins."""
        url = url_for("api-plugins.app_group_lifecycle_plugins")
        response = client.get(url)
        assert response.status_code == 200

        data = response.get_json()
        assert isinstance(data, list)
        plugin_ids = [p["id"] for p in data]
        assert DummyPlugin.ID in plugin_ids

    def test_get_app_config_properties(self, client: FlaskClient, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/app-config-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_app_config_props", plugin_id=DummyPlugin.ID)
        response = client.get(url)
        assert response.status_code == 200

        data = response.get_json()
        assert "enabled" in data
        assert "category" in data
        assert data["enabled"]["required"] is True

    def test_get_group_config_properties(self, client: FlaskClient, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/group-config-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_group_config_props", plugin_id=DummyPlugin.ID)
        response = client.get(url)
        assert response.status_code == 200

        data = response.get_json()
        assert "group_id" in data
        assert data["group_id"]["required"] is True

    def test_get_app_status_properties(self, client: FlaskClient, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/app-status-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_app_status_props", plugin_id=DummyPlugin.ID)
        response = client.get(url)
        assert response.status_code == 200

        data = response.get_json()
        assert "last_sync" in data

    def test_get_group_status_properties(self, client: FlaskClient, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/group-status-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_group_status_props", plugin_id=DummyPlugin.ID)
        response = client.get(url)
        assert response.status_code == 200

        data = response.get_json()
        assert "member_count" in data

    def test_get_nonexistent_plugin(self, client: FlaskClient, db: SQLAlchemy) -> None:
        """Test that requesting a non-existent plugin returns 404."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_app_config_props", plugin_id="nonexistent_plugin")
        response = client.get(url)
        assert response.status_code == 404


class TestPluginConfigAuthorization:
    """Tests for plugin configuration authorization - positive cases (should succeed)."""

    def test_access_admin_can_configure_plugin_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin
    ) -> None:
        """Test that Access admins can configure plugins on apps."""
        # Use the default Access admin user (wumpus@discord.com) created in conftest
        # No need to create a new user or modify app.config["CURRENT_OKTA_USER_EMAIL"]
        test_app = AppFactory.build(name="TestApp", description="Test App")

        db.session.add(test_app)
        db.session.commit()

        # Configure plugin on the test app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

        response_data = response.get_json()
        assert response_data["app_group_lifecycle_plugin"] == DummyPlugin.ID
        assert response_data["plugin_data"][DummyPlugin.ID]["configuration"]["enabled"] is True

    def test_app_owner_cannot_configure_plugin_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin
    ) -> None:
        """Test that app owners (non-Access admins) cannot configure plugins on apps."""
        # Create app owner user

        app_owner = OktaUserFactory.build()
        test_app = AppFactory.build(name="TestApp2", description="Test App 2")
        test_app_owner_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_owner=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        )

        db.session.add(app_owner)
        db.session.add(test_app)
        db.session.add(test_app_owner_group)

        # Make the user an owner of the test app (but not Access admin) by directly adding membership
        membership = OktaUserGroupMember(user_id=app_owner.id, group_id=test_app_owner_group.id, is_owner=True)
        db.session.add(membership)
        db.session.commit()

        # Set current user to app owner
        app.config["CURRENT_OKTA_USER_EMAIL"] = app_owner.email

        # Try to configure plugin on the test app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 403

    def test_app_owner_cannot_modify_existing_plugin_config_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin
    ) -> None:
        """Test that app owners cannot modify existing plugin configuration."""
        # Create app owner

        app_owner = OktaUserFactory.build()
        test_app = AppFactory.build(
            name="TestApp3",
            description="Test App 3",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "category": "original_id"}}},
        )
        test_app_owner_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_owner=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        )

        db.session.add(app_owner)
        db.session.add(test_app)
        db.session.add(test_app_owner_group)

        # Make app_owner an owner of the test app by directly adding membership
        membership = OktaUserGroupMember(user_id=app_owner.id, group_id=test_app_owner_group.id, is_owner=True)
        db.session.add(membership)
        db.session.commit()

        # Set current user to app owner
        app.config["CURRENT_OKTA_USER_EMAIL"] = app_owner.email

        # Try to modify plugin configuration
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "plugin_data": {
                DummyPlugin.ID: {
                    "configuration": {
                        "enabled": False,  # Changed
                        "category": "modified_id",  # Changed
                    }
                }
            },
        }

        response = client.put(url, json=data)
        assert response.status_code == 403

    def test_access_admin_can_configure_plugin_at_group_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test that Access admins can configure plugins on groups."""
        # Use the default Access admin user (wumpus@discord.com) created in conftest
        test_app = AppFactory.build(
            name="TestApp4", description="Test App 4", app_group_lifecycle_plugin=DummyPlugin.ID
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.commit()

        # Mock Okta update_group call
        mocker.patch.object(okta, "update_group")

        # Configure plugin on the group
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "external_group_123"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

    def test_app_owner_can_configure_plugin_at_group_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test that app owners can configure plugins on their app's groups."""
        # Create app owner

        app_owner = OktaUserFactory.build()
        test_app = AppFactory.build(
            name="TestApp5", description="Test App 5", app_group_lifecycle_plugin=DummyPlugin.ID
        )
        test_app_owner_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_owner=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}",
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup2",
        )

        db.session.add(app_owner)
        db.session.add(test_app)
        db.session.add(test_app_owner_group)
        db.session.add(test_group)

        # Make user app owner by directly adding membership
        membership = OktaUserGroupMember(user_id=app_owner.id, group_id=test_app_owner_group.id, is_owner=True)
        db.session.add(membership)
        db.session.commit()

        # Set current user
        app.config["CURRENT_OKTA_USER_EMAIL"] = app_owner.email

        # Mock Okta update_group call
        mocker.patch.object(okta, "update_group")

        # Configure plugin on the group
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "external_group_456"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

    def test_group_owner_cannot_configure_plugin_at_group_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin
    ) -> None:
        """Test that group owners (non-app owners) cannot configure plugins on groups."""
        # Create group owner (but not app owner)

        group_owner = OktaUserFactory.build()
        test_app = AppFactory.build(
            name="TestApp6", description="Test App 6", app_group_lifecycle_plugin=DummyPlugin.ID
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup3",
        )

        db.session.add(group_owner)
        db.session.add(test_app)
        db.session.add(test_group)

        # Make user a group owner (not app owner) by directly adding membership
        membership = OktaUserGroupMember(user_id=group_owner.id, group_id=test_group.id, is_owner=True)
        db.session.add(membership)
        db.session.commit()

        # Set current user
        app.config["CURRENT_OKTA_USER_EMAIL"] = group_owner.email

        # Try to configure plugin on the group
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "external_group_789"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 403


class TestPluginHelperFunctions:
    """Tests for plugin helper functions like get_config_value, set_status_value, etc."""

    def test_get_config_value(self, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test getting configuration values from plugin data."""
        test_app = AppFactory.build(
            name="TestApp7",
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id_123"}}},
        )
        db.session.add(test_app)
        db.session.commit()

        enabled = get_config_value(test_app, "enabled", DummyPlugin.ID)
        category = get_config_value(test_app, "category", DummyPlugin.ID)

        assert enabled is True
        assert category == "test_id_123"

    def test_get_status_value(self, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test getting status values from plugin data."""
        test_app = AppFactory.build(
            name="TestApp8",
            plugin_data={DummyPlugin.ID: {"status": {"last_sync": "2025-01-15T10:30:00Z", "sync_count": 42}}},
        )
        db.session.add(test_app)
        db.session.commit()

        last_sync = get_status_value(test_app, "last_sync", DummyPlugin.ID)
        sync_count = get_status_value(test_app, "sync_count", DummyPlugin.ID)

        assert last_sync == "2025-01-15T10:30:00Z"
        assert sync_count == 42

    def test_set_status_value(self, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test setting status values in plugin data."""
        test_app = AppFactory.build(name="TestApp9", plugin_data={})
        db.session.add(test_app)
        db.session.commit()

        set_status_value(test_app, "last_sync", "2025-01-15T11:00:00Z", DummyPlugin.ID)
        db.session.commit()

        # Refresh from DB
        db.session.expire(test_app)

        last_sync = get_status_value(test_app, "last_sync", DummyPlugin.ID)
        assert last_sync == "2025-01-15T11:00:00Z"


class TestPluginValidation:
    """Tests for plugin configuration validation."""

    def test_valid_app_config(self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test that valid app configuration is accepted."""
        test_app = AppFactory.build(name="TestApp10")

        db.session.add(test_app)
        db.session.commit()

        # Valid configuration
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "valid_id"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

    def test_invalid_app_config(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin
    ) -> None:
        """Test that invalid app configuration is rejected."""
        test_app = AppFactory.build(name="TestApp11")

        db.session.add(test_app)
        db.session.commit()

        # Invalid configuration (missing required 'enabled' field)
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {
                DummyPlugin.ID: {
                    "configuration": {
                        "category": "some_id"
                        # Missing 'enabled' which is required
                    }
                }
            },
        }

        response = client.put(url, json=data)
        assert response.status_code == 400
        assert "enabled" in str(response.get_json())

    def test_valid_group_config(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test that valid group configuration is accepted."""
        test_app = AppFactory.build(name="TestApp12", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.commit()

        # Mock Okta update_group call
        mocker.patch.object(okta, "update_group")

        # Valid configuration
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "external_123"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

    def test_invalid_group_config(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin
    ) -> None:
        """Test that invalid group configuration is rejected."""
        test_app = AppFactory.build(name="TestApp13", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup2",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.commit()

        # Invalid configuration (missing required 'group_id' field)
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {
                DummyPlugin.ID: {
                    "configuration": {
                        # Missing 'group_id' which is required
                    }
                }
            },
        }

        response = client.put(url, json=data)
        assert response.status_code == 400
        assert "group_id" in str(response.get_json())


class TestPluginDataRestore:
    """Tests for the restore_unchanged_app_lifecycle_plugin_data function."""

    def test_restore_unchanged_app_data(self, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test that restore function merges configuration updates while preserving status."""
        # Create an app with existing plugin data (this simulates the OLD state before update)
        test_app = AppFactory.build(name="TestAppRestore1")
        db.session.add(test_app)
        db.session.commit()

        # Save the OLD complete plugin data (before the update)
        old_plugin_data = {
            DummyPlugin.ID: {
                "configuration": {"enabled": True, "category": "original"},
                "status": {"last_sync": "2025-01-01T00:00:00Z", "sync_count": 10},
            }
        }

        # Simulate a partial update from the request (NEW data - only configuration changes)
        test_app.plugin_data = {
            DummyPlugin.ID: {
                "configuration": {"enabled": False},  # Partial update - only changed field
                "status": {},  # No status changes from request
            }
        }

        # Restore should merge the NEW configuration into OLD while preserving status
        merge_app_lifecycle_plugin_data(test_app, old_plugin_data)

        # Check that configuration was updated with the new value
        assert test_app.plugin_data[DummyPlugin.ID]["configuration"]["enabled"] is False
        # Check that unchanged configuration field was preserved
        assert test_app.plugin_data[DummyPlugin.ID]["configuration"]["category"] == "original"
        # Check that status was preserved from old data
        assert test_app.plugin_data[DummyPlugin.ID]["status"]["last_sync"] == "2025-01-01T00:00:00Z"
        assert test_app.plugin_data[DummyPlugin.ID]["status"]["sync_count"] == 10

    def test_restore_unchanged_group_data(self, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test that restore function works with app groups."""
        test_app = AppFactory.build(name="TestAppRestore2", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}RestoreTest",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        db.session.commit()

        # Save the OLD complete plugin data (before the update)
        old_plugin_data = {
            DummyPlugin.ID: {"configuration": {"group_id": "external_123"}, "status": {"member_count": 5}}
        }

        # Simulate a partial update from the request (NEW data)
        test_group.plugin_data = {
            DummyPlugin.ID: {
                "configuration": {"group_id": "external_456"},  # Changed value
                "status": {},  # No status changes from request
            }
        }

        merge_app_lifecycle_plugin_data(test_group, old_plugin_data)

        # Check configuration was updated with new value
        assert test_group.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "external_456"
        # Check status was preserved from old data
        assert test_group.plugin_data[DummyPlugin.ID]["status"]["member_count"] == 5

    def test_restore_ignores_non_plugin_data(self, db: SQLAlchemy, test_plugin: DummyPlugin) -> None:
        """Test that restore function only processes registered plugin IDs."""
        test_app = AppFactory.build(name="TestAppRestore3")
        db.session.add(test_app)
        db.session.commit()

        # OLD data includes both a non-existent plugin and the valid plugin
        old_plugin_data = {
            "non_existent_plugin": {"configuration": {"some_key": "some_value"}, "status": {"some_status": "value"}},
            DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {"last_sync": "2025-01-01T00:00:00Z"}},
        }

        # NEW data from request (partial update)
        test_app.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": False}, "status": {}}}

        # Should not error and should only process the registered plugin
        merge_app_lifecycle_plugin_data(test_app, old_plugin_data)

        # Check the valid plugin was processed correctly
        assert test_app.plugin_data[DummyPlugin.ID]["configuration"]["enabled"] is False
        assert test_app.plugin_data[DummyPlugin.ID]["status"]["last_sync"] == "2025-01-01T00:00:00Z"
        # The non-existent plugin should not be in the result
        assert "non_existent_plugin" not in test_app.plugin_data


class TestPluginDirectFunctions:
    """Tests for direct function calls to plugin functions."""

    def test_get_group_config_properties(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test getting group-level configuration properties directly."""
        props = get_app_group_lifecycle_plugin_group_config_properties(DummyPlugin.ID)

        assert "group_id" in props
        assert props["group_id"].required is True
        assert props["group_id"].type == "text"

    def test_validate_app_config_direct(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test validating app configuration directly."""
        # Valid configuration
        valid_config: dict[str, object] = {"enabled": True, "category": "test"}
        plugin_data = {DummyPlugin.ID: {"configuration": valid_config, "status": {}}}

        errors = validate_app_group_lifecycle_plugin_app_config(plugin_data, DummyPlugin.ID)
        assert errors == {}

        # Invalid configuration (missing required field)
        invalid_config: dict[str, object] = {"category": "test"}  # Missing "enabled"
        plugin_data = {DummyPlugin.ID: {"configuration": invalid_config, "status": {}}}

        errors = validate_app_group_lifecycle_plugin_app_config(plugin_data, DummyPlugin.ID)
        assert "enabled" in errors

    def test_validate_group_config_direct(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test validating group configuration directly."""
        # Valid configuration
        valid_config: dict[str, object] = {"group_id": "external_123"}
        plugin_data = {DummyPlugin.ID: {"configuration": valid_config, "status": {}}}

        errors = validate_app_group_lifecycle_plugin_group_config(plugin_data, DummyPlugin.ID)
        assert errors == {}

        # Invalid configuration (missing required field)
        invalid_config: dict[str, object] = {}  # Missing "group_id"
        plugin_data = {DummyPlugin.ID: {"configuration": invalid_config, "status": {}}}

        errors = validate_app_group_lifecycle_plugin_group_config(plugin_data, DummyPlugin.ID)
        assert "group_id" in errors

    def test_get_app_status_properties(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test getting app-level status properties directly."""
        props = get_app_group_lifecycle_plugin_app_status_properties(DummyPlugin.ID)

        assert "last_sync" in props
        assert props["last_sync"].type == "date"
        assert props["last_sync"].display_name == "Last Sync"

    def test_get_group_status_properties(self, app: Flask, test_plugin: DummyPlugin) -> None:
        """Test getting group-level status properties directly."""
        props = get_app_group_lifecycle_plugin_group_status_properties(DummyPlugin.ID)

        assert "member_count" in props
        assert props["member_count"].type == "number"
        assert props["member_count"].display_name == "Member Count"


class TestPluginMembershipHooks:
    """Tests for plugin lifecycle hooks when members are added/removed."""

    def test_direct_member_removed_loses_all_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is called when a member is removed and loses all access to the group."""
        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_MemberRemoved",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_123"}}},
        )
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_remove_user_from_group", return_value=None)

        # Add the user to the group (this will trigger members_added hook)
        from api.operations import ModifyGroupUsers

        ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the user from the group (user has no other access paths)
        ModifyGroupUsers(group=test_group, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user lost all access
        assert len(test_plugin.members_removed_calls) == 1
        assert test_plugin.members_removed_calls[0] == (test_group.id, [user.id])

    def test_direct_member_removed_but_has_redundant_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is NOT called when trying to remove direct access but user only has role-based access."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RedundantMember",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup2",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_124"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleForRedundancy", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_remove_user_from_group", return_value=None)

        # Associate the app group with a role
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them access to the group via role)
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Try to remove direct membership (but user only has role-based access, no direct access to remove)
        # This should not trigger the hook because user still has role-based access
        ModifyGroupUsers(group=test_group, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user still has access via role
        assert len(test_plugin.members_removed_calls) == 0

    def test_direct_member_added_gains_first_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is called when a member is added for the first time."""
        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_MemberAdded",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup3",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_125"}}},
        )
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        # Add the user to the group for the first time
        from api.operations import ModifyGroupUsers

        ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user gained first access
        assert len(test_plugin.members_added_calls) == 1
        assert test_plugin.members_added_calls[0] == (test_group.id, [user.id])

    def test_direct_member_added_but_already_has_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is NOT called when a member is added directly but already has access via a role."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_MemberAlreadyHasAccess",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup4",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_126"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleForExisting", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        # Associate the app group with a role
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them access to the group)
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding via role
        test_plugin.members_added_calls.clear()

        # Add the user directly to the group (they already have access via role)
        ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user already had access via role
        assert len(test_plugin.members_added_calls) == 0

    def test_role_member_removed_loses_all_access_to_associated_group(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is called when a role member is removed and loses all access to role-associated groups."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleRemoved",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}RoleGroup",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_127"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRole", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_remove_user_from_group", return_value=None)

        # Associate the app group with the role as a member group
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (which gives them access to the associated group)
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the user from the role (user loses access to associated group)
        ModifyGroupUsers(group=role_group, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called for the associated group because user lost all access
        assert len(test_plugin.members_removed_calls) == 1
        assert test_plugin.members_removed_calls[0] == (test_group.id, [user.id])

    def test_role_member_removed_but_has_redundant_access_via_another_role(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is NOT called when a role member is removed but still has access via another role."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleRedundant",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}RoleGroup2",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_128"}}},
        )
        role_group_1 = RoleGroupFactory.build(name="TestRole1", is_managed=True)
        role_group_2 = RoleGroupFactory.build(name="TestRole2", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group_1)
        db.session.add(role_group_2)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_remove_user_from_group", return_value=None)

        # Associate the app group with both roles as member groups
        ModifyRoleGroups(role_group=role_group_1, groups_to_add=[test_group.id], sync_to_okta=False).execute()
        ModifyRoleGroups(role_group=role_group_2, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to both roles (gives them redundant access to the associated group)
        ModifyGroupUsers(group=role_group_1, members_to_add=[user.id], sync_to_okta=False).execute()
        ModifyGroupUsers(group=role_group_2, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the user from one role (user still has access via the other role)
        ModifyGroupUsers(group=role_group_1, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user still has access via role_group_2
        assert len(test_plugin.members_removed_calls) == 0

    def test_role_member_added_gains_first_access_to_associated_group(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is called when a role member is added and gains first access to role-associated groups."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleAdded",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}RoleGroup3",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_129"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRole3", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        # Associate the app group with the role as a member group
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them first access to the associated group)
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called for the associated group because user gained first access
        assert len(test_plugin.members_added_calls) == 1
        assert test_plugin.members_added_calls[0] == (test_group.id, [user.id])

    def test_role_member_added_but_already_has_access_to_associated_group(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is NOT called when a role member is added but already has access to role-associated groups."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleAlreadyHasAccess",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}RoleGroup4",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_130"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRole4", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        # First, give the user direct access to the group
        ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from first add
        test_plugin.members_added_calls.clear()

        # Now associate the app group with a role and add the user to the role
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called for the associated group because user already had access
        assert len(test_plugin.members_added_calls) == 0

    def test_role_removed_from_group_user_loses_all_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is called when a role is removed from a group and user loses all access."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleRemoved",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup_RoleRemoved",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_role_removed"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleToRemove", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_remove_user_from_group", return_value=None)

        # Associate the app group with the role
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them access to the group via role)
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the group from the role (user loses all access to the group)
        ModifyRoleGroups(role_group=role_group, groups_to_remove=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user lost all access
        assert len(test_plugin.members_removed_calls) == 1
        assert test_plugin.members_removed_calls[0] == (test_group.id, [user.id])

    def test_role_removed_from_group_user_has_redundant_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is NOT called when a role is removed from a group but user has direct access."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleRemovedRedundant",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup_RoleRemovedRedundant",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_role_removed_redundant"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleToRemoveRedundant", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)
        mocker.patch.object(okta, "async_remove_user_from_group", return_value=None)

        # Give the user direct access to the group
        ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Also give them role-based access
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the group from the role (user still has direct access to the group)
        ModifyRoleGroups(role_group=role_group, groups_to_remove=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user still has direct access
        assert len(test_plugin.members_removed_calls) == 0

    def test_role_added_to_group_user_gains_first_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is called when a role is added to a group and user gains first access."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleAdded",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup_RoleAdded",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_role_added"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleToAdd", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        # Add the user to the role first (before associating the group with the role)
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding user to role
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Now associate the app group with the role (user gains first access to the group)
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user gained first access
        assert len(test_plugin.members_added_calls) == 1
        assert test_plugin.members_added_calls[0] == (test_group.id, [user.id])

    def test_role_added_to_group_user_already_has_access(
        self, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Test hook is NOT called when a role is added to a group but user already has direct access."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        # Setup: Create an app group with the plugin enabled
        test_app = AppFactory.build(
            name="TestApp_RoleAddedRedundant",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup_RoleAddedRedundant",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_role_added_redundant"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleToAddRedundant", is_managed=True)
        user = OktaUserFactory.build()

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.add(role_group)
        db.session.add(user)
        db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "async_add_user_to_group", return_value=None)

        # Give the user direct access to the group first
        ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Add the user to the role
        ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Now associate the app group with the role (user already has direct access)
        ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user already had access
        assert len(test_plugin.members_added_calls) == 0


class TestPluginAuditLogging:
    """Tests for plugin configuration audit logging."""

    def test_audit_log_plugin_assignment_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, caplog: Any
    ) -> None:
        """Test that assigning a plugin to an app creates an audit log entry."""
        import json
        import logging

        from api.views.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(name="TestApp", description="Test App")
        db.session.add(test_app)
        db.session.commit()

        # Assign plugin to the app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

        # Check audit log
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        assert len(audit_logs) > 0

        # Find the plugin modification log
        plugin_logs = [log for log in audit_logs if EventType.app_modify_plugin.value in log.message]
        assert len(plugin_logs) == 1

        log_data = json.loads(plugin_logs[0].message)
        assert log_data["event_type"] == EventType.app_modify_plugin.value
        assert log_data["app"]["id"] == test_app.id
        assert log_data["old_app_group_lifecycle_plugin"] is None
        assert log_data["current_user_email"] == app.config["CURRENT_OKTA_USER_EMAIL"]

    def test_audit_log_plugin_configuration_change_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, caplog: Any
    ) -> None:
        """Test that changing app-level plugin configuration creates an audit log entry."""
        import json
        import logging

        from api.views.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp2",
            description="Test App 2",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "category": "original"}}},
        )
        db.session.add(test_app)
        db.session.commit()

        caplog.clear()

        # Update plugin configuration
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": False, "category": "updated"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

        # Check audit log
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        assert len(audit_logs) > 0

        # Find the plugin modification log
        plugin_logs = [log for log in audit_logs if EventType.app_modify_plugin.value in log.message]
        assert len(plugin_logs) == 1

        log_data = json.loads(plugin_logs[0].message)
        assert log_data["event_type"] == EventType.app_modify_plugin.value
        assert log_data["app"]["id"] == test_app.id
        assert log_data["old_app_group_lifecycle_plugin"] == DummyPlugin.ID
        assert log_data["old_plugin_data"][DummyPlugin.ID]["configuration"]["category"] == "original"
        assert log_data["current_user_email"] == app.config["CURRENT_OKTA_USER_EMAIL"]

    def test_audit_log_plugin_removal_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, caplog: Any
    ) -> None:
        """Test that removing a plugin from an app creates an audit log entry."""
        import json
        import logging

        from api.views.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp3",
            description="Test App 3",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        db.session.add(test_app)
        db.session.commit()

        caplog.clear()

        # Remove plugin from the app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {"name": test_app.name, "app_group_lifecycle_plugin": None}

        response = client.put(url, json=data)
        assert response.status_code == 200

        # Check audit log
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        assert len(audit_logs) > 0

        # Find the plugin modification log
        plugin_logs = [log for log in audit_logs if EventType.app_modify_plugin.value in log.message]
        assert len(plugin_logs) == 1

        log_data = json.loads(plugin_logs[0].message)
        assert log_data["event_type"] == EventType.app_modify_plugin.value
        assert log_data["old_app_group_lifecycle_plugin"] == DummyPlugin.ID
        assert log_data["current_user_email"] == app.config["CURRENT_OKTA_USER_EMAIL"]

    def test_audit_log_plugin_configuration_change_at_group_level(
        self,
        client: FlaskClient,
        db: SQLAlchemy,
        app: Flask,
        test_plugin: DummyPlugin,
        caplog: Any,
        mocker: MockerFixture,
    ) -> None:
        """Test that changing group-level plugin configuration creates an audit log entry."""
        import json
        import logging

        from api.views.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp4",
            description="Test App 4",
            app_group_lifecycle_plugin=DummyPlugin.ID,
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_owner=False,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup",
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "custom_tag": "original"}}},
        )

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.commit()

        caplog.clear()

        # Mock Okta update_group call
        mocker.patch.object(okta, "update_group")

        # Update group plugin configuration
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "name": test_group.name,
            "type": test_group.type,
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "external-456"}}},
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

        # Check audit log
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        assert len(audit_logs) > 0

        # Find the plugin modification log
        plugin_logs = [log for log in audit_logs if EventType.group_modify_plugin.value in log.message]
        assert len(plugin_logs) == 1

        log_data = json.loads(plugin_logs[0].message)
        assert log_data["event_type"] == EventType.group_modify_plugin.value
        assert log_data["group"]["id"] == test_group.id
        assert log_data["old_plugin_data"][DummyPlugin.ID]["configuration"]["custom_tag"] == "original"
        assert log_data["current_user_email"] == app.config["CURRENT_OKTA_USER_EMAIL"]

    def test_no_audit_log_when_plugin_unchanged_at_app_level(
        self, client: FlaskClient, db: SQLAlchemy, app: Flask, test_plugin: DummyPlugin, caplog: Any
    ) -> None:
        """Test that no audit log is created when plugin configuration is unchanged."""
        import logging

        from api.views.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp5",
            description="Test App 5",
            app_group_lifecycle_plugin=DummyPlugin.ID,
        )
        db.session.add(test_app)
        db.session.commit()

        caplog.clear()

        # Update app without changing plugin
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {"name": test_app.name, "description": "Updated description"}

        response = client.put(url, json=data)
        assert response.status_code == 200

        # Check that no plugin audit log was created
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        plugin_logs = [log for log in audit_logs if EventType.app_modify_plugin.value in log.message]
        assert len(plugin_logs) == 0

    def test_no_audit_log_when_plugin_unchanged_at_group_level(
        self,
        client: FlaskClient,
        db: SQLAlchemy,
        app: Flask,
        test_plugin: DummyPlugin,
        caplog: Any,
        mocker: MockerFixture,
    ) -> None:
        """Test that no audit log is created when group plugin configuration is unchanged."""
        import logging

        from api.views.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp6",
            description="Test App 6",
            app_group_lifecycle_plugin=DummyPlugin.ID,
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_owner=False,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}TestGroup2",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        db.session.commit()

        caplog.clear()

        # Mock Okta update_group call
        mocker.patch.object(okta, "update_group")

        # Update group without changing plugin
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "name": test_group.name,
            "type": test_group.type,
            "app_id": test_group.app_id,
            "description": "Updated description",
        }

        response = client.put(url, json=data)
        assert response.status_code == 200

        # Check that no plugin audit log was created
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        plugin_logs = [log for log in audit_logs if EventType.group_modify_plugin.value in log.message]
        assert len(plugin_logs) == 0
