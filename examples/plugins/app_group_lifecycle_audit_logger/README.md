# App Group Lifecycle Audit Logger Plugin

This is an example plugin that demonstrates how to implement an **App Group Lifecycle Plugin** for Access. The plugin logs all group lifecycle events (creation, deletion, and membership changes) to the application logs, providing a simple audit trail.

## Overview

This plugin serves as a reference implementation demonstrating:

- How to implement all required hooks from `AppGroupLifecyclePluginSpec`
- How to define configuration properties for apps and groups
- How to define status properties for monitoring
- How to validate configuration data
- How to handle group lifecycle events

The plugin doesn't integrate with any external systems—it simply logs events, making it easy to understand and test.

## Plugin Features

### Configuration

**App-Level Configuration:**
- `enabled` (boolean): Enable/disable audit logging for the app
- `log_level` (text): Log level to use (INFO, WARNING, ERROR)

**Group-Level Configuration:**
- `enabled` (boolean): Enable/disable audit logging for the group
- `custom_tag` (text): Custom tag to include in log messages

### Status

**App-Level Status:**
- `total_events_logged` (number): Total events logged for this app
- `last_sync_at` (date): When the last sync occurred

**Group-Level Status:**
- `events_logged` (number): Events logged for this group
- `last_event_at` (date): When the last event occurred

## Installation

To install the plugin, add these lines to the App container Dockerfile:

```dockerfile
# Install the audit logger plugin
WORKDIR /app/plugins
ADD ./examples/plugins/app_group_lifecycle_audit_logger ./app_group_lifecycle_audit_logger
RUN pip install ./app_group_lifecycle_audit_logger

# Reset working directory
WORKDIR /app
```

Alternatively, for local development, install it with pip:

```bash
pip install -e examples/plugins/app_group_lifecycle_audit_logger
```

## Usage

After installation, the plugin will be automatically discovered and registered. You can configure it through the Access web UI, or the API as described below.

### 1. List Available Plugins

```bash
curl http://localhost:6060/api/plugins/app-group-lifecycle
```

### 2. Configure an App

```bash
curl -X PUT http://localhost:6060/api/apps/{app_id} \
  -H "Content-Type: application/json" \
  -d '{
    "app_group_lifecycle_plugin": "audit_logger",
    "plugin_data": {
      "audit_logger": {
        "configuration": {
          "enabled": true,
          "log_level": "INFO"
        }
      }
    }
  }'
```

### 3. Configure a Group

```bash
curl -X PUT http://localhost:6060/api/groups/{group_id} \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_data": {
      "audit_logger": {
        "configuration": {
          "enabled": true,
          "custom_tag": "production"
        }
      }
    }
  }'
```

## Files

- **[`__init__.py`](./__init__.py)**: Plugin package initialization
- **[`plugin.py`](./plugin.py)**: Plugin implementation with all hook implementations
- **[`setup.py`](./setup.py)**: Setup script defining the plugin metadata and entry points

## Extending This Example

You can use this plugin as a template for creating your own app group lifecycle plugins. To create a custom plugin:

1. Copy this directory structure
2. Replace the logging logic with your integration (e.g., Discord API, GitHub API, Google Groups API)
3. Update the plugin ID, display name, and description
4. Add any required dependencies to `setup.py`
5. Implement the configuration and status properties your integration needs
6. Update the validation logic for your specific requirements

## Testing

To test the plugin locally:

1. Install the plugin in development mode: `pip install -e examples/plugins/app_group_lifecycle_audit_logger`
2. Start the Access application
3. Create/modify/delete groups and observe the logs

You should see log messages like:

```
[AUDIT_LOGGER] Group created: App-MyApp-Engineers (plugin enabled)
[AUDIT_LOGGER] Members added to App-MyApp-Engineers: user1@example.com, user2@example.com
[AUDIT_LOGGER] Group deleted: App-MyApp-Engineers
```

You'll likely need to ensure that your environment has `SQLALCHEMY_ECHO=false`, or the audit logs may be drowned out by echoed SQL queries.
