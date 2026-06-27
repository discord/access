"""Tests for the Google Groups Lifecycle Plugin."""

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest
from pytest_mock import MockerFixture

# The plugin instantiates at import time and needs these env vars + Google libs.
os.environ["GOOGLE_WORKSPACE_OKTA_APP_ID"] = "test-okta-app-123"
os.environ["GOOGLE_WORKSPACE_DOMAIN"] = "test-company.com"
os.environ["GOOGLE_WORKSPACE_CUSTOMER_ID"] = "C0test"

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
    GROUP_DISCUSSION_FORUM_LABEL,
    PLUGIN_ID,
    STATUS_GOOGLE_GROUP_ID,
    STATUS_PUSH_MAPPING_ID,
    STATUS_SYNC_ERROR,
    STATUS_SYNC_STATUS,
    SYNC_ERROR,
    SYNC_PENDING,
    SYNC_SYNCED,
    GoogleGroupManagerPlugin,
)

from api.models import App, AppGroup  # noqa: E402


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
            "GOOGLE_WORKSPACE_CUSTOMER_ID": "C0test",
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


def test_is_enabled_reads_app_config(plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture) -> None:
    group = _group(mocker)
    mocker.patch("plugin.get_config_value", return_value=True)
    assert plugin_instance._is_enabled(group) is True


def test_validate_email_against_pattern(plugin_instance: GoogleGroupManagerPlugin) -> None:
    assert plugin_instance._validate_email_against_pattern("platform", r"^sec-") is not None
    assert plugin_instance._validate_email_against_pattern("sec-platform", r"^sec-") is None
    assert plugin_instance._validate_email_against_pattern("anything", None) is None


def test_group_config_returns_pair_or_none(plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture) -> None:
    # both present -> tuple; missing one -> None
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    assert plugin_instance._group_config(_group(mocker)) == ("sec", "Security")

    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "email": "sec",
        }.get(key, default),
    )
    assert plugin_instance._group_config(_group(mocker)) is None


