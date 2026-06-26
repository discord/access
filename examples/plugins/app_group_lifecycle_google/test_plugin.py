"""Tests for the Google Groups Lifecycle Plugin."""

import os
import sys
from pathlib import Path
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
    GROUP_DISCUSSION_FORUM_LABEL,
    PLUGIN_ID,
    GoogleGroupManagerPlugin,
)

from api.models import App, AppGroup  # noqa: E402


@pytest.fixture
def mock_groups_api(mocker: MockerFixture):
    mocker.patch("plugin.default", return_value=(Mock(), None))
    discovery_client = MagicMock()
    mocker.patch("plugin.build", return_value=discovery_client)
    groups_api = MagicMock()
    discovery_client.groups.return_value = groups_api
    return groups_api


@pytest.fixture
def plugin_instance(mocker: MockerFixture, mock_groups_api):
    mocker.patch.dict(os.environ, {
        "GOOGLE_WORKSPACE_OKTA_APP_ID": "test-okta-app-123",
        "GOOGLE_WORKSPACE_DOMAIN": "test-company.com",
        "GOOGLE_WORKSPACE_CUSTOMER_ID": "C0test",
    })
    return GoogleGroupManagerPlugin()


def test_metadata(plugin_instance):
    meta = plugin_instance.get_plugin_metadata()
    assert meta.id == PLUGIN_ID
    assert meta.display_name


def test_app_config_properties_shape(plugin_instance):
    props = plugin_instance.get_plugin_app_config_properties(PLUGIN_ID)
    assert set(props) == {"enabled", "email_pattern"}
    assert props["enabled"].required is True


def test_group_config_properties_shape(plugin_instance):
    props = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})
    assert set(props) == {"email", "display_name"}
    assert props["email"].required is True
    assert props["display_name"].required is True


def test_group_config_properties_surface_validation_patterns(plugin_instance):
    from plugin import GOOGLE_LOCAL_PART_RE

    # With no app pattern, the email property carries just the Google-safe charset rule.
    patterns = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})["email"].validation["patterns"]
    assert [p["regex"] for p in patterns] == [GOOGLE_LOCAL_PART_RE.pattern]

    # With an app email_pattern, it is appended as a second rule.
    patterns = plugin_instance.get_plugin_group_config_properties(
        PLUGIN_ID, {"email_pattern": r"^sec-"}
    )["email"].validation["patterns"]
    assert [p["regex"] for p in patterns] == [GOOGLE_LOCAL_PART_RE.pattern, r"^sec-"]


def test_group_status_properties_shape(plugin_instance):
    props = plugin_instance.get_plugin_group_status_properties(PLUGIN_ID)
    assert set(props) == {
        "push_mapping_id", "google_group_id", "sync_status", "sync_error", "last_synced_at",
    }


@pytest.mark.parametrize("pattern,ok", [(None, True), (r"^[a-z-]+$", True), (r"([", False)])
def test_validate_app_config_email_pattern(plugin_instance, pattern, ok):
    config = {"enabled": True}
    if pattern is not None:
        config["email_pattern"] = pattern
    errors = plugin_instance.validate_plugin_app_config(config, PLUGIN_ID)
    assert (errors == {}) is ok


def test_validate_app_config_requires_enabled(plugin_instance):
    errors = plugin_instance.validate_plugin_app_config({}, PLUGIN_ID)
    assert "enabled" in errors


def test_validate_group_config_valid(plugin_instance):
    errors = plugin_instance.validate_plugin_group_config(
        {"email": "platform-security", "display_name": "Platform Security"}, {}, PLUGIN_ID
    )
    assert errors == {}


@pytest.mark.parametrize("config,bad_key", [
    ({"display_name": "X"}, "email"),                       # missing email
    ({"email": "ok"}, "display_name"),                      # missing display_name
    ({"email": "Bad-Upper", "display_name": "X"}, "email"), # uppercase fails charset
    ({"email": "-bad", "display_name": "X"}, "email"),      # leading hyphen fails charset
])


def test_validate_group_config_errors(plugin_instance, config, bad_key):
    errors = plugin_instance.validate_plugin_group_config(config, {}, PLUGIN_ID)
    assert bad_key in errors


def test_validate_group_config_ignores_other_plugin(plugin_instance):
    assert plugin_instance.validate_plugin_group_config({}, {}, "some_other_plugin") is None


def test_validate_group_config_enforces_app_email_pattern(plugin_instance):
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


