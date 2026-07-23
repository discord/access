"""
Tests for the App Group Lifecycle Plugin functionality.

This includes tests for:
- Plugin registration and discovery
- Plugin configuration and validation
- API endpoints for plugin configuration
- Authorization checks for plugin configuration (positive and negative cases)
- Plugin lifecycle hooks
"""

from dataclasses import asdict
from typing import Any, Generator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.extensions import Db
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
    is_plugin_config_changed,
    merge_app_lifecycle_plugin_data,
    set_config_value,
    set_status_value,
    validate_app_group_lifecycle_plugin_app_config,
    validate_app_group_lifecycle_plugin_group_config,
)
from api.services import okta
from tests.factories import AppFactory, AppGroupFactory, OktaGroupFactory, OktaUserFactory, RoleGroupFactory


class DummyPlugin:
    """A simple test plugin for unit testing."""

    ID: str = "test_plugin"

    def __init__(self) -> None:
        self.group_created_calls: list[str] = []
        # Config observed at group_created time, to assert the hook sees final state
        # (i.e. that group_created fires after plugin_data is applied, not before).
        self.group_created_configs: list[dict[str, Any]] = []
        self.group_updated_calls: list[tuple[str, str, str]] = []
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
        self, plugin_id: str | None
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
    def validate_plugin_app_config(self, config: dict[str, Any], plugin_id: str | None) -> dict[str, str] | None:
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
        self, plugin_id: str | None
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
            "region": AppGroupLifecyclePluginConfigProperty(
                display_name="Region",
                help_text="Immutable region; set once at creation",
                type="text",
                required=False,
                immutable=True,
            ),
        }

    @hookimpl
    def validate_plugin_group_config(self, config: dict[str, Any], plugin_id: str | None) -> dict[str, str] | None:
        if plugin_id is not None and plugin_id != self.ID:
            return None

        errors: dict[str, str] = {}
        if "group_id" not in config:
            errors["group_id"] = "The 'group_id' field is required"
        elif not isinstance(config["group_id"], str):
            errors["group_id"] = "The 'group_id' field must be a string"
        # `region` is immutable; a value outside the allowed set models a constraint added
        # after some groups were created (i.e. a grandfathered/adopted value).
        if config.get("region") not in (None, "us", "eu"):
            errors["region"] = "The 'region' field must be 'us' or 'eu'"

        return errors

    @hookimpl
    def get_plugin_app_status_properties(
        self, plugin_id: str | None
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
        self, plugin_id: str | None
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
    async def group_created(self, session: AsyncSession, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        # Await a read through the AsyncSession. The hookspec promises an
        # AsyncSession; if an operation hands us something else (a broken bridge),
        # this raises and the call below is never recorded — so the recorded-calls
        # assertions in these tests fail loudly.
        (await session.scalars(select(AppGroup))).all()
        # A hook may also read group.app, which is lazy="raise_on_sql". The invoking
        # operation must eager-load AppGroup.app (or seed the identity map) so this
        # resolves without emitting SQL; otherwise it raises here and the recorded-
        # calls assertion fails. Guards that eager-load across every op path.
        _ = group.app.name
        self.group_created_calls.append(group.id)
        self.group_created_configs.append((group.plugin_data or {}).get(self.ID, {}).get("configuration", {}))

    @hookimpl
    async def group_updated(
        self, session: AsyncSession, group: AppGroup, old_name: str, old_description: str, plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        (await session.scalars(select(AppGroup))).all()  # exercise the AsyncSession (see group_created)
        _ = group.app.name  # exercise group.app eager-load (see group_created)
        self.group_updated_calls.append((group.id, old_name, old_description))

    @hookimpl
    async def group_deleted(self, session: AsyncSession, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        (await session.scalars(select(AppGroup))).all()  # exercise the AsyncSession (see group_created)
        _ = group.app.name  # exercise group.app eager-load (see group_created)
        self.group_deleted_calls.append(group.id)

    @hookimpl
    async def group_members_added(
        self, session: AsyncSession, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        (await session.scalars(select(AppGroup))).all()  # exercise the AsyncSession (see group_created)
        self.members_added_calls.append((group.id, [m.id for m in members]))

    @hookimpl
    async def group_members_removed(
        self, session: AsyncSession, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        (await session.scalars(select(AppGroup))).all()  # exercise the AsyncSession (see group_created)
        self.members_removed_calls.append((group.id, [m.id for m in members]))


@pytest.fixture
def test_plugin(app: FastAPI, mocker: MockerFixture) -> Generator[DummyPlugin, None, None]:
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


def test_config_property_immutable_defaults_false_and_serializes() -> None:
    prop = AppGroupLifecyclePluginConfigProperty(display_name="X")
    assert prop.immutable is False
    assert asdict(prop)["immutable"] is False

    prop2 = AppGroupLifecyclePluginConfigProperty(display_name="Y", immutable=True)
    assert asdict(prop2)["immutable"] is True


def test_config_property_suffix_rejected_on_non_text() -> None:
    # suffix is presentational and only rendered on text inputs; declaring it on a number/boolean
    # is a silently-inert mistake, so construction fails fast.
    AppGroupLifecyclePluginConfigProperty(display_name="X", type="text", suffix="@example.com")  # ok
    with pytest.raises(ValueError, match="suffix is only supported on text"):
        AppGroupLifecyclePluginConfigProperty(display_name="X", type="number", suffix="@example.com")


def test_plugin_schema_models_mirror_their_dataclasses() -> None:
    # The Pydantic wire models mirror the plugin dataclasses; routes serialize via
    # asdict() -> model_validate(), and Pydantic silently drops unknown keys, so a field added to
    # one side but not the other would vanish from the API response with no error. Guard the
    # mirror by asserting identical field names (the drift the review flagged).
    import dataclasses

    from api.plugins.app_group_lifecycle import (
        AppGroupLifecyclePluginConfigProperty as _ConfigDC,
    )
    from api.plugins.app_group_lifecycle import (
        AppGroupLifecyclePluginMetadata as _MetaDC,
    )
    from api.plugins.app_group_lifecycle import (
        AppGroupLifecyclePluginStatusProperty as _StatusDC,
    )
    from api.schemas.plugin_schemas import PluginConfigProp, PluginInfo, PluginStatusProp

    def dc_fields(dc: type) -> set[str]:
        return {f.name for f in dataclasses.fields(dc)}

    assert dc_fields(_ConfigDC) == set(PluginConfigProp.model_fields)
    assert dc_fields(_StatusDC) == set(PluginStatusProp.model_fields)
    assert dc_fields(_MetaDC) == set(PluginInfo.model_fields)


class TestPluginRegistration:
    """Tests for plugin registration and discovery."""

    def test_plugin_metadata(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
        """Test that plugin metadata is correctly retrieved."""
        plugins = get_app_group_lifecycle_plugins()
        test_plugin_meta = next((p for p in plugins if p.id == DummyPlugin.ID), None)
        assert test_plugin_meta is not None
        assert test_plugin_meta.display_name == "Test Plugin"
        assert test_plugin_meta.description == "A test plugin for unit testing"

    def test_get_plugin_config_properties(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
        """Test retrieving plugin configuration properties."""
        app_props = get_app_group_lifecycle_plugin_app_config_properties(DummyPlugin.ID)
        assert "enabled" in app_props
        assert "category" in app_props
        assert app_props["enabled"].required is True
        assert app_props["category"].required is False


class TestPluginAPIEndpoints:
    """Tests for plugin-related API endpoints."""

    async def test_list_plugins(self, client: AsyncClient, db: Db, test_plugin: DummyPlugin, url_for: Any) -> None:
        """Test GET /api/plugins/app-group-lifecycle returns all plugins."""
        url = url_for("api-plugins.app_group_lifecycle_plugins")
        response = await client.get(url)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        plugin_ids = [p["id"] for p in data]
        assert DummyPlugin.ID in plugin_ids

    async def test_get_app_config_properties(
        self, client: AsyncClient, db: Db, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/app-config-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_app_config_props", plugin_id=DummyPlugin.ID)
        response = await client.get(url)
        assert response.status_code == 200

        data = response.json()
        assert "enabled" in data
        assert "category" in data
        assert data["enabled"]["required"] is True

    async def test_get_group_config_properties(
        self, client: AsyncClient, db: Db, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/group-config-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_group_config_props", plugin_id=DummyPlugin.ID)
        response = await client.get(url)
        assert response.status_code == 200

        data = response.json()
        assert "group_id" in data
        assert data["group_id"]["required"] is True

    async def test_get_app_status_properties(
        self, client: AsyncClient, db: Db, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/app-status-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_app_status_props", plugin_id=DummyPlugin.ID)
        response = await client.get(url)
        assert response.status_code == 200

        data = response.json()
        assert "last_sync" in data

    async def test_get_group_status_properties(
        self, client: AsyncClient, db: Db, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test GET /api/plugins/app-group-lifecycle/<plugin_id>/group-status-props."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_group_status_props", plugin_id=DummyPlugin.ID)
        response = await client.get(url)
        assert response.status_code == 200

        data = response.json()
        assert "member_count" in data

    async def test_get_nonexistent_plugin(self, client: AsyncClient, db: Db, url_for: Any) -> None:
        """Test that requesting a non-existent plugin returns 404."""
        url = url_for("api-plugins.app_group_lifecycle_plugin_app_config_props", plugin_id="nonexistent_plugin")
        response = await client.get(url)
        assert response.status_code == 404

    async def test_plugin_not_found_returns_error_envelope(self, client: AsyncClient, db: Db, url_for: Any) -> None:
        """The plugin endpoints' 404 path must respond with
        `{"error": "..."}` (the React client reads the `error` field).
        The plugin router raises `PluginNotFoundError`, which the
        exception handler in `api/exception_handlers.py` serializes with
        the `error` envelope — distinct from the global RFC 9457
        problem-detail envelope used by every other HTTPException."""
        for endpoint in (
            "api-plugins.app_group_lifecycle_plugin_app_config_props",
            "api-plugins.app_group_lifecycle_plugin_group_config_props",
            "api-plugins.app_group_lifecycle_plugin_app_status_props",
            "api-plugins.app_group_lifecycle_plugin_group_status_props",
        ):
            url = url_for(endpoint, plugin_id="does-not-exist")
            response = await client.get(url)
            assert response.status_code == 404
            body = response.json()
            assert body == {"error": "Plugin 'does-not-exist' not found"}
            assert "message" not in body
            assert "detail" not in body


class TestPluginConfigAuthorization:
    """Tests for plugin configuration authorization - positive cases (should succeed)."""

    async def test_access_admin_can_configure_plugin_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test that Access admins can configure plugins on apps."""
        # Use the default Access admin user (wumpus@discord.com) created in conftest
        # No need to create a new user or modify settings.CURRENT_OKTA_USER_EMAIL
        test_app = AppFactory.build(name="TestApp", description="Test App")

        db.session.add(test_app)
        await db.session.commit()

        # Configure plugin on the test app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        response_data = response.json()
        assert response_data["app_group_lifecycle_plugin"] == DummyPlugin.ID
        assert response_data["plugin_data"][DummyPlugin.ID]["configuration"]["enabled"] is True

    async def test_app_owner_cannot_configure_plugin_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
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
        await db.session.commit()

        # Set current user to app owner
        app.state.current_user_email = app_owner.email

        # Try to configure plugin on the test app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 403

    async def test_app_owner_cannot_modify_existing_plugin_config_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
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
        await db.session.commit()

        # Set current user to app owner
        app.state.current_user_email = app_owner.email

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

        response = await client.put(url, json=data)
        assert response.status_code == 403

    async def test_access_admin_can_configure_plugin_at_group_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
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
        await db.session.commit()

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

        response = await client.put(url, json=data)
        assert response.status_code == 200

    async def test_app_owner_can_configure_plugin_at_group_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
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
        await db.session.commit()

        # Set current user
        app.state.current_user_email = app_owner.email

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

        response = await client.put(url, json=data)
        assert response.status_code == 200

    async def test_group_owner_cannot_configure_plugin_at_group_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
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
        await db.session.commit()

        # Set current user
        app.state.current_user_email = group_owner.email

        # Try to configure plugin on the group
        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "external_group_789"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 403


class TestPluginHelperFunctions:
    """Tests for plugin helper functions like get_config_value, set_status_value, etc."""

    async def test_get_config_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test getting configuration values from plugin data."""
        test_app = AppFactory.build(
            name="TestApp7",
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id_123"}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        enabled = get_config_value(test_app, "enabled", DummyPlugin.ID)
        category = get_config_value(test_app, "category", DummyPlugin.ID)

        assert enabled is True
        assert category == "test_id_123"

    async def test_get_status_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test getting status values from plugin data."""
        test_app = AppFactory.build(
            name="TestApp8",
            plugin_data={DummyPlugin.ID: {"status": {"last_sync": "2025-01-15T10:30:00Z", "sync_count": 42}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        last_sync = get_status_value(test_app, "last_sync", DummyPlugin.ID)
        sync_count = get_status_value(test_app, "sync_count", DummyPlugin.ID)

        assert last_sync == "2025-01-15T10:30:00Z"
        assert sync_count == 42

    async def test_set_status_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test setting status values in plugin data."""
        test_app = AppFactory.build(name="TestApp9", plugin_data={})
        db.session.add(test_app)
        await db.session.commit()

        set_status_value(test_app, "last_sync", "2025-01-15T11:00:00Z", DummyPlugin.ID)
        await db.session.commit()

        # Refresh from DB (expire + sync lazy read would raise under async)
        await db.session.refresh(test_app)

        last_sync = get_status_value(test_app, "last_sync", DummyPlugin.ID)
        assert last_sync == "2025-01-15T11:00:00Z"

    async def test_set_config_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test setting configuration values in plugin data."""
        test_app = AppFactory.build(name="TestApp9b", plugin_data={})
        db.session.add(test_app)
        await db.session.commit()

        set_config_value(test_app, "category", "inferred_id", DummyPlugin.ID)
        await db.session.commit()
        await db.session.refresh(test_app)

        assert get_config_value(test_app, "category", DummyPlugin.ID) == "inferred_id"

    def test_is_plugin_config_changed(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Only configuration differences count as a change; status differences do not."""
        base = {DummyPlugin.ID: {"configuration": {"group_id": "g1"}, "status": {"member_count": 1}}}

        # Identical configuration -> not changed, even when status differs.
        status_only = {DummyPlugin.ID: {"configuration": {"group_id": "g1"}, "status": {"member_count": 9}}}
        assert is_plugin_config_changed(base, status_only, DummyPlugin.ID) is False

        # Different configuration -> changed.
        config_changed = {DummyPlugin.ID: {"configuration": {"group_id": "g2"}, "status": {"member_count": 1}}}
        assert is_plugin_config_changed(base, config_changed, DummyPlugin.ID) is True

        # Missing plugin entries are treated as empty configuration.
        assert is_plugin_config_changed({}, {}, DummyPlugin.ID) is False


class TestPluginValidation:
    """Tests for plugin configuration validation."""

    async def test_valid_app_config(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test that valid app configuration is accepted."""
        test_app = AppFactory.build(name="TestApp10")

        db.session.add(test_app)
        await db.session.commit()

        # Valid configuration
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "valid_id"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

    async def test_invalid_app_config(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test that invalid app configuration is rejected."""
        test_app = AppFactory.build(name="TestApp11")

        db.session.add(test_app)
        await db.session.commit()

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

        response = await client.put(url, json=data)
        assert response.status_code == 400
        assert "enabled" in str(response.json())

    async def test_valid_group_config(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that valid group configuration is accepted."""
        test_app = AppFactory.build(name="TestApp12", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

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

        response = await client.put(url, json=data)
        assert response.status_code == 200

    async def test_put_group_rejects_immutable_field_change(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that changing an immutable group configuration field is rejected."""
        test_app = AppFactory.build(name="TestAppImm1", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Immg",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}, "status": {}}},
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "eu"}}},
        }
        response = await client.put(url, json=data)
        assert response.status_code == 400
        assert "region" in response.json()["detail"]

    async def test_put_group_allows_mutable_field_change(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that changing a mutable group configuration field is accepted and persisted."""
        test_app = AppFactory.build(name="TestAppImm2", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Mutg",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}, "status": {}}},
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g2", "region": "us"}}},
        }
        response = await client.put(url, json=data)
        assert response.status_code == 200
        assert response.json()["plugin_data"][DummyPlugin.ID]["configuration"]["group_id"] == "g2"

    async def test_invalid_group_config(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Test that invalid group configuration is rejected."""
        test_app = AppFactory.build(name="TestApp13", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup2",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

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

        response = await client.put(url, json=data)
        assert response.status_code == 400
        assert "group_id" in str(response.json())


class TestPluginDataRestore:
    """Tests for the restore_unchanged_app_lifecycle_plugin_data function."""

    async def test_restore_unchanged_app_data(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test that restore function merges configuration updates while preserving status."""
        # Create an app with existing plugin data (this simulates the OLD state before update)
        test_app = AppFactory.build(name="TestAppRestore1")
        db.session.add(test_app)
        await db.session.commit()

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

    async def test_restore_unchanged_group_data(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test that restore function works with app groups."""
        test_app = AppFactory.build(name="TestAppRestore2", app_group_lifecycle_plugin=DummyPlugin.ID)
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}RestoreTest",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

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

    async def test_restore_ignores_non_plugin_data(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test that restore function only processes registered plugin IDs."""
        test_app = AppFactory.build(name="TestAppRestore3")
        db.session.add(test_app)
        await db.session.commit()

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

    def test_get_group_config_properties(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
        """Test getting group-level configuration properties directly."""
        props = get_app_group_lifecycle_plugin_group_config_properties(DummyPlugin.ID)

        assert "group_id" in props
        assert props["group_id"].required is True
        assert props["group_id"].type == "text"

    def test_validate_app_config_direct(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
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

    def test_validate_group_config_direct(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
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

    def test_get_app_status_properties(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
        """Test getting app-level status properties directly."""
        props = get_app_group_lifecycle_plugin_app_status_properties(DummyPlugin.ID)

        assert "last_sync" in props
        assert props["last_sync"].type == "date"
        assert props["last_sync"].display_name == "Last Sync"

    def test_get_group_status_properties(self, app: FastAPI, test_plugin: DummyPlugin) -> None:
        """Test getting group-level status properties directly."""
        props = get_app_group_lifecycle_plugin_group_status_properties(DummyPlugin.ID)

        assert "member_count" in props
        assert props["member_count"].type == "number"
        assert props["member_count"].display_name == "Member Count"


class TestPluginGroupUpdatedHook:
    """Tests for the group_updated lifecycle hook fired via the group PUT endpoint."""

    async def test_name_change_fires_hook(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that renaming an app group fires the group_updated hook with old name."""
        test_app = AppFactory.build(
            name="TestAppUpdated1",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}OldName",
            description="Same description",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        old_name = test_group.name
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        new_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}NewName"
        data = {
            "type": "app_group",
            "name": new_name,
            "description": "Same description",
            "app_id": test_group.app_id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_updated_calls) == 1
        group_id, hook_old_name, hook_old_desc = test_plugin.group_updated_calls[0]
        assert group_id == test_group.id
        assert hook_old_name == old_name
        assert hook_old_desc == "Same description"

    async def test_description_change_fires_hook(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that changing an app group's description fires the group_updated hook."""
        test_app = AppFactory.build(
            name="TestAppUpdated2",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}SameName",
            description="Old description",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "New description",
            "app_id": test_group.app_id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_updated_calls) == 1
        _, hook_old_name, hook_old_desc = test_plugin.group_updated_calls[0]
        assert hook_old_name == test_group.name
        assert hook_old_desc == "Old description"

    async def test_no_change_does_not_fire_hook(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that updating a group without changing name or description does not fire the hook."""
        test_app = AppFactory.build(
            name="TestAppUpdated3",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Unchanged",
            description="Same",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "Same",
            "app_id": test_group.app_id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_updated_calls) == 0

    async def test_hook_not_fired_without_lifecycle_plugin(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that group_updated hook does not fire when the app has no lifecycle plugin configured."""
        test_app = AppFactory.build(
            name="TestAppUpdated4",
            # No app_group_lifecycle_plugin set
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}NoPlugin",
            description="Old",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Renamed",
            "description": "New",
            "app_id": test_group.app_id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_updated_calls) == 0

    async def test_null_description_normalized(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that a group with NULL description is treated as empty string for comparison."""
        test_app = AppFactory.build(
            name="TestAppUpdated5",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}NullDesc",
            description=None,
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        # NULL -> "" should not be considered a change
        assert len(test_plugin.group_updated_calls) == 0


class TestPluginGroupDeletedOnTypeChange:
    """Tests for the group_deleted hook fired when an AppGroup's type is changed to Group or Role."""

    async def test_app_group_to_okta_group_fires_group_deleted(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that converting an AppGroup to a plain Group fires the group_deleted hook."""
        test_app = AppFactory.build(
            name="TestAppTypeChange1",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}ToDelete",
            description="Will change type",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {
            "type": "okta_group",
            "name": "Converted-To-OktaGroup",
            "description": "Now a plain group",
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200
        assert response.json()["type"] == "okta_group"

        assert len(test_plugin.group_deleted_calls) == 1
        assert test_plugin.group_deleted_calls[0] == group_id

    async def test_app_group_to_role_group_fires_group_deleted(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that converting an AppGroup to a Role fires the group_deleted hook."""
        test_app = AppFactory.build(
            name="TestAppTypeChange2",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}ToRole",
            description="Will become a role",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {
            "type": "role_group",
            "name": "Role-ConvertedToRole",
            "description": "Now a role",
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200
        assert response.json()["type"] == "role_group"

        assert len(test_plugin.group_deleted_calls) == 1
        assert test_plugin.group_deleted_calls[0] == group_id

    async def test_no_hook_without_lifecycle_plugin(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that group_deleted hook does not fire when the app has no lifecycle plugin."""
        test_app = AppFactory.build(
            name="TestAppTypeChange3",
            # No app_group_lifecycle_plugin
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}NoPlugin",
            description="No plugin configured",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "okta_group",
            "name": "NoPlugin-Converted",
            "description": "No plugin",
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_deleted_calls) == 0


class TestPluginGroupCreatedOnTypeChange:
    """Tests for the group_created hook fired when a Group or Role is converted to an AppGroup."""

    async def test_okta_group_to_app_group_fires_group_created(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that converting an OktaGroup to an AppGroup fires the group_created hook."""
        test_app = AppFactory.build(
            name="TestAppCreate1",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = OktaGroupFactory.build(name="PlainGroup-ToAppGroup")
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {
            "type": "app_group",
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Created",
            "description": "Now an app group",
            "app_id": test_app.id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200
        assert response.json()["type"] == "app_group"

        assert len(test_plugin.group_created_calls) == 1
        assert test_plugin.group_created_calls[0] == group_id

    async def test_role_group_to_app_group_fires_group_created(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that converting a RoleGroup to an AppGroup fires the group_created hook."""
        test_app = AppFactory.build(
            name="TestAppCreate2",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = RoleGroupFactory.build(name="Role-ToAppGroup")
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {
            "type": "app_group",
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}FromRole",
            "description": "Role became app group",
            "app_id": test_app.id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200
        assert response.json()["type"] == "app_group"

        assert len(test_plugin.group_created_calls) == 1
        assert test_plugin.group_created_calls[0] == group_id

    async def test_no_hook_without_lifecycle_plugin(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Test that group_created hook does not fire when the app has no lifecycle plugin."""
        test_app = AppFactory.build(
            name="TestAppCreate3",
            # No app_group_lifecycle_plugin
        )
        test_group = OktaGroupFactory.build(name="PlainGroup-NoPlugin")
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}NoPlugin",
            "description": "No plugin configured",
            "app_id": test_app.id,
        }

        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_created_calls) == 0


class TestPluginTypeChangeConsolidatedFire:
    """A PUT fires exactly one app group lifecycle hook, after every field is applied, choosing the
    event by whether (and how) the type changed. Covers the three transitions."""

    async def test_convert_to_app_group_with_rename_and_config_fires_created_once(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        # Case 1: convert a plain group -> app group while also renaming and setting plugin config.
        # Exactly one group_created fires (not created + updated), and it observes the final config
        # (i.e. it fires after plugin_data is applied, not the pre-config state from ModifyGroupType).
        test_app = AppFactory.build(
            name="TestAppConvTo",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = OktaGroupFactory.build(name="Plain-ConvertTo")
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()
        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {
            "type": "app_group",
            "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Conv",
            "description": "converted",
            "app_id": test_app.id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "ext-123"}}},
        }
        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert test_plugin.group_created_calls == [group_id]
        assert test_plugin.group_updated_calls == []
        assert test_plugin.group_deleted_calls == []
        # group_created saw the config set in the same request (fired after plugin_data applied).
        assert test_plugin.group_created_configs[0].get("group_id") == "ext-123"

    async def test_convert_away_from_app_group_with_rename_fires_deleted_only(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        # Case 2: convert an app group -> plain group while renaming. group_deleted fires once
        # (cleanup for the departing plugin, from ModifyGroupType), and neither created nor updated.
        test_app = AppFactory.build(
            name="TestAppConvAway",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Away",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g1"}, "status": {}}},
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()
        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {"type": "okta_group", "name": "Plain-AwayRenamed", "description": "no longer an app group"}
        response = await client.put(url, json=data)
        assert response.status_code == 200
        assert response.json()["type"] == "okta_group"

        assert test_plugin.group_deleted_calls == [group_id]
        assert test_plugin.group_updated_calls == []
        assert test_plugin.group_created_calls == []

    async def test_no_type_change_name_and_config_fires_updated_only(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        # Case 3: no type change, but name + config both change -> exactly one group_updated, no
        # created/deleted.
        test_app = AppFactory.build(
            name="TestAppStay",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        base = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{base}Stay",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g1"}, "status": {}}},
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()
        group_id = test_group.id
        mocker.patch.object(okta, "update_group")

        url = url_for("api-groups.group_by_id", group_id=group_id)
        data = {
            "type": "app_group",
            "name": f"{base}StayRenamed",
            "description": "renamed",
            "app_id": test_group.app_id,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g2"}}},
        }
        response = await client.put(url, json=data)
        assert response.status_code == 200

        assert len(test_plugin.group_updated_calls) == 1
        assert test_plugin.group_updated_calls[0][0] == group_id
        assert test_plugin.group_created_calls == []
        assert test_plugin.group_deleted_calls == []


class TestPluginMembershipHooks:
    """Tests for plugin lifecycle hooks when members are added/removed."""

    async def test_direct_member_removed_loses_all_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        # Add the user to the group (this will trigger members_added hook)
        from api.operations import ModifyGroupUsers

        await ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the user from the group (user has no other access paths)
        await ModifyGroupUsers(group=test_group, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user lost all access
        assert len(test_plugin.members_removed_calls) == 1
        assert test_plugin.members_removed_calls[0] == (test_group.id, [user.id])

    async def test_direct_member_removed_but_has_redundant_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        # Associate the app group with a role
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them access to the group via role)
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Try to remove direct membership (but user only has role-based access, no direct access to remove)
        # This should not trigger the hook because user still has role-based access
        await ModifyGroupUsers(group=test_group, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user still has access via role
        assert len(test_plugin.members_removed_calls) == 0

    async def test_direct_member_added_gains_first_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)

        # Add the user to the group for the first time
        from api.operations import ModifyGroupUsers

        await ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user gained first access
        assert len(test_plugin.members_added_calls) == 1
        assert test_plugin.members_added_calls[0] == (test_group.id, [user.id])

    async def test_direct_member_added_but_already_has_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)

        # Associate the app group with a role
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them access to the group)
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding via role
        test_plugin.members_added_calls.clear()

        # Add the user directly to the group (they already have access via role)
        await ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user already had access via role
        assert len(test_plugin.members_added_calls) == 0

    async def test_role_member_removed_loses_all_access_to_associated_group(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        # Associate the app group with the role as a member group
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (which gives them access to the associated group)
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the user from the role (user loses access to associated group)
        await ModifyGroupUsers(group=role_group, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called for the associated group because user lost all access
        assert len(test_plugin.members_removed_calls) == 1
        assert test_plugin.members_removed_calls[0] == (test_group.id, [user.id])

    async def test_role_associated_lifecycle_hooks_survive_cold_session(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """Regression for the AppGroup.app raise_on_sql flip: the role-associated
        lifecycle paths read `group.app` via get_app_group_lifecycle_plugin_to_invoke,
        so the operation's own query must eager-load `active_group` + `AppGroup.app`.
        Warm-session tests miss a missing eager-load because `app` is many-to-one
        and resolves from the identity map without SQL; each membership change here
        runs after `expunge_all()` to force the cold load a production request sees.
        Covers modify_group_users add/remove cascades and the modify_role_groups
        group-removal cascade.
        """
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        test_app = AppFactory.build(
            name="TestApp_ColdSession",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}ColdGroup",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "external_cold"}}},
        )
        role_group = RoleGroupFactory.build(name="TestRoleCold", is_managed=True)
        user = OktaUserFactory.build()
        db.session.add_all([test_app, test_group, role_group, user])
        await db.session.commit()

        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add a member to the role on a cold session -> modify_group_users add
        # cascade reads associated_group.app.
        db.session.expunge_all()
        test_plugin.members_added_calls.clear()
        await ModifyGroupUsers(group=role_group.id, members_to_add=[user.id], sync_to_okta=False).execute()
        assert test_plugin.members_added_calls == [(test_group.id, [user.id])]

        # Remove the member on a cold session -> modify_group_users remove cascade.
        db.session.expunge_all()
        test_plugin.members_removed_calls.clear()
        await ModifyGroupUsers(group=role_group.id, members_to_remove=[user.id], sync_to_okta=False).execute()
        assert test_plugin.members_removed_calls == [(test_group.id, [user.id])]

        # Re-add the member, then remove the *group* from the role on a cold
        # session -> modify_role_groups groups_to_remove cascade reads group.app.
        await ModifyGroupUsers(group=role_group.id, members_to_add=[user.id], sync_to_okta=False).execute()
        db.session.expunge_all()
        test_plugin.members_removed_calls.clear()
        await ModifyRoleGroups(role_group=role_group.id, groups_to_remove=[test_group.id], sync_to_okta=False).execute()
        assert test_plugin.members_removed_calls == [(test_group.id, [user.id])]

    async def test_role_member_removed_but_has_redundant_access_via_another_role(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        # Associate the app group with both roles as member groups
        await ModifyRoleGroups(role_group=role_group_1, groups_to_add=[test_group.id], sync_to_okta=False).execute()
        await ModifyRoleGroups(role_group=role_group_2, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to both roles (gives them redundant access to the associated group)
        await ModifyGroupUsers(group=role_group_1, members_to_add=[user.id], sync_to_okta=False).execute()
        await ModifyGroupUsers(group=role_group_2, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the user from one role (user still has access via the other role)
        await ModifyGroupUsers(group=role_group_1, members_to_remove=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user still has access via role_group_2
        assert len(test_plugin.members_removed_calls) == 0

    async def test_role_member_added_gains_first_access_to_associated_group(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)

        # Associate the app group with the role as a member group
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them first access to the associated group)
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should be called for the associated group because user gained first access
        assert len(test_plugin.members_added_calls) == 1
        assert test_plugin.members_added_calls[0] == (test_group.id, [user.id])

    async def test_role_member_added_but_already_has_access_to_associated_group(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)

        # First, give the user direct access to the group
        await ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from first add
        test_plugin.members_added_calls.clear()

        # Now associate the app group with a role and add the user to the role
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called for the associated group because user already had access
        assert len(test_plugin.members_added_calls) == 0

    async def test_role_removed_from_group_user_loses_all_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        # Associate the app group with the role
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Add the user to the role (gives them access to the group via role)
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the group from the role (user loses all access to the group)
        await ModifyRoleGroups(role_group=role_group, groups_to_remove=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user lost all access
        assert len(test_plugin.members_removed_calls) == 1
        assert test_plugin.members_removed_calls[0] == (test_group.id, [user.id])

    async def test_role_removed_from_group_user_has_redundant_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)

        # Give the user direct access to the group
        await ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Also give them role-based access
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Remove the group from the role (user still has direct access to the group)
        await ModifyRoleGroups(role_group=role_group, groups_to_remove=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user still has direct access
        assert len(test_plugin.members_removed_calls) == 0

    async def test_role_added_to_group_user_gains_first_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)

        # Add the user to the role first (before associating the group with the role)
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding user to role
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Now associate the app group with the role (user gains first access to the group)
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should be called because user gained first access
        assert len(test_plugin.members_added_calls) == 1
        assert test_plugin.members_added_calls[0] == (test_group.id, [user.id])

    async def test_role_added_to_group_user_already_has_access(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
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
        await db.session.commit()

        # Mock Okta calls
        mocker.patch.object(okta, "add_user_to_group", return_value=None)

        # Give the user direct access to the group first
        await ModifyGroupUsers(group=test_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Add the user to the role
        await ModifyGroupUsers(group=role_group, members_to_add=[user.id], sync_to_okta=False).execute()

        # Clear the hook calls from adding
        test_plugin.members_added_calls.clear()
        test_plugin.members_removed_calls.clear()

        # Now associate the app group with the role (user already has direct access)
        await ModifyRoleGroups(role_group=role_group, groups_to_add=[test_group.id], sync_to_okta=False).execute()

        # Assert: Hook should NOT be called because user already had access
        assert len(test_plugin.members_added_calls) == 0


class TestPluginAuditLogging:
    """Tests for plugin configuration audit logging."""

    async def test_audit_log_plugin_assignment_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, caplog: Any, url_for: Any
    ) -> None:
        """Test that assigning a plugin to an app creates an audit log entry."""
        import json
        import logging

        from api.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(name="TestApp", description="Test App")
        db.session.add(test_app)
        await db.session.commit()

        # Assign plugin to the app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": DummyPlugin.ID,
        }

        response = await client.put(url, json=data)
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
        assert log_data["current_user_email"] == settings.CURRENT_OKTA_USER_EMAIL

    async def test_audit_log_plugin_configuration_change_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, caplog: Any, url_for: Any
    ) -> None:
        """Test that changing app-level plugin configuration creates an audit log entry."""
        import json
        import logging

        from api.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp2",
            description="Test App 2",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "category": "original"}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        caplog.clear()

        # Update plugin configuration
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": False, "category": "updated"}}},
        }

        response = await client.put(url, json=data)
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
        assert log_data["current_user_email"] == settings.CURRENT_OKTA_USER_EMAIL

    async def test_audit_log_plugin_removal_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, caplog: Any, url_for: Any
    ) -> None:
        """Test that removing a plugin from an app creates an audit log entry."""
        import json
        import logging

        from api.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp3",
            description="Test App 3",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        caplog.clear()

        # Remove plugin from the app
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {"name": test_app.name, "app_group_lifecycle_plugin": None}

        response = await client.put(url, json=data)
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
        assert log_data["current_user_email"] == settings.CURRENT_OKTA_USER_EMAIL

    async def test_audit_log_plugin_configuration_change_at_group_level(
        self,
        client: AsyncClient,
        db: Db,
        app: FastAPI,
        test_plugin: DummyPlugin,
        caplog: Any,
        mocker: MockerFixture,
        url_for: Any,
    ) -> None:
        """Test that changing group-level plugin configuration creates an audit log entry."""
        import json
        import logging

        from api.schemas import EventType

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
        await db.session.commit()

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

        response = await client.put(url, json=data)
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
        assert log_data["current_user_email"] == settings.CURRENT_OKTA_USER_EMAIL

    async def test_no_audit_log_when_plugin_unchanged_at_app_level(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, caplog: Any, url_for: Any
    ) -> None:
        """Test that no audit log is created when plugin configuration is unchanged."""
        import logging

        from api.schemas import EventType

        caplog.set_level(logging.INFO)

        test_app = AppFactory.build(
            name="TestApp5",
            description="Test App 5",
            app_group_lifecycle_plugin=DummyPlugin.ID,
        )
        db.session.add(test_app)
        await db.session.commit()

        caplog.clear()

        # Update app without changing plugin
        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {"name": test_app.name, "description": "Updated description"}

        response = await client.put(url, json=data)
        assert response.status_code == 200

        # Check that no plugin audit log was created
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        plugin_logs = [log for log in audit_logs if EventType.app_modify_plugin.value in log.message]
        assert len(plugin_logs) == 0

    async def test_no_audit_log_when_plugin_unchanged_at_group_level(
        self,
        client: AsyncClient,
        db: Db,
        app: FastAPI,
        test_plugin: DummyPlugin,
        caplog: Any,
        mocker: MockerFixture,
        url_for: Any,
    ) -> None:
        """Test that no audit log is created when group plugin configuration is unchanged."""
        import logging

        from api.schemas import EventType

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
        await db.session.commit()

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

        response = await client.put(url, json=data)
        assert response.status_code == 200

        # Check that no plugin audit log was created
        audit_logs = [record for record in caplog.records if record.levelname == "INFO"]
        plugin_logs = [log for log in audit_logs if EventType.group_modify_plugin.value in log.message]
        assert len(plugin_logs) == 0


class TestModifyGroupPluginData:
    """ModifyGroupPluginData fires group_updated only on configuration changes."""

    async def _make_app_group(self, db: Db, mocker: MockerFixture) -> tuple[Any, AppGroup]:
        mocker.patch.object(okta, "update_group")
        mocker.patch.object(okta, "create_group")
        app = AppFactory.build()
        app.app_group_lifecycle_plugin = DummyPlugin.ID
        app.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        db.session.add(app)
        group = AppGroupFactory.build(
            app_id=app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{app.name}-Eng",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {}}},
        )
        db.session.add(group)
        await db.session.commit()
        return app, group

    async def test_fires_group_updated_on_config_change(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        _, group = await self._make_app_group(db, mocker)

        new_plugin_data = {DummyPlugin.ID: {"configuration": {"group_id": "g-new"}, "status": {}}}
        await ModifyGroupPluginData(group=group, plugin_data=new_plugin_data).execute()

        assert len(test_plugin.group_updated_calls) == 1
        group_id, _old_name, _old_desc = test_plugin.group_updated_calls[0]
        assert group_id == group.id
        assert group.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "g-new"

    async def test_does_not_fire_on_status_only_change(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        _, group = await self._make_app_group(db, mocker)

        new_plugin_data = {DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {"member_count": 5}}}
        await ModifyGroupPluginData(group=group, plugin_data=new_plugin_data).execute()

        assert test_plugin.group_updated_calls == []
        assert group.plugin_data[DummyPlugin.ID]["status"]["member_count"] == 5

    async def test_partial_patch_omitting_unchanged_config_key_does_not_fire(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        # A partial patch that omits an unchanged config key (here the immutable `region`) merges
        # back to an identical effective config, so it must not be misread as a change and must not
        # fire a redundant group_updated. Regression: config_changed was previously computed against
        # the pre-merge patch, before the omitted key was restored, yielding a false positive.
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        mocker.patch.object(okta, "update_group")
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        db.session.add(a)
        group = AppGroupFactory.build(
            app_id=a.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}, "status": {}}},
        )
        db.session.add(group)
        await db.session.commit()

        # Keep group_id unchanged and omit the immutable region entirely (a valid partial patch).
        patch = {DummyPlugin.ID: {"configuration": {"group_id": "g1"}}}
        op = ModifyGroupPluginData(group=group, plugin_data=patch)
        await op.execute()

        assert op.config_changed is False
        assert test_plugin.group_updated_calls == []
        # The omitted region is preserved by the partial-patch merge, so nothing actually changed.
        assert group.plugin_data[DummyPlugin.ID]["configuration"]["region"] == "us"

    async def test_fires_group_updated_on_first_config_set(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        # A group with a lifecycle plugin but no plugin_data yet: a config-only change that adds
        # config for the first time must still fire group_updated. config_changed is keyed on the
        # effective config, not on whether prior plugin_data existed -- a regression coupling the
        # fire to "old data was present" would silently skip the very first reconcile.
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        mocker.patch.object(okta, "update_group")
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        db.session.add(a)
        group = AppGroupFactory.build(app_id=a.id, name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng", plugin_data={})
        db.session.add(group)
        await db.session.commit()

        op = ModifyGroupPluginData(group=group, plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-new"}}})
        await op.execute()

        assert op.config_changed is True
        assert len(test_plugin.group_updated_calls) == 1
        assert group.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "g-new"

    async def test_does_not_fire_without_lifecycle_plugin(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        mocker.patch.object(okta, "update_group")
        mocker.patch.object(okta, "create_group")
        a = AppFactory.build()
        # no app_group_lifecycle_plugin set on the app
        group = AppGroupFactory.build(
            app_id=a.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {}}},
        )
        db.session.add(a)
        db.session.add(group)
        await db.session.commit()

        new_plugin_data = {DummyPlugin.ID: {"configuration": {"group_id": "g-new"}, "status": {}}}
        await ModifyGroupPluginData(group=group, plugin_data=new_plugin_data).execute()

        assert test_plugin.group_updated_calls == []

    async def test_preserves_other_plugins_top_level_entry(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """A patch mentioning only one plugin must not drop other plugins' entries."""
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        mocker.patch.object(okta, "update_group")
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        group = AppGroupFactory.build(
            app_id=a.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
            plugin_data={
                DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {}},
                "other_plugin": {"configuration": {"keep": "me"}, "status": {}},
            },
        )
        db.session.add(a)
        db.session.add(group)
        await db.session.commit()

        await ModifyGroupPluginData(
            group=group,
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-new"}, "status": {}}},
        ).execute()

        assert group.plugin_data["other_plugin"] == {"configuration": {"keep": "me"}, "status": {}}
        assert group.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "g-new"

    async def test_preserves_status_omitted_from_config_patch(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """A config-only patch must preserve plugin-managed status it didn't mention."""
        from api.operations.modify_group_plugin_data import ModifyGroupPluginData

        mocker.patch.object(okta, "update_group")
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        group = AppGroupFactory.build(
            app_id=a.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {"member_count": 7}}},
        )
        db.session.add(a)
        db.session.add(group)
        await db.session.commit()

        await ModifyGroupPluginData(
            group=group,
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-new"}}},
        ).execute()

        assert group.plugin_data[DummyPlugin.ID]["configuration"]["group_id"] == "g-new"
        assert group.plugin_data[DummyPlugin.ID]["status"] == {"member_count": 7}

    async def test_put_group_config_change_fires_group_updated(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        mocker.patch.object(okta, "update_group")
        mocker.patch.object(okta, "create_group")
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        group = AppGroupFactory.build(
            app_id=a.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {}}},
        )
        db.session.add(a)
        db.session.add(group)
        await db.session.commit()

        url = url_for("api-groups.group_by_id_put", group_id=group.id)
        response = await client.put(
            url,
            json={
                "type": "app_group",
                "name": group.name,
                "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g-new"}, "status": {}}},
            },
        )
        assert response.status_code == 200
        assert any(call[0] == group.id for call in test_plugin.group_updated_calls)
        assert response.json()["plugin_data"][DummyPlugin.ID]["configuration"]["group_id"] == "g-new"

    async def test_put_group_name_and_config_change_fires_group_updated_once(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        # A single PUT that changes both the name and the plugin config must fire group_updated
        # exactly once (one reconcile), not once per operation.
        mocker.patch.object(okta, "update_group")
        mocker.patch.object(okta, "create_group")
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        group = AppGroupFactory.build(
            app_id=a.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
            description="Old description",
            plugin_data={DummyPlugin.ID: {"configuration": {"group_id": "g-old"}, "status": {}}},
        )
        db.session.add(a)
        db.session.add(group)
        await db.session.commit()

        old_name = group.name
        new_name = f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Platform"
        url = url_for("api-groups.group_by_id_put", group_id=group.id)
        response = await client.put(
            url,
            json={
                "type": "app_group",
                "name": new_name,
                "description": "New description",
                "app_id": a.id,
                "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "g-new"}, "status": {}}},
            },
        )
        assert response.status_code == 200

        fires = [call for call in test_plugin.group_updated_calls if call[0] == group.id]
        assert len(fires) == 1
        # The single fire reports the original pre-update name/description.
        _, hook_old_name, hook_old_desc = fires[0]
        assert hook_old_name == old_name
        assert hook_old_desc == "Old description"
        assert response.json()["plugin_data"][DummyPlugin.ID]["configuration"]["group_id"] == "g-new"


class TestPostGroupPluginValidation:
    async def test_post_group_rejects_invalid_group_config(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        from okta.models.group import Group as OktaGroupModel

        mocker.patch.object(okta, "create_group", return_value=OktaGroupModel.from_dict({"id": "test-okta-id-123"}))
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        db.session.add(a)
        await db.session.commit()

        url = url_for("api-groups.groups_create")
        response = await client.post(
            url,
            json={
                "type": "app_group",
                "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
                "app_id": a.id,
                # DummyPlugin.validate_plugin_group_config requires "group_id"
                "plugin_data": {DummyPlugin.ID: {"configuration": {}, "status": {}}},
            },
        )
        assert response.status_code == 400
        assert "group_id" in response.text

    async def test_post_group_accepts_valid_group_config(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        from okta.models.group import Group as OktaGroupModel

        mocker.patch.object(okta, "create_group", return_value=OktaGroupModel.from_dict({"id": "test-okta-id-456"}))
        a = AppFactory.build()
        a.app_group_lifecycle_plugin = DummyPlugin.ID
        a.plugin_data = {DummyPlugin.ID: {"configuration": {"enabled": True}, "status": {}}}
        db.session.add(a)
        await db.session.commit()

        url = url_for("api-groups.groups_create")
        response = await client.post(
            url,
            json={
                "type": "app_group",
                "name": f"{AppGroup.APP_GROUP_NAME_PREFIX}{a.name}-Eng",
                "app_id": a.id,
                "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "ext-123"}, "status": {}}},
            },
        )
        assert response.status_code == 201

    async def test_put_group_filtering_error_returns_clean_500(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        # A plugin that doesn't answer the config-validation hook with exactly one response raises
        # AppGroupLifecyclePluginFilteringError (a plain Exception, not ValueError). The PUT must
        # surface a clean 500 (server-side misconfiguration) rather than an unhandled stack trace.
        from api.plugins import app_group_lifecycle as agl

        test_app = AppFactory.build(
            name="TestAppFilter",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Filt",
        )
        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        mocker.patch.object(okta, "update_group")
        mocker.patch.object(
            agl,
            "validate_app_group_lifecycle_plugin_group_config",
            side_effect=agl.AppGroupLifecyclePluginFilteringError(DummyPlugin.ID, 2),
        )

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        response = await client.put(
            url,
            json={
                "type": "app_group",
                "name": test_group.name,
                "description": "",
                "app_id": test_group.app_id,
                "plugin_data": {DummyPlugin.ID: {"configuration": {"group_id": "x"}}},
            },
        )
        assert response.status_code == 500
        # Distinguishes the deliberate clean-500 path from an unhandled exception (which would also
        # be a 500, but without this message).
        assert "Misconfigured app group lifecycle plugin" in response.text


def test_validate_group_config_rejects_immutable_change_on_update(test_plugin: DummyPlugin) -> None:
    old = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}}}
    new = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "eu"}}}
    errors = validate_app_group_lifecycle_plugin_group_config(new, DummyPlugin.ID, old_plugin_data=old)
    assert "region" in errors


def test_validate_group_config_allows_immutable_on_create(test_plugin: DummyPlugin) -> None:
    new = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}}}
    # No old_plugin_data -> create path -> immutable field freely set.
    errors = validate_app_group_lifecycle_plugin_group_config(new, DummyPlugin.ID)
    assert "region" not in errors


def test_validate_group_config_allows_mutable_change_on_update(test_plugin: DummyPlugin) -> None:
    old = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}}}
    new = {DummyPlugin.ID: {"configuration": {"group_id": "g2", "region": "us"}}}
    errors = validate_app_group_lifecycle_plugin_group_config(new, DummyPlugin.ID, old_plugin_data=old)
    assert errors == {}


def test_validate_group_config_enforces_immutable_field_on_create(test_plugin: DummyPlugin) -> None:
    # On create (no old_plugin_data) an immutable field is validated like any other.
    new = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "legacy"}}}
    errors = validate_app_group_lifecycle_plugin_group_config(new, DummyPlugin.ID)
    assert "region" in errors