def test_create_google_group_calls_create(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    mock_groups_api.create().execute.return_value = {
        "done": True,
        "response": {"name": "groups/ggid-1", "groupKey": {"id": "platform-security@test-company.com"}},
    }
    group_id = plugin_instance._create_google_group("platform-security", "Platform Security", "desc")
    assert group_id == "ggid-1"
    kwargs = mock_groups_api.create.call_args.kwargs
    assert kwargs["initialGroupConfig"] == "EMPTY"
    assert kwargs["body"] == {
        "parent": "customers/C0test",
        "groupKey": {"id": "platform-security@test-company.com"},
        "displayName": "Platform Security",
        "description": "desc",
        "labels": {GROUP_DISCUSSION_FORUM_LABEL: ""},
    }


def test_get_google_group_calls_get_by_resource_name(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    mock_groups_api.get().execute.return_value = {"name": "groups/ggid-1"}
    assert plugin_instance._get_google_group("ggid-1")["name"] == "groups/ggid-1"
    assert mock_groups_api.get.call_args.kwargs == {"name": "groups/ggid-1"}


def test_patch_google_group_sets_update_mask(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    plugin_instance._patch_google_group("ggid-1", display_name="New", description="d")
    kwargs = mock_groups_api.patch.call_args.kwargs
    assert kwargs["name"] == "groups/ggid-1"
    assert kwargs["body"] == {"displayName": "New", "description": "d"}
    assert kwargs["updateMask"] == "description,displayName"


def test_patch_google_group_noop_when_no_fields(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    plugin_instance._patch_google_group("ggid-1")
    mock_groups_api.patch.assert_not_called()


def test_delete_google_group_calls_delete_by_resource_name(
    plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock
) -> None:
    plugin_instance._delete_google_group("ggid-1")
    assert mock_groups_api.delete.call_args.kwargs == {"name": "groups/ggid-1"}


def test_lookup_returns_bare_id(plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock) -> None:
    mock_groups_api.lookup().execute.return_value = {"name": "groups/ggid-9"}
    assert plugin_instance._lookup_google_group_id("x@test-company.com") == "ggid-9"
    assert mock_groups_api.lookup.call_args.kwargs == {"groupKey_id": "x@test-company.com"}


def test_lookup_returns_none_on_404(plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock) -> None:
    from googleapiclient.errors import HttpError

    mock_groups_api.lookup().execute.side_effect = HttpError(404)
    assert plugin_instance._lookup_google_group_id("missing@test-company.com") is None


def test_lookup_returns_none_on_403(plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock) -> None:
    # Cloud Identity returns 403 (permission-denied "or it may not exist") for a group
    # that doesn't exist, not 404; the lookup must treat it as absent, not raise.
    from googleapiclient.errors import HttpError

    mock_groups_api.lookup().execute.side_effect = HttpError(403)
    assert plugin_instance._lookup_google_group_id("missing@test-company.com") is None


def test_email_from_status_returns_email_when_present(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker, status={STATUS_GOOGLE_GROUP_ID: "ggid-1"})
    mocker.patch.object(plugin_instance, "_get_google_group", return_value={"groupKey": {"id": "sec@test-company.com"}})
    assert plugin_instance._email_from_status(group) == "sec@test-company.com"


@pytest.mark.parametrize("status", [403, 404])
def test_email_from_status_returns_none_when_group_absent(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, status: int
) -> None:
    # The cached group was deleted out of band: recovering the email must treat it as absent
    # (like _owned_group_id) and return None, not raise -- otherwise reconcile turns a transient
    # race into a hard SYNC_ERROR instead of a clean deferral.
    from googleapiclient.errors import HttpError

    group = _group(mocker, status={STATUS_GOOGLE_GROUP_ID: "ggid-gone"})
    mocker.patch.object(plugin_instance, "_get_google_group", side_effect=HttpError(status))
    assert plugin_instance._email_from_status(group) is None


def test_email_from_status_reraises_non_absent_error(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # A non-absent error (e.g. 500) is a real failure and must surface, not be swallowed.
    from googleapiclient.errors import HttpError

    group = _group(mocker, status={STATUS_GOOGLE_GROUP_ID: "ggid-1"})
    mocker.patch.object(plugin_instance, "_get_google_group", side_effect=HttpError(500))
    with pytest.raises(HttpError):
        plugin_instance._email_from_status(group)


def test_email_config_property_is_immutable(plugin_instance: GoogleGroupManagerPlugin) -> None:
    props = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})
    assert props["email"].immutable is True
    assert props["display_name"].immutable is False


def test_create_push_mapping_sets_status(plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture) -> None:
    group = _group(mocker)
    mocker.patch("plugin.okta.list_groups", return_value=[Mock(group=Mock(id="okta-tgt-1"))])
    mocker.patch("plugin.okta.create_group_push_mapping", return_value={"id": "map-1"})
    set_status = mocker.patch("plugin.set_status_value")

    created = plugin_instance._create_push_mapping(group, "sec@test-company.com")

    assert created is True
    set_status.assert_any_call(group, STATUS_PUSH_MAPPING_ID, "map-1", PLUGIN_ID)


def test_create_push_mapping_defers_when_target_not_imported(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker)
    mocker.patch("plugin.okta.list_groups", return_value=[])  # Okta hasn't imported it yet
    create = mocker.patch("plugin.okta.create_group_push_mapping")

    created = plugin_instance._create_push_mapping(group, "sec@test-company.com")

    assert created is False
    create.assert_not_called()


def test_discover_existing_link_finds_mapping(plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture) -> None:
    group = _group(mocker)
    mocker.patch(
        "plugin.okta.list_group_push_mappings",
        return_value=[{"id": "map-9", "sourceGroupId": "grp-1", "targetGroupId": "okta-tgt-9"}],
    )
    tgt = Mock()
    tgt.group = Mock(id="okta-tgt-9")
    tgt.group.profile = Mock(googleGroupEmail="found@test-company.com")
    mocker.patch("plugin.okta.get_group", return_value=tgt)

    link = plugin_instance._discover_existing_link(group)

    assert link == {
        "email": "found@test-company.com",
        "push_mapping_id": "map-9",
    }


def test_discover_existing_link_returns_none_when_no_mapping(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker)
    mocker.patch("plugin.okta.list_group_push_mappings", return_value=[])
    assert plugin_instance._discover_existing_link(group) is None


@pytest.fixture
def session_mock() -> MagicMock:
    return MagicMock()


def test_reconcile_creates_when_no_link_and_config_present(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(
        mocker, group_config={"email": "platform-security", "display_name": "Platform Security"}, description="Sec team"
    )
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "platform-security",
            "display_name": "Platform Security",
            "email_pattern": None,
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    create = mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-1")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    create.assert_called_once_with("platform-security", "Platform Security", "Sec team")
    set_status.assert_any_call(group, STATUS_GOOGLE_GROUP_ID, "ggid-1", PLUGIN_ID)
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SYNCED, PLUGIN_ID)


def test_reconcile_enforces_config_onto_existing_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(
        mocker,
        group_config={"email": "new-prefix", "display_name": "New Name"},
        status={"google_group_id": "ggid-1", "push_mapping_id": "map-1"},
        description="New desc",
    )
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "new-prefix",
            "display_name": "New Name",
            "email_pattern": None,
        }.get(key, default),
    )
    mocker.patch(
        "plugin.get_status_value",
        side_effect=lambda obj, key, pid, default=None: {
            "google_group_id": "ggid-1",
            "push_mapping_id": "map-1",
        }.get(key, default),
    )
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
    mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    patch.assert_called_once()
    # The email (groupKey) is immutable and never patched; only displayName/description.
    assert patch.call_args.kwargs == {"display_name": "New Name", "description": "New desc"}


def test_reconcile_adopts_missing_config_from_live_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={}, description="")  # no config, no description
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)
    mocker.patch.object(
        plugin_instance,
        "_discover_existing_link",
        return_value={
            "google_group_id": "ggid-1",
            "push_mapping_id": "map-1",
            "email": "adopted@test-company.com",
        },
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-1")
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
    mocker.patch("plugin.set_status_value")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    mocker.patch.object(plugin_instance, "_google_group_owner", return_value=None)  # not owned elsewhere
    seed = mocker.patch("plugin.set_config_value")
    update_group = mocker.patch("plugin.okta.update_group")
    patch = mocker.patch.object(plugin_instance, "_patch_google_group")

    plugin_instance._reconcile(session_mock, group)

    seed.assert_any_call(group, CONFIG_EMAIL, "adopted", PLUGIN_ID)
    seed.assert_any_call(group, CONFIG_DISPLAY_NAME, "Adopted Name", PLUGIN_ID)
    # Empty Access description backfilled from Google + pushed to Okta; Google not mutated.
    assert group.description == "Adopted desc"
    update_group.assert_called_once()
    patch.assert_not_called()


def test_reconcile_flags_error_on_domain_mismatch_adoption(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={})
    mocker.patch(
        "plugin.get_config_value", side_effect=lambda obj, key, pid, default=None: {"enabled": True}.get(key, default)
    )
    mocker.patch("plugin.get_status_value", return_value=None)
    mocker.patch.object(
        plugin_instance,
        "_discover_existing_link",
        return_value={
            "google_group_id": "ggid-1",
            "push_mapping_id": "map-1",
            "email": "x@other-domain.com",
        },
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-1")
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
    mocker.patch.object(plugin_instance, "_google_group_owner", return_value=None)  # not owned elsewhere
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, PLUGIN_ID)


def test_reconcile_grandfathers_unchanged_legacy_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
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
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "legacy",
            "display_name": "Legacy",
            "email_pattern": r"^sec-",
        }.get(key, default),
    )
    mocker.patch(
        "plugin.get_status_value",
        side_effect=lambda obj, key, pid, default=None: {
            "google_group_id": "ggid-1",
            "push_mapping_id": "map-1",
        }.get(key, default),
    )
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
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    # Marked synced, not error, despite the prefix not matching ^sec-.
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_SYNCED, PLUGIN_ID)


