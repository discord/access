<p align="center"><img src="https://raw.githubusercontent.com/discord/access/main/public/logo.png" width="350"></p>

# ACCESS

Meet Access, a centralized portal for employees to transparently discover, request, and manage their access to all internal systems needed to do their jobs. If you're interested in the project, come chat with us!

<p align="center"><a href="https://discord.gg/access-enjoyers"><img src="https://discordapp.com/api/guilds/1232815453907189922/widget.png?style=banner2" alt="Join our Discord!"></a></p>

## Purpose

The Access service exists to help answer the following questions for each persona:

- All Users
  - What do I have access to?
  - What does a teammate have access to that I don’t?
  - What groups and roles are available?
  - Can I request access?
- Team Leads
  - How do I give access to a new team member easily?
  - How do I give temporary access to an individual for a cross-functional effort?
  - Which roles do I administer?
  - How can I create, merge, or split a role based on a team re-org?
- Application Owners
  - Who has access to my application?
  - How do I setup access for a new application?
  - How do I create a new access group for my application?
  - How do I give a role access to one of my application's groups?

## Development Setup

Access is a React and Typescript single-page application (SPA) with a FastAPI backend that connects to the Okta API.

You'll need an Okta API Token from an Okta user with the `Group Admin` and `Application Admin`
Okta administrator roles granted as well as all Group permissions (ie. `Manage groups` checkbox checked)
in a custom Admin role. If you want to manage Groups which grant Okta Admin permissions, then the Okta API
Token will need to be created from an Okta user with the `Super Admin` Okta administrator role.

### Backend

Create a `.env` file in the repo root with the following variables:

```
CURRENT_OKTA_USER_EMAIL=<YOUR_OKTA_USER_EMAIL>
OKTA_DOMAIN=<YOUR_OKTA_DOMAIN> # For example, "mydomain.oktapreview.com"
OKTA_API_TOKEN=<YOUR_SANDBOX_API_TOKEN>
DATABASE_URI="sqlite:///access.db"
CLIENT_ORIGIN_URL=http://localhost:3000
VITE_API_SERVER_URL=
```

> **Note:** `VITE_API_SERVER_URL` is left empty so the frontend uses relative URLs. The Vite dev server proxies `/api` requests to the backend on port 6060.

Create the Python virtual environment. Access is built and tested against Python 3.13:

```
python3 -m venv venv
. venv/bin/activate
```

The included [Makefile](Makefile) wraps the rest. Install Python deps:

```
make dev
```

Seed the DB and create the built-in Access app:

```
make db-migrate
make db-init email=<YOUR_OKTA_USER_EMAIL>
```

Run the API on port 6060:

```
make run-backend
```

