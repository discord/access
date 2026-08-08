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
from unittest.mock import MagicMock
from datetime import UTC, datetime
from typing import Any, Generator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pytest_mock import MockerFixture
from sqlalchemy import select, text
from sqlalchemy.orm import joinedload

from api.config import settings
from api.extensions import Db
from api.models import App, AppGroup, OktaUser, OktaUserGroupMember
from api.plugins.app_group_lifecycle import (
    AmbiguousOktaTargetError,
    AppGroupLifecycleContext,
    AppGroupLifecycleHook,
    AppGroupLifecyclePluginConfigProperty,
    AppGroupLifecyclePluginFilteringError,
    AppGroupLifecyclePluginMetadata,
    AppGroupLifecyclePluginStatusProperty,
    MissingOktaTargetError,
    _StatusWrite,
    get_app_group_lifecycle_plugin_app_config_properties,
    get_app_group_lifecycle_plugin_app_status_properties,
    get_app_group_lifecycle_plugin_group_config_properties,
    get_app_group_lifecycle_plugin_group_status_properties,
    get_app_group_lifecycle_plugins,
    hookimpl,
    is_plugin_config_changed,
    merge_app_lifecycle_plugin_data,
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
        # (group id, "owning app name") per sync_group call.
        self.sync_group_calls: list[tuple[str, str]] = []
        # Group names whose sync_group should raise, so tests can exercise the caller's
        # per-group error isolation.
        self.sync_group_failures: set[str] = set()
        # Group ids whose membership hooks should raise, so tests can exercise the
        # callers' per-group error isolation when one operation affects several groups.
        self.members_added_failures: set[str] = set()
        self.members_removed_failures: set[str] = set()
        # Group names / ids whose single-fire hooks should raise, so tests can exercise that a
        # failing plugin doesn't take the surrounding operation or request down with it.
        self.group_created_failures: set[str] = set()
        self.group_updated_failures: set[str] = set()
        self.group_deleted_failures: set[str] = set()
        # (property_name, value, durable_on_failure) triples the group_updated hook writes before
        # its failure check, so tests can drive the host's durable-status replay.
        self.status_writes_on_update: list[tuple[str, Any, bool]] = []

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
    async def group_created(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        # Await a read through a real capability. If the host hands us a broken context this
        # raises and the call below is never recorded — so the recorded-calls assertions in these
        # tests fail loudly.
        await ctx.find_groups_by_status("probe", "unset")
        # A hook may also read group.app, which is lazy="raise_on_sql". The invoking
        # operation must eager-load AppGroup.app (or seed the identity map) so this
        # resolves without emitting SQL; otherwise it raises here and the recorded-
        # calls assertion fails. Guards that eager-load across every op path.
        _ = group.app.name
        if group.name in self.group_created_failures:
            raise RuntimeError(f"group_created failed for {group.name}")
        self.group_created_calls.append(group.id)
        self.group_created_configs.append((group.plugin_data or {}).get(self.ID, {}).get("configuration", {}))

    @hookimpl
    async def group_updated(
        self,
        ctx: AppGroupLifecycleContext,
        group: AppGroup,
        old_name: str,
        old_description: str,
        plugin_id: str | None,
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        await ctx.find_groups_by_status("probe", "unset")  # exercise the context (see group_created)
        _ = group.app.name  # exercise group.app eager-load (see group_created)
        for property_name, value, durable in self.status_writes_on_update:
            ctx.set_status(group, property_name, value, durable_on_failure=durable)
        if group.id in self.group_updated_failures:
            raise RuntimeError(f"group_updated failed for {group.id}")
        self.group_updated_calls.append((group.id, old_name, old_description))

    @hookimpl
    async def group_deleted(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        await ctx.find_groups_by_status("probe", "unset")  # exercise the context (see group_created)
        _ = group.app.name  # exercise group.app eager-load (see group_created)
        if group.id in self.group_deleted_failures:
            raise RuntimeError(f"group_deleted failed for {group.id}")
        self.group_deleted_calls.append(group.id)

    @hookimpl
    async def group_members_added(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        await ctx.find_groups_by_status("probe", "unset")  # exercise the context (see group_created)
        _ = group.app.name  # exercise group.app eager-load (see group_created)
        if group.id in self.members_added_failures:
            raise RuntimeError(f"group_members_added failed for {group.id}")
        self.members_added_calls.append((group.id, [m.id for m in members]))

    @hookimpl
    async def group_members_removed(
        self, ctx: AppGroupLifecycleContext, group: AppGroup, members: list[OktaUser], plugin_id: str | None
    ) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        await ctx.find_groups_by_status("probe", "unset")  # exercise the context (see group_created)
        _ = group.app.name  # exercise group.app eager-load (see group_created)
        if group.id in self.members_removed_failures:
            raise RuntimeError(f"group_members_removed failed for {group.id}")
        self.members_removed_calls.append((group.id, [m.id for m in members]))

    @hookimpl
    async def sync_group(self, ctx: AppGroupLifecycleContext, group: AppGroup, plugin_id: str | None) -> None:
        if plugin_id is not None and plugin_id != self.ID:
            return
        await ctx.find_groups_by_status("probe", "unset")  # exercise the context (see group_created)
        # Read `group.app`, which the hookspec promises eager-loaded. It is
        # lazy="raise_on_sql", so this raises unless the caller loaded it.
        app_name = group.app.name
        if group.name in self.sync_group_failures:
            raise RuntimeError(f"sync failed for {group.name}")
        self.sync_group_calls.append((group.id, app_name))


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
    """Tests for the plugin-facing capability surface, `AppGroupLifecycleContext`.

    These go through the context rather than the module-level accessors it delegates to, because the
    context is what a plugin actually calls -- the accessors take a `plugin_id` the context owns.
    """

    def _ctx(self, db: Db) -> AppGroupLifecycleContext:
        return AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)

    async def test_get_config_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test getting configuration values from plugin data."""
        test_app = AppFactory.build(
            name="TestApp7",
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True, "category": "test_id_123"}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        ctx = self._ctx(db)
        assert ctx.get_config(test_app, "enabled") is True
        assert ctx.get_config(test_app, "category") == "test_id_123"
        assert ctx.get_config(test_app, "absent", "fallback") == "fallback"

    async def test_get_status_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test getting status values from plugin data."""
        test_app = AppFactory.build(
            name="TestApp8",
            plugin_data={DummyPlugin.ID: {"status": {"last_sync": "2025-01-15T10:30:00Z", "sync_count": 42}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        ctx = self._ctx(db)
        assert ctx.get_status(test_app, "last_sync") == "2025-01-15T10:30:00Z"
        assert ctx.get_status(test_app, "sync_count") == 42

    async def test_set_status_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test setting status values in plugin data."""
        test_app = AppFactory.build(name="TestApp9", plugin_data={})
        db.session.add(test_app)
        await db.session.commit()

        # No session.add by the caller: the context does it, so a plugin cannot forget.
        self._ctx(db).set_status(test_app, "last_sync", "2025-01-15T11:00:00Z")
        await db.session.commit()

        # Refresh from DB (expire + sync lazy read would raise under async)
        await db.session.refresh(test_app)

        assert self._ctx(db).get_status(test_app, "last_sync") == "2025-01-15T11:00:00Z"
        assert test_app.plugin_data[DummyPlugin.ID]["status"]["last_sync"] == "2025-01-15T11:00:00Z"

    async def test_set_config_value(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Test setting configuration values in plugin data."""
        test_app = AppFactory.build(name="TestApp9b", plugin_data={})
        db.session.add(test_app)
        await db.session.commit()

        self._ctx(db).set_config(test_app, "category", "inferred_id")
        await db.session.commit()
        await db.session.refresh(test_app)

        assert self._ctx(db).get_config(test_app, "category") == "inferred_id"
        assert test_app.plugin_data[DummyPlugin.ID]["configuration"]["category"] == "inferred_id"

    async def test_context_is_bound_to_its_own_plugin_namespace(self, db: Db, test_plugin: DummyPlugin) -> None:
        """A context can only read and write its own plugin's slice of plugin_data. This is the point
        of binding plugin_id at construction: a plugin cannot reach another plugin's namespace."""
        test_app = AppFactory.build(
            name="TestApp9c",
            plugin_data={
                DummyPlugin.ID: {"configuration": {"category": "mine"}},
                "other_plugin": {"configuration": {"category": "theirs"}},
            },
        )
        db.session.add(test_app)
        await db.session.commit()

        assert self._ctx(db).get_config(test_app, "category") == "mine"

        self._ctx(db).set_config(test_app, "category", "changed")
        await db.session.commit()
        await db.session.refresh(test_app)

        assert test_app.plugin_data[DummyPlugin.ID]["configuration"]["category"] == "changed"
        assert test_app.plugin_data["other_plugin"]["configuration"]["category"] == "theirs"

    async def test_set_status_records_only_durable_writes(self, db: Db, test_plugin: DummyPlugin) -> None:
        """`durable_on_failure` is what the host replays after a failed hook. Ownership tokens must
        stay out of that buffer, so only the opted-in writes are recorded."""
        test_app = AppFactory.build(name="TestApp9d", plugin_data={})
        db.session.add(test_app)
        await db.session.commit()

        ctx = self._ctx(db)
        ctx.set_status(test_app, "sync_status", "error", durable_on_failure=True)
        ctx.set_status(test_app, "external_group_id", "tok-1")

        assert [(w.entity_type, w.entity_id, w.property_name, w.value) for w in ctx._status_writes] == [
            ("app", test_app.id, "sync_status", "error")
        ]

    async def _advisory_locks_held(self, db: Db) -> int:
        """Advisory locks held by this session's own backend. `pg_advisory_xact_lock` is
        transaction-scoped and never explicitly released, so they stay visible for the rest of the
        transaction."""
        return await db.session.scalar(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()")
        )

    async def test_lock_takes_a_namespaced_advisory_lock_on_postgres_only(
        self, db: Db, test_plugin: DummyPlugin
    ) -> None:
        """Postgres-only by design: a transaction-scoped advisory lock there, keyed by plugin id so
        two plugins choosing the same string don't contend; elsewhere a no-op rather than a raise,
        since the relevant sync paths there are single-writer. The suite runs against both backends,
        so each run asserts what its own dialect calls for rather than assuming one of them."""
        ctx = self._ctx(db)

        if db.session.get_bind().dialect.name != "postgresql":
            await ctx.lock("some-external-id")  # must not raise
            return

        assert await self._advisory_locks_held(db) == 0
        await ctx.lock("external-1")
        assert await self._advisory_locks_held(db) == 1

        # Same key string from a different plugin takes its own lock rather than contending.
        await AppGroupLifecycleContext(session=db.session, plugin_id="other_plugin").lock("external-1")
        assert await self._advisory_locks_held(db) == 2


class TestContextFindGroupsByStatus:
    """`ctx.find_groups_by_status` — the generalized ownership/uniqueness lookup that replaces a
    plugin building its own cross-app query."""

    def _ctx(self, db: Db) -> AppGroupLifecycleContext:
        return AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)

    async def _app_with_group(
        self,
        db: Db,
        app_name: str,
        group_suffix: str,
        status: dict[str, Any],
        *,
        plugin_id: str | None = DummyPlugin.ID,
    ) -> tuple[App, AppGroup]:
        test_app = AppFactory.build(name=app_name, app_group_lifecycle_plugin=plugin_id)
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{app_name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        group = AppGroupFactory.build(
            app_id=test_app.id,
            is_managed=True,
            name=f"{prefix}{group_suffix}",
            plugin_data={DummyPlugin.ID: {"status": status}},
        )
        db.session.add_all([test_app, group])
        await db.session.commit()
        return test_app, group

    async def test_spans_every_app_configured_with_this_plugin(self, db: Db, test_plugin: DummyPlugin) -> None:
        """One external system can back several Access apps, and they all name the same plugin, so a
        group in any of them can own the external id."""
        _, group_a = await self._app_with_group(db, "FindAppOne", "A", {"ext_id": "shared"})
        _, group_b = await self._app_with_group(db, "FindAppTwo", "B", {"ext_id": "shared"})

        found = await self._ctx(db).find_groups_by_status("ext_id", "shared")

        assert {g.id for g in found} == {group_a.id, group_b.id}

    async def test_excludes_other_plugins_and_soft_deleted_rows(self, db: Db, test_plugin: DummyPlugin) -> None:
        """Scoped to this plugin's apps, and both `deleted_at` filters apply."""
        _, keeper = await self._app_with_group(db, "FindAppKeep", "K", {"ext_id": "target"})
        await self._app_with_group(db, "FindAppOther", "O", {"ext_id": "target"}, plugin_id="a_different_plugin")

        deleted_app, _ = await self._app_with_group(db, "FindAppGoneApp", "G", {"ext_id": "target"})
        deleted_app.deleted_at = datetime.now(UTC)
        _, deleted_group = await self._app_with_group(db, "FindAppGoneGroup", "D", {"ext_id": "target"})
        deleted_group.deleted_at = datetime.now(UTC)
        await db.session.commit()

        found = await self._ctx(db).find_groups_by_status("ext_id", "target")

        assert [g.id for g in found] == [keeper.id]

    async def test_honors_exclude_group_and_limit(self, db: Db, test_plugin: DummyPlugin) -> None:
        _, group_a = await self._app_with_group(db, "FindAppExclA", "A", {"ext_id": "dupe"})
        _, group_b = await self._app_with_group(db, "FindAppExclB", "B", {"ext_id": "dupe"})

        assert [g.id for g in await self._ctx(db).find_groups_by_status("ext_id", "dupe", exclude_group=group_a)] == [
            group_b.id
        ]
        assert len(await self._ctx(db).find_groups_by_status("ext_id", "dupe", limit=1)) == 1

    async def test_no_match_returns_empty(self, db: Db, test_plugin: DummyPlugin) -> None:
        await self._app_with_group(db, "FindAppNoMatch", "N", {"ext_id": "something-else"})
        assert await self._ctx(db).find_groups_by_status("ext_id", "unclaimed") == []

    async def test_returned_groups_have_app_loaded_on_a_cold_session(self, db: Db, test_plugin: DummyPlugin) -> None:
        """`AppGroup.app` is lazy="raise_on_sql" and reading it is the obvious next thing a plugin
        does with a result (e.g. to name the owning app in an error). The eager-load in the query is
        therefore mandatory, not decorative -- and a warm session would hide a missing one, since
        many-to-one resolves from the identity map without SQL.
        """
        _, group = await self._app_with_group(db, "FindAppCold", "C", {"ext_id": "cold"})
        expected_app_name = "FindAppCold"

        db.session.expunge_all()
        found = await self._ctx(db).find_groups_by_status("ext_id", "cold")

        assert len(found) == 1
        assert found[0].app.name == expected_app_name  # must not raise

    async def test_predicate_is_pushed_into_sql_as_a_json_path(self, db: Db, test_plugin: DummyPlugin) -> None:
        """The id predicate compiles to a JSON path lookup on the stored status, so this stays a point
        lookup rather than a scan of every plugin-managed group. Compiled against the Postgres dialect
        because the tuple-index JSON path only renders there.
        """
        from sqlalchemy.dialects import postgresql

        stmt = (
            select(AppGroup)
            .join(App, AppGroup.app_id == App.id)
            .where(AppGroup.plugin_data[(DummyPlugin.ID, "status", "ext_id")].as_string() == "x")
        )
        compiled = str(stmt.compile(dialect=postgresql.dialect()))

        assert "plugin_data" in compiled
        assert "#>>" in compiled or "->>" in compiled

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

    async def test_app_config_with_unregistered_plugin_in_body_returns_400(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Pointing an app at a plugin id no registered plugin claims. The validation
        helpers raise AppGroupLifecyclePluginFilteringError, which is not a ValueError;
        an unknown id is bad input, so it must be a clean 400 and not a 500."""
        test_app = AppFactory.build(name="TestApp15")

        db.session.add(test_app)
        await db.session.commit()

        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "app_group_lifecycle_plugin": "unregistered_plugin",
            "plugin_data": {"unregistered_plugin": {"configuration": {"enabled": True, "category": "valid_id"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 400, response.text
        assert "unregistered_plugin" in str(response.json())

    async def test_app_config_with_unregistered_stored_plugin_returns_400(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """Same failure when the unregistered id is the app's stored config rather
        than something the body supplied, e.g. an operator dropped the plugin from
        the deployment while apps still referenced it."""
        test_app = AppFactory.build(name="TestApp16", app_group_lifecycle_plugin="unregistered_plugin")

        db.session.add(test_app)
        await db.session.commit()

        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "plugin_data": {"unregistered_plugin": {"configuration": {"enabled": True, "category": "valid_id"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 400, response.text
        assert "unregistered_plugin" in str(response.json())

    async def test_app_config_with_misconfigured_registered_plugin_returns_500(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """The other half of the filtering-error split: the plugin id is registered, but
        its hook does not answer with exactly one response. That is a deployment fault
        rather than bad input, so it stays a 500 -- an explicit one, carrying the plugin
        id, rather than an unhandled stack trace."""
        test_app = AppFactory.build(name="TestApp17", app_group_lifecycle_plugin=DummyPlugin.ID)

        db.session.add(test_app)
        await db.session.commit()

        mocker.patch(
            "api.plugins.app_group_lifecycle.validate_app_group_lifecycle_plugin_app_config",
            side_effect=AppGroupLifecyclePluginFilteringError(DummyPlugin.ID, 2),
        )

        url = url_for("api-apps.app_by_id", app_id=test_app.id)
        data = {
            "name": test_app.name,
            "plugin_data": {DummyPlugin.ID: {"configuration": {"enabled": True, "category": "valid_id"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 500, response.text
        assert "Misconfigured" in str(response.json())
        assert DummyPlugin.ID in str(response.json())

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

    async def test_group_config_with_unregistered_plugin_returns_400(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, url_for: Any
    ) -> None:
        """An app can name a lifecycle plugin id that isn't registered in this
        deployment — e.g. an operator dropped the plugin while apps still referenced
        it. Validation then raises AppGroupLifecyclePluginFilteringError, which is not
        a ValueError, so the edit must still be a clean 400 and not a 500."""
        test_app = AppFactory.build(name="TestApp14", app_group_lifecycle_plugin="unregistered_plugin")
        test_group = AppGroupFactory.build(
            app_id=test_app.id,
            name=f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}Testgroup3",
        )

        db.session.add(test_app)
        db.session.add(test_group)
        await db.session.commit()

        url = url_for("api-groups.group_by_id", group_id=test_group.id)
        data = {
            "type": "app_group",
            "name": test_group.name,
            "description": "",
            "app_id": test_group.app_id,
            "plugin_data": {"unregistered_plugin": {"configuration": {"group_id": "external_123"}}},
        }

        response = await client.put(url, json=data)
        assert response.status_code == 400, response.text
        assert "unregistered_plugin" in str(response.json())


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


class TestContextSetGroupDescription:
    """`ctx.set_group_description` — the capability that replaces a plugin importing
    `api.operations.ModifyGroupDetails` directly."""

    async def _app_group(self, db: Db, suffix: str, description: str) -> AppGroup:
        test_app = AppFactory.build(name=f"TestAppDesc{suffix}", app_group_lifecycle_plugin=DummyPlugin.ID)
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        group = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=f"{prefix}G", description=description)
        db.session.add_all([test_app, group])
        await db.session.commit()
        return group

    async def test_updates_the_group_and_pushes_to_okta(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        group = await self._app_group(db, "Push", "")
        update_group = mocker.patch.object(okta, "update_group")
        ctx = AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)

        await ctx.set_group_description(group, "adopted from the external system")

        assert group.description == "adopted from the external system"
        update_group.assert_awaited_once()

    async def test_does_not_commit(self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture) -> None:
        """The host owns the transaction. Committing inside a hook would release any advisory lock the
        hook holds and publish the surrounding operation's in-flight work."""
        group = await self._app_group(db, "NoCommit", "original")
        mocker.patch.object(okta, "update_group")
        ctx = AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)

        # Hold the id across the rollback: it expires every instance in the identity map, so a
        # `group.id` read afterwards would raise MissingGreenlet from the assertion itself.
        group_id = group.id
        await ctx.set_group_description(group, "not yet persisted")
        await db.session.rollback()

        reloaded = (
            await db.session.scalars(
                select(AppGroup).where(AppGroup.id == group_id).execution_options(populate_existing=True)
            )
        ).one()
        assert reloaded.description == "original"

    async def test_does_not_refire_the_lifecycle_hook(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """A plugin adopting a description must not recurse back into its own group_updated hook."""
        group = await self._app_group(db, "NoRefire", "")
        mocker.patch.object(okta, "update_group")
        test_plugin.group_updated_calls.clear()
        ctx = AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)

        await ctx.set_group_description(group, "adopted")

        assert test_plugin.group_updated_calls == []

    async def test_unchanged_description_still_no_ops_cleanly(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        group = await self._app_group(db, "Same", "identical")
        mocker.patch.object(okta, "update_group")
        ctx = AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)

        await ctx.set_group_description(group, "identical")

        assert group.description == "identical"


class TestModifyGroupDetailsCommitFlag:
    """`commit_db_changes` defaults True, so the two request-path callers are unaffected."""

    async def test_defaults_to_committing(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        from api.operations import ModifyGroupDetails

        test_app = AppFactory.build(name="TestAppCommitFlag", app_group_lifecycle_plugin=DummyPlugin.ID)
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        group = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=f"{prefix}G", description="before")
        db.session.add_all([test_app, group])
        await db.session.commit()
        mocker.patch.object(okta, "update_group")

        group_id = group.id  # held across the rollback below, which expires the identity map
        await ModifyGroupDetails(group=group, description="after", fire_lifecycle_hook=False).execute()
        await db.session.rollback()  # a committed change survives this

        reloaded = (
            await db.session.scalars(
                select(AppGroup).where(AppGroup.id == group_id).execution_options(populate_existing=True)
            )
        ).one()
        assert reloaded.description == "after"


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

    async def _build_two_group_role_scenario(
        self, db: Db, mocker: MockerFixture, suffix: str
    ) -> tuple[str, str, str, str]:
        """An app with two plugin-managed groups, plus a role and a user, so that a single
        operation affects both groups and the callers' per-group hook isolation is exercised.

        Returns plain ids, not ORM instances, for the same reason the operations under test now hold
        ids: the hook failure these tests inject rolls the session back, which expires every
        instance in the identity map, so a `group_a.id` read *after* the operation would raise
        MissingGreenlet from the assertion itself rather than reporting the behavior.
        """
        test_app = AppFactory.build(
            name=f"TestApp_Isolation{suffix}",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        group_a = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=f"{prefix}A")
        group_b = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=f"{prefix}B")
        role_group = RoleGroupFactory.build(name=f"Role-Isolation{suffix}", is_managed=True)
        user = OktaUserFactory.build()
        db.session.add_all([test_app, group_a, group_b, role_group, user])
        await db.session.commit()

        mocker.patch.object(okta, "add_user_to_group", return_value=None)
        mocker.patch.object(okta, "remove_user_from_group", return_value=None)
        return role_group.id, group_a.id, group_b.id, user.id

    # A failing plugin must not strand the groups queued behind it. invoke_app_group_lifecycle_hook
    # rolls the session back when a plugin raises, and a top-level rollback expires the *entire*
    # identity map, so these loops used to hand the next iteration an expired group whose `app` read
    # raised straight out of the operation -- meaning one bad plugin took down every group behind it.
    #
    # Each test fails group A on one run and group B on the next. Iteration follows dict insertion
    # order, which follows an unordered query, so failing only the group that happens to come second
    # would let the regression pass by luck.

    async def test_modify_group_users_removal_hook_failure_does_not_strand_other_groups(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """modify_group_users role-member removal cascade: one group's failure is isolated."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        for run, failing in enumerate(("A", "B")):
            role_id, group_a_id, group_b_id, user_id = await self._build_two_group_role_scenario(
                db, mocker, f"UsrRm{run}"
            )
            groups = {"A": group_a_id, "B": group_b_id}
            await ModifyRoleGroups(
                role_group=role_id, groups_to_add=[group_a_id, group_b_id], sync_to_okta=False
            ).execute()
            await ModifyGroupUsers(group=role_id, members_to_add=[user_id], sync_to_okta=False).execute()

            test_plugin.members_removed_calls.clear()
            test_plugin.members_removed_failures = {groups[failing]}
            survivor_id = groups["B" if failing == "A" else "A"]

            await ModifyGroupUsers(group=role_id, members_to_remove=[user_id], sync_to_okta=False).execute()

            assert test_plugin.members_removed_calls == [(survivor_id, [user_id])], (
                f"group {failing} failing stranded the other group's group_members_removed hook"
            )
            test_plugin.members_removed_failures.clear()

    async def test_modify_group_users_add_hook_failure_does_not_strand_other_groups(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """modify_group_users role-member add cascade: one group's failure is isolated."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        for run, failing in enumerate(("A", "B")):
            role_id, group_a_id, group_b_id, user_id = await self._build_two_group_role_scenario(
                db, mocker, f"UsrAdd{run}"
            )
            groups = {"A": group_a_id, "B": group_b_id}
            await ModifyRoleGroups(
                role_group=role_id, groups_to_add=[group_a_id, group_b_id], sync_to_okta=False
            ).execute()

            test_plugin.members_added_calls.clear()
            test_plugin.members_added_failures = {groups[failing]}
            survivor_id = groups["B" if failing == "A" else "A"]

            await ModifyGroupUsers(group=role_id, members_to_add=[user_id], sync_to_okta=False).execute()

            assert test_plugin.members_added_calls == [(survivor_id, [user_id])], (
                f"group {failing} failing stranded the other group's group_members_added hook"
            )
            test_plugin.members_added_failures.clear()

    async def test_modify_role_groups_removal_hook_failure_does_not_strand_other_groups(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """modify_role_groups group-removal cascade: one group's failure is isolated."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        for run, failing in enumerate(("A", "B")):
            role_id, group_a_id, group_b_id, user_id = await self._build_two_group_role_scenario(
                db, mocker, f"RoleRm{run}"
            )
            groups = {"A": group_a_id, "B": group_b_id}
            await ModifyRoleGroups(
                role_group=role_id, groups_to_add=[group_a_id, group_b_id], sync_to_okta=False
            ).execute()
            await ModifyGroupUsers(group=role_id, members_to_add=[user_id], sync_to_okta=False).execute()

            test_plugin.members_removed_calls.clear()
            test_plugin.members_removed_failures = {groups[failing]}
            survivor_id = groups["B" if failing == "A" else "A"]

            # Both groups leave the role in one operation, so the hook fires once per group.
            await ModifyRoleGroups(
                role_group=role_id, groups_to_remove=[group_a_id, group_b_id], sync_to_okta=False
            ).execute()

            assert test_plugin.members_removed_calls == [(survivor_id, [user_id])], (
                f"group {failing} failing stranded the other group's group_members_removed hook"
            )
            test_plugin.members_removed_failures.clear()

    async def test_modify_role_groups_add_hook_failure_does_not_strand_other_groups(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """modify_role_groups group-add cascade: one group's failure is isolated."""
        from api.operations import ModifyGroupUsers, ModifyRoleGroups

        for run, failing in enumerate(("A", "B")):
            role_id, group_a_id, group_b_id, user_id = await self._build_two_group_role_scenario(
                db, mocker, f"RoleAdd{run}"
            )
            groups = {"A": group_a_id, "B": group_b_id}
            # Member joins the role first, so attaching the groups grants first access to both.
            await ModifyGroupUsers(group=role_id, members_to_add=[user_id], sync_to_okta=False).execute()

            test_plugin.members_added_calls.clear()
            test_plugin.members_added_failures = {groups[failing]}
            survivor_id = groups["B" if failing == "A" else "A"]

            await ModifyRoleGroups(
                role_group=role_id, groups_to_add=[group_a_id, group_b_id], sync_to_okta=False
            ).execute()

            assert test_plugin.members_added_calls == [(survivor_id, [user_id])], (
                f"group {failing} failing stranded the other group's group_members_added hook"
            )
            test_plugin.members_added_failures.clear()

    # The single-fire hook call sites have the same hazard as the loops above, one frame out: the
    # rollback expires the caller's own group, and the very next statement reads it. A plugin that
    # raises would take the whole operation (or request) down with it.

    async def test_create_group_survives_failing_plugin(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """CreateGroup re-reads `self.group.id` after firing group_created, to load the group for
        its audit log. A failing plugin must not turn a successful create into MissingGreenlet."""
        from api.operations import CreateGroup

        test_app = AppFactory.build(
            name="TestApp_CreateSurvives",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        db.session.add(test_app)
        await db.session.commit()

        group_name = (
            f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}CreateSurvives"
        )
        mocker.patch.object(okta, "create_group", return_value=mocker.Mock(id="okta-create-survives"))
        test_plugin.group_created_failures = {group_name}

        # Must not raise: the plugin failure is logged and swallowed, the group is still created.
        await CreateGroup(
            group=AppGroupFactory.build(app_id=test_app.id, name=group_name, is_managed=True),
        ).execute()

        created = (await db.session.scalars(select(AppGroup).where(AppGroup.name == group_name))).one_or_none()
        assert created is not None
        assert test_plugin.group_created_calls == []  # the hook raised before recording

    async def test_put_group_survives_failing_plugin(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """PUT /groups/{id} reads `group.id` after firing group_updated, to apply tags and re-load
        the response body. A failing plugin must not turn a successful PUT into a 500."""
        # No underscore in the app name: this goes through the router, which validates the derived
        # group name against ACCESS_CONFIG's `^[A-Z][A-Za-z0-9-]*$` pattern.
        test_app = AppFactory.build(
            name="TestAppPutSurvives",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        test_group = AppGroupFactory.build(app_id=test_app.id, name=f"{prefix}Before", description="Unchanged")
        db.session.add_all([test_app, test_group])
        await db.session.commit()

        mocker.patch.object(okta, "update_group")
        test_plugin.group_updated_failures = {test_group.id}

        response = await client.put(
            url_for("api-groups.group_by_id", group_id=test_group.id),
            json={
                "type": "app_group",
                "name": f"{prefix}After",
                "description": "Unchanged",
                "app_id": test_group.app_id,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == f"{prefix}After"
        assert test_plugin.group_updated_calls == []  # the hook raised before recording

    # The call sites below read ORM state after a hook fires too, and were missed when this was
    # being fixed one site at a time. They are covered here because the SAVEPOINT is a single
    # host-side fix rather than a per-call-site convention: whatever fires a hook is protected,
    # including sites nobody enumerated. Each of these was a reproducible 500 (or a
    # MissingGreenlet out of the operation) before the savepoint.

    async def test_modify_group_type_survives_a_failing_group_deleted(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """ModifyGroupType fires group_deleted while converting away from an app group, then
        reads group.id on the very next statement to clear the app tag maps."""
        test_app = AppFactory.build(
            name="TestAppConvAway",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        test_group = AppGroupFactory.build(app_id=test_app.id, name=f"{prefix}Conv", description="Unchanged")
        db.session.add_all([test_app, test_group])
        await db.session.commit()
        group_id = test_group.id

        mocker.patch.object(okta, "update_group")
        test_plugin.group_deleted_failures = {group_id}

        response = await client.put(
            url_for("api-groups.group_by_id", group_id=group_id),
            json={"type": "okta_group", "name": "PlainConvGroup", "description": "Unchanged"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["type"] == "okta_group"
        assert test_plugin.group_deleted_calls == []  # the hook raised before recording

    async def test_modify_group_type_survives_a_failing_group_created(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """ModifyGroupType fires group_created when converting *into* an app group, then reads
        group.type for its audit log and returns the instance. Reachable from CreateApp, which
        leaves fire_created_hook at its default."""
        from api.operations import ModifyGroupType

        test_app = AppFactory.build(
            name="TestAppConvInto",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        group_name = f"{prefix}Into"
        test_group = OktaGroupFactory.build(name=group_name, description="Unchanged")
        actor = OktaUserFactory.build()
        db.session.add_all([test_app, test_group, actor])
        await db.session.commit()

        mocker.patch.object(okta, "update_group")
        test_plugin.group_created_failures = {group_name}

        # Must not raise: the plugin failure is logged and swallowed, the conversion still lands.
        converted = await ModifyGroupType(
            group=test_group.id,
            group_changes=AppGroup(app_id=test_app.id, is_owner=False),
            current_user_id=actor.id,
        ).execute()

        assert converted.type == "app_group"
        assert test_plugin.group_created_calls == []  # the hook raised before recording

    async def test_app_rename_survives_a_failing_group_updated(
        self, client: AsyncClient, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture, url_for: Any
    ) -> None:
        """Renaming an app loops ModifyGroupDetails over every one of its groups, and that
        operation reads self.group.name right after its own group_updated fire. A failure part
        way through used to 500 and leave the app renamed with only some groups renamed."""
        test_app = AppFactory.build(
            name="TestAppRename",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        owner_group = AppGroupFactory.build(
            app_id=test_app.id, is_owner=True, name=f"{prefix}{AppGroup.APP_OWNERS_GROUP_NAME_SUFFIX}"
        )
        group_a = AppGroupFactory.build(app_id=test_app.id, name=f"{prefix}Aaa")
        group_b = AppGroupFactory.build(app_id=test_app.id, name=f"{prefix}Bbb")
        db.session.add_all([test_app, owner_group, group_a, group_b])
        await db.session.commit()
        app_id = test_app.id
        # Fail every group's hook, so whichever the loop reaches first cannot be the only one
        # that matters and no group is left unrenamed by luck of iteration order.
        test_plugin.group_updated_failures = {owner_group.id, group_a.id, group_b.id}

        mocker.patch.object(okta, "update_group")

        response = await client.put(
            url_for("api-apps.app_by_id", app_id=app_id),
            json={"name": "TestAppRenamed", "description": ""},
        )

        assert response.status_code == 200, response.text
        new_prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}TestAppRenamed{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        renamed = sorted(
            g.name for g in (await db.session.scalars(select(AppGroup).where(AppGroup.app_id == app_id))).all()
        )
        assert renamed == [f"{new_prefix}Aaa", f"{new_prefix}Bbb", f"{new_prefix}Owners"], (
            f"the rename stopped part way through: {renamed}"
        )

    async def test_operation_returns_a_usable_instance_after_a_failing_hook(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """ModifyGroupUsers returns the group it operated on. A caller reading that instance is
        the same post-hook read one frame out, so the operation's return value has to survive a
        plugin failure too."""
        from api.operations import ModifyGroupUsers

        test_app = AppFactory.build(
            name="TestAppReturn",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        test_group = AppGroupFactory.build(app_id=test_app.id, name=f"{prefix}Return", is_managed=True)
        user = OktaUserFactory.build()
        db.session.add_all([test_app, test_group, user])
        await db.session.commit()
        group_id = test_group.id
        test_plugin.members_added_failures = {group_id}

        returned = await ModifyGroupUsers(group=group_id, members_to_add=[user.id], sync_to_okta=False).execute()

        assert returned.id == group_id
        assert returned.name == f"{prefix}Return"
        assert test_plugin.members_added_calls == []  # the hook raised before recording

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


class TestDurableStatusReplay:
    """The host replays `set_status(..., durable_on_failure=True)` after a failed hook, so an operator
    can still see why reconciliation failed -- without replaying ownership tokens, which would be
    unsound."""

    async def _app_group(self, db: Db, suffix: str) -> AppGroup:
        test_app = AppFactory.build(
            name=f"TestAppDurable{suffix}",
            app_group_lifecycle_plugin=DummyPlugin.ID,
            plugin_data={DummyPlugin.ID: {"configuration": {"enabled": True}}},
        )
        prefix = f"{AppGroup.APP_GROUP_NAME_PREFIX}{test_app.name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}"
        group = AppGroupFactory.build(app_id=test_app.id, is_managed=True, name=f"{prefix}G", description="before")
        db.session.add_all([test_app, group])
        await db.session.commit()
        # Re-load with `app` eager-loaded, the way the request path does: the hook reads `group.app`
        # and the relationship is lazy="raise_on_sql", so a factory-built instance would raise.
        return (
            await db.session.scalars(select(AppGroup).where(AppGroup.id == group.id).options(joinedload(AppGroup.app)))
        ).one()

    async def test_diagnostics_survive_a_failing_hook_but_tokens_do_not(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """The load-bearing test for the durable/transactional split.

        A plugin's sync status must outlive its own failure, or an operator sees a group stuck with no
        explanation. An ownership token must NOT: it is only sound when committed in the same
        transaction as the check that justified it (under `ctx.lock`), and replaying it in a fresh
        transaction after the lock released would let a second Access group, which has meanwhile
        passed its own ownership check, claim the same external group. Both then record it, and
        deleting either would delete the shared external group.

        Fails against a host that replays every recorded write instead of only the opted-in ones.
        """
        from api.operations import ModifyGroupDetails

        group = await self._app_group(db, "Split")
        group_id = group.id
        mocker.patch.object(okta, "update_group")
        test_plugin.status_writes_on_update = [
            ("sync_status", "error", True),  # diagnostic -> replayed
            ("sync_error", "the external system said no", True),  # diagnostic -> replayed
            ("external_group_id", "token-should-not-persist", False),  # ownership token -> discarded
        ]
        test_plugin.group_updated_failures = {group_id}

        await ModifyGroupDetails(group=group, description="after").execute()

        reloaded = (
            await db.session.scalars(
                select(AppGroup).where(AppGroup.id == group_id).execution_options(populate_existing=True)
            )
        ).one()
        status = (reloaded.plugin_data or {})[DummyPlugin.ID]["status"]
        assert status["sync_status"] == "error"
        assert status["sync_error"] == "the external system said no"
        assert "external_group_id" not in status

    async def test_nothing_is_replayed_when_the_hook_succeeds(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """On success the ordinary post-hook commit persists everything, tokens included -- the replay
        path is only for failures."""
        from api.operations import ModifyGroupDetails

        group = await self._app_group(db, "Success")
        group_id = group.id
        mocker.patch.object(okta, "update_group")
        test_plugin.status_writes_on_update = [
            ("sync_status", "synced", True),
            ("external_group_id", "token-persists", False),
        ]

        await ModifyGroupDetails(group=group, description="after").execute()

        reloaded = (
            await db.session.scalars(
                select(AppGroup).where(AppGroup.id == group_id).execution_options(populate_existing=True)
            )
        ).one()
        status = (reloaded.plugin_data or {})[DummyPlugin.ID]["status"]
        assert status["sync_status"] == "synced"
        assert status["external_group_id"] == "token-persists"

    async def test_replay_against_a_vanished_row_logs_and_returns(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin
    ) -> None:
        """The row can be gone by replay time (rolled out of existence, or deleted concurrently). That
        must not raise out of the host, since the surrounding operation already committed its work."""
        ctx = AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)
        ctx._status_writes.append(
            _StatusWrite(entity_type="group", entity_id="does-not-exist", property_name="s", value="v")
        )

        await ctx._reapply_durable_status(AppGroupLifecycleHook.GROUP_UPDATED, context="test")

    async def test_group_deleted_skips_the_replay(
        self, db: Db, app: FastAPI, test_plugin: DummyPlugin, mocker: MockerFixture
    ) -> None:
        """group_deleted fires while the row is on its way out -- ModifyGroupType fires it *before*
        deleting the app_group row. Nothing will read that status again, and committing here would
        land a commit inside the caller's half-finished conversion."""
        group = await self._app_group(db, "Deleted")
        ctx = AppGroupLifecycleContext(session=db.session, plugin_id=DummyPlugin.ID)
        ctx._status_writes.append(
            _StatusWrite(entity_type="group", entity_id=group.id, property_name="sync_status", value="error")
        )
        commit = mocker.patch.object(db.session, "commit", new_callable=mocker.AsyncMock)

        await ctx._reapply_durable_status(AppGroupLifecycleHook.GROUP_DELETED, context="test")

        commit.assert_not_awaited()


class TestSyncGroupHook:
    """Tests for `_sync_all_app_groups`, the `access sync-app-groups` CLI body and the only caller of
    the `sync_group` hook.

    The hook now gets one group per invocation, each in its own transaction, so these cover what is
    only true of the batch caller: every group of every configured app is visited, `group.app`
    arrives loaded as the hookspec promises, and one group's failure must not strand the groups
    behind it.
    """

    @staticmethod
    async def _add_app_with_groups(db: Db, name: str, group_suffixes: list[str]) -> tuple[str, dict[str, str]]:
        """Returns (app id, {suffix: group id}) as plain strings -- a rolled-back group in the loop
        under test expires every instance in the identity map, so a test holding ORM objects across
        that boundary would fail in its own assertions rather than reporting the behaviour."""
        test_app = AppFactory.build(name=name, app_group_lifecycle_plugin=DummyPlugin.ID)
        db.session.add(test_app)
        await db.session.flush()
        group_ids = {}
        for suffix in group_suffixes:
            group = AppGroupFactory.build(
                app_id=test_app.id,
                name=(f"{AppGroup.APP_GROUP_NAME_PREFIX}{name}{AppGroup.APP_NAME_GROUP_NAME_SEPARATOR}{suffix}"),
            )
            db.session.add(group)
            group_ids[suffix] = group.id
        await db.session.commit()
        return test_app.id, group_ids

    async def test_every_group_is_synced_with_its_app_loaded(self, db: Db, test_plugin: DummyPlugin) -> None:
        # `AppGroup.app` is lazy="raise_on_sql", so a plugin reading it (the natural way to reach
        # app-level config) raises InvalidRequestError unless the caller eager-loaded it.
        from api.cli import _sync_all_app_groups

        _, group_ids = await self._add_app_with_groups(db, "SyncApp", ["Beta", "Alpha"])

        failures = await _sync_all_app_groups()

        assert failures == 0
        # Ordered by app name then group name, so a run's sequence is reproducible.
        assert test_plugin.sync_group_calls == [
            (group_ids["Alpha"], "SyncApp"),
            (group_ids["Beta"], "SyncApp"),
        ]

    async def test_groups_after_a_failing_group_still_sync(self, db: Db, test_plugin: DummyPlugin) -> None:
        # Rolling back the failed group expires every instance in the identity map, so a graph loaded
        # before the loop is unusable afterwards: reading a column raises MissingGreenlet (no
        # greenlet for the implicit refresh) and reading a relationship raises InvalidRequestError
        # again. Both must be impossible here. This is also the isolation the plugins no longer
        # implement themselves -- it lives in the caller now.
        from api.cli import _sync_all_app_groups

        _, a_ids = await self._add_app_with_groups(db, "SyncAppA", ["One", "Two"])
        _, b_ids = await self._add_app_with_groups(db, "SyncAppB", ["Three"])
        test_plugin.sync_group_failures.add("App-SyncAppA-One")

        failures = await _sync_all_app_groups()

        assert failures == 1
        # The failing group's siblings and the following app's groups all still sync.
        assert test_plugin.sync_group_calls == [
            (a_ids["Two"], "SyncAppA"),
            (b_ids["Three"], "SyncAppB"),
        ]

    async def test_apps_without_a_plugin_are_not_synced(self, db: Db, test_plugin: DummyPlugin) -> None:
        # The Access app seeded by the db fixture has no lifecycle plugin configured.
        from api.cli import _sync_all_app_groups

        db.session.add(AppFactory.build(name="NoPluginApp"))
        await db.session.commit()

        failures = await _sync_all_app_groups()

        assert failures == 0
        assert test_plugin.sync_group_calls == []

    # The two below cover the command wrapper rather than the helper: the failure count has to
    # reach the process exit status, or a cronjob run that reconciled nothing still reports
    # success. That is the one deliberate behavior change in this command, so it gets a test.
    #
    # These call the command's async body (`sync_app_groups.callback.__wrapped__`) rather than
    # driving click.testing.CliRunner. CliRunner is synchronous, so invoking it from an async
    # test means `_with_app_context` calls asyncio.run() on another thread's event loop while
    # the test's async engine is bound to this one. SQLAlchemy's asyncio extension requires an
    # engine be used from the loop that created it, so that shape is unsound whether or not it
    # happens to pass on a given driver. `raise SystemExit` propagating out of asyncio.run to
    # Click is Python's behavior, not this repo's; what needs guarding is the `if failures`.

    async def test_command_exits_non_zero_when_a_group_failed(self, db: Db, test_plugin: DummyPlugin) -> None:
        from api.cli import sync_app_groups

        _, a_groups = await self._add_app_with_groups(db, "SyncExitA", ["One"])
        _, b_groups = await self._add_app_with_groups(db, "SyncExitB", ["Two"])
        test_plugin.sync_group_failures.add("App-SyncExitA-One")

        with pytest.raises(SystemExit) as exc_info:
            await sync_app_groups.callback.__wrapped__()

        assert exc_info.value.code == 1
        # Every group is still attempted; only the exit status reflects the failure.
        assert test_plugin.sync_group_calls == [(b_groups["Two"], "SyncExitB")]
        assert a_groups["One"] not in [call[0] for call in test_plugin.sync_group_calls]

    async def test_command_exits_zero_when_every_group_syncs(self, db: Db, test_plugin: DummyPlugin) -> None:
        from api.cli import sync_app_groups

        _, groups = await self._add_app_with_groups(db, "SyncExitOk", ["One"])

        # Must not raise SystemExit: a clean run has to leave the exit status at 0.
        await sync_app_groups.callback.__wrapped__()

        assert test_plugin.sync_group_calls == [(groups["One"], "SyncExitOk")]


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


# ---------------------------------------------------------------------------
# Okta group-push helpers exposed to app group lifecycle plugins
#
# These wrap the Okta group push mapping surface so lifecycle plugins that back Access groups with
# an external provider (e.g. Google) can create/discover/resolve links through the plugin interface
# rather than importing api.services.okta directly. okta is patched on this module so the tests
# exercise the helpers without any real Okta client.
# ---------------------------------------------------------------------------

_OKTA_APP_ID = "test-okta-app-123"
_EXTERNAL_ID_FIELD = "googleGroupEmail"


def _push_ctx() -> AppGroupLifecycleContext:
    """A context over a stand-in session: the Okta group-push capabilities do network I/O only and
    never touch it. Going through the context rather than the module-level helpers keeps the tested
    surface the one plugins actually use, and covers the delegation."""
    return AppGroupLifecycleContext(session=MagicMock(), plugin_id="google_group_manager")


def _push_source_group(mocker: MockerFixture) -> Any:
    """A minimal stand-in for the Access group a push mapping sources from."""
    group = mocker.Mock()
    group.id = "grp-1"
    group.name = "App-Google-Platform-Security"
    return group


async def test_create_push_mapping_for_existing_group_links_by_resolved_target(mocker: MockerFixture) -> None:
    # Adoption path: resolve the already-imported Okta target group by its external id profile
    # field, then link the source group to it. Returns the new mapping id.
    group = _push_source_group(mocker)
    list_groups = mocker.patch(
        "api.plugins.app_group_lifecycle.okta.list_groups",
        return_value=[mocker.Mock(group=mocker.Mock(id="okta-tgt-1"))],
    )
    create = mocker.patch(
        "api.plugins.app_group_lifecycle.okta.create_group_push_mapping", return_value={"id": "map-1"}
    )

    mapping_id = await _push_ctx().create_push_mapping_for_existing_group(
        group, _OKTA_APP_ID, _EXTERNAL_ID_FIELD, "sec@test-company.com"
    )

    assert mapping_id == "map-1"
    # The target is resolved by the external id profile field and value...
    search = list_groups.call_args.kwargs["query_params"]["search"]
    assert _EXTERNAL_ID_FIELD in search
    assert "sec@test-company.com" in search
    # ...and linked by that resolved target group id (never a target name on the adoption path).
    create.assert_called_once_with(appId=_OKTA_APP_ID, sourceGroupId="grp-1", targetGroupId="okta-tgt-1")


async def test_create_push_mapping_for_existing_group_raises_when_target_not_imported(
    mocker: MockerFixture,
) -> None:
    # Zero Okta matches means the target group hasn't been imported yet -> MissingOktaTargetError
    # (which the caller treats as "defer"), never a silent link.
    mocker.patch("api.plugins.app_group_lifecycle.okta.list_groups", return_value=[])
    create = mocker.patch("api.plugins.app_group_lifecycle.okta.create_group_push_mapping")

    with pytest.raises(MissingOktaTargetError):
        await _push_ctx().create_push_mapping_for_existing_group(
            _push_source_group(mocker), _OKTA_APP_ID, _EXTERNAL_ID_FIELD, "sec@test-company.com"
        )
    create.assert_not_called()


async def test_create_push_mapping_for_existing_group_raises_on_ambiguous_target(
    mocker: MockerFixture,
) -> None:
    # More than one Okta target carrying the same external id is a misconfiguration that never
    # self-heals; it surfaces as AmbiguousOktaTargetError rather than being conflated with "not
    # imported" or resolved arbitrarily.
    mocker.patch(
        "api.plugins.app_group_lifecycle.okta.list_groups",
        return_value=[mocker.Mock(group=mocker.Mock(id="okta-tgt-1")), mocker.Mock(group=mocker.Mock(id="okta-tgt-2"))],
    )
    create = mocker.patch("api.plugins.app_group_lifecycle.okta.create_group_push_mapping")

    with pytest.raises(AmbiguousOktaTargetError):
        await _push_ctx().create_push_mapping_for_existing_group(
            _push_source_group(mocker), _OKTA_APP_ID, _EXTERNAL_ID_FIELD, "sec@test-company.com"
        )
    create.assert_not_called()


async def test_create_push_mapping_and_new_group_creates_by_name(mocker: MockerFixture) -> None:
    # Create path: Okta creates its target group AND the downstream external group from the given
    # name and links them in one step, so we pass targetGroupName (never a targetGroupId). Returns
    # the new mapping id.
    group = _push_source_group(mocker)
    create = mocker.patch(
        "api.plugins.app_group_lifecycle.okta.create_group_push_mapping", return_value={"id": "map-1"}
    )

    mapping_id = await _push_ctx().create_push_mapping_and_new_group(group, _OKTA_APP_ID, "platform-security")

    assert mapping_id == "map-1"
    create.assert_called_once_with(appId=_OKTA_APP_ID, sourceGroupId="grp-1", targetGroupName="platform-security")


async def test_discover_existing_push_mapping_returns_mapping_id_and_external_id(mocker: MockerFixture) -> None:
    group = _push_source_group(mocker)
    mocker.patch(
        "api.plugins.app_group_lifecycle.okta.list_group_push_mappings",
        return_value=[{"id": "map-9", "sourceGroupId": "grp-1", "targetGroupId": "okta-tgt-9"}],
    )
    tgt = mocker.Mock()
    # Custom Okta attributes live in the profile union's actual_instance.additional_properties,
    # not directly on the profile object.
    tgt.group.profile.actual_instance.additional_properties = {_EXTERNAL_ID_FIELD: "found@test-company.com"}
    mocker.patch("api.plugins.app_group_lifecycle.okta.get_group", return_value=tgt)

    result = await _push_ctx().discover_existing_push_mapping_and_target_group_external_id(
        group, _OKTA_APP_ID, _EXTERNAL_ID_FIELD
    )

    assert result == ("map-9", "found@test-company.com")


async def test_discover_existing_push_mapping_returns_none_when_no_mapping(mocker: MockerFixture) -> None:
    mocker.patch("api.plugins.app_group_lifecycle.okta.list_group_push_mappings", return_value=[])
    result = await _push_ctx().discover_existing_push_mapping_and_target_group_external_id(
        _push_source_group(mocker), _OKTA_APP_ID, _EXTERNAL_ID_FIELD
    )
    assert result is None


async def test_delete_push_mapping_unlinks_without_deleting_target_by_default(mocker: MockerFixture) -> None:
    # The default unlink leaves the downstream target group in place (deleteTargetGroup=False);
    # callers that also want the target removed pass delete_target_group=True.
    delete = mocker.patch("api.plugins.app_group_lifecycle.okta.delete_group_push_mapping")

    await _push_ctx().delete_push_mapping(_OKTA_APP_ID, "map-1")

    delete.assert_called_once_with(appId=_OKTA_APP_ID, mappingId="map-1", deleteTargetGroup=False)
