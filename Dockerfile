# Build Arg on whether to push the sentry release or not
# Default is false as it requires mounting a .sentryclirc secret file
ARG PUSH_SENTRY_RELEASE="false"

# Build step #1: build the React front end
FROM node:23-alpine AS build-step
ARG SENTRY_RELEASE=""
WORKDIR /app
ENV PATH /app/node_modules/.bin:$PATH
COPY craco.config.js package.json package-lock.json tsconfig.json tsconfig.paths.json .env.production* ./
COPY ./src ./src
COPY ./public ./public
RUN npm install
RUN touch .env.production
ENV REACT_APP_SENTRY_RELEASE $SENTRY_RELEASE
ENV REACT_APP_API_SERVER_URL ""
RUN npm run build

# Optional build step #2: upload the source maps by pushing a release to sentry
FROM getsentry/sentry-cli:2 AS sentry
ARG SENTRY_RELEASE=""
RUN --mount=type=secret,id=SENTRY_CLI_RC \
  cp /run/secrets/SENTRY_CLI_RC ~/.sentryclirc
WORKDIR /app
COPY --from=build-step /app/build ./build
RUN sentry-cli releases new ${SENTRY_RELEASE}
RUN sentry-cli releases files ${SENTRY_RELEASE} upload-sourcemaps /app/build/static/js
RUN sentry-cli releases finalize ${SENTRY_RELEASE}
RUN touch sentry

# Build step #3: build the API with the client as static files
FROM python:3.13 AS false
ARG SENTRY_RELEASE=""
WORKDIR /app
COPY --from=build-step /app/build ./build

RUN rm ./build/static/js/*.map
RUN mkdir ./api && mkdir ./migrations
COPY requirements.txt api/ ./api/
COPY migrations/ ./migrations/
RUN pip install -r ./api/requirements.txt

# Build an image that includes the optional sentry release push build step
FROM false AS true
COPY --from=sentry /app/sentry ./sentry

# Final build step: copy the API and the client from the previous steps
# Choose whether to include the sentry release push build step or not
FROM ${PUSH_SENTRY_RELEASE}

ENV FLASK_ENV production
ENV FLASK_APP api.app:create_app
ENV SENTRY_RELEASE $SENTRY_RELEASE

EXPOSE 3000

CMD ["gunicorn", "-w", "4", "-t", "600", "-b", ":3000", "--access-logfile", "-", "api.wsgi:app"]