Go to [http://localhost:6060/api/users](http://localhost:6060/api/users) to view the API.

`make run` boots the backend and the Vite dev server together. `make help` lists every target (`sync`, `notify`, `db-revision msg=…`, `pytest`, etc.).

### Node

In a separate window, set up and run the React frontend:

```
make run-frontend
```

Go to [http://localhost:3000/](http://localhost:3000/) to view the React SPA.

#### Generating Typescript React Query API Client

We use [openapi-codegen](https://github.com/fabien0102/openapi-codegen) to generate a TypeScript React-Query (v5) API client — typed schemas, fetchers, and hooks — from our OpenAPI schema, which FastAPI auto-publishes at [http://localhost:6060/api/openapi.json](http://localhost:6060/api/openapi.json) when running in development mode (`ENV=development`). The codegen CLI and generator are pinned in `devDependencies` and the config in [openapi-codegen.config.ts](openapi-codegen.config.ts) points at that live URL, so to regenerate after a backend schema change just run the dev backend and then:

```
npm install
npm run codegen
```

Re-running codegen overwrites the generated files (`src/api/apiComponents.ts`, `apiSchemas.ts`) but preserves the hand-customized `apiFetcher.ts`, `apiContext.ts`, and `apiUtils.ts`.

A CI check ([openapi-client-drift.yml](.github/workflows/openapi-client-drift.yml)) regenerates the client from the spec on every PR and fails if `src/api/` is out of sync, so remember to commit the regenerated files when you change a route or schema. (That workflow dumps the spec to a file and points the config at it via the `OPENAPI_SPEC_FILE` env var, so it needs no running server.)

## Tests

```
make pytest          # run pytest
make test            # run ruff + mypy + pytest
```

Under the hood this calls tox (`tox -e test`, etc.); the Makefile is just a thin wrapper.

## Linting

```
make ruff
make mypy
```

## Production Setup

Create a `.env.production` file in the repo root with the following variables. Access supports running against PostgreSQL 14 and above.

```
OKTA_DOMAIN=<YOUR_OKTA_DOMAIN> # For example, "mydomain.okta.com"
OKTA_API_TOKEN=<YOUR_OKTA_API_TOKEN>
DATABASE_URI=<YOUR_DATABASE_URI> # For example, "postgresql+pg8000://postgres:postgres@localhost:5432/access"
CLIENT_ORIGIN_URL=http://localhost:3000
VITE_API_SERVER_URL=""
FASTAPI_SENTRY_DSN=https://<key>@sentry.io/<project>
REACT_SENTRY_DSN=https://<key>@sentry.io/<project>
```

### Google Cloud Cloud SQL Configuration

If you want to use the Cloud SQL Python Connector, set the following variables in your `.env.production` file:

```
CLOUDSQL_CONNECTION_NAME=<YOUR_CLOUDSQL_CONNECTION_NAME> # For example, "project:region:instance-name"
DATABASE_URI="postgresql+pg8000://"
DATABASE_USER=<YOUR_DATABASE_USER> # For a service account, this is the service account's email without the .gserviceaccount.com domain suffix.
DATABASE_NAME=<YOUR_DATABASE_NAME>
DATABASE_USES_PUBLIC_IP=[True|False]
```

### Authentication

Authentication is required when running Access in production. Currently, we support
[OpenID Connect (OIDC)](https://openid.net/developers/how-connect-works/) (including Okta)
and [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-apps/) as methods to authenticate users to Access.

#### OpenID Connect (OIDC)

To use OpenID Connect (OIDC) authentication, such as with Okta:

Go to your Okta Admin dashboard -> Applications -> Create App Integration.

In the Create a new app integration, select:
- Sign-in method: `OIDC - OpenID Connect`
- Application type: `Web Application`

Then on the New Web App Integration page:
- App integration name: `Access`
- Logo: (optional)
- Grant type:
  - Client acting on behalf of user: `Authorization Code`
- Sign-in redirect URIs: `https://<YOUR_ACCESS_DEPLOYMENT_DOMAIN_NAME>/oidc/authorize`
- Sign-out redirect URIs: `https://<YOUR_ACCESS_DEPLOYMENT_DOMAIN_NAME>/oidc/logout`

Then click `Save` and go to the General tab of the new app integration to find
the `Client ID` and `Client secret`. You'll need these for the next step.

Create a `client_secrets.json` file containing your OIDC client secrets, that looks something like the following:
```
{
  "secrets": {
    "client_id":"<YOUR_OKTA_APPLICATION_CLIENT_ID>",
    "client_secret":"<YOUR_OKTA_APPLICATION_CLIENT_SECRET>",
    "issuer": "https://<YOUR_OKTA_INSTANCE>.okta.com/"
  }
}
```

Then set the following variables in your `.env.production` file:
```
# Generate a secure secret key using `python -c 'import secrets; print(secrets.token_hex())'`
# this is used to sign the OIDC session cookie
SECRET_KEY=<YOUR_SECRET_KEY>
# The path to your client_secrets.json file or if you prefer, inline the entire JSON string
OIDC_CLIENT_SECRETS=./client_secrets.json or '{"secrets":..'
```

When deployed behind a proxy or load balancer, also pin the OIDC callback and
validate the Host header so the `redirect_uri` sent to your IdP cannot be
poisoned by a spoofed `Host`:
```
# Fixed callback URL; matches the Sign-in redirect URI registered above
OIDC_OVERWRITE_REDIRECT_URI=https://<YOUR_ACCESS_DEPLOYMENT_DOMAIN_NAME>/oidc/authorize
# Comma-separated Host header allowlist (required outside development/test)
ALLOWED_HOSTS=<YOUR_ACCESS_DEPLOYMENT_DOMAIN_NAME>
```

#### Cloudflare Access

To use Cloudflare Access authentication, set up a
[Self-Hosted Cloudflare Access Application](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-apps/)
using a Cloudflare Tunnel. Next, set the following variables in your `.env.production` file:

```
# Your Cloudflare "Team domain" under Zero Trust -> Settings -> Custom Pages in the Cloudflare dashboard
# For example, "mydomain.cloudflareaccess.com"
CLOUDFLARE_TEAM_DOMAIN=<CLOUDFLARE_ACCESS_TEAM_DOMAIN>
# Your Cloudflare "Audience" tag under Zero Trust -> Access -> Applications -> <Your Application> -> Overview in the Cloudflare dashboard
# found under "Application Audience (AUD) Tag"
CLOUDFLARE_APPLICATION_AUDIENCE=<CLOUDFLARE_ACCESS_AUDIENCE_TAG>
```

### Docker Build and Run

Build the Docker image:

```
docker build -t access .
```

Or build and run it using Docker Compose:

```
docker compose up --build
```

The command above will build and run the container.

Go to [http://localhost:3000/](http://localhost:3000/) to view the application.

### Docker configuration

Before launching the container with Docker, make sure to configure `.env.psql` and `.env.production`:

#### Configuration for `.env.psql`

The `.env.psql` file is where you configure the PostgreSQL server credentials, which is also Dockerized.

- `POSTGRES_USER`: Specifies the username for the PostgreSQL server.
- `POSTGRES_PASSWORD`: Specifies the password for the PostgreSQL server.

#### Configuration for `.env.production`

The `.env.production` file is where you configure the application.

- `OKTA_DOMAIN`: Specifies the [Okta](https://okta.com) domain to use.
- `OKTA_API_TOKEN`: Specifies the [Okta](https://okta.com) [API Token](https://developer.okta.com/docs/api/openapi/okta-management/management/tag/ApiToken/) to use.
- `DATABASE_URI`: Specifies the Database connection URI. **Example:** `postgresql+pg8000://<POSTGRES_USER>:<POSTGRES_PASSWORD>@postgres:5432/<DB_NAME>`.
- `CLIENT_ORIGIN_URL`: Specifies the origin URL used by plugins (e.g. for building notification URLs).
- `VITE_API_SERVER_URL`: Specifies the API base URL which is used by the frontend. Set to an empty string "" to use the same URL as the frontend.
- `FASTAPI_SENTRY_DSN`: See the [Sentry documentation](https://docs.sentry.io/product/sentry-basics/concepts/dsn-explainer/). **[OPTIONAL] You can safely remove this from your env file**
- `REACT_SENTRY_DSN`: See the [Sentry documentation](https://docs.sentry.io/product/sentry-basics/concepts/dsn-explainer/). **[OPTIONAL] You can safely remove this from your env file**
- `CLOUDFLARE_TEAM_DOMAIN`: Specifies the Team Domain used by [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/).
- `CLOUDFLARE_APPLICATION_AUDIENCE`: Specifies the Audience Tag used by [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/).
- `SECRET_KEY`: Specifies the secret key used to sign the OIDC session cookie. WARNING: Ensure this is something secure you can generate a good secret key using `python -c 'import secrets; print(secrets.token_hex())'`.
- `OIDC_CLIENT_SECRETS`: Specifies the path to your client_secrets.json file or if you prefer, inline the entire JSON string.
- `ALLOWED_HOSTS`: **[REQUIRED for OIDC deployments outside development/test; recommended otherwise]** Comma-separated allowlist of `Host` header values accepted by the app (wildcards like `*.example.com` supported). Rejects spoofed `Host` headers, which would otherwise poison URLs derived from the request (notably the OIDC `redirect_uri`). Cloudflare Access deployments don't hit that path and aren't required to set it, but it remains useful defense-in-depth. Set to your public host, e.g. `access.example.com`.
- `OIDC_OVERWRITE_REDIRECT_URI`: **[OPTIONAL, recommended for OIDC behind a proxy]** Pins the OIDC callback URL handed to your IdP instead of deriving it from the request `Host` header. Set to your registered sign-in redirect URI, e.g. `https://access.example.com/oidc/authorize`.
- `ENABLE_MCP`: **[OPTIONAL]** Set to `true` to mount the embedded Model Context Protocol server at `/mcp`. Off by default. See [MCP Server (optional)](#mcp-server-optional) below.
- `MCP_FALLBACK_SCOPES`: **[OPTIONAL]** Comma-separated scopes granted to MCP tokens that carry no `scope` claim. Defaults to `read_all,create_requests` (read + filing requests). Set to `read_all` for read-only MCP sessions, or `""` to fail closed. Only relevant when `ENABLE_MCP=true`.
- `OIDC_MCP_AUDIENCE`: **[REQUIRED when `ENABLE_MCP=true` and `OIDC_SERVER_METADATA_URL` is set]** The OAuth audience to validate against the `aud` claim on incoming MCP bearer tokens. Typically the OAuth client identifier of the MCP application registered with your IdP, e.g. `access-mcp`.
- `MCP_RESOURCE_URL`: **[OPTIONAL]** Canonical public URL of the MCP resource (e.g. `https://access.example.com/mcp`), published in the RFC 9728 metadata document and the 401 `resource_metadata` pointer. Derived from the request when unset; set it explicitly behind a proxy that rewrites Host. Only relevant when `ENABLE_MCP=true`.

**Check out `.env.psql.example` or `.env.production.example` for an example configuration file structure**.

**NOTE:**

If you are using Cloudflare Access, ensure that you configure `CLOUDFLARE_TEAM_DOMAIN` and `CLOUDFLARE_APPLICATION_AUDIENCE`. `SECRET_KEY` and `OIDC_CLIENT_SECRETS` do not need to be set and can be removed from your env file.

Else, if you are using a generic OIDC identity provider (such as Okta), then you should configure `SECRET_KEY` and `OIDC_CLIENT_SECRETS`. `CLOUDFLARE_TEAM_DOMAIN` and `CLOUDFLARE_APPLICATION_AUDIENCE` do not need to be set and can be removed from your env file. Make sure to also mount your `client-secrets.json` file to the container if you don't have it inline.

### Access application configuration

_All front-end and back-end configuration overrides are **optional**._

The default config for the application is at [`config/config.default.json`](config/config.default.json).

The file is structured with two keys, `FRONTEND` and `BACKEND`, which contain the configuration overrides for the
front-end and back-end respectively.

If you want to override either front-end or back-end values, create your own config file based on 
[`config/config.default.json`](config/config.default.json). Any values that you don't override will fall back to 
the values in the default config.

To use your custom config file, set the `ACCESS_CONFIG_FILE` environment variable to the name of your config
override file in the project-level `config` directory.

### Sample Usage

To override environment variables, create an override config file in the `config` directory. (You can name
this file whatever you want because the name of the file is specified by your `ACCESS_CONFIG_FILE` environment
variable.)

For example, if you want to set the default access time to 5 days in production, you might create a file named
`config.production.json` in the `config` directory:

```json
{
  "FRONTEND": {
    "DEFAULT_ACCESS_TIME": "432000"
  }
}
```

Then, in your `.env.production` file, set the `ACCESS_CONFIG_FILE` environment variable to the name of your
config file:

```
ACCESS_CONFIG_FILE=config.production.json
```

This tells the application to use `config.production.json` for configuration overrides.

#### Frontend Configuration

To override values on the front-end, modify these key-value pairs inside the `FRONTEND` key in your custom config file.

| Name                      | Details                                                                                                                                                                                                  | Example                                                        |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| `ACCESS_TIME_LABELS`      | Specifies the time access labels to use for dropdowns on the front end. Contains a JSON object of the format `{"NUM_SECONDS": "LABEL"}`.                                                                 | `{"86400": "1 day", "604800": "1 week", "2592000": "1 month"}` |
| `DEFAULT_ACCESS_TIME`     | Specifies the default time access label to use for dropdowns on the front end. Contains a string with a number of seconds corresponding to a key in the access time labels.                              | `"86400"`                                                      |
| `NAME_VALIDATION_PATTERN` | Specifies the regex pattern to use for validating role, group, and tag names.  Should include preceding `^` and trailing `$` but is not a regex literal so omit `/`  at beginning and end of the pattern | `"^[a-zA-Z0-9-]*$"`                                            |
| `NAME_VALIDATION_ERROR`   | Specifies the error message to display when a name does not match the validation pattern.                                                                                                                | `"Name must contain only letters, numbers, and underscores."`  |

The front-end config is loaded in [`vite.config.ts`](vite.config.ts). See
[`src/config/loadAccessConfig.js`](src/config/loadAccessConfig.js) for more details.

#### Backend Configuration

To override values on the back-end, modify these key-value pairs inside the `BACKEND` key in your custom config file.

| Name                      | Details                                                                                                                                                                                            | Example                                                                                |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| `NAME_VALIDATION_PATTERN` | PCRE regex used for validating role, group, and tag names. Should not explicitly declare pattern boundaries: depending on context, may be used with or without a preceding `^` and a trailing `$`. | `[A-Z][A-Za-z0-9-]*`                                                                   |
| `NAME_VALIDATION_ERROR`   | Error message to display when a name does not match the validation pattern.                                                                                                                        | `Name must start with a capital letter and contain only letters, numbers, and hypens.` |

The back-end config is loaded in [`api/access_config.py`](api/access_config.py).

See [`api/schemas/core_schemas.py`](api/schemas/core_schemas.py) for details about how the pattern override
supplied here will be used.

#### Database Setup

After `docker compose up --build`, you can run the following commands to setup the database:

Create the database in the postgres container:
```
docker compose exec postgres createdb -U <POSTGRES_USER> <DB_NAME>
```

Run the initial migrations and seed the initial data from Okta:
```
docker compose exec discord-access /bin/bash
```

Then run the following commands inside the container:

```
alembic upgrade head
access init <YOUR_OKTA_USER_EMAIL>
```

Visit [http://localhost:3000/](http://localhost:3000/) to view your running version of Access!

### Kubernetes Deployment and CronJobs

As Access is a web application packaged with Docker, it can easily be deployed to a Kubernetes cluster. We've included example Kubernetes yaml objects you can use to deploy Access in the [examples/kubernetes](https://github.com/discord/access/tree/main/examples/kubernetes) directory.

These examples include a Deployment, Service, Namespace, and Service Account object for serving the stateless web application. Additionally there are examples for deploying the `access sync` and `access notify` commands as cronjobs to periodically synchronize users, groups, and their memberships and send expiring access notifications respectively.

## MCP Server (optional)

Access can embed a [Model Context Protocol](https://modelcontextprotocol.io/) server alongside the REST API so that MCP-compatible LLM clients (Claude Code, Claude.ai, Cursor, Zed, self-hosted models, …) can browse groups, roles, apps, and requests, and file access requests on the authenticated user's behalf. The feature is **off by default** — operators who don't run LLM tooling pay nothing at runtime.

### Enabling

Set `ENABLE_MCP=true` in your `.env.production`. When the flag is on, Access mounts the FastMCP server at `POST /mcp` and activates the MCP auth middleware. When off, the `/mcp` route is not registered and the MCP code path is never imported.

`mcp[cli]` is pinned in [`requirements.txt`](requirements.txt) and imported unconditionally; only the runtime wiring is gated by the flag.

### Tool surface (v1)

21 tools and one prompt. Reads (`list_*` / `get_*`) cover groups, roles, apps, users, tags, audit entries, group memberships, and all three request types. Writes are limited to filing **pending** requests — approval, rejection, and direct mutation of groups/roles/apps are intentionally **not** exposed via MCP:

- `create_access_request` — user requests membership or ownership for themselves
- `create_role_request` — role owner requests that a role be granted access to a group
- `create_group_request` — user requests creation of a new group, role, or app group

Every tool runs the same authorization predicate and operation pipeline the matching REST endpoint uses, so MCP cannot grant an LLM agent more than the user it's authenticating as.

### Scopes (`MCP_FALLBACK_SCOPES`)

Two coarse scopes:

| Scope             | Required by                                                            |
|-------------------|------------------------------------------------------------------------|
| `read_all`        | every `list_*` / `get_*` tool                                          |
| `create_requests` | `create_access_request`, `create_role_request`, `create_group_request` |

When an MCP token carries an explicit `scope` (space-separated) or `scp` (list) claim, that set controls the session. When the claim is absent — the typical case under Cloudflare Managed OAuth today, which does not currently issue scope claims — the operator-configured `MCP_FALLBACK_SCOPES` value is applied. Three meaningful settings:

- `MCP_FALLBACK_SCOPES=read_all,create_requests` *(default)* — read tools plus the three write tools (`create_access_request`, `create_role_request`, `create_group_request`). Per-tool authorization still applies, so users get no capability beyond what they have via REST.
- `MCP_FALLBACK_SCOPES=read_all` — read-only sessions; LLM agents can browse but cannot file any request via MCP.
- `MCP_FALLBACK_SCOPES=""` — fail-closed; tokens with no scope claim cannot call any tool. The right setting once your provider starts emitting scope claims.

Scopes attenuate — they never grant. A token with `create_requests` still cannot file a role request for a role the user does not own; the per-tool authorization check fires after the scope check.

### Authentication

MCP ships with two built-in auth providers: **Cloudflare Access** and **OIDC**. Each opts in automatically when its config is set. They are mutually exclusive for the MCP surface — the app refuses to start if both `CLOUDFLARE_TEAM_DOMAIN` and `OIDC_SERVER_METADATA_URL` are configured with `ENABLE_MCP=true`.

Both providers do **credential verification** only. Access is a *resource server*: it verifies bearer tokens and publishes discovery metadata (see below), but the OAuth/OIDC flow — `/authorize`, `/token`, dynamic client registration, callbacks — runs on the operator's authorization server (a Cloudflare-Access-style proxy or your IdP), not in Access.

**Cloudflare Access.** Activates when `CLOUDFLARE_TEAM_DOMAIN` is set. Reads the CF-issued JWT from `Cf-Access-Jwt-Assertion`, `Cf-Access-Token`, or `Authorization: Bearer`, verifies it via `verify_cloudflare_token`, and resolves the `email` claim to an `OktaUser`. CF deployments using [Managed OAuth for Access](https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/mcp-servers/) need no extra wiring — enable Managed OAuth on the Access application in the CF dashboard and any MCP-compliant client connects with just the `/mcp` URL.

**OIDC.** Activates when `OIDC_SERVER_METADATA_URL` is set. Reads an OIDC bearer token from `Authorization: Bearer`, fetches the IdP's JWKS via the discovery document, and verifies signature, `iss`, `exp`, and `aud` against `OIDC_MCP_AUDIENCE`. `OIDC_MCP_AUDIENCE` is **required** when OIDC is enabled — skipping audience validation would let a token issued for another resource server authenticate to Access MCP. The MCP OIDC integration is intentionally different from the REST OIDC integration: REST uses a browser session-cookie flow (`api/auth/oidc.py`), MCP uses bearer-token verification (`api/mcp/auth/oidc.py`), because MCP clients aren't browsers and the MCP OAuth spec uses bearer tokens.

For local development there's also a dev provider that activates when `ENV` is `development` or `test`. It resolves `CURRENT_OKTA_USER_EMAIL` to an `OktaUser` and grants the full v1 scope set, so you can exercise tools locally without faking a token.

When every provider defers (no credential present, or the credential is invalid), the MCP middleware emits a `401` whose `WWW-Authenticate` header carries an RFC 9728 `resource_metadata` pointer (see below).

### Client discovery (RFC 9728)

To let a spec-compliant client connect cold with just the `/mcp` URL, Access implements the resource-server half of the [MCP authorization flow](https://modelcontextprotocol.io/docs/tutorials/security/authorization):

- The `401` challenge advertises `WWW-Authenticate: Bearer realm="access", resource_metadata="<url>"`.
- That URL serves a [Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728) document at `/.well-known/oauth-protected-resource` (and `/.well-known/oauth-protected-resource/mcp`), listing the `resource`, the `authorization_servers` (derived from `OIDC_SERVER_METADATA_URL` / `CLOUDFLARE_TEAM_DOMAIN`), and the supported scopes. It is served unauthenticated so a token-less client can read it.

The client then discovers the authorization server, registers, and runs the `/authorize` + `/token` dance against *that* server — Access never sees those steps. Set `MCP_RESOURCE_URL` to your public `/mcp` URL when running behind a proxy that rewrites Host or terminates TLS; otherwise it's derived from the request. (Cloudflare Managed OAuth handles discovery at the edge, so CF deployments don't depend on this.)

### Audit logging

Every MCP-originated mutation is tagged with `source: "mcp"` in the audit log payload (REST traffic is tagged `source: "web"`). The tag is injected automatically from the active request context — no per-tool plumbing — so an admin investigating an incident can distinguish agent activity from interactive use.

### Endpoint

  `POST https://<your-access-deployment>/mcp`

Standard MCP Streamable HTTP. Any MCP-compliant client connects with just this URL plus the auth credential your registered provider expects.

### Local development

A dev MCP auth provider ships in `api/mcp/auth/dev.py` and activates only when `ENV` is `development` or `test`. In those modes it resolves `CURRENT_OKTA_USER_EMAIL` to an `OktaUser` and grants the full v1 scope set — the same shortcut the REST path takes in `api/auth/dependencies.py`. The Cloudflare provider stays registered but opts out automatically when `CLOUDFLARE_TEAM_DOMAIN` is unset, so the two don't compete.

To exercise the MCP server locally, add to your `.env`:

```
ENABLE_MCP=true
```

Then run `make run-backend` and point an MCP client (Claude Code, mcp-inspector, etc.) at `http://localhost:6060/mcp` with no auth credential. The dev provider grants both `read_all` and `create_requests`, so every tool is reachable.

This path is gated on `ENV` and explicitly defers in any production-style environment, so the dev provider is safe to ship alongside the Cloudflare default.

## Plugins

Access uses the [Python pluggy framework](https://pluggy.readthedocs.io/en/latest/) to allow for new functionality to be added to the system. Plugins are Python packages that are installed into the Access Docker container. For example, a notification plugin could add a new type of notification such as Email, SMS, or a Discord message for when new access requests are made and resolved.

### Creating a Plugin

Plugins in Access follow the conventions defined by the [Python pluggy framework](https://pluggy.readthedocs.io/en/latest/). Access defines several hook specifications you can implement; runnable example plugins for each live under [examples/plugins](https://github.com/discord/access/tree/main/examples/plugins):

| Plugin type | Hook spec | Example(s) |
|-------------|-----------|------------|
| **Notifications** — send messages (Email, SMS, Discord, Slack, …) when access requests are made and resolved | [`NotificationPluginSpec`](https://github.com/discord/access/blob/main/api/plugins/notifications.py) | [notifications](https://github.com/discord/access/tree/main/examples/plugins/notifications), [notifications_slack](https://github.com/discord/access/tree/main/examples/plugins/notifications_slack) |
| **Conditional access** — conditionally approve or deny requests | [`ConditionalAccessPluginSpec`](https://github.com/discord/access/blob/main/api/plugins/conditional_access.py) | [conditional_access](https://github.com/discord/access/tree/main/examples/plugins/conditional_access) |
| **Metrics reporter** — emit counters, gauges, histograms, and timers to an external metrics backend | [`MetricsReporterPluginSpec`](https://github.com/discord/access/blob/main/api/plugins/metrics_reporter.py) | [datadog_metrics_reporter](https://github.com/discord/access/tree/main/examples/plugins/datadog_metrics_reporter) |
| **App group lifecycle** — expose configurable properties and react to the creation/deletion of app-owned groups | [`AppGroupLifecyclePluginSpec`](https://github.com/discord/access/blob/main/api/plugins/app_group_lifecycle.py) | [app_group_lifecycle_audit_logger](https://github.com/discord/access/tree/main/examples/plugins/app_group_lifecycle_audit_logger) |

Plugins can also extend the `access` CLI with new commands via setuptools entry points — see [health_check_plugin](https://github.com/discord/access/tree/main/examples/plugins/health_check_plugin), which adds an `access health` command.

### Installing a Plugin in the Docker Container

Below is an example Dockerfile that would install the example notification plugin into the Access Docker container, which was built above using the top-level application [Dockerfile](https://github.com/discord/access/blob/main/Dockerfile). The plugin is installed into the `/app/plugins` directory and then installed using pip.

```Dockerfile
FROM access:latest

WORKDIR /app/plugins
ADD ./examples/plugins/ ./

RUN pip install ./notifications

WORKDIR /app
```

## License

```
Copyright (C) 2024 Discord Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

For code dependencies, libraries, and frameworks used by this project that are dual-licensed or allow the option under their terms to select either the Apache Version 2.0 License, MIT License, or BSD 3-Clause License, this project selects those licenses for use of those dependencies in that order of preference.