def test_validate_group_config_allows_partial_patch_omitting_immutable_field(test_plugin: DummyPlugin) -> None:
    # A partial patch that omits the immutable field entirely is not an edit to it, so it must
    # not be rejected -- otherwise an API PUT of only the mutable fields would 400 on the
    # untouched immutable one.
    old = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}}}
    new = {DummyPlugin.ID: {"configuration": {"group_id": "g2"}}}
    errors = validate_app_group_lifecycle_plugin_group_config(new, DummyPlugin.ID, old_plugin_data=old)
    assert "region" not in errors


def test_validate_group_config_suppresses_unchanged_immutable_field_error_on_update(test_plugin: DummyPlugin) -> None:
    # A grandfathered/adopted immutable value that now fails plugin validation must not block
    # an update that leaves it unchanged (it's locked and can't be fixed via this update).
    old = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "legacy"}}}
    new = {DummyPlugin.ID: {"configuration": {"group_id": "g2", "region": "legacy"}}}
    errors = validate_app_group_lifecycle_plugin_group_config(new, DummyPlugin.ID, old_plugin_data=old)
    assert "region" not in errors

    # But changing the immutable field is still rejected.
    changed = {DummyPlugin.ID: {"configuration": {"group_id": "g1", "region": "us"}}}
    errors = validate_app_group_lifecycle_plugin_group_config(changed, DummyPlugin.ID, old_plugin_data=old)
    assert "region" in errors