def _group(mocker, *, app_config=None, group_config=None, status=None, description=""):
    app = Mock(spec=App)
    app.plugin_data = {PLUGIN_ID: {"configuration": app_config or {"enabled": True}, "status": {}}}
    group = Mock(spec=AppGroup)
    group.id = "grp-1"
    group.name = "App-Google-Platform-Security"
    group.description = description
    group.app = app
    group.plugin_data = {PLUGIN_ID: {"configuration": group_config or {}, "status": status or {}}}
    return group


def test_full_email_appends_domain(plugin_instance):
    assert plugin_instance._full_email("platform-security") == "platform-security@test-company.com"


def test_prefix_from_email_strips_domain(plugin_instance):
    assert plugin_instance._prefix_from_email("platform-security@test-company.com") == "platform-security"


def test_prefix_from_email_returns_none_on_domain_mismatch(plugin_instance):
    assert plugin_instance._prefix_from_email("x@other.com") is None


def test_is_enabled_reads_app_config(plugin_instance, mocker):
    group = _group(mocker)
    mocker.patch("plugin.get_config_value", return_value=True)
    assert plugin_instance._is_enabled(group) is True


def test_validate_email_against_pattern(plugin_instance):
    assert plugin_instance._validate_email_against_pattern("platform", r"^sec-") is not None
    assert plugin_instance._validate_email_against_pattern("sec-platform", r"^sec-") is None
    assert plugin_instance._validate_email_against_pattern("anything", None) is None


def test_group_config_returns_pair_or_none(plugin_instance, mocker):
    # both present -> tuple; missing one -> None
    mocker.patch("plugin.get_config_value", side_effect=lambda obj, key, pid, default=None: {
        "email": "sec", "display_name": "Security",
    }.get(key, default))
    assert plugin_instance._group_config(_group(mocker)) == ("sec", "Security")

    mocker.patch("plugin.get_config_value", side_effect=lambda obj, key, pid, default=None: {
        "email": "sec",
    }.get(key, default))
    assert plugin_instance._group_config(_group(mocker)) is None


def test_create_google_group_calls_create(plugin_instance, mock_groups_api):
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


def test_get_google_group_calls_get_by_resource_name(plugin_instance, mock_groups_api):
    mock_groups_api.get().execute.return_value = {"name": "groups/ggid-1"}
    assert plugin_instance._get_google_group("ggid-1")["name"] == "groups/ggid-1"
    assert mock_groups_api.get.call_args.kwargs == {"name": "groups/ggid-1"}


def test_patch_google_group_sets_update_mask(plugin_instance, mock_groups_api):
    plugin_instance._patch_google_group("ggid-1", display_name="New", description="d")
    kwargs = mock_groups_api.patch.call_args.kwargs
    assert kwargs["name"] == "groups/ggid-1"
    assert kwargs["body"] == {"displayName": "New", "description": "d"}
    assert kwargs["updateMask"] == "description,displayName"


def test_patch_google_group_noop_when_no_fields(plugin_instance, mock_groups_api):
    plugin_instance._patch_google_group("ggid-1")
    mock_groups_api.patch.assert_not_called()


def test_delete_google_group_calls_delete_by_resource_name(plugin_instance, mock_groups_api):
    plugin_instance._delete_google_group("ggid-1")
    assert mock_groups_api.delete.call_args.kwargs == {"name": "groups/ggid-1"}


def test_lookup_returns_bare_id(plugin_instance, mock_groups_api):
    mock_groups_api.lookup().execute.return_value = {"name": "groups/ggid-9"}
    assert plugin_instance._lookup_google_group_id("x@test-company.com") == "ggid-9"
    assert mock_groups_api.lookup.call_args.kwargs == {"groupKey_id": "x@test-company.com"}


def test_lookup_returns_none_on_404(plugin_instance, mock_groups_api):
    from googleapiclient.errors import HttpError

    mock_groups_api.lookup().execute.side_effect = HttpError(404)
    assert plugin_instance._lookup_google_group_id("missing@test-company.com") is None


def test_lookup_returns_none_on_403(plugin_instance: GoogleGroupManagerPlugin, mock_groups_api: MagicMock) -> None:
    # Cloud Identity returns 403 (permission-denied "or it may not exist") for a group
    # that doesn't exist, not 404; the lookup must treat it as absent, not raise.
    from googleapiclient.errors import HttpError

    mock_groups_api.lookup().execute.side_effect = HttpError(403)
    assert plugin_instance._lookup_google_group_id("missing@test-company.com") is None


def test_email_config_property_is_immutable(plugin_instance: GoogleGroupManagerPlugin) -> None:
    props = plugin_instance.get_plugin_group_config_properties(PLUGIN_ID, {})
    assert props["email"].immutable is True
    assert props["display_name"].immutable is False
