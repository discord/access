# Access plugins

Access is extended through **plugins**: separate Python distributions that
register themselves against Access's plugin hooks. Operator-specific behavior —
notifications, conditional access, metrics, external group provisioning, extra
CLI commands — lives in a plugin, not in the Access repo itself.

This directory holds runnable **example plugins**. Each is a working reference
you can enable as-is, and a template to copy when writing your own.

## Available examples

| Directory | Extends | Entry-point group | Docker build arg |
|-----------|---------|-------------------|------------------|
| [`app_group_lifecycle_audit_logger`](./app_group_lifecycle_audit_logger) | Log app group lifecycle events (create/update/delete/membership) | `access_app_group_lifecycle` | `INSTALL_AUDIT_LOGGER_PLUGIN` |
| [`conditional_access`](./conditional_access) | Auto approve/deny access requests | `access_conditional_access` | `INSTALL_CONDITIONAL_ACCESS_PLUGIN` |
| [`datadog_metrics_reporter`](./datadog_metrics_reporter) | Export metrics to DataDog | `access_metrics_reporter` | `INSTALL_DATADOG_METRICS_PLUGIN` |
| [`notifications`](./notifications) | Request/expiry notifications to logger | `access_notifications` | `INSTALL_NOTIFICATIONS_PLUGIN` |
| [`notifications_slack`](./notifications_slack) | Request/expiry notifications to Slack | `access_notifications` | `INSTALL_SLACK_NOTIFICATIONS_PLUGIN` |
| [`health_check_plugin`](./health_check_plugin) | A new `access` CLI command | `access.commands` | `INSTALL_HEALTH_CHECK_PLUGIN` |

## How Access discovers plugins

Access uses [pluggy](https://pluggy.readthedocs.io/) with setuptools
**entry points**. At startup (and for the CLI), Access loads every installed
distribution that advertises an entry point in one of the groups above; there
is no registry to edit and no import path to configure. Installing the
distribution into the same environment as the running app is all that's needed;
if the app "sees" 0 plugins, it's almost always because the distribution landed
in a different interpreter than the one serving requests (see
[Installing into an image](#installing-into-a-built-image)).

The hook specifications each group implements live in
[`api/plugins/`](../../api/plugins/). Read the hookspec for the surface you're
extending before writing an implementation.

## Authoring a plugin

A plugin is an ordinary Python distribution with three parts:

1. **The implementation.** A module/package whose functions or class methods are
   decorated with the `hookimpl` marker for the plugin type and match the
   corresponding hookspec in `api/plugins/`. The effectful hooks are **async**
   (`async def`); a hook registered as a plain `def` fails fast at load. Pure
   schema/metadata/validation hooks stay synchronous. CLI-command plugins are
   ordinary Click commands and drive their own `asyncio.run(...)`.

2. **Entry point registration.** A `setup.py` (or `pyproject.toml`) declaring the
   distribution and an `entry_points` mapping in the right group.

3. **Dependencies.** Prefer declaring runtime deps in `install_requires` so a
   plain `uv pip install ./your_plugin` pulls them. Some examples instead keep
   deps in a `requirements.txt`; the image build and the `make` target below
   both install that file first when it's present.

The quickest start is to copy the example closest to your use case, change the
distribution name, entry-point name, and hook bodies, and adjust its
dependencies.

## Installing into a built image

The example sources ship inside the Access build context, but the default image
installs **none** of them. Each is opt-in via a `--build-arg` that defaults to
`false`, so `docker build .` produces an app image with no plugins, and a derived
build enables exactly what it wants, e.g.:

```bash
docker build \
  --build-arg INSTALL_CONDITIONAL_ACCESS_PLUGIN=true \
  --build-arg INSTALL_SLACK_NOTIFICATIONS_PLUGIN=true \
  .
```

See the table above for every arg. The build installs each enabled plugin (and
its `requirements.txt`, when present) into the app's `uv` virtualenv at
`/app/.venv` with `uv pip install`. Use `uv pip install`, **not** `pip`: the
venv has no `pip`, and plain `pip` installs into the base image's system
interpreter, where the running app never looks — the classic
"Registered 0 plugins" symptom.

### Baking in your own plugin

Suppose your plugin lives in your own repo, not this one. Add it to a Dockerfile that
builds `FROM` the Access image (or extends this build) with the same pattern:

```dockerfile
FROM access:latest

WORKDIR /app/plugins

COPY ./path/to/your_plugin ./your_plugin
RUN uv pip install ./your_plugin

WORKDIR /app
```

## Local development

`make run-backend` (and `make run`) sync the venv with `uv sync` before starting
the server. Two ways to get plugins into that dev venv:

- **The audit logger** is wired into the `dev` dependency group with an editable
  `[tool.uv.sources]` entry (see [`pyproject.toml`](../../pyproject.toml)), so it
  installs and registers automatically — no flag needed. You'll see
  `Registered 1 app group lifecycle plugin(s): ['audit_logger']` at startup.

- **Any other example** can be installed for a run via the `PLUGINS` variable,
  which names one or more directories under `examples/plugins/`:

  ```bash
  make run-backend PLUGINS="conditional_access notifications_slack"
  ```

  These are installed **editable** after `uv sync` (which would otherwise prune
  anything not in the lockfile), so edits to the plugin are picked up on reload.
  Because `uv sync` prunes them, pass `PLUGINS=` again on the next run — or, for
  a plugin you use constantly, add it to the `dev` group like the audit logger.

To install a plugin into the venv by hand (equivalently), use
`uv pip install -e examples/plugins/<dir>`.
