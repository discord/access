"""Tests for the Google Groups Lifecycle Plugin."""

import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from pytest_mock import MockerFixture

# The plugin instantiates at import time and needs these env vars + Google libs.
os.environ["GOOGLE_WORKSPACE_OKTA_APP_ID"] = "test-okta-app-123"
os.environ["GOOGLE_WORKSPACE_DOMAIN"] = "test-company.com"

mock_google_auth = MagicMock()
mock_google_auth.default = MagicMock(return_value=(MagicMock(), None))
mock_googleapiclient_discovery = MagicMock()
mock_googleapiclient_discovery.build = MagicMock(return_value=MagicMock())

sys.modules["google"] = MagicMock()
sys.modules["google.auth"] = mock_google_auth
sys.modules["google.cloud"] = MagicMock()
sys.modules["google.cloud.sql"] = MagicMock()
sys.modules["google.cloud.sql.connector"] = MagicMock()
sys.modules["googleapiclient"] = MagicMock()
sys.modules["googleapiclient.discovery"] = mock_googleapiclient_discovery


class _FakeHttpError(Exception):
    """Stand-in for googleapiclient.errors.HttpError carrying an HTTP status."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.resp = Mock(status=status)


_errors_module = MagicMock()
_errors_module.HttpError = _FakeHttpError
sys.modules["googleapiclient.errors"] = _errors_module

plugin_dir = Path(__file__).parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

from plugin import (  # noqa: E402
    CONFIG_DISPLAY_NAME,
    CONFIG_EMAIL,
    CONFIG_ENABLED,
    PLUGIN_ID,
    STATUS_GOOGLE_GROUP_ID,
    STATUS_PUSH_MAPPING_ID,
    STATUS_SYNC_ERROR,
    STATUS_SYNC_STATUS,
    SYNC_ERROR,
    SYNC_PENDING,
    SYNC_SKIPPED,
    SYNC_SYNCED,
    GoogleGroupManagerPlugin,
)

from api.models import App, AppGroup  # noqa: E402
from api.plugins.app_group_lifecycle import (  # noqa: E402
    DanglingPushMappingError,
    UnresolvableOktaTargetError,
)


@pytest.fixture
def mock_groups_api(mocker: MockerFixture) -> MagicMock:
    mocker.patch("plugin.default", return_value=(Mock(), None))
    discovery_client = MagicMock()
    mocker.patch("plugin.build", return_value=discovery_client)
    groups_api = MagicMock()
    discovery_client.groups.return_value = groups_api
    return groups_api


@pytest.fixture
def plugin_instance(mocker: MockerFixture, mock_groups_api: MagicMock) -> GoogleGroupManagerPlugin:
    mocker.patch.dict(
        os.environ,
        {
            "GOOGLE_WORKSPACE_OKTA_APP_ID": "test-okta-app-123",
            "GOOGLE_WORKSPACE_DOMAIN": "test-company.com",
        },
    )
    return GoogleGroupManagerPlugin()


def test_metadata(plugin_instance: GoogleGroupManagerPlugin) -> None:
    meta = plugin_instance.get_plugin_metadata()
    assert meta.id == PLUGIN_ID
    assert meta.display_name


def test_app_config_properties_shape(plugin_instance: GoogleGroupManagerPlugin) -> None:
    props = plugin_instance.get_plugin_app_config_properties(PLUGIN_ID)
    assert set(props) == {"enabled", "email_pattern"}
    assert props["enabled"].required is True


def test_group_config_properties_shape(plugin_instance: GoogleGroupManagerPlugin) -> None:
    props = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})
    assert set(props) == {"email", "display_name"}
    assert props["email"].required is True
    assert props["display_name"].required is True


def test_group_config_email_property_carries_domain_suffix(plugin_instance: GoogleGroupManagerPlugin) -> None:
    # The email prefix field surfaces the domain as an inline suffix so the operator sees the
    # full address; the stored value remains the prefix only.
    props = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})
    assert props["email"].suffix == "@test-company.com"
    assert props["display_name"].suffix is None


def test_group_config_properties_surface_validation_patterns(plugin_instance: GoogleGroupManagerPlugin) -> None:
    from plugin import GOOGLE_LOCAL_PART_RE

    # With no app pattern, the email property carries just the Google-safe charset rule.
    patterns = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})["email"].validation["patterns"]
    assert [p["regex"] for p in patterns] == [GOOGLE_LOCAL_PART_RE.pattern]

    # With an app email_pattern, it is appended as a second rule.
    patterns = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {"email_pattern": r"^sec-"})[
        "email"
    ].validation["patterns"]
    assert [p["regex"] for p in patterns] == [GOOGLE_LOCAL_PART_RE.pattern, r"^sec-"]


def test_group_status_properties_shape(plugin_instance: GoogleGroupManagerPlugin) -> None:
    props = plugin_instance.get_plugin_group_status_properties(PLUGIN_ID)
    assert set(props) == {
        "push_mapping_id",
        "google_group_id",
        "sync_status",
        "sync_error",
        "last_synced_at",
    }


@pytest.mark.parametrize("pattern,ok", [(None, True), (r"^[a-z-]+$", True), (r"([", False)])
def test_validate_app_config_email_pattern(
    plugin_instance: GoogleGroupManagerPlugin, pattern: str | None, ok: bool
) -> None:
    config: dict[str, Any] = {"enabled": True}
    if pattern is not None:
        config["email_pattern"] = pattern
    errors = plugin_instance.validate_plugin_app_config(config, PLUGIN_ID)
    assert (errors == {}) is ok


def test_validate_app_config_requires_enabled(plugin_instance: GoogleGroupManagerPlugin) -> None:
    errors = plugin_instance.validate_plugin_app_config({}, PLUGIN_ID)
    assert "enabled" in errors


def test_validate_group_config_valid(plugin_instance: GoogleGroupManagerPlugin) -> None:
    errors = plugin_instance.validate_plugin_group_config(
        {"email": "platform-security", "display_name": "Platform Security"}, {}, PLUGIN_ID
    )
    assert errors == {}


@pytest.mark.parametrize(
    "config,bad_key",
    [
        ({"display_name": "X"}, "email"),  # missing email
        ({"email": "ok"}, "display_name"),  # missing display_name
        ({"email": "Bad-Upper", "display_name": "X"}, "email"),  # uppercase fails charset
        ({"email": "-bad", "display_name": "X"}, "email"),  # leading hyphen fails charset
    ],
)
def test_validate_group_config_errors(
    plugin_instance: GoogleGroupManagerPlugin, config: dict[str, Any], bad_key: str
) -> None:
    errors = plugin_instance.validate_plugin_group_config(config, {}, PLUGIN_ID)
    assert bad_key in errors


def test_validate_group_config_ignores_other_plugin(plugin_instance: GoogleGroupManagerPlugin) -> None:
    assert plugin_instance.validate_plugin_group_config({}, {}, "some_other_plugin") is None


def test_validate_group_config_enforces_app_email_pattern(plugin_instance: GoogleGroupManagerPlugin) -> None:
    # A prefix that is charset-valid but violates the app's email_pattern is rejected.
    app_config = {"email_pattern": r"^sec-"}
    errors = plugin_instance.validate_plugin_group_config(
        {"email": "platform", "display_name": "X"}, app_config, PLUGIN_ID
    )
    assert "email" in errors

    # A prefix that satisfies the app pattern passes.
    errors = plugin_instance.validate_plugin_group_config(
        {"email": "sec-platform", "display_name": "X"}, app_config, PLUGIN_ID
    )
    assert errors == {}


def test_mark_helpers_route_log_levels_by_who_must_act(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock, caplog: Any
) -> None:
    """The log level is a routing decision: only _mark_error means an Access admin has to act, so
    only it logs at ERROR. A single combined helper could not do this -- it would have to infer the
    level from whether a detail was passed, and pending/skipped both carry one."""
    group = _group(mocker)

    with caplog.at_level(logging.DEBUG, logger="plugin"):
        plugin_instance._mark_synced(ctx_mock, group)
        plugin_instance._mark_pending(ctx_mock, group, "waiting on Okta")
        plugin_instance._mark_skipped(ctx_mock, group, "no email configured")
        plugin_instance._mark_skipped(ctx_mock, group)  # nothing to act on -> DEBUG, no detail
        plugin_instance._mark_error(ctx_mock, group, "okta link is broken")

    assert {r.levelno for r in caplog.records} == {logging.DEBUG, logging.INFO, logging.ERROR}
    # Exactly one ERROR, and it is the admin-actionable one.
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == ["Google group reconciliation failed for group App-Google-Platform-Security: okta link is broken"]
    # A successful reconcile stays out of an INFO-level log entirely.
    assert [r.levelno for r in caplog.records if "Reconciled" in r.getMessage()] == [logging.DEBUG]
    # Pending and skipped are both INFO -- progress and misconfiguration, neither an admin's problem.
    # A reason-less skip is the exception: nothing to act on, so it drops to DEBUG.
    assert sorted(r.getMessage() for r in caplog.records if r.levelno == logging.INFO) == [
        "Deferring App-Google-Platform-Security: waiting on Okta",
        "Skipping App-Google-Platform-Security: no email configured",
    ]
    assert [r.levelno for r in caplog.records if "not managed by this plugin" in r.getMessage()] == [logging.DEBUG]
    ctx_mock.set_status.assert_any_call(group, STATUS_SYNC_ERROR, None, durable_on_failure=True)


async def test_reconcile_clears_a_stale_detail_on_recovery(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # A group that failed and then recovered must not keep showing the old explanation. Starts from
    # real recorded failure state and asserts against the group's own plugin_data, rather than
    # asserting a mock was called with None.
    group = _group(
        mocker,
        group_config={"email": "sec", "display_name": "Sec"},
        status={
            STATUS_SYNC_STATUS: SYNC_ERROR,
            STATUS_SYNC_ERROR: "something went wrong on an earlier pass",
            STATUS_GOOGLE_GROUP_ID: "ggid-1",
            STATUS_PUSH_MAPPING_ID: "map-1",
        },
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Sec",
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_get_owned_group_id", return_value="ggid-1")
    mocker.patch.object(plugin_instance, "_get_google_group", return_value={"groupKey": {"id": "sec@test-company.com"}})
    mocker.patch.object(plugin_instance, "_adopt_or_enforce", return_value=None)

    await plugin_instance._reconcile(ctx_mock, group)

    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status[STATUS_SYNC_STATUS] == SYNC_SYNCED
    assert status[STATUS_SYNC_ERROR] is None  # the stale explanation is gone, not merely overwritten


def _group(
    mocker: MockerFixture,
    *,
    app_config: dict[str, Any] | None = None,
    group_config: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    description: str = "",
) -> Mock:
    app = Mock(spec=App)
    app.plugin_data = {PLUGIN_ID: {"configuration": app_config or {"enabled": True}, "status": {}}}
    group = Mock(spec=AppGroup)
    group.id = "grp-1"
    group.name = "App-Google-Platform-Security"
    group.description = description
    group.app = app
    group.plugin_data = {PLUGIN_ID: {"configuration": group_config or {}, "status": status or {}}}
    return group


def test_full_email_appends_domain(plugin_instance: GoogleGroupManagerPlugin) -> None:
    assert plugin_instance._full_email("platform-security") == "platform-security@test-company.com"


def test_prefix_from_email_strips_domain(plugin_instance: GoogleGroupManagerPlugin) -> None:
    assert plugin_instance._prefix_from_email("platform-security@test-company.com") == "platform-security"


def test_prefix_from_email_returns_none_on_domain_mismatch(plugin_instance: GoogleGroupManagerPlugin) -> None:
    assert plugin_instance._prefix_from_email("x@other.com") is None


def test_is_enabled_reads_app_config(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker)
    ctx_mock.get_config.side_effect = lambda *_a, **_k: True
    assert plugin_instance._is_enabled(ctx_mock, group) is True


def test_validate_email_against_pattern(plugin_instance: GoogleGroupManagerPlugin) -> None:
    assert plugin_instance._validate_email_against_pattern("platform", r"^sec-") is not None
    assert plugin_instance._validate_email_against_pattern("sec-platform", r"^sec-") is None
    assert plugin_instance._validate_email_against_pattern("anything", None) is None


def test_configured_accessors_read_group_config(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # The accessors surface the group's configured email prefix and display name from plugin config.
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "email": "sec",
        "display_name": "Security",
    }.get(key, default)
    group = _group(mocker)
    assert plugin_instance._get_configured_email_prefix(ctx_mock, group) == "sec"
    assert plugin_instance._get_configured_display_name(ctx_mock, group) == "Security"

    # A missing value falls through to None rather than raising.
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {}.get(key, default)
    assert plugin_instance._get_configured_email_prefix(ctx_mock, group) is None
    assert plugin_instance._get_configured_display_name(ctx_mock, group) is None


async def test_get_google_group_calls_get_by_resource_name(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    mock_groups_api.get().execute.return_value = {"name": "groups/ggid-1"}
    assert (await plugin_instance._get_google_group("ggid-1"))["name"] == "groups/ggid-1"
    assert mock_groups_api.get.call_args.kwargs == {"name": "groups/ggid-1"}


async def test_patch_google_group_sets_update_mask(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    await plugin_instance._patch_google_group("ggid-1", display_name="New", description="d")
    kwargs = mock_groups_api.patch.call_args.kwargs
    assert kwargs["name"] == "groups/ggid-1"
    assert kwargs["body"] == {"displayName": "New", "description": "d"}
    assert kwargs["updateMask"] == "description,displayName"


async def test_patch_google_group_noop_when_no_fields(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    await plugin_instance._patch_google_group("ggid-1")
    mock_groups_api.patch.assert_not_called()


async def test_delete_google_group_calls_delete_by_resource_name(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    await plugin_instance._delete_google_group("ggid-1")
    assert mock_groups_api.delete.call_args.kwargs == {"name": "groups/ggid-1"}


@pytest.mark.parametrize("status", [403, 404])
async def test_delete_google_group_tolerates_absent_group(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock, status: int
) -> None:
    # Deleting an already-absent group (deleted out of band, or a delete replay/retry the
    # reconcile design intends to be safe) must not raise: Cloud Identity returns 403
    # ("permission denied ... or it may not exist") or 404 for a group we can't see. Otherwise
    # group_deleted would raise and the host would roll back the surrounding delete.
    from googleapiclient.errors import HttpError

    mock_groups_api.delete().execute.side_effect = HttpError(status)
    assert await plugin_instance._delete_google_group("ggid-gone") is None


async def test_delete_google_group_reraises_non_absent_error(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    # A non-absent error (e.g. 500) is a real failure and must surface, not be swallowed.
    from googleapiclient.errors import HttpError

    mock_groups_api.delete().execute.side_effect = HttpError(500)
    with pytest.raises(HttpError):
        await plugin_instance._delete_google_group("ggid-1")


async def test_lookup_returns_bare_id(plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock) -> None:
    mock_groups_api.lookup().execute.return_value = {"name": "groups/ggid-9"}
    assert await plugin_instance._look_up_google_group_id("x@test-company.com") == "ggid-9"
    assert mock_groups_api.lookup.call_args.kwargs == {"groupKey_id": "x@test-company.com"}


async def test_lookup_returns_none_on_404(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    from googleapiclient.errors import HttpError

    mock_groups_api.lookup().execute.side_effect = HttpError(404)
    assert await plugin_instance._look_up_google_group_id("missing@test-company.com") is None


async def test_lookup_returns_none_on_403(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    # Cloud Identity returns 403 (permission-denied "or it may not exist") for a group
    # that doesn't exist, not 404; the lookup must treat it as absent, not raise.
    from googleapiclient.errors import HttpError

    mock_groups_api.lookup().execute.side_effect = HttpError(403)
    assert await plugin_instance._look_up_google_group_id("missing@test-company.com") is None


async def test_email_from_status_returns_email_when_present(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker, status={STATUS_GOOGLE_GROUP_ID: "ggid-1"})
    mocker.patch.object(plugin_instance, "_get_google_group", return_value={"groupKey": {"id": "sec@test-company.com"}})
    assert await plugin_instance._get_email_from_status(ctx_mock, group) == "sec@test-company.com"


@pytest.mark.parametrize("status", [403, 404])
async def test_email_from_status_returns_none_when_group_absent(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, status: int, ctx_mock: MagicMock
) -> None:
    # The cached group was deleted out of band: recovering the email must treat it as absent
    # (like _get_owned_group_id) and return None, not raise -- otherwise reconcile turns a transient
    # race into a hard SYNC_ERROR instead of a clean deferral.
    from googleapiclient.errors import HttpError

    group = _group(mocker, status={STATUS_GOOGLE_GROUP_ID: "ggid-gone"})
    mocker.patch.object(plugin_instance, "_get_google_group", side_effect=HttpError(status))
    assert await plugin_instance._get_email_from_status(ctx_mock, group) is None


async def test_email_from_status_reraises_non_absent_error(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # A non-absent error (e.g. 500) is a real failure and must surface, not be swallowed.
    from googleapiclient.errors import HttpError

    group = _group(mocker, status={STATUS_GOOGLE_GROUP_ID: "ggid-1"})
    mocker.patch.object(plugin_instance, "_get_google_group", side_effect=HttpError(500))
    with pytest.raises(HttpError):
        await plugin_instance._get_email_from_status(ctx_mock, group)


def test_email_config_property_is_immutable(plugin_instance: GoogleGroupManagerPlugin) -> None:
    props = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})
    assert props["email"].immutable is True
    assert props["display_name"].immutable is False


@pytest.fixture
def ctx_mock() -> MagicMock:
    """A stand-in for the host's `AppGroupLifecycleContext`.

    The plugin's entire Access surface is this object, so these tests assert against it directly
    rather than patching host module attributes two layers down. Config/status accessors are
    synchronous; everything that does I/O is awaited.
    """
    ctx = MagicMock()
    ctx.plugin_id = PLUGIN_ID
    ctx.lock = AsyncMock()
    ctx.find_groups_by_status = AsyncMock(return_value=[])
    ctx.set_group_description = AsyncMock()
    ctx.create_push_mapping_and_new_group = AsyncMock()
    ctx.create_push_mapping_for_existing_group = AsyncMock()
    ctx.discover_existing_push_mapping_and_target_group_external_id = AsyncMock(return_value=None)
    ctx.delete_push_mapping = AsyncMock()

    # Synchronous by design: the real ones mutate plugin_data in memory and mark the object for
    # persistence. The getters read through to the object's plugin_data like the real context does,
    # so a test that seeds `_group(config=..., status=...)` doesn't also have to stub them, and tests
    # override `.side_effect` where they want specific values. The setters write through as well, so
    # assertions can read the resulting `plugin_data` -- being MagicMocks, they still record every
    # call for tests that assert on the write itself.
    def _read(section: str):
        def read(obj: Any, key: str, default: Any = None) -> Any:
            return (getattr(obj, "plugin_data", None) or {}).get(PLUGIN_ID, {}).get(section, {}).get(key, default)

        return read

    def _write(section: str):
        def write(obj: Any, key: str, value: Any, **_: Any) -> None:
            obj.plugin_data.setdefault(PLUGIN_ID, {}).setdefault(section, {})[key] = value

        return write

    ctx.get_config = MagicMock(side_effect=_read("configuration"))
    ctx.set_config = MagicMock(side_effect=_write("configuration"))
    ctx.get_status = MagicMock(side_effect=_read("status"))
    ctx.set_status = MagicMock(side_effect=_write("status"))
    return ctx


async def test_reconcile_creates_when_no_link_and_config_present(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(
        mocker, group_config={"email": "platform-security", "display_name": "Platform Security"}, description="Sec team"
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "platform-security",
        "display_name": "Platform Security",
        "email_pattern": None,
    }.get(key, default)
    # No existing group on the adoption lookup; after Okta creates it via the push mapping, the
    # second lookup resolves the new Cloud Identity id.
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", side_effect=[None, "ggid-1"])
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = None
    ctx_mock.create_push_mapping_and_new_group.return_value = "map-1"
    ctx_mock.find_groups_by_status.return_value = []
    # Okta names the fresh group after the email prefix; enforce patches the real display name.
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "platform-security@test-company.com"},
            "displayName": "platform-security",
            "description": "",
        },
    )
    patch = mocker.patch.object(plugin_instance, "_patch_google_group")

    await plugin_instance._reconcile(ctx_mock, group)

    ctx_mock.create_push_mapping_and_new_group.assert_awaited_once_with(group, "test-okta-app-123", "platform-security")
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status[STATUS_GOOGLE_GROUP_ID] == "ggid-1"
    assert status[STATUS_PUSH_MAPPING_ID] == "map-1"
    assert status[STATUS_SYNC_STATUS] == SYNC_SYNCED
    patch.assert_called_once()  # metadata enforced onto the freshly-created group


async def test_reconcile_enforces_config_onto_existing_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(
        mocker,
        group_config={"email": "new-prefix", "display_name": "New Name"},
        status={"google_group_id": "ggid-1", "push_mapping_id": "map-1"},
        description="New desc",
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "new-prefix",
        "display_name": "New Name",
        "email_pattern": None,
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda obj, key, default=None: {
        "google_group_id": "ggid-1",
        "push_mapping_id": "map-1",
    }.get(key, default)
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "old-prefix@test-company.com"},
            "displayName": "Old Name",
            "description": "Old desc",
        },
    )
    patch = mocker.patch.object(plugin_instance, "_patch_google_group")

    await plugin_instance._reconcile(ctx_mock, group)

    patch.assert_called_once()
    # The email (groupKey) is immutable and never patched; only displayName/description.
    assert patch.call_args.kwargs == {"display_name": "New Name", "description": "New desc"}


async def test_reconcile_clears_description_on_existing_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Emptying the Access description of an Access-owned group must clear it in Google rather
    # than being backfilled straight back from Google's stale value.
    group = _group(
        mocker,
        group_config={"email": "new-prefix", "display_name": "New Name"},
        status={"google_group_id": "ggid-1", "push_mapping_id": "map-1"},
        description="",
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "new-prefix",
        "display_name": "New Name",
        "email_pattern": None,
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda obj, key, default=None: {
        "google_group_id": "ggid-1",
        "push_mapping_id": "map-1",
    }.get(key, default)
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "new-prefix@test-company.com"},
            "displayName": "New Name",
            "description": "Old desc",
        },
    )
    patch = mocker.patch.object(plugin_instance, "_patch_google_group")

    await plugin_instance._reconcile(ctx_mock, group)

    # The clear is pushed to Google (empty description), and Access is not backfilled from it.
    assert patch.call_args.kwargs["description"] == ""
    assert group.description == ""
    ctx_mock.set_group_description.assert_not_awaited()


async def test_reconcile_adopts_missing_config_from_live_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={}, description="")  # no config, no description
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = (
        "map-1",
        "adopted@test-company.com",
    )
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-1")
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "adopted@test-company.com"},
            "displayName": "Adopted Name",
            "description": "Adopted desc",
        },
    )
    ctx_mock.create_push_mapping_for_existing_group.return_value = "map-existing"
    ctx_mock.find_groups_by_status.return_value = []  # not owned elsewhere
    seed = ctx_mock.set_config
    modify = ctx_mock.set_group_description
    patch = mocker.patch.object(plugin_instance, "_patch_google_group")

    await plugin_instance._reconcile(ctx_mock, group)

    seed.assert_any_call(group, CONFIG_EMAIL, "adopted")
    seed.assert_any_call(group, CONFIG_DISPLAY_NAME, "Adopted Name")
    # Empty Access description backfilled from Google via the ModifyGroupDetails operation (which
    # updates Access + syncs Okta); the group_updated hook is suppressed to avoid re-entering this
    # plugin, and Google itself is not mutated.
    modify.assert_awaited_once_with(group, "Adopted desc")
    patch.assert_not_called()


async def test_reconcile_flags_error_on_domain_mismatch_adoption(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {"enabled": True}.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = ("map-1", "x@other-domain.com")
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-1")
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "x@other-domain.com"},
            "displayName": "X",
            "description": "",
        },
    )
    ctx_mock.find_groups_by_status.return_value = []  # not owned elsewhere
    set_status = ctx_mock.set_status

    await plugin_instance._reconcile(ctx_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, durable_on_failure=True)


async def test_reconcile_skips_when_existing_mapping_email_mismatches_config(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # An out-of-band push mapping points at a different Google group than the group's configured
    # email -> a conflict that won't self-heal, so reconcile refuses to adopt the wrong group. The
    # group's own config is one of the two ways out, so this is SKIPPED (the owner's to fix) rather
    # than SYNC_ERROR (an admin's), and the reason is recorded for them to read.
    group = _group(mocker, group_config={"email": "platform-security", "display_name": "Platform Security"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "platform-security",
        "display_name": "Platform Security",
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    # No Google group at the configured email, so discovery runs and finds a mapping pointing elsewhere.
    lookup = mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value=None)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = (
        "map-1",
        "someone-else@test-company.com",
    )
    set_status = ctx_mock.set_status
    claim = mocker.patch.object(plugin_instance, "_claim_group_id")

    await plugin_instance._reconcile(ctx_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SKIPPED, durable_on_failure=True)
    # The reason names both the mapped and the configured email.
    error_msgs = [c.args[2] for c in set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR]
    assert error_msgs
    assert "someone-else@test-company.com" in error_msgs[0]
    assert "platform-security@test-company.com" in error_msgs[0]
    # It bails before trying to claim/adopt; only the configured-email lookup ran (not the mapped one).
    claim.assert_not_called()
    lookup.assert_called_once()


async def test_reconcile_skips_when_mapped_target_group_has_no_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # An out-of-band push mapping whose Okta target group Okta created via group push: it carries
    # no googleGroupEmail and never will, because Okta writes that attribute only on import and
    # does not backfill push-created targets. Unlike the awaiting-import case this cannot self-heal,
    # so reconcile records it instead of deferring -- and does NOT re-raise, since there is
    # nothing for the host to retry. It is SKIPPED, not SYNC_ERROR: setting this group's email
    # config resolves it, so it belongs to the group's owners rather than to Access admins.
    group = _group(mocker, group_config={})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {"enabled": True}.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    lookup = mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value=None)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.side_effect = UnresolvableOktaTargetError(
        "target group has no 'googleGroupEmail'", mapping_id="map-7", target_group_name="platform-sec"
    )
    set_status = ctx_mock.set_status
    claim = mocker.patch.object(plugin_instance, "_claim_group_id")

    await plugin_instance._reconcile(ctx_mock, group)  # must not raise

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SKIPPED, durable_on_failure=True)
    error_msgs = [c.args[2] for c in set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR]
    assert error_msgs
    # The reason is always recorded in the sync-error field even though the status is SKIPPED --
    # it is the only place the group's owners see what to fix. And it must name the one action
    # that fixes this: setting the email config. The host's neutral message is not passed through.
    assert "googleGroupEmail" in error_msgs[0]
    assert f"'{CONFIG_EMAIL}'" in error_msgs[0]
    assert "test-company.com" in error_msgs[0]
    # It names the target group, which is the only thing telling the owner WHICH address to
    # configure; without it they could link this group to an unrelated Google group.
    assert "platform-sec" in error_msgs[0]
    # The discovered mapping is still recorded, so a later pass doesn't create a duplicate.
    set_status.assert_any_call(group, STATUS_PUSH_MAPPING_ID, "map-7")
    # It bails before adopting anything; no Google group was resolved to claim.
    claim.assert_not_called()
    lookup.assert_not_called()


async def test_reconcile_errors_when_unresolvable_target_and_email_is_configured(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Case 3 is also reached when an email IS configured and the Google lookup simply found
    # nothing, e.g. the linked Google group was deleted out of band. That is not a configuration
    # gap -- the owner already did the one thing a skip would ask of them -- so it must be an
    # admin-facing error, not the quietest state the plugin has.
    group = _group(mocker, group_config={"email": "sec", "display_name": "Sec"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Sec",
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value=None)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.side_effect = UnresolvableOktaTargetError(
        "target group has no 'googleGroupEmail'", mapping_id="map-7", target_group_name="platform-sec"
    )
    set_status = ctx_mock.set_status

    await plugin_instance._reconcile(ctx_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, durable_on_failure=True)
    error_msgs = [c.args[2] for c in set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR]
    assert error_msgs
    # It names the configured address that resolved to nothing, and does NOT tell the owner to set
    # the email config they have already set.
    assert "sec@test-company.com" in error_msgs[0]
    assert "Set this group's" not in error_msgs[0]


async def test_recorded_mapping_id_prevents_creating_a_duplicate_on_the_next_pass(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # The follow-on pass after an unresolvable skip, which is the whole point of recording the
    # mapping id. Once the owner configures the address, Case 2 resolves the Google group and the
    # recorded mapping must be reused. Without it the mapping step would look the target up by
    # googleGroupEmail -- the attribute a push-created target never has -- and park the group on
    # SYNC_PENDING forever instead of linking it.
    group = _group(mocker, group_config={"email": "platform-sec", "display_name": "Sec"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "platform-sec",
        "display_name": "Sec",
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda _obj, key, default=None: {
        STATUS_PUSH_MAPPING_ID: "map-7",  # recorded by the earlier skip
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_get_owned_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-1")
    mocker.patch.object(plugin_instance, "_claim_group_id", return_value="ggid-1")
    mocker.patch.object(plugin_instance, "_get_google_group", return_value={"groupKey": {"id": "x"}})
    mocker.patch.object(plugin_instance, "_adopt_or_enforce", return_value=None)

    await plugin_instance._reconcile(ctx_mock, group)

    ctx_mock.create_push_mapping_for_existing_group.assert_not_awaited()
    ctx_mock.create_push_mapping_and_new_group.assert_not_awaited()
    ctx_mock.set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SYNCED, durable_on_failure=True)


async def test_sync_group_raises_so_a_failed_group_fails_the_batch_run(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # _reconcile records an admin-actionable failure as status instead of raising, so the batch
    # sync would otherwise count the group as a success and the CLI would exit 0. sync_group
    # re-raises on SYNC_ERROR specifically; SKIPPED and PENDING must not fail the run.
    group = _group(mocker)
    mocker.patch.object(plugin_instance, "_reconcile")

    ctx_mock.get_status.side_effect = lambda _obj, key, default=None: {
        STATUS_SYNC_STATUS: SYNC_ERROR,
        STATUS_SYNC_ERROR: "the okta link is broken",
    }.get(key, default)
    with pytest.raises(Exception, match="the okta link is broken"):
        await plugin_instance.sync_group(ctx_mock, group, PLUGIN_ID)

    for quiet in (SYNC_SKIPPED, SYNC_PENDING, SYNC_SYNCED):
        ctx_mock.get_status.side_effect = lambda _obj, key, default=None, _s=quiet: {
            STATUS_SYNC_STATUS: _s,
            STATUS_SYNC_ERROR: "detail",
        }.get(key, default)
        await plugin_instance.sync_group(ctx_mock, group, PLUGIN_ID)  # must not raise


async def test_reconcile_errors_when_push_mapping_target_group_is_gone(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # The push mapping outlived its Okta target group. Nothing downstream is resolvable through a
    # broken link and it cannot self-heal, so reconcile reports it instead of re-raising a raw SDK
    # 404 on every periodic pass. Crucially the mapping id is NOT recorded: it is unusable, and
    # recording it would make a later pass believe this group is already linked.
    group = _group(mocker, group_config={})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {"enabled": True}.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value=None)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.side_effect = DanglingPushMappingError(
        "target group gone", mapping_id="map-7", target_group_id="okta-tgt-gone"
    )
    set_status = ctx_mock.set_status
    claim = mocker.patch.object(plugin_instance, "_claim_group_id")

    await plugin_instance._reconcile(ctx_mock, group)  # must not raise

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, durable_on_failure=True)
    error_msgs = [c.args[2] for c in set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR]
    assert error_msgs and "okta-tgt-gone" in error_msgs[0]
    # A broken mapping must never be recorded as this group's link.
    assert not [c for c in set_status.call_args_list if c.args[1] == STATUS_PUSH_MAPPING_ID]
    claim.assert_not_called()


async def test_reconcile_grandfathers_unchanged_legacy_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # An existing group whose prefix violates a later-added pattern is left alone:
    # the email (groupKey) is immutable and never patched, so the pattern is never
    # re-enforced on an existing group and reconcile marks it synced, not error.
    group = _group(
        mocker,
        group_config={"email": "legacy", "display_name": "Legacy"},
        status={"google_group_id": "ggid-1", "push_mapping_id": "map-1"},
        description="d",
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "legacy",
        "display_name": "Legacy",
        "email_pattern": r"^sec-",
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda obj, key, default=None: {
        "google_group_id": "ggid-1",
        "push_mapping_id": "map-1",
    }.get(key, default)
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "legacy@test-company.com"},
            "displayName": "Legacy",
            "description": "d",
        },
    )
    mocker.patch.object(plugin_instance, "_patch_google_group")
    set_status = ctx_mock.set_status

    await plugin_instance._reconcile(ctx_mock, group)

    # Marked synced, not error, despite the prefix not matching ^sec-.
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SYNCED, durable_on_failure=True)


async def test_reconcile_skips_when_disabled(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock, caplog: Any
) -> None:
    group = _group(mocker)
    ctx_mock.get_config.side_effect = lambda *_a, **_k: False  # enabled = False
    discover = ctx_mock.discover_existing_push_mapping_and_target_group_external_id
    with caplog.at_level(logging.DEBUG, logger="plugin"):
        await plugin_instance._reconcile(ctx_mock, group)
    discover.assert_not_called()
    # Recorded, not silent: an empty status is indistinguishable from "the hook has not run yet",
    # and the group page polls for the whole refresh window on every view when it sees one.
    ctx_mock.set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SKIPPED, durable_on_failure=True)
    # An app that does not use this plugin is not something anyone acts on, so this skip carries
    # no detail and stays out of an INFO-level log. Skips that DO need an owner's attention still
    # record a reason.
    ctx_mock.set_status.assert_any_call(group, STATUS_SYNC_ERROR, None, durable_on_failure=True)
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]


async def test_reconcile_marks_skipped_when_config_is_missing(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    """The other way a group ends up unmanaged: enabled for the app, but with no email config to
    resolve a Google group from. Common for groups that predate plugin enablement, since only the
    create path enforces the config."""
    group = _group(mocker)
    ctx_mock.get_config.side_effect = lambda _entity, key, *a, **k: True if key == CONFIG_ENABLED else None
    ctx_mock.get_status.return_value = None
    ctx_mock.find_groups_by_status.return_value = []
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = None

    await plugin_instance._reconcile(ctx_mock, group)

    ctx_mock.set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SKIPPED, durable_on_failure=True)
    # The reason names the config the owner has to set, not just that the group was skipped.
    reasons = [c.args[2] for c in ctx_mock.set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR]
    assert reasons and f"'{CONFIG_EMAIL}'" in reasons[0]
    assert (
        SYNC_SKIPPED
        not in (
            GoogleGroupManagerPlugin.get_plugin_group_status_properties(plugin_instance, plugin_id=PLUGIN_ID) or {}
        )[STATUS_SYNC_STATUS].pending_values
    )


async def test_reconcile_marks_pending_when_google_group_not_yet_created(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Okta creates the downstream Google group asynchronously; until it appears the second lookup
    # returns None and the group is parked SYNC_PENDING (never SYNCED), to be patched on a later
    # reconcile once it materializes. The mapping is still recorded meanwhile.
    group = _group(mocker, group_config={"email": "sec", "display_name": "Sec"}, description="d")
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Sec",
        "email_pattern": None,
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", side_effect=[None, None])
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = None
    ctx_mock.create_push_mapping_and_new_group.return_value = "map-1"

    await plugin_instance._reconcile(ctx_mock, group)

    ctx_mock.create_push_mapping_and_new_group.assert_awaited_once()
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status[STATUS_SYNC_STATUS] == SYNC_PENDING
    assert status.get(STATUS_PUSH_MAPPING_ID) == "map-1"  # mapping recorded even while deferred


async def test_reconcile_skips_recreate_when_mapping_already_recorded(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Second reconcile after a deferred create: the push mapping id is already recorded and the
    # Google group has now materialized. Reconcile must adopt it and mark SYNCED WITHOUT
    # re-creating the mapping/group -- the core idempotency guarantee.
    group = _group(
        mocker,
        group_config={"email": "platform-security", "display_name": "Platform Security"},
        status={"push_mapping_id": "map-1"},  # recorded on the prior deferred pass; not yet owned
        description="Sec team",
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "platform-security",
        "display_name": "Platform Security",
        "email_pattern": None,
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-1")  # now materialized
    ctx_mock.find_groups_by_status.return_value = []  # not owned elsewhere
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-1",
            "groupKey": {"id": "platform-security@test-company.com"},
            "displayName": "platform-security",
            "description": "",
        },
    )
    mocker.patch.object(plugin_instance, "_patch_google_group")

    create_new = ctx_mock.create_push_mapping_and_new_group

    await plugin_instance._reconcile(ctx_mock, group)

    ctx_mock.create_push_mapping_and_new_group.assert_not_awaited()
    create_new.assert_not_called()
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status[STATUS_SYNC_STATUS] == SYNC_SYNCED
    assert status[STATUS_GOOGLE_GROUP_ID] == "ggid-1"


async def test_reconcile_marks_error_and_reraises_on_unexpected_failure(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # An unexpected error mid-reconcile must both persist SYNC_ERROR (committed inside the hook so
    # it survives the host's post-hook rollback) AND propagate to the host.
    group = _group(
        mocker,
        group_config={"email": "sec", "display_name": "Sec"},
        status={"google_group_id": "ggid-1"},
        description="d",
    )
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Sec",
    }.get(key, default)
    # A cached, owned group is live, so reconcile proceeds to the enforce step -- where the Google
    # read blows up with an unexpected (non-absent) error.
    mocker.patch.object(plugin_instance, "_get_owned_group_id", return_value="ggid-1")
    mocker.patch.object(plugin_instance, "_get_google_group", side_effect=RuntimeError("boom"))
    set_status = ctx_mock.set_status

    with pytest.raises(RuntimeError, match="boom"):
        await plugin_instance._reconcile(ctx_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, durable_on_failure=True)


async def test_reconcile_create_path_skips_on_pattern_violation(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={"email": "platform", "display_name": "P"}, description="d")
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "platform",
        "display_name": "P",
        "email_pattern": r"^sec-",
    }.get(key, default)
    ctx_mock.get_status.side_effect = lambda *_a, **_k: None
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value=None)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = None
    create = ctx_mock.create_push_mapping_and_new_group
    set_status = ctx_mock.set_status

    await plugin_instance._reconcile(ctx_mock, group)

    create.assert_not_called()
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SKIPPED, durable_on_failure=True)


async def test_reconcile_creates_when_no_group_exists(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Security",
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", side_effect=[None, "ggid-new"])
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = None
    ctx_mock.create_push_mapping_and_new_group.return_value = "map-1"
    ctx_mock.find_groups_by_status.return_value = []
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={"name": "groups/ggid-new", "groupKey": {"id": "sec@test-company.com"}, "displayName": "sec"},
    )
    mocker.patch.object(plugin_instance, "_patch_google_group")

    await plugin_instance._reconcile(ctx_mock, group)

    ctx_mock.create_push_mapping_and_new_group.assert_awaited_once_with(group, "test-okta-app-123", "sec")
    assert group.plugin_data[PLUGIN_ID]["status"][STATUS_GOOGLE_GROUP_ID] == "ggid-new"


async def test_reconcile_creates_when_lookup_403s_for_absent_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, mock_groups_api: MagicMock, ctx_mock: Any
) -> None:
    # Repro: Cloud Identity's groups:lookup returns 403 ("permission denied ... or it may
    # not exist") for a group that does not exist yet, not 404. Reconcile must treat that
    # as absent and create via push (deferring until the group appears), never marking error.
    from googleapiclient.errors import HttpError

    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Security",
    }.get(key, default)
    mock_groups_api.lookup().execute.side_effect = HttpError(403)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = None
    ctx_mock.create_push_mapping_and_new_group.return_value = "map-1"

    await plugin_instance._reconcile(ctx_mock, group)

    # The 403 lookup is treated as absent, so we create via push and defer (not error).
    ctx_mock.create_push_mapping_and_new_group.assert_awaited_once()
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status[STATUS_SYNC_STATUS] == SYNC_PENDING


async def test_reconcile_adopts_existing_group_by_email_lookup(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Security",
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-existing")
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={
            "name": "groups/ggid-existing",
            "groupKey": {"id": "sec@test-company.com"},
            "displayName": "Security",
            "description": "",
        },
    )
    create = ctx_mock.create_push_mapping_and_new_group
    ctx_mock.create_push_mapping_for_existing_group.return_value = "map-existing"
    ctx_mock.find_groups_by_status.return_value = []

    await plugin_instance._reconcile(ctx_mock, group)

    create.assert_not_called()  # existing group is adopted, not created via push
    assert group.plugin_data[PLUGIN_ID]["status"][STATUS_GOOGLE_GROUP_ID] == "ggid-existing"


async def test_reconcile_refuses_google_group_owned_by_another_access_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # A group whose email resolves to a Google group already managed by another Access group --
    # in a *different* app sharing this plugin -- must be refused, not adopted/clobbered.
    group = _group(mocker, group_config={"email": "shared", "display_name": "Shared"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "shared",
        "display_name": "Shared",
    }.get(key, default)
    # No push mapping yet -> resolve adopts the existing group by email and the owner check runs.
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-shared")
    # The established owner lives in a different app but already records the id + a push mapping.
    owner = Mock(spec=AppGroup)
    owner.id = "owner-grp"
    owner.name = "App-Other-Owner"
    owner.plugin_data = {
        PLUGIN_ID: {
            "configuration": {},
            "status": {STATUS_GOOGLE_GROUP_ID: "ggid-shared", STATUS_PUSH_MAPPING_ID: "map-owner"},
        }
    }
    ctx_mock.find_groups_by_status.return_value = [owner]

    enforce = mocker.patch.object(plugin_instance, "_adopt_or_enforce")
    get_live = mocker.patch.object(plugin_instance, "_get_google_group")
    set_status = ctx_mock.set_status

    await plugin_instance._reconcile(ctx_mock, group)

    # Bailed before fetching/enforcing the live group or creating a second mapping by either path.
    get_live.assert_not_called()
    enforce.assert_not_called()
    ctx_mock.create_push_mapping_for_existing_group.assert_not_awaited()
    ctx_mock.create_push_mapping_and_new_group.assert_not_awaited()
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SKIPPED, durable_on_failure=True)
    # The owning group's name is plumbed into the error.
    error_msg = next(c.args[2] for c in set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR)
    assert "App-Other-Owner" in error_msg


async def test_reconcile_does_not_persist_id_when_owned_by_another_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Refusing adoption must not leave the other group's id in this group's status. If it did,
    # group_deleted (which keys off that id) would later delete a Google group we never owned.
    # Uses the real status helpers so we can observe what is (not) persisted.
    group = _group(mocker, group_config={"email": "shared", "display_name": "Shared"}, status={})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "shared",
        "display_name": "Shared",
    }.get(key, default)
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-shared")
    owner = Mock(spec=AppGroup)
    owner.name = "App-Other-Owner"
    ctx_mock.find_groups_by_status.return_value = [owner]

    await plugin_instance._reconcile(ctx_mock, group)

    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status.get(STATUS_GOOGLE_GROUP_ID) is None  # id of the group we don't own was NOT recorded
    assert status.get(STATUS_SYNC_STATUS) == SYNC_SKIPPED
    # The reason must reach the group's owners, who resolve it by changing their email config.
    assert "App-Other-Owner" in status.get(STATUS_SYNC_ERROR)


async def test_reconcile_runs_owner_check_in_config_absent_adoption(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Adoption path: no Access-side config (so the resolved email is None), but an out-of-band
    # Okta link resolves to a Google group already owned by another Access group. The ownership
    # check must run here too -- previously it was skipped whenever the config email was absent,
    # letting two groups co-manage one Google group.
    group = _group(mocker, group_config={}, status={})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {"enabled": True}.get(key, default)
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = (
        "map-x",
        "shared@test-company.com",
    )
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", return_value="ggid-shared")
    owner = Mock(spec=AppGroup)
    owner.name = "App-Other-Owner"
    ctx_mock.find_groups_by_status.return_value = [owner]
    enforce = mocker.patch.object(plugin_instance, "_adopt_or_enforce")

    await plugin_instance._reconcile(ctx_mock, group)

    enforce.assert_not_called()  # refused before adopting/clobbering the shared group
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status.get(STATUS_GOOGLE_GROUP_ID) is None  # neither the id...
    assert status.get(STATUS_PUSH_MAPPING_ID) is None  # ...nor the link's mapping was adopted
    assert status.get(STATUS_SYNC_STATUS) == SYNC_SKIPPED


async def test_claim_locks_before_checking_ownership(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # The check-then-claim is serialized by `ctx.lock`, so two concurrent reconciles can't both pass
    # the ownership check and adopt the same pre-existing Google group. The lock must be taken BEFORE
    # the ownership lookup, and keyed on the candidate id.
    #
    # What the lock *is* -- a Postgres transaction-level advisory lock, a no-op on other backends --
    # is host behaviour, covered by TestPluginHelperFunctions in tests/test_app_group_lifecycle_plugin.py.
    # What belongs here is the plugin's ordering policy.
    group = _group(mocker, status={})
    ctx_mock.find_groups_by_status.return_value = []

    await plugin_instance._claim_group_id(ctx_mock, group, "ggid-x", "x@test-company.com")

    ctx_mock.lock.assert_awaited_once_with("ggid-x")
    order = [name for name, *_ in ctx_mock.mock_calls if name in ("lock", "find_groups_by_status")]
    assert order[:2] == ["lock", "find_groups_by_status"]


async def test_claim_keys_ownership_on_the_group_id_alone(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Ownership keys on the google_group_id status ALONE -- not push_mapping_id. A group that has
    # claimed the id but not yet created its push mapping (the mapping defers until Okta imports the
    # group) still counts as the owner, so a racing group won't double-claim during that window.
    group = _group(mocker, status={})
    ctx_mock.find_groups_by_status.return_value = []

    await plugin_instance._claim_group_id(ctx_mock, group, "ggid-x", "x@test-company.com")

    ctx_mock.find_groups_by_status.assert_awaited_once_with(
        STATUS_GOOGLE_GROUP_ID, "ggid-x", exclude_group=group, limit=1
    )


async def test_reconcile_ignores_stale_push_mapping_when_group_gone(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Out-of-band Okta link exists, but its Google group was deleted (lookup -> None).
    # The stale push_mapping_id must NOT be adopted; reconcile re-creates and re-links.
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    ctx_mock.get_config.side_effect = lambda obj, key, default=None: {
        "enabled": True,
        "email": "sec",
        "display_name": "Security",
    }.get(key, default)
    # Adoption lookups miss (the linked Google group is gone); the create path then resolves the
    # freshly-pushed group. Order: lookup(email) -> discover's email -> create-path lookup.
    mocker.patch.object(plugin_instance, "_look_up_google_group_id", side_effect=[None, None, "ggid-new"])
    ctx_mock.discover_existing_push_mapping_and_target_group_external_id.return_value = (
        "stale-map",
        "sec@test-company.com",
    )
    ctx_mock.create_push_mapping_and_new_group.return_value = "fresh-map"
    ctx_mock.find_groups_by_status.return_value = []
    mocker.patch.object(
        plugin_instance,
        "_get_google_group",
        return_value={"name": "groups/ggid-new", "groupKey": {"id": "sec@test-company.com"}, "displayName": "sec"},
    )
    mocker.patch.object(plugin_instance, "_patch_google_group")

    await plugin_instance._reconcile(ctx_mock, group)

    # The stale mapping id was not adopted; a fresh group + mapping were created via push.
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status.get(STATUS_PUSH_MAPPING_ID) == "fresh-map"
    assert status[STATUS_GOOGLE_GROUP_ID] == "ggid-new"
    ctx_mock.create_push_mapping_and_new_group.assert_awaited_once()


async def test_sync_group_runs_the_same_reconcile(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # The periodic sync is the same idempotent reconcile as the event-driven hooks, one group at a
    # time. The host invokes it per group in its own transaction, so there is no batch loop here.
    group = _group(mocker)
    reconcile = mocker.patch.object(plugin_instance, "_reconcile")

    await plugin_instance.sync_group(ctx=ctx_mock, group=group, plugin_id=PLUGIN_ID)

    reconcile.assert_awaited_once_with(ctx_mock, group)


async def test_sync_group_lets_failures_propagate_to_the_host(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    # Deliberately NOT swallowed: the host isolates each group in its own transaction and counts the
    # failure, which is what makes `access sync-app-groups` exit non-zero. Catching here would hide a
    # systemic outage behind a clean exit -- the reason the plugin's own batch loop used to keep a
    # failure tally.
    group = _group(mocker)
    mocker.patch.object(plugin_instance, "_reconcile", side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await plugin_instance.sync_group(ctx=ctx_mock, group=group, plugin_id=PLUGIN_ID)


async def test_sync_group_ignores_other_plugins(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, ctx_mock: MagicMock
) -> None:
    group = _group(mocker)
    reconcile = mocker.patch.object(plugin_instance, "_reconcile")

    await plugin_instance.sync_group(ctx=ctx_mock, group=group, plugin_id="a_different_plugin")

    reconcile.assert_not_awaited()


def test_plugin_only_imports_the_access_plugin_interface() -> None:
    """The plugin must integrate with Access solely through the plugin interface.

    Pins the outcome of the Access 2.0 context refactor: no `api.operations`, no `api.services`, no
    session, no hand-built queries. `api.models` is allowed because the hookspecs are typed in terms
    of those models -- types, not behaviour. If a new capability is needed, add it to
    `AppGroupLifecycleContext` rather than importing past this boundary.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).with_name("plugin.py").read_text()
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)

    access_imports = {m for m in modules if m == "api" or m.startswith(("api.", "sqlalchemy"))}
    assert access_imports == {"api.models", "api.plugins.app_group_lifecycle"}, (
        f"plugin.py reaches past the plugin interface: {sorted(access_imports)}"
    )
