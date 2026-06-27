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
RUN npm run build

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
  npm run build
# Source maps are automatically uploaded and deleted by Vite Sentry plugin during build
RUN touch sentry

# Build step #3: build the API with the client as static files
FROM python:3.13 AS false
ARG SENTRY_RELEASE=""
WORKDIR /app
COPY --from=build-step /app/build ./build

RUN mkdir ./api && mkdir ./migrations
COPY requirements.txt api/ ./api/
COPY migrations/ ./migrations/
COPY alembic.ini setup.py ./
COPY ./config ./config

# Ship prod-ready plugin source into the image WITHOUT installing it here.
# Downstream images opt in by pip installing the plugins,
# which keeps the source single-homed in this repo while leaving activation to the consumer.
COPY ./examples/plugins/app_group_lifecycle_google ./plugins/app_group_lifecycle_google

RUN pip install -r ./api/requirements.txt
# Install the project itself so the `access` console script declared in
# setup.py (entry_points -> api.manage:cli) is available on PATH for
# CronJobs and other CLI invocations. --no-deps keeps the pinned
# versions from requirements.txt authoritative.
RUN pip install --no-deps .

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
