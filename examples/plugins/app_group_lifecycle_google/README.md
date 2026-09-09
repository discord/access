# App Group Lifecycle Google Group Management Plugin

This plugin automatically creates, modifies, and deletes Google Groups corresponding to the Access groups that are configured to use it. It links those Google Groups to the corresponding Access-managed Okta group via Okta group push. Group membership is handled entirely by Okta group push; the plugin does not implement membership hooks.

## Overview

When an Access group is created or deleted or its plugin configuration is modified, the plugin:

1. On create, creates an Okta group push mapping with a new target group named after the email prefix. Okta creates the downstream Google Group *and* links it in one step, so the plugin never has to wait for Okta to import a group created directly in Google (which requires a manual trigger). It then updates that Google Group's display name, description, and security label from Access.
2. On modify, updates the corresponding Google Group's properties (display name, description, security label) in the configured Google Workspace domain. The email is immutable.
3. On delete, removes the Okta group push mapping and the Google Group.

Membership is kept in sync automatically by Okta group push. A Google Group linked out-of-band is adopted rather than recreated; if Okta hasn't yet pushed a newly-created group to Google, the group is marked pending and finalized on a later reconcile. Groups are also periodically reconciled to ensure eventual alignment to the source of truth in Access.

## Files

- **[`__init__.py`](./__init__.py)**: Plugin package initialization
- **[`plugin.py`](./plugin.py)**: Plugin implementation
- **[`pyproject.toml`](./pyproject.toml)**: Packaging metadata and the entry point that registers the plugin

## Configuration

### App-Level Configuration

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `enabled` | boolean | yes | Enable or disable this plugin for the app. |
| `email_pattern` | text | no | Optional regex applied to the group email prefix to validate it before creating the Google Group. |
| `require_security_groups` | boolean | no | Label every one of this app's Google Groups as a [Workspace security group](https://knowledge.workspace.google.com/admin/groups/control-access-to-sensitive-data-with-security-groups), whatever the individual groups are set to. Defaults to off. |

### Group-Level Configuration

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `email` | text | yes | The local-part (prefix) of the Google Group email address. The full address is `{email}@{GOOGLE_WORKSPACE_DOMAIN}`. Immutable after the group is created (the Cloud Identity `groupKey` cannot be changed). |
| `display_name` | text | yes | The display name for the Google Group. |
| `security_group` | boolean | no | Label this Google Group as a [Google Workspace security group](https://knowledge.workspace.google.com/admin/groups/control-access-to-sensitive-data-with-security-groups) so it can grant access to sensitive data. Defaults to off, or to on when the app sets `require_security_groups`. |

### Group-Level Status

| Key | Description |
|-----|-------------|
| `push_mapping_id` | The Okta group push mapping ID linking the Okta group to the Google Group. |
| `google_group_id` | The Google Group resource ID. |
| `sync_status` | One of `synced`, `pending`, `skipped`, or `error`. |
| `sync_error` | Error message if `sync_status` is `error`; otherwise empty. |
| `last_synced_at` | Timestamp of the last successful sync. |

There are no app-level status properties.

## Security groups

Setting a group's `security_group` configuration adds the
[`cloudidentity.googleapis.com/groups.security` label](https://docs.cloud.google.com/identity/docs/groups#group_labels)
to the linked Google Group, which is what makes it a [Workspace security
group](https://knowledge.workspace.google.com/admin/groups/control-access-to-sensitive-data-with-security-groups):
a group that may be named in policies granting access to sensitive data.

An app whose groups all gate sensitive data can mandate it instead, with the app-level
`require_security_groups`. The app setting and the group setting are OR'd rather than one
overriding the other, so the app can only raise the requirement: a mandate covers the app's
existing groups without their stored configuration having to be rewritten one by one, and it
rejects a group trying to opt out. Because nothing in the plugin interface fires on an app
configuration change, a newly-set mandate reaches already-created groups on their next reconcile:
the periodic `sync-app-groups` run, or the group's next update.

Two properties of the label shape how the plugin handles it:

**It is one-way.** [Google does not convert a security group back to an ordinary
group](https://docs.cloud.google.com/identity/docs/how-to/update-group-to-security-group), so the
configuration is a floor rather than a switch. The plugin raises the label and never attempts a
downgrade; clearing the setting on a group that is already labeled has no effect in Google, and the
next reconcile restores the setting so the UI keeps agreeing with Google. The setting is still
mutable, because promoting an existing group to a security group is a legitimate (and supported)
operation.

**Google enforces [membership
requirements](https://docs.cloud.google.com/identity/docs/how-to/update-group-to-security-group).** A
security group may contain only users, service accounts, and other security groups in your own
domain, and only a Workspace Super Admin or Groups Admin may apply the label (the **Group
Administrator** role under [Calling the Google API](#calling-the-google-api) covers the latter). The
plugin pre-checks none of that: Google rejects the label outright when it does not hold, and the
plugin records the rejection as a sync error naming the requirement. That error is deliberately loud
(it fails the `sync-app-groups` run) because Access is otherwise showing a security group it does
not actually have.

Because a new group is created through Okta group push rather than the Groups API, the Google Group
exists as an ordinary group for the moment between Okta creating it and the plugin's reconcile
labeling it.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_WORKSPACE_OKTA_APP_ID` | The Okta application ID for the Google Workspace application. See https://support.okta.com/help/s/article/How-to-obtain-an-application-ID. |
| `GOOGLE_WORKSPACE_DOMAIN` | The Google Workspace domain (e.g. `acme.com`). Used to construct the full Google Group email address. |

## Authentication and Authorization

### Calling the Okta API

For this plugin to work, Access must have permission to create and delete group push mappings (e.g. via the App Admin role) for the configured Google Workspace Okta application.

### Calling the Google API

This plugin uses the **Cloud Identity Groups API** (`cloudidentity.googleapis.com`, scope `https://www.googleapis.com/auth/cloud-identity.groups`) to look up groups and to update and delete them. (Group *creation* goes through Okta group push, not this API.) Because that API authorizes the calling principal directly, assign the service account reachable via [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials) the Google Workspace **Group Administrator** admin role: https://knowledge.workspace.google.com/admin/users/assign-specific-admin-roles#service-account. Group membership is still synchronized by Okta group push.

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