def test_reconcile_skips_when_disabled(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker)
    mocker.patch("plugin.get_config_value", return_value=False)  # enabled = False
    discover = mocker.patch.object(plugin_instance, "_discover_existing_link")
    plugin_instance._reconcile(session_mock, group)
    discover.assert_not_called()


def test_reconcile_marks_pending_when_push_mapping_defers(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={"email": "sec", "display_name": "Sec"}, description="d")
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Sec",
            "email_pattern": None,
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-1")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=False)  # Okta import not ready -> defer
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_PENDING, PLUGIN_ID)
    synced = [c for c in set_status.call_args_list if c.args[1:] == (STATUS_SYNC_STATUS, SYNC_SYNCED, PLUGIN_ID)]
    assert synced == []  # never reached SYNC_SYNCED


def test_reconcile_create_path_rejects_pattern_violation(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker, group_config={"email": "platform", "display_name": "P"}, description="d")
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "platform",
            "display_name": "P",
            "email_pattern": r"^sec-",
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    create = mocker.patch.object(plugin_instance, "_create_google_group")
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    create.assert_not_called()
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, PLUGIN_ID)


def test_reconcile_creates_when_no_group_exists(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    create = mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-new")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    session = MagicMock()

    plugin_instance._reconcile(session, group)

    create.assert_called_once()
    assert group.plugin_data[PLUGIN_ID]["status"][STATUS_GOOGLE_GROUP_ID] == "ggid-new"


def test_reconcile_creates_when_lookup_403s_for_absent_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, mock_groups_api: MagicMock, session_mock: Any
) -> None:
    # Repro: Cloud Identity's groups:lookup returns 403 ("permission denied ... or it may
    # not exist") for a group that does not exist yet, not 404. Reconcile must treat that
    # as absent and create the group (deferring the link to pending), never marking error.
    from googleapiclient.errors import HttpError

    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    mock_groups_api.lookup().execute.side_effect = HttpError(403)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    create = mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-new")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=False)  # Okta hasn't imported yet
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    create.assert_called_once()
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_PENDING, PLUGIN_ID)
    assert not [c for c in set_status.call_args_list if c.args == (group, STATUS_SYNC_STATUS, SYNC_ERROR, PLUGIN_ID)]


