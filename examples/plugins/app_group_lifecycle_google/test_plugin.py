"""
Tests for the Google Groups Lifecycle Plugin.

This includes tests for:
- Helper methods with no external dependencies
- Google API integration with mocked services
- Okta service integration with mocked services
- Configuration validation
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from pytest_mock import MockerFixture

# Set required environment variables before importing the plugin
# The plugin module creates an instance at import time, which requires these
os.environ['GOOGLE_WORKSPACE_OKTA_APP_ID'] = 'test-okta-app-123'
os.environ['GOOGLE_WORKSPACE_DOMAIN'] = 'test-company.com'

# Mock Google API modules before importing the plugin
# This allows tests to run without installing google-api-python-client
mock_google_auth = MagicMock()
mock_google_auth.default = MagicMock(return_value=(MagicMock(), None))

mock_googleapiclient = MagicMock()
mock_googleapiclient_discovery = MagicMock()
mock_googleapiclient_discovery.build = MagicMock(return_value=MagicMock())

# Mock google.cloud modules that api.extensions imports
mock_google_cloud = MagicMock()
mock_google_cloud_sql = MagicMock()
mock_google_cloud_sql_connector = MagicMock()

sys.modules['google'] = MagicMock()
sys.modules['google.auth'] = mock_google_auth
sys.modules['google.cloud'] = mock_google_cloud
sys.modules['google.cloud.sql'] = mock_google_cloud_sql
sys.modules['google.cloud.sql.connector'] = mock_google_cloud_sql_connector
sys.modules['googleapiclient'] = mock_googleapiclient
sys.modules['googleapiclient.discovery'] = mock_googleapiclient_discovery
sys.modules['googleapiclient.errors'] = MagicMock()

# Add the plugin directory to the path so we can import the plugin module
plugin_dir = Path(__file__).parent
if str(plugin_dir) not in sys.path:
    sys.path.insert(0, str(plugin_dir))

from plugin import PLUGIN_ID, GoogleGroupManagerPlugin


@pytest.fixture
def mock_env(monkeypatch):
    """Set required environment variables."""
    monkeypatch.setenv('GOOGLE_WORKSPACE_OKTA_APP_ID', 'test-okta-app-123')
    monkeypatch.setenv('GOOGLE_WORKSPACE_DOMAIN', 'test-company.com')


@pytest.fixture
def mock_google_client(mocker: MockerFixture):
    """Mock Google Admin SDK API client."""
    mock_credentials = Mock()
    mock_default = mocker.patch('plugin.default', return_value=(mock_credentials, None))

    mock_client = MagicMock()
    mock_build = mocker.patch('plugin.build', return_value=mock_client)

    return mock_client


@pytest.fixture
def plugin_instance(mock_env, mock_google_client):
    """Create a plugin instance with mocked dependencies."""
    return GoogleGroupManagerPlugin()


@pytest.fixture
def mock_app():
    """Create a mock app with plugin configuration."""
    app = Mock()
    app.id = 'test-app-id'
    app.name = 'TestApp'
    app.plugin_data = {
        PLUGIN_ID: {
            'config': {
                'enabled': True,
                'email_prefix': 'test-prefix'
            }
        }
    }
    return app


@pytest.fixture
def mock_app_group(mock_app):
    """Create a mock AppGroup."""
    group = Mock()
    group.id = 'test-group-id'
    group.name = 'App-TestApp-Engineering'
    group.description = 'Engineering team access'
    group.app = mock_app
    group.plugin_data = {PLUGIN_ID: {'status': {}}}
    return group


class TestHelperMethods:
    """Test helper methods with no external dependencies."""

    @pytest.mark.parametrize("enabled_value,expected", [
        (True, True),
        (False, False),
        (None, False),  # Missing config - defaults to False
    ])
    def test_is_enabled(self, plugin_instance, mock_app_group, enabled_value, expected, mocker: MockerFixture):
        """Test _is_enabled returns correct value based on configuration."""
        if enabled_value is None:
            mock_app_group.app.plugin_data = {}
        else:
            mock_app_group.app.plugin_data = {
                PLUGIN_ID: {'config': {'enabled': enabled_value}}
            }

        # Mock get_config_value to return the enabled value or False by default
        mocker.patch(
            'plugin.get_config_value',
            return_value=enabled_value if enabled_value is not None else False
        )

        result = plugin_instance._is_enabled(mock_app_group)
        assert result == expected

    @pytest.mark.parametrize("group_name,app_name,expected", [
        ("App-TestApp-Engineering", "TestApp", "Engineering"),
        ("App-GCP-Platform-Team", "GCP", "Platform-Team"),
        ("App-AWS-Security", "AWS", "Security"),
        ("App-Platform-Data-Science-Team", "Platform", "Data-Science-Team"),
    ])
    def test_get_group_name_without_app_prefix(self, plugin_instance, mock_app_group, group_name, app_name, expected):
        """Test stripping app prefix from group name."""
        mock_app_group.name = group_name
        mock_app_group.app.name = app_name

        result = plugin_instance._get_group_name_without_app_prefix(mock_app_group)
        assert result == expected

    @pytest.mark.parametrize("group_name,app_name,email_prefix,expected_email", [
        ("App-GCP-Security", "GCP", "iam", "iam-security@test-company.com"),
        ("App-AWS-Admin", "AWS", "", "admin@test-company.com"),
        ("App-Platform-Data-Team", "Platform", "dev", "dev-data-team@test-company.com"),
        ("App-Test-UPPERCASE", "Test", "pre", "pre-uppercase@test-company.com"),
    ])
    def test_generate_group_email(
        self, plugin_instance, mock_app_group, group_name, app_name, email_prefix, expected_email, mocker: MockerFixture
    ):
        """Test Google Group email generation with various prefixes."""
        mock_app_group.name = group_name
        mock_app_group.app.name = app_name
        mock_app_group.app.plugin_data = {
            PLUGIN_ID: {'config': {'enabled': True, 'email_prefix': email_prefix}}
        }

        # Mock get_config_value to return the email_prefix
        mocker.patch('plugin.get_config_value', return_value=email_prefix)

        result = plugin_instance._generate_group_email(mock_app_group)
        assert result == expected_email

    @pytest.mark.parametrize("group_name,app_name,expected_display_name", [
        ("App-GCP-Security", "GCP", "GCP - Security"),
        ("App-google-cloud-Platform", "google-cloud", "google cloud - Platform"),
        ("App-AWS-data-engineering", "AWS", "AWS - data engineering"),
        ("App-my-app-my-team", "my-app", "my app - my team"),
    ])
    def test_generate_display_name(self, plugin_instance, mock_app_group, group_name, app_name, expected_display_name):
        """Test Google Group display name generation with hyphen-to-space conversion."""
        mock_app_group.name = group_name
        mock_app_group.app.name = app_name

        result = plugin_instance._generate_display_name(mock_app_group)
        assert result == expected_display_name


class TestGoogleAPIIntegration:
    """Test Google API integration with mocking."""

    def test_create_google_group_success(self, plugin_instance, mock_app_group, mock_google_client, mocker: MockerFixture):
        """Test successful Google Group creation."""
        # Mock API response
        mock_google_client.groups().insert().execute.return_value = {
            'id': 'google-group-123',
            'email': 'test-prefix-engineering@test-company.com',
            'name': 'TestApp - Engineering',
            'description': 'Managed by Access...'
        }

        # Mock status setter
        mock_set_status = mocker.patch('plugin.set_status_value')

        # Mock helper methods
        mocker.patch.object(plugin_instance, '_generate_group_email', return_value='test-prefix-engineering@test-company.com')
        mocker.patch.object(plugin_instance, '_generate_display_name', return_value='TestApp - Engineering')

        # Execute
        result = plugin_instance._create_google_group(mock_app_group)

        # Verify
        assert result == 'TestApp - Engineering'
        assert mock_google_client.groups().insert.called
        assert mock_set_status.call_count == 2  # Called for 'name' and 'email'

    def test_create_google_group_missing_name(self, plugin_instance, mock_app_group, mock_google_client, mocker: MockerFixture):
        """Test error when Google API response missing 'name' field."""
        mock_google_client.groups().insert().execute.return_value = {
            'email': 'test@test-company.com',
            # 'name' is missing
        }

        # Mock helper methods
        mocker.patch.object(plugin_instance, '_generate_group_email', return_value='test@test-company.com')
        mocker.patch.object(plugin_instance, '_generate_display_name', return_value='Test Group')

        with pytest.raises(ValueError, match="Expected to get a Google group name"):
            plugin_instance._create_google_group(mock_app_group)

    def test_create_google_group_missing_email(self, plugin_instance, mock_app_group, mock_google_client, mocker: MockerFixture):
        """Test error when Google API response missing 'email' field."""
        mock_google_client.groups().insert().execute.return_value = {
            'name': 'Test Group',
            # 'email' is missing
        }

        # Mock helper methods
        mocker.patch.object(plugin_instance, '_generate_group_email', return_value='test@test-company.com')
        mocker.patch.object(plugin_instance, '_generate_display_name', return_value='Test Group')

        with pytest.raises(ValueError, match="Expected to get a Google group email"):
            plugin_instance._create_google_group(mock_app_group)

    def test_create_google_group_api_error(self, plugin_instance, mock_app_group, mock_google_client, mocker: MockerFixture):
        """Test handling of Google API errors."""
        # Create a real exception to raise
        error = Exception("Google API error: Permission denied")

        # Mock API to raise error - need to properly configure the mock chain
        mock_execute = Mock(side_effect=error)
        mock_insert = Mock(return_value=Mock(execute=mock_execute))
        mock_groups = Mock(return_value=Mock(insert=mock_insert))
        mock_google_client.groups = mock_groups

        # Mock helper methods
        mocker.patch.object(plugin_instance, '_generate_group_email', return_value='test@test-company.com')
        mocker.patch.object(plugin_instance, '_generate_display_name', return_value='Test Group')

        with pytest.raises(Exception) as exc_info:
            plugin_instance._create_google_group(mock_app_group)

        # Verify note was added
        assert len(exc_info.value.__notes__) > 0
        assert "Failed to create Google group" in exc_info.value.__notes__[0]


class TestOktaIntegration:
    """Test Okta service integration with mocking."""

    def test_create_okta_push_mapping_success(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test successful Okta group push mapping creation."""
        # Mock okta service
        mock_okta_create = mocker.patch('plugin.okta.create_group_push_mapping')
        mock_okta_create.return_value = {
            'id': 'okta-mapping-123',
            'sourceGroupId': 'test-group-id',
            'targetGroupName': 'TestApp - Engineering'
        }

        # Mock status setter
        mock_set_status = mocker.patch('plugin.set_status_value')

        # Execute
        plugin_instance._create_okta_group_push_mapping(mock_app_group, 'TestApp - Engineering')

        # Verify
        assert mock_okta_create.called
        mock_set_status.assert_called_once_with(
            mock_app_group,
            'push_mapping_id',
            'okta-mapping-123',
            PLUGIN_ID
        )

    def test_create_okta_push_mapping_missing_id(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test error when Okta response missing mapping ID."""
        mock_okta_create = mocker.patch('plugin.okta.create_group_push_mapping')
        mock_okta_create.return_value = {
            'sourceGroupId': 'test-group-id',
            # 'id' is missing
        }

        with pytest.raises(Exception, match="Expected to get a mapping ID"):
            plugin_instance._create_okta_group_push_mapping(mock_app_group, 'Test Group')

    def test_create_okta_push_mapping_api_error(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test handling of Okta API errors."""
        mock_okta_create = mocker.patch('plugin.okta.create_group_push_mapping')
        mock_okta_create.side_effect = Exception("Okta API error")

        with pytest.raises(Exception) as exc_info:
            plugin_instance._create_okta_group_push_mapping(mock_app_group, 'Test Group')

        # Verify note was added
        assert len(exc_info.value.__notes__) > 0
        assert "Failed to create Okta group push mapping" in exc_info.value.__notes__[0]

    def test_delete_okta_push_mapping_success(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test successful deletion of Okta push mapping."""
        # Set up mapping ID in status
        mock_app_group.plugin_data[PLUGIN_ID]['status'] = {'push_mapping_id': 'okta-mapping-123'}

        # Mock get_status_value and okta service
        mocker.patch('plugin.get_status_value', return_value='okta-mapping-123')
        mock_okta_delete = mocker.patch('plugin.okta.delete_group_push_mapping')

        # Execute
        plugin_instance._delete_okta_group_push_mapping_and_google_group(mock_app_group)

        # Verify
        mock_okta_delete.assert_called_once_with(
            appId=plugin_instance._google_okta_app_id,
            mappingId='okta-mapping-123',
            deleteTargetGroup=True
        )

    def test_delete_okta_push_mapping_no_mapping_id(self, plugin_instance, mock_app_group, mocker: MockerFixture, caplog):
        """Test early return when no mapping ID present."""
        # Mock get_status_value to return None
        mocker.patch('plugin.get_status_value', return_value=None)
        mock_okta_delete = mocker.patch('plugin.okta.delete_group_push_mapping')

        # Execute
        plugin_instance._delete_okta_group_push_mapping_and_google_group(mock_app_group)

        # Verify
        assert not mock_okta_delete.called
        assert "No push mapping ID found" in caplog.text

    def test_delete_okta_push_mapping_api_error(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test handling of Okta API errors during deletion."""
        # Mock get_status_value and okta service
        mocker.patch('plugin.get_status_value', return_value='okta-mapping-123')
        mock_okta_delete = mocker.patch('plugin.okta.delete_group_push_mapping')
        mock_okta_delete.side_effect = Exception("Okta API error")

        with pytest.raises(Exception) as exc_info:
            plugin_instance._delete_okta_group_push_mapping_and_google_group(mock_app_group)

        # Verify note was added
        assert len(exc_info.value.__notes__) > 0
        assert "Failed to delete Okta push mapping" in exc_info.value.__notes__[0]


class TestConfigurationValidation:
    """Test configuration validation."""

    @pytest.mark.parametrize("config,expected_errors", [
        # Valid cases
        ({'enabled': True, 'email_prefix': 'dev-team'}, {}),
        ({'enabled': False}, {}),
        ({'enabled': True, 'email_prefix': ''}, {}),
        ({'enabled': True, 'email_prefix': 'a'}, {}),
        ({'enabled': True, 'email_prefix': 'dev123'}, {}),
        ({'enabled': True, 'email_prefix': 'dev-team-test'}, {}),

        # Invalid cases
        ({'email_prefix': 'test'}, {'enabled': "The 'enabled' field is required"}),
        ({'enabled': 'yes'}, {'enabled': "The 'enabled' field must be a boolean"}),
        ({'enabled': 1}, {'enabled': "The 'enabled' field must be a boolean"}),
        ({'enabled': True, 'email_prefix': 'Dev-Team'}, {'email_prefix': "must contain only hyphen-delimited groups of lowercase letters and numbers (kebab-case)"}),
        ({'enabled': True, 'email_prefix': '-dev'}, {'email_prefix': "must contain only hyphen-delimited groups of lowercase letters and numbers (kebab-case)"}),
        ({'enabled': True, 'email_prefix': 'dev-'}, {'email_prefix': "must contain only hyphen-delimited groups of lowercase letters and numbers (kebab-case)"}),
        ({'enabled': True, 'email_prefix': 'dev_team'}, {'email_prefix': "must contain only hyphen-delimited groups of lowercase letters and numbers (kebab-case)"}),
        ({'enabled': True, 'email_prefix': 'dev team'}, {'email_prefix': "must contain only hyphen-delimited groups of lowercase letters and numbers (kebab-case)"}),
        ({'enabled': True, 'email_prefix': 123}, {'email_prefix': "The 'email_prefix' field must be a string"}),
    ])
    def test_validate_app_config(self, plugin_instance, config, expected_errors):
        """Test app configuration validation."""
        errors = plugin_instance.validate_plugin_app_config(config, PLUGIN_ID)

        if expected_errors:
            # Check that all expected errors are present
            for key, expected_msg in expected_errors.items():
                assert key in errors
                assert expected_msg in errors[key]
        else:
            assert errors == {} or errors is None

    def test_validate_app_config_wrong_plugin_id(self, plugin_instance):
        """Test that validation returns None for wrong plugin ID."""
        config = {'enabled': True}
        result = plugin_instance.validate_plugin_app_config(config, 'wrong_plugin_id')
        assert result is None

    def test_validate_group_config_returns_empty(self, plugin_instance):
        """Test that group config validation returns empty dict."""
        result = plugin_instance.validate_plugin_group_config({}, PLUGIN_ID)
        assert result == {}

    def test_validate_group_config_wrong_plugin_id(self, plugin_instance):
        """Test that group validation returns None for wrong plugin ID."""
        result = plugin_instance.validate_plugin_group_config({}, 'wrong_plugin_id')
        assert result is None


class TestLifecycleHooks:
    """Test lifecycle hook orchestration with mocked helper methods."""

    def test_group_created_success_flow(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test successful group creation workflow orchestration."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=True)
        mock_create_google = mocker.patch.object(
            plugin_instance,
            '_create_google_group',
            return_value='TestApp - Engineering'
        )
        mock_create_okta = mocker.patch.object(
            plugin_instance,
            '_create_okta_group_push_mapping'
        )

        # Mock session
        mock_session = Mock()

        # Execute
        plugin_instance.group_created(mock_session, mock_app_group, plugin_id=None)

        # Verify orchestration
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_create_google.assert_called_once_with(mock_app_group)
        mock_create_okta.assert_called_once_with(mock_app_group, 'TestApp - Engineering')
        assert mock_session.add.call_count == 2  # Called twice to persist status updates
        assert mock_session.commit.call_count == 2  # Called twice after each add

    def test_group_created_plugin_disabled(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that disabled plugin skips all operations."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=False)
        mock_create_google = mocker.patch.object(plugin_instance, '_create_google_group')
        mock_create_okta = mocker.patch.object(plugin_instance, '_create_okta_group_push_mapping')

        # Mock session
        mock_session = Mock()

        # Execute
        plugin_instance.group_created(mock_session, mock_app_group, plugin_id=None)

        # Verify early return - no operations performed
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_create_google.assert_not_called()
        mock_create_okta.assert_not_called()
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    def test_group_created_wrong_plugin_id(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that wrong plugin_id causes early return."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled')
        mock_create_google = mocker.patch.object(plugin_instance, '_create_google_group')

        # Mock session
        mock_session = Mock()

        # Execute with wrong plugin_id
        plugin_instance.group_created(mock_session, mock_app_group, plugin_id='wrong_plugin')

        # Verify early return - no operations performed
        mock_is_enabled.assert_not_called()
        mock_create_google.assert_not_called()

    def test_group_created_google_group_creation_raises_exception(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that exception during Google Group creation propagates."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=True)
        mock_create_google = mocker.patch.object(
            plugin_instance,
            '_create_google_group',
            side_effect=Exception("Google API error")
        )
        mock_create_okta = mocker.patch.object(plugin_instance, '_create_okta_group_push_mapping')

        # Mock session
        mock_session = Mock()

        # Execute and verify exception propagates
        with pytest.raises(Exception, match="Google API error"):
            plugin_instance.group_created(mock_session, mock_app_group, plugin_id=None)

        # Verify orchestration stopped at Google Group creation
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_create_google.assert_called_once_with(mock_app_group)
        mock_create_okta.assert_not_called()  # Should not be called when exception raised
        mock_session.add.assert_not_called()  # No session operations when exception raised
        mock_session.commit.assert_not_called()

    def test_group_created_okta_push_mapping_raises_exception(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that exception during Okta push mapping creation propagates."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=True)
        mock_create_google = mocker.patch.object(
            plugin_instance,
            '_create_google_group',
            return_value='TestApp - Engineering'
        )
        mock_create_okta = mocker.patch.object(
            plugin_instance,
            '_create_okta_group_push_mapping',
            side_effect=Exception("Okta API error")
        )

        # Mock session
        mock_session = Mock()

        # Execute and verify exception propagates
        with pytest.raises(Exception, match="Okta API error"):
            plugin_instance.group_created(mock_session, mock_app_group, plugin_id=None)

        # Verify Google Group was created but Okta mapping failed
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_create_google.assert_called_once_with(mock_app_group)
        assert mock_session.add.call_count == 1  # Called once after Google Group creation
        assert mock_session.commit.call_count == 1  # Called once after Google Group creation
        mock_create_okta.assert_called_once_with(mock_app_group, 'TestApp - Engineering')

    def test_group_deleted_success_flow(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test successful group deletion workflow orchestration."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=True)
        mock_get_status = mocker.patch('plugin.get_status_value', return_value='test@test-company.com')
        mock_delete = mocker.patch.object(
            plugin_instance,
            '_delete_okta_group_push_mapping_and_google_group'
        )

        # Mock session
        mock_session = Mock()

        # Execute
        plugin_instance.group_deleted(mock_session, mock_app_group, plugin_id=None)

        # Verify orchestration
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_get_status.assert_called_once_with(mock_app_group, "email", PLUGIN_ID)
        mock_delete.assert_called_once_with(mock_app_group)

    def test_group_deleted_plugin_disabled(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that disabled plugin skips all operations."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=False)
        mock_get_status = mocker.patch('plugin.get_status_value')
        mock_delete = mocker.patch.object(
            plugin_instance,
            '_delete_okta_group_push_mapping_and_google_group'
        )

        # Mock session
        mock_session = Mock()

        # Execute
        plugin_instance.group_deleted(mock_session, mock_app_group, plugin_id=None)

        # Verify early return - no operations performed
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_get_status.assert_not_called()
        mock_delete.assert_not_called()

    def test_group_deleted_wrong_plugin_id(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that wrong plugin_id causes early return."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled')
        mock_delete = mocker.patch.object(
            plugin_instance,
            '_delete_okta_group_push_mapping_and_google_group'
        )

        # Mock session
        mock_session = Mock()

        # Execute with wrong plugin_id
        plugin_instance.group_deleted(mock_session, mock_app_group, plugin_id='wrong_plugin')

        # Verify early return - no operations performed
        mock_is_enabled.assert_not_called()
        mock_delete.assert_not_called()

    def test_group_deleted_no_email_status(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test early return when no email status (group not managed by plugin)."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=True)
        mock_get_status = mocker.patch('plugin.get_status_value', return_value=None)
        mock_delete = mocker.patch.object(
            plugin_instance,
            '_delete_okta_group_push_mapping_and_google_group'
        )

        # Mock session
        mock_session = Mock()

        # Execute
        plugin_instance.group_deleted(mock_session, mock_app_group, plugin_id=None)

        # Verify orchestration
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_get_status.assert_called_once_with(mock_app_group, "email", PLUGIN_ID)
        mock_delete.assert_not_called()  # Should not be called when no email status

    def test_group_deleted_deletion_raises_exception(self, plugin_instance, mock_app_group, mocker: MockerFixture):
        """Test that exception during deletion propagates."""
        # Mock helper methods
        mock_is_enabled = mocker.patch.object(plugin_instance, '_is_enabled', return_value=True)
        mock_get_status = mocker.patch('plugin.get_status_value', return_value='test@test-company.com')
        mock_delete = mocker.patch.object(
            plugin_instance,
            '_delete_okta_group_push_mapping_and_google_group',
            side_effect=Exception("Deletion failed")
        )

        # Mock session
        mock_session = Mock()

        # Execute and verify exception propagates
        with pytest.raises(Exception, match="Deletion failed"):
            plugin_instance.group_deleted(mock_session, mock_app_group, plugin_id=None)

        # Verify orchestration reached deletion attempt
        mock_is_enabled.assert_called_once_with(mock_app_group)
        mock_get_status.assert_called_once_with(mock_app_group, "email", PLUGIN_ID)
        mock_delete.assert_called_once_with(mock_app_group)
