# Build Arg on whether to push the sentry release or not
# Default is false as it requires mounting a .sentryclirc secret file
ARG PUSH_SENTRY_RELEASE="false"

# Build step #1: build the React front end
FROM node:24-alpine AS build-step
WORKDIR /app
ENV PATH=/app/node_modules/.bin:$PATH
# Pin npm >= 11.16 so the v12 install defaults in .npmrc are enforced
# regardless of the npm bundled with the base image. Run before copying the
# project so this step isn't gated by package.json "engines".
RUN npm install -g npm@11.16.0
COPY .npmrc index.html package.json package-lock.json tsconfig.json tsconfig.paths.json vite.config.ts .env.production* ./
COPY ./src ./src
COPY ./public ./public
COPY ./config ./config

RUN npm install
RUN touch .env.production
# Set Vite environment variables
ENV VITE_API_SERVER_URL=""
# Set Sentry plugin environment variables for production build
ENV NODE_ENV=production
# If a frontend config override (e.g. IdP deep-link URLs) is provided as a build
# secret, write it where Vite's config loader picks it up; otherwise build against
# config.default.json. The secret is absent for open-source builds, so plain
# `docker build` still works.
RUN --mount=type=secret,id=ACCESS_CONFIG_OVERRIDE \
  if [ -s /run/secrets/ACCESS_CONFIG_OVERRIDE ]; then \
    cp /run/secrets/ACCESS_CONFIG_OVERRIDE config/config.override.json; \
  fi; \
  ACCESS_CONFIG_FILE=$(test -f config/config.override.json && echo config.override.json) npm run build

# Optional build step #2: upload source maps to Sentry
FROM build-step AS sentry
ARG SENTRY_RELEASE=""
ENV SENTRY_RELEASE=$SENTRY_RELEASE
# Use secret mount for SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT
RUN --mount=type=secret,id=SENTRY_AUTH_TOKEN \
  --mount=type=secret,id=SENTRY_ORG \
  --mount=type=secret,id=SENTRY_PROJECT \
  SENTRY_AUTH_TOKEN=$(cat /run/secrets/SENTRY_AUTH_TOKEN) \
  SENTRY_ORG=$(cat /run/secrets/SENTRY_ORG) \
  SENTRY_PROJECT=$(cat /run/secrets/SENTRY_PROJECT) \
  ACCESS_CONFIG_FILE=$(test -f config/config.override.json && echo config.override.json) \
  npm run build
# Source maps are automatically uploaded and deleted by Vite Sentry plugin during build
RUN touch sentry

# Build step #3: build the API with the client as static files
FROM python:3.13 AS false
ARG SENTRY_RELEASE=""
WORKDIR /app

# Pull the uv binary from its published image and point it at the base
# image's interpreter (UV_PYTHON_DOWNLOADS=0 — no managed-Python download).
# The project venv lives at /app/.venv; putting it on PATH exposes both
# `gunicorn` and the `access` console script (entry point api.cli:cli)
# for CronJobs and other CLI invocations.
COPY --from=ghcr.io/astral-sh/uv:0.11.10 /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=build-step /app/build ./build

# Install dependencies first from the frozen lockfile (this layer is cached
# until pyproject.toml/uv.lock change), then the project itself. --no-dev
# keeps the dev/test dependency groups out of the production image.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev
COPY ./api ./api
COPY ./migrations ./migrations
COPY ./config ./config
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# --- Optional example plugins -------------------------------------------------
# The example plugins under examples/plugins/ are opt-in. Each installs into the
# app venv only when its build arg is set to "true" (default "false"), so the
# default image ships without them and derived images enable just what they want:
#   docker build --build-arg INSTALL_SLACK_NOTIFICATIONS_PLUGIN=true .
# Installs use `uv pip install` because the venv at /app/.venv has no `pip`;
# plain `pip` would install into the system interpreter where the app won't find
# it. A plugin's requirements.txt is installed first when present (some examples
# keep runtime deps there rather than in setup.py). See examples/plugins/README.md
# for the full list and for how to bake in your own plugin.
ARG INSTALL_AUDIT_LOGGER_PLUGIN="false"
ARG INSTALL_CONDITIONAL_ACCESS_PLUGIN="false"
ARG INSTALL_DATADOG_METRICS_PLUGIN="false"
ARG INSTALL_HEALTH_CHECK_PLUGIN="false"
ARG INSTALL_NOTIFICATIONS_PLUGIN="false"
ARG INSTALL_SLACK_NOTIFICATIONS_PLUGIN="false"
# Bind-mount the plugin sources for the length of this RUN only: enabled plugins
# are installed (non-editable) into /app/.venv, so nothing needs to persist in
# the final image and the default (all-false) build stays byte-identical.
RUN --mount=type=bind,source=examples/plugins,target=examples/plugins \
    set -eu; \
    install_plugin() { \
        if [ -f "$1/requirements.txt" ]; then uv pip install -r "$1/requirements.txt"; fi; \
        uv pip install "$1"; \
    }; \
    if [ "$INSTALL_AUDIT_LOGGER_PLUGIN" = "true" ]; then install_plugin ./examples/plugins/app_group_lifecycle_audit_logger; else echo "Skipping app_group_lifecycle_audit_logger plugin"; fi; \
    if [ "$INSTALL_CONDITIONAL_ACCESS_PLUGIN" = "true" ]; then install_plugin ./examples/plugins/conditional_access; else echo "Skipping conditional_access plugin"; fi; \
    if [ "$INSTALL_DATADOG_METRICS_PLUGIN" = "true" ]; then install_plugin ./examples/plugins/datadog_metrics_reporter; else echo "Skipping datadog_metrics_reporter plugin"; fi; \
    if [ "$INSTALL_HEALTH_CHECK_PLUGIN" = "true" ]; then install_plugin ./examples/plugins/health_check_plugin; else echo "Skipping health_check_plugin plugin"; fi; \
    if [ "$INSTALL_NOTIFICATIONS_PLUGIN" = "true" ]; then install_plugin ./examples/plugins/notifications; else echo "Skipping notifications plugin"; fi; \
    if [ "$INSTALL_SLACK_NOTIFICATIONS_PLUGIN" = "true" ]; then install_plugin ./examples/plugins/notifications_slack; else echo "Skipping notifications_slack plugin"; fi

# Build an image that includes the optional sentry release push build step
FROM false AS true
COPY --from=sentry /app/sentry ./sentry

# Final build step: copy the API and the client from the previous steps
# Choose whether to include the sentry release push build step or not
FROM ${PUSH_SENTRY_RELEASE}

ENV ENV=production
ENV SENTRY_RELEASE=$SENTRY_RELEASE

EXPOSE 3000

CMD ["gunicorn", "-w", "4", "-t", "600", "-b", ":3000", "-k", "uvicorn.workers.UvicornWorker", "--access-logfile", "-", "api.asgi:app"]