def test_reconcile_adopts_existing_group_by_email_lookup(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-existing")
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
    create = mocker.patch.object(plugin_instance, "_create_google_group")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    mocker.patch.object(plugin_instance, "_google_group_owner", return_value=None)
    session = MagicMock()

    plugin_instance._reconcile(session, group)

    create.assert_not_called()
    assert group.plugin_data[PLUGIN_ID]["status"][STATUS_GOOGLE_GROUP_ID] == "ggid-existing"


def test_reconcile_refuses_google_group_owned_by_another_access_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # A group whose email resolves to a Google group already managed by another Access group --
    # in a *different* app sharing this plugin -- must be flagged error, not adopted/clobbered.
    group = _group(mocker, group_config={"email": "shared", "display_name": "Shared"})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "shared",
            "display_name": "Shared",
        }.get(key, default),
    )
    # No push mapping yet -> resolve adopts the existing group by email and the owner check runs.
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-shared")
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
    session = MagicMock()
    session.scalars.return_value.first.return_value = owner

    enforce = mocker.patch.object(plugin_instance, "_adopt_or_enforce")
    get_live = mocker.patch.object(plugin_instance, "_get_google_group")
    create_mapping = mocker.patch.object(plugin_instance, "_create_push_mapping")
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session, group)

    # Bailed before fetching/enforcing the live group or creating a second mapping.
    get_live.assert_not_called()
    enforce.assert_not_called()
    create_mapping.assert_not_called()
    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, PLUGIN_ID)
    # The owning group's name is plumbed into the error.
    error_msg = next(c.args[2] for c in set_status.call_args_list if c.args[1] == STATUS_SYNC_ERROR)
    assert "App-Other-Owner" in error_msg


def test_reconcile_does_not_persist_id_when_owned_by_another_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # Refusing adoption must not leave the other group's id in this group's status. If it did,
    # group_deleted (which keys off that id) would later delete a Google group we never owned.
    # Uses the real status helpers so we can observe what is (not) persisted.
    group = _group(mocker, group_config={"email": "shared", "display_name": "Shared"}, status={})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "shared",
            "display_name": "Shared",
        }.get(key, default),
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-shared")
    owner = Mock(spec=AppGroup)
    owner.name = "App-Other-Owner"
    session = MagicMock()
    session.scalars.return_value.first.return_value = owner

    plugin_instance._reconcile(session, group)

    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status.get(STATUS_GOOGLE_GROUP_ID) is None  # id of the group we don't own was NOT recorded
    assert status.get(STATUS_SYNC_STATUS) == SYNC_ERROR


