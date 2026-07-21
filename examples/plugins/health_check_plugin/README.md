# Health Check Plugin

This is an example plugin that demonstrates how to extend the Access CLI using plugins. The `health_check_plugin` adds a custom `health` command to the `access` CLI which performs a health check of the application, including verifying database connectivity.

## Overview

The plugin consists of the following files:

- **`cli.py`**: Contains the implementation of the `health` command.
- **`setup.py`**: Defines the plugin's setup configuration and registers the entry point for the CLI command.

## Installation

This plugin's source ships in the Access build context under
`examples/plugins/`, but the default image does **not** install it. Enable it at
build time with its build arg (default `false`):

```bash
docker build --build-arg INSTALL_HEALTH_CHECK_PLUGIN=true .
# or, with docker compose:
docker compose build --build-arg INSTALL_HEALTH_CHECK_PLUGIN=true
```

The image installs the plugin into its `uv` virtualenv (`/app/.venv`) with
`uv pip install` — the venv has no `pip`, and plain `pip` would install into the
system interpreter where the running app won't find it. See
[the plugins README](../README.md) for every plugin's build arg and for baking
in a plugin of your own.

## Usage

After installing the plugin, the `health` command becomes available in the `access` CLI:

```bash
access health
```

This command outputs the application's health status in JSON format, indicating the database connection status and the application version.

## Purpose

This plugin serves as an example of how to extend the Access CLI commands using plugins and entry points. It demonstrates:

- How to create a custom Click command in a plugin.
- How to register the command using the `access.commands` entry point group in `setup.py`.

By following this example, you can create your own plugins to extend the functionality of the Access CLI in a modular and scalable way.

## Files

- **[`cli.py`](./cli.py)**: Implementation of the `health` CLI command.
- **[`setup.py`](./setup.py)**: Setup script defining the plugin metadata and entry points.
