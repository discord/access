# Conditional Access Plugin

This plugin will allow you to automatically approve or deny access requests based on the group or tag membership of the group.

## Installation

This plugin's source ships in the Access build context under
`examples/plugins/`, but the default image does **not** install it. Enable it at
build time with its build arg (default `false`):

```bash
docker build --build-arg INSTALL_CONDITIONAL_ACCESS_PLUGIN=true .
# or, with docker compose:
docker compose build --build-arg INSTALL_CONDITIONAL_ACCESS_PLUGIN=true
```

The image installs the plugin into its `uv` virtualenv (`/app/.venv`) with
`uv pip install` — the venv has no `pip`, and plain `pip` would install into the
system interpreter where the running app won't find it. See
[the plugins README](../README.md) for every plugin's build arg and for baking
in a plugin of your own.


## Configuration

You can set the following environment variables to configure the plugin but note that neither are required by default. If you only want to use the specific tag `Auto-Approve` then no environment variables are required. You must however create the tag within the Access Application.

- `AUTO_APPROVED_GROUP_NAMES`: A comma-separated list of group names that will be auto-approved.
- `AUTO_APPROVED_TAG_NAMES`: A comma-separated list of tag names that will be auto-approved.


## Usage

The plugin will automatically approve access requests to the groups or tags specified in the environment variables by running a check on each access request that is processed. If neither the group name nor the tag name match, then a log line stating manual approval is required will be output.