def test_reconcile_runs_owner_check_in_config_absent_adoption(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # Adoption path: no Access-side config (so the resolved email is None), but an out-of-band
    # Okta link resolves to a Google group already owned by another Access group. The ownership
    # check must run here too -- previously it was skipped whenever the config email was absent,
    # letting two groups co-manage one Google group.
    group = _group(mocker, group_config={}, status={})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {"enabled": True}.get(key, default),
    )
    mocker.patch.object(
        plugin_instance,
        "_discover_existing_link",
        return_value={"email": "shared@test-company.com", "push_mapping_id": "map-x"},
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-shared")
    owner = Mock(spec=AppGroup)
    owner.name = "App-Other-Owner"
    session = MagicMock()
    session.scalars.return_value.first.return_value = owner
    enforce = mocker.patch.object(plugin_instance, "_adopt_or_enforce")

    plugin_instance._reconcile(session, group)

    enforce.assert_not_called()  # refused before adopting/clobbering the shared group
    status = group.plugin_data[PLUGIN_ID]["status"]
    assert status.get(STATUS_GOOGLE_GROUP_ID) is None  # neither the id...
    assert status.get(STATUS_PUSH_MAPPING_ID) is None  # ...nor the link's mapping was adopted
    assert status.get(STATUS_SYNC_STATUS) == SYNC_ERROR


def test_google_group_owner_matches_on_google_group_id_alone(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # Ownership keys on the google_group_id status path ALONE -- not push_mapping_id. A group
    # that has claimed the id but not yet created its push mapping (the mapping defers until
    # Okta imports the group) still counts as the owner, so a racing group won't double-claim
    # during that window. The predicate is enforced in SQL, across this plugin's apps; the
    # helper returns its single match, or None when there is none.
    from sqlalchemy.dialects import postgresql

    group = _group(mocker)
    session = MagicMock()

    session.scalars.return_value.first.return_value = None
    assert plugin_instance._google_group_owner(session, group, "ggid-x") is None

    stmt = session.scalars.call_args.args[0]
    # The JSON path elements are bound parameters, so render against the Postgres dialect (which
    # can compile JSONPathType) and inspect the SQL plus its params for the status path used.
    compiled = stmt.compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    rendered = str(compiled) + str(compiled.params)
    assert STATUS_GOOGLE_GROUP_ID in rendered  # filters on the ownership id
    assert STATUS_PUSH_MAPPING_ID not in rendered  # but NOT on whether a mapping exists yet

    owner = Mock(spec=AppGroup)
    session.scalars.return_value.first.return_value = owner
    assert plugin_instance._google_group_owner(session, group, "ggid-x") is owner


def test_claim_takes_advisory_lock_before_owner_check_on_postgres(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # The check-then-claim is serialized by a Postgres transaction-level advisory lock keyed on
    # the candidate id, so two concurrent reconciles can't both pass the ownership check and adopt
    # the same pre-existing Google group. The lock must be taken BEFORE the ownership query.
    group = _group(mocker, status={})
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "postgresql"
    session.scalars.return_value.first.return_value = None  # not owned elsewhere
    mocker.patch("plugin.set_status_value")

    plugin_instance._claim_group_id(session, group, "ggid-x", "x@test-company.com")

    lock_call = session.execute.call_args_list[0]
    assert "pg_advisory_xact_lock" in str(lock_call.args[0])
    assert lock_call.args[1] == {"key": "ggid-x"}  # keyed on the candidate id
    order = [c[0] for c in session.mock_calls if c[0] in ("execute", "scalars")]
    assert order[0] == "execute"  # lock precedes the ownership lookup


def test_claim_skips_advisory_lock_off_postgres(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # Advisory locks are Postgres-only; on other backends (e.g. the SQLite test DB) the claim
    # must not emit the lock statement, which would error.
    group = _group(mocker, status={})
    session = MagicMock()
    session.get_bind.return_value.dialect.name = "sqlite"
    session.scalars.return_value.first.return_value = None
    mocker.patch("plugin.set_status_value")

    plugin_instance._claim_group_id(session, group, "ggid-x", None)

    session.execute.assert_not_called()


def test_reconcile_relooks_up_when_cached_id_404s(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    from googleapiclient.errors import HttpError

    group = _group(
        mocker,
        group_config={"email": "sec", "display_name": "Security"},
        status={STATUS_GOOGLE_GROUP_ID: "stale-id"},
    )
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    mocker.patch.object(plugin_instance, "_get_google_group", side_effect=HttpError(404))
    relookup = mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    create = mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-fresh")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    session = MagicMock()

    plugin_instance._reconcile(session, group)

    relookup.assert_called_once_with("sec@test-company.com")
    create.assert_called_once()
    assert group.plugin_data[PLUGIN_ID]["status"][STATUS_GOOGLE_GROUP_ID] == "ggid-fresh"


def test_enforce_patches_display_name_not_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(
        mocker,
        group_config={"email": "sec", "display_name": "New Name"},
        status={STATUS_GOOGLE_GROUP_ID: "ggid-1"},
        description="d",
    )
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "New Name",
        }.get(key, default),
    )
    live = {
        "name": "groups/ggid-1",
        "groupKey": {"id": "sec@test-company.com"},
        "displayName": "Old Name",
        "description": "d",
    }
    mocker.patch.object(plugin_instance, "_get_google_group", return_value=live)
    patch = mocker.patch.object(plugin_instance, "_patch_google_group")
    mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    mocker.patch.object(plugin_instance, "_google_group_owner", return_value=None)
    session = MagicMock()

    plugin_instance._reconcile(session, group)

    patch.assert_called_once()
    # Only displayName changes; description unchanged (None) and groupKey is immutable.
    assert patch.call_args.kwargs == {"display_name": "New Name", "description": None}


def test_group_created_calls_reconcile(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker)
    reconcile = mocker.patch.object(plugin_instance, "_reconcile")
    plugin_instance.group_created(session=session_mock, group=group, plugin_id=PLUGIN_ID)
    reconcile.assert_called_once_with(session_mock, group)


def test_group_updated_calls_reconcile(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker)
    reconcile = mocker.patch.object(plugin_instance, "_reconcile")
    plugin_instance.group_updated(
        session=session_mock, group=group, old_name="old", old_description="d", plugin_id=PLUGIN_ID
    )
    reconcile.assert_called_once_with(session_mock, group)


def test_hooks_ignore_other_plugin(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker)
    reconcile = mocker.patch.object(plugin_instance, "_reconcile")
    plugin_instance.group_created(session=session_mock, group=group, plugin_id="some_other_plugin")
    reconcile.assert_not_called()


def test_group_deleted_unlinks_then_deletes(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker, status={"push_mapping_id": "map-1", "google_group_id": "ggid-1"})
    mocker.patch("plugin.get_config_value", return_value=True)  # enabled
    mocker.patch(
        "plugin.get_status_value",
        side_effect=lambda obj, key, pid, default=None: {
            "push_mapping_id": "map-1",
            "google_group_id": "ggid-1",
        }.get(key, default),
    )
    delete_mapping = mocker.patch("plugin.okta.delete_group_push_mapping")
    delete_group = mocker.patch.object(plugin_instance, "_delete_google_group")
    mgr = mocker.MagicMock()
    mgr.attach_mock(delete_mapping, "delete_mapping")
    mgr.attach_mock(delete_group, "delete_group")

    plugin_instance.group_deleted(session=session_mock, group=group, plugin_id=PLUGIN_ID)

    delete_mapping.assert_called_once_with(
        appId=plugin_instance._okta_app_id, mappingId="map-1", deleteTargetGroup=False
    )
    delete_group.assert_called_once_with("ggid-1")
    # unlink must precede the Google group delete
    assert [c[0] for c in mgr.mock_calls] == ["delete_mapping", "delete_group"]


def test_group_deleted_skips_when_unmanaged(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    group = _group(mocker, status={})
    # enabled=True, but no email/display_name config -> genuinely unmanaged.
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)  # no google_group_id
    delete_group = mocker.patch.object(plugin_instance, "_delete_google_group")
    plugin_instance.group_deleted(session=session_mock, group=group, plugin_id=PLUGIN_ID)
    delete_group.assert_not_called()


def test_group_deleted_deletes_google_group_even_if_unlink_fails(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    # The Google group is the authoritative resource; a failure to unlink the Okta push mapping
    # must not prevent deleting it when the Access group is deleted.
    group = _group(mocker, status={"push_mapping_id": "map-1", "google_group_id": "ggid-1"})
    mocker.patch("plugin.get_config_value", return_value=True)  # enabled
    mocker.patch(
        "plugin.get_status_value",
        side_effect=lambda obj, key, pid, default=None: {
            "push_mapping_id": "map-1",
            "google_group_id": "ggid-1",
        }.get(key, default),
    )
    mocker.patch("plugin.okta.delete_group_push_mapping", side_effect=Exception("okta boom"))
    delete_group = mocker.patch.object(plugin_instance, "_delete_google_group")

    plugin_instance.group_deleted(session=session_mock, group=group, plugin_id=PLUGIN_ID)

    delete_group.assert_called_once_with("ggid-1")


def test_group_deleted_does_not_fall_back_to_email_lookup(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    # With no recorded google_group_id, group_deleted must NOT resolve a Google group by the
    # (shared) email and delete it: that email could resolve to a group owned by a different
    # Access group -- e.g. one that collided on prefix and was refused adoption. Deletion is
    # gated on the ownership-recording status id, which a refused group never carries.
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"}, status={})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)  # no google_group_id, no push_mapping_id
    lookup = mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value="ggid-del")
    delete_group = mocker.patch.object(plugin_instance, "_delete_google_group")

    plugin_instance.group_deleted(session=session_mock, group=group, plugin_id=PLUGIN_ID)

    lookup.assert_not_called()  # never resolves by the shared email
    delete_group.assert_not_called()  # nothing we provably own -> nothing to delete


def test_sync_all_reconciles_each_group(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    app = Mock(spec=App)
    g1, g2 = Mock(spec=AppGroup), Mock(spec=AppGroup)
    session_mock.scalars.return_value.all.return_value = [g1, g2]
    reconcile = mocker.patch.object(plugin_instance, "_reconcile")
    plugin_instance.sync_all_groups(session=session_mock, app=app, plugin_id=PLUGIN_ID)
    assert reconcile.call_count == 2


def test_okta_target_group_id_searches_by_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    list_groups = mocker.patch("plugin.okta.list_groups", return_value=[Mock(group=Mock(id="okta-tgt-7"))])
    assert plugin_instance._okta_target_group_id("sec@test-company.com") == "okta-tgt-7"
    search = list_groups.call_args.kwargs["query_params"]["search"]
    assert "googleGroupEmail" in search
    assert "sec@test-company.com" in search


def test_okta_target_group_id_none_when_not_imported(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # Zero matches means Okta hasn't imported the group yet -> defer (None), not an error.
    mocker.patch("plugin.okta.list_groups", return_value=[])
    assert plugin_instance._okta_target_group_id("sec@test-company.com") is None


def test_okta_target_group_id_raises_on_ambiguous_match(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # More than one Okta target group carrying the same googleGroupEmail is a misconfiguration
    # that will never self-heal; it must surface as an error, not be conflated with "not imported".
    from plugin import AmbiguousOktaTargetError

    mocker.patch(
        "plugin.okta.list_groups",
        return_value=[Mock(group=Mock(id="okta-tgt-1")), Mock(group=Mock(id="okta-tgt-2"))],
    )
    with pytest.raises(AmbiguousOktaTargetError):
        plugin_instance._okta_target_group_id("sec@test-company.com")


def test_reconcile_errors_on_ambiguous_okta_target(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    # A duplicate Okta target must mark the group SYNC_ERROR, not park it as SYNC_PENDING forever.
    group = _group(mocker, group_config={"email": "sec", "display_name": "Sec"}, description="d")
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Sec",
            "email_pattern": None,
        }.get(key, default),
    )
    mocker.patch("plugin.get_status_value", return_value=None)
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(plugin_instance, "_discover_existing_link", return_value=None)
    mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-1")
    # Two Okta target groups match the same email -> _create_push_mapping hits ambiguity.
    mocker.patch(
        "plugin.okta.list_groups",
        return_value=[Mock(group=Mock(id="okta-tgt-1")), Mock(group=Mock(id="okta-tgt-2"))],
    )
    set_status = mocker.patch("plugin.set_status_value")

    plugin_instance._reconcile(session_mock, group)

    set_status.assert_any_call(group, STATUS_SYNC_STATUS, SYNC_ERROR, PLUGIN_ID)
    assert not [c for c in set_status.call_args_list if c.args[1:] == (STATUS_SYNC_STATUS, SYNC_PENDING, PLUGIN_ID)]
    assert not [c for c in set_status.call_args_list if c.args[1:] == (STATUS_SYNC_STATUS, SYNC_SYNCED, PLUGIN_ID)]


def test_create_push_mapping_resolves_target_by_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker)
    mocker.patch("plugin.okta.list_groups", return_value=[Mock(group=Mock(id="okta-tgt-1"))])
    mocker.patch("plugin.okta.create_group_push_mapping", return_value={"id": "map-1"})
    set_status = mocker.patch("plugin.set_status_value")

    assert plugin_instance._create_push_mapping(group, "sec@test-company.com") is True
    set_status.assert_any_call(group, STATUS_PUSH_MAPPING_ID, "map-1", PLUGIN_ID)


def test_discover_existing_link_recovers_email(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    group = _group(mocker)
    profile = Mock(googleGroupEmail="sec@test-company.com")
    mocker.patch(
        "plugin.okta.list_group_push_mappings",
        return_value=[
            {"id": "map-1", "sourceGroupId": "grp-1", "targetGroupId": "okta-tgt-1"},
        ],
    )
    mocker.patch("plugin.okta.get_group", return_value=Mock(group=Mock(profile=profile)))

    link = plugin_instance._discover_existing_link(group)
    assert link == {"email": "sec@test-company.com", "push_mapping_id": "map-1"}


def test_reconcile_ignores_stale_push_mapping_when_group_gone(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture
) -> None:
    # Out-of-band Okta link exists, but its Google group was deleted (lookup -> None).
    # The stale push_mapping_id must NOT be adopted; reconcile re-creates and re-links.
    group = _group(mocker, group_config={"email": "sec", "display_name": "Security"})
    mocker.patch(
        "plugin.get_config_value",
        side_effect=lambda obj, key, pid, default=None: {
            "enabled": True,
            "email": "sec",
            "display_name": "Security",
        }.get(key, default),
    )
    mocker.patch.object(plugin_instance, "_lookup_google_group_id", return_value=None)
    mocker.patch.object(
        plugin_instance,
        "_discover_existing_link",
        return_value={
            "email": "sec@test-company.com",
            "push_mapping_id": "stale-map",
        },
    )
    create = mocker.patch.object(plugin_instance, "_create_google_group", return_value="ggid-new")
    create_mapping = mocker.patch.object(plugin_instance, "_create_push_mapping", return_value=True)
    session = MagicMock()

    plugin_instance._reconcile(session, group)

    # The stale mapping id was not adopted, so a fresh mapping is created.
    assert group.plugin_data[PLUGIN_ID]["status"].get(STATUS_PUSH_MAPPING_ID) != "stale-map"
    create.assert_called_once()
    create_mapping.assert_called_once()


def test_sync_all_continues_after_group_failure(
    plugin_instance: GoogleGroupManagerPlugin, mocker: MockerFixture, session_mock: MagicMock
) -> None:
    app = Mock(spec=App)
    g1, g2 = Mock(spec=AppGroup), Mock(spec=AppGroup)
    g1.name = "Group1"
    session_mock.scalars.return_value.all.return_value = [g1, g2]
    reconcile = mocker.patch.object(plugin_instance, "_reconcile", side_effect=[RuntimeError("boom"), None])
    plugin_instance.sync_all_groups(session=session_mock, app=app, plugin_id=PLUGIN_ID)
    assert reconcile.call_count == 2  # g2 still reconciled despite g1 raising
