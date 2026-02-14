# App Group Lifecycle Plugin for Google Group Management

This plugin allows Access applications to be configured to manage Google groups following specific naming conventions.

When Access groups are created with this plugin configured, Access will automatically create corresponding Google Groups via [Google's API](https://docs.cloud.google.com/identity/docs/groups) and link them via [Okta's group push feature](https://help.okta.com/oie/en-us/content/topics/users-groups-profiles/usgp-group-push-main.htm) and [API](https://help.okta.com/oie/en-us/content/topics/users-groups-profiles/usgp-group-push-main.htm). Likewise, when groups with this plugin configured are deleted, Access will automatically delete the linked Google groups.

This plugin does not leverage any of the membership hooks, since Okta's group push functionality does that already.

## Plugin Configuration

### Application

- `enabled` (bool): whether or not the plugin is active
- `email_prefix` (str, optional): a prefix for the email addresses of created Google groups

### Group

None.

## Plugin Status Reporting

### Application

None.

### Group

- `name` (str): the display name of the linked Google group
- `email` (str): the email address of the linked Google group
- `push_mapping_id` (str): the Okta ID of the group push mapping

## Requirements

To install this plugin and run Access with the necessary permissions to manage Google groups, the following conditions must be met.

### Google Identity and Permissions

The following are prerequisite to [using Google's Groups API](https://docs.cloud.google.com/identity/docs/how-to/setup#auth-no-dwd).
1. The Access workload has an associated Google service account and can authenticate using [application default credentials](https://docs.cloud.google.com/docs/authentication/application-default-credentials).
2. This service account has been [assigned the `Google Workspace Group Administrator Role` on the workspace](https://docs.cloud.google.com/identity/docs/how-to/setup#assigning_an_admin_role_to_the_service_account) where groups will be managed.

### Okta Permissions

The Access workload must have permissions to [create group push mappings in Okta](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/GroupPushMapping/#tag/GroupPushMapping/operation/createGroupPushMapping).

### Environment Variables

The following environment variables are required by the plugin:
- `GOOGLE_WORKSPACE_OKTA_APP_ID`: the ID of the Google application in Okta
- `GOOGLE_WORKSPACE_DOMAIN`: the domain associated with the Google Workspace


### Installation

To install the plugin, add these lines to the Access container Dockerfile:

```dockerfile
# Install the google group management plugin
WORKDIR /app/plugins
ADD ./examples/plugins/app_group_lifecycle_google ./app_group_lifecycle_google
RUN pip install ./app_group_lifecycle_google

# Reset working directory
WORKDIR /app
```

## Behavior

As an example, suppose we have `GOOGLE_WORKSPACE_DOMAIN=acme.com` and an app called `GCP` with prefix `gcp-iam`, for which a new group is created: `App-GCP-Security`. Then the corresponding Google group would have the name `GCP - Security` and email address `gcp-iam-security@acme.com`.

## Development

### Install the Plugin

Add the plugin and all its requirements to your virtual environment:

```bash
pip install -e examples/plugins/app_group_lifecycle_google
```

### Set the Google Application Default Credentials

Use your own identity, if you have the needed permissions:

```bash
gcloud auth login --update-adc
```

Otherwise, impersonate Access via:

```bash
gcloud auth application-default login --impersonate-service-account <ACCESS_SERVICE_ACCOUNT_EMAIL>
```

See https://docs.cloud.google.com/docs/authentication/use-service-account-impersonation#adc.

### Testing

#### Structure

Tests are organized into groups:
- **TestHelperMethods:** Tests for helper methods with no external dependencies
- **TestGoogleAPIIntegration, TestOktaIntegration:** Tests for API integrations with mocked services
- **TestConfigurationValidation:** Tests for configuration validation logic
- **TestLifecycleHooks:** Tests for lifecycle hook orchestration (group_created, group_deleted)

#### Execution

The plugin includes comprehensive unit tests that can be run using tox:

**Run all plugin tests:**
```bash
tox -e test -- examples/plugins/app_group_lifecycle_google/test_plugin.py
```

**Run specific test class:**
```bash
tox -e test -- examples/plugins/app_group_lifecycle_google/test_plugin.py::TestHelperMethods
```

**Run specific test:**
```bash
tox -e test -- examples/plugins/app_group_lifecycle_google/test_plugin.py::TestHelperMethods::test_generate_group_email
```

**Run with verbose output:**
```bash
tox -e test -- examples/plugins/app_group_lifecycle_google/test_plugin.py -v
```