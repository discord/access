# Health Check Plugin

This is an example plugin that demonstrates how to extend Flask CLI commands using plugins. The `health_check_plugin` adds a custom `health` command to the Flask CLI, which performs a health check of the application, including verifying database connectivity.

## Overview

The plugin consists of the following files:

- **`__init__.py`**: Initializes the plugin by defining an `init_app` function that registers the CLI commands.
- **`cli.py`**: Contains the implementation of the `health` command.
- **`setup.py`**: Defines the plugin's setup configuration and registers the entry point for the CLI command.

## Installation

To install the plugin the App container Dockerfile

```
WORKDIR /app/plugins
ADD ./examples/plugins/health_check_plugin ./health_check_plugin
RUN pip install ./health_check_plugin

# Reset working directory
WORKDIR /app
```

## Usage

After installing the plugin, the `health` command becomes available in the Flask CLI:

```bash
flask health
```

This command outputs the application's health status in JSON format, indicating the database connection status and the application version.

## Purpose

This plugin serves as an example of how to extend Flask CLI commands using plugins and entry points. It demonstrates:

- How to create a custom CLI command in a plugin.
- How to register the command using entry points in `setup.py`.

By following this example, you can create your own plugins to extend the functionality of your Flask application's CLI in a modular and scalable way.

## Files

- **[`__init__.py`](./__init__.py)**: Plugin initialization code.
- **[`cli.py`](./cli.py)**: Implementation of the `health` CLI command.
- **[`setup.py`](./setup.py)**: Setup script defining the plugin metadata and entry points.

