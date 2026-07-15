# App Group Lifecycle Google Group Management Plugin

This plugin automatically creates, modifies, and deletes Google Groups corresponding to the Access groups that are configured to use it. It links those Google Groups to the corresponding Access-managed Okta group via Okta group push. Group membership is handled entirely by Okta group push; the plugin does not implement membership hooks.

## Overview

When an Access group is created or deleted or its plugin configuration is modified, the plugin:

1. Creates, deletes, or modifies the properties (name, email, description) the corresponding Google Group in the configured Google Workspace domain.
2. Creates or removes an Okta group push mapping between the Access-managed Okta group and the Google Group, so Okta keeps group membership in sync automatically.

Additionally, Google groups are periodically reconciled (created or updated) to ensure eventual alignment to the source of truth in Access.

## Files

- **[`__init__.py`](./__init__.py)**: Plugin package initialization
- **[`plugin.py`](./plugin.py)**: Plugin implementation
- **[`setup.py`](./setup.py)**: Setup script defining plugin metadata and entry points

## Configuration

### App-Level Configuration

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `enabled` | boolean | yes | Enable or disable this plugin for the app. |
| `email_pattern` | text | no | Optional regex applied to the group email prefix to validate it before creating the Google Group. |

### Group-Level Configuration

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `email` | text | yes | The local-part (prefix) of the Google Group email address. The full address is `{email}@{GOOGLE_WORKSPACE_DOMAIN}`. Immutable after the group is created (the Cloud Identity `groupKey` cannot be changed). |
| `display_name` | text | yes | The display name for the Google Group. |

### Group-Level Status

| Key | Description |
|-----|-------------|
| `push_mapping_id` | The Okta group push mapping ID linking the Okta group to the Google Group. |
| `google_group_id` | The Google Group resource ID. |
| `sync_status` | One of `synced`, `pending`, or `error`. |
| `sync_error` | Error message if `sync_status` is `error`; otherwise empty. |
| `last_synced_at` | Timestamp of the last successful sync. |

There are no app-level status properties.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_WORKSPACE_OKTA_APP_ID` | The Okta application ID for the Google Workspace application. See https://support.okta.com/help/s/article/How-to-obtain-an-application-ID. |
| `GOOGLE_WORKSPACE_DOMAIN` | The Google Workspace domain (e.g. `acme.com`). Used to construct the full Google Group email address. |
| `GOOGLE_WORKSPACE_CUSTOMER_ID` | The Google Workspace customer ID (e.g. `C0xxxxxxx`), used as the Cloud Identity group parent (`customers/{id}`). See https://knowledge.workspace.google.com/admin/getting-started/find-your-customer-id. |

## Authentication and Authorization

### Calling the Okta API

For this plugin to work, Access must have permission to create and delete group push mappings (e.g. via the App Admin role) for the configured Google Workspace Okta application.

### Calling the Google API

This plugin uses the **Cloud Identity Groups API** (`cloudidentity.googleapis.com`, scope `https://www.googleapis.com/auth/cloud-identity.groups`) for all group CRUD. Because that API authorizes the calling principal directly, assign the service account reachable via [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials) the Google Workspace **Group Administrator** admin role: https://knowledge.workspace.google.com/admin/users/assign-specific-admin-roles#service-account. Group membership is still synchronized by Okta group push.

## Installation

The plugin code is included in the published Access Docker image, but not installed.
Add these lines to the overlay Dockerfile based on the published image:

```dockerfile
# Install the google group management plugin into the image's uv-managed venv
WORKDIR /app/plugins
RUN uv pip install ./app_group_lifecycle_google

# Reset working directory
WORKDIR /app
```

For local development, see [Development](#development) below.

## Development

### Install the Plugin

Add the plugin and all its requirements to your virtual environment:

```bash
uv pip install -e examples/plugins/app_group_lifecycle_google
```

### Set the Google Application Default Credentials

Use your own identity, if you have the needed permissions:

```bash
gcloud auth login --update-adc
```

Otherwise, impersonate Access via:

```bash
gcloud auth application-default login --impersonate-service-account <ACCESS_SERVICE_ACCOUNT_EMAIL> --scopes=https://www.googleapis.com/auth/cloud-identity.groups,https://www.googleapis.com/auth/cloud-platform
```

See https://docs.cloud.google.com/docs/authentication/use-service-account-impersonation#adc.

### Testing

The plugin's tests live alongside it in [`test_plugin.py`](./test_plugin.py). They stub the
Cloud Identity Groups API client and the Okta service, so they require neither Google/Okta
credentials nor the `google-*` libraries to be installed.

Run the test suite from the repository root with `make test` (or `uv run pytest`). pytest
collects the whole repository, including this plugin, so the plugin suite runs alongside the
core Access tests both locally and in CI:

```bash
make test
```

To run just this plugin's tests, or a single test, pass the path to `uv run pytest`:

```bash
# Just this plugin's suite
uv run pytest examples/plugins/app_group_lifecycle_google/test_plugin.py

# A single test
uv run pytest examples/plugins/app_group_lifecycle_google/test_plugin.py::test_metadata
```