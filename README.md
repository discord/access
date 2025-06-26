<p align="center"><img src="https://raw.githubusercontent.com/discord/access/main/public/logo.png" width="350"></p>

# ACCESS

Meet Access, a centralized portal for employees to transparently discover, request, and manage their access for all internal systems needed to do their jobs. If you're interested in the project, come chat with us!

<p align="center"><a href="https://discord.gg/access-enjoyers"><img src="https://discordapp.com/api/guilds/1232815453907189922/widget.png?style=banner2" alt="Join our Discord!"></a></p>

## Purpose

The access service exists to help answer the following questions for each persona:

- All Users
  - What do I have access to?
  - What does a teammate have access to that I don’t?
  - What groups and roles are available?
  - Can I get access?
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

Access is a React and Typescript single-page application (SPA) with a Flask API that connects to the Okta API.

You'll need an Okta API Token from an Okta user with the `Group Admin` and `Application Admin`
Okta administrator roles granted as well as all Group permissions (ie. `Manage groups` checkbox checked)
in a custom Admin role. If you want to manage Groups which grant Okta Admin permissions, then the Okta API
Token will need to be created from an Okta user with the `Super Admin` Okta administrator role.

### Flask

Create a `.env` file in the repo root with the following variables:

```
CURRENT_OKTA_USER_EMAIL=<YOUR_OKTA_USER_EMAIL>
OKTA_DOMAIN=<YOUR_OKTA_DOMAIN> # For example, "mydomain.oktapreview.com"
OKTA_API_TOKEN=<YOUR_SANDBOX_API_TOKEN>
DATABASE_URI="sqlite:///access.db"
CLIENT_ORIGIN_URL=http://localhost:3000
REACT_APP_API_SERVER_URL=http://localhost:6060
```

Next, run the following commands to set up your python virtual environment. Access can be run with Python 3.11 and above:

```
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

Afterwards, seed the db:

```
flask db upgrade
flask init <YOUR_OKTA_USER_EMAIL>
```

Finally, you can run the server:

```
flask run
```

Go to [http://localhost:6060/api/users](http://localhost:6060/api/users) to view the API.

### Node

In a separate window, setup and run nodejs:

```
npm install
```

```
npm start
```

Go to [http://localhost:3000/](http://localhost:3000/) to view the React SPA.

#### Generating Typescript React-Query API Client

We use [openapi-codegen](https://github.com/fabien0102/openapi-codegen) to generate a Typescript React-Query v4 API Fetch Client based on our Swagger API schema available at [http://localhost:6060/api/swagger.json](http://localhost:6060/api/swagger.json). We've modified that generated Swagger schema in [api/swagger.json](api/swagger.json), which is then used in [openapi-codegen.config.ts](openapi-codegen.config.ts) by the following commands:

```
npm install @openapi-codegen/cli
npm install @openapi-codegen/typescript
npm install --only=dev
npx openapi-codegen gen api
```

## Tests

We use tox to run our tests, which should be installed into the python venv from
our `requirements.txt`.

Invoke the tests using `tox -e test`.

## Linting

Run `tox -e ruff` and `tox -e mypy` to run the linters.

## Production Setup

Create a `.env.production` file in the repo root with the following variables. Access supports running against PostgreSQL 14 and above.

```
OKTA_DOMAIN=<YOUR_OKTA_DOMAIN> # For example, "mydomain.okta.com"
OKTA_API_TOKEN=<YOUR_OKTA_API_TOKEN>
DATABASE_URI=<YOUR_DATABASE_URI> # For example, "postgresql+pg8000://postgres:postgres@localhost:5432/access"
CLIENT_ORIGIN_URL=http://localhost:3000
REACT_APP_API_SERVER_URL=""
FLASK_SENTRY_DSN=https://<key>@sentry.io/<project>
REACT_SENTRY_DSN=https://<key>@sentry.io/<project>
```

### Google Cloud CloudSQL Configuration

If you want to use the CloudSQL Python Connector, set the following variables in your `.env.production` file:

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
# Generate a good secret key using `python -c 'import secrets; print(secrets.token_hex())'`
# this is used to encrypt Flask cookies
SECRET_KEY=<YOUR_SECRET_KEY>
# The path to your client_secrets.json file or if you prefer, inline the entire JSON string
OIDC_CLIENT_SECRETS=./client_secrets.json or '{"secrets":..'
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
CLOUDFLARE_APPLICATION_AUDIENCE=<CLOUFLARE_ACCESS_AUDIENCE_TAG>
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
- `CLIENT_ORIGIN_URL`: Specifies the origin URL which is used by CORS.
- `REACT_APP_API_SERVER_URL`: Specifies the API base URL which is used by the frontend. Set to an empty string "" to use the same URL as the frontend.
- `FLASK_SENTRY_DSN`: See the [Sentry documentation](https://docs.sentry.io/product/sentry-basics/concepts/dsn-explainer/). **[OPTIONAL] You can safely remove this from your env file**
- `REACT_SENTRY_DSN`: See the [Sentry documentation](https://docs.sentry.io/product/sentry-basics/concepts/dsn-explainer/). **[OPTIONAL] You can safely remove this from your env file**
- `CLOUDFLARE_TEAM_DOMAIN`: Specifies the Team Domain used by [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/).
- `CLOUDFLARE_APPLICATION_AUDIENCE`: Specifies the Audience Tag used by [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/).
- `SECRET_KEY`: Specifies the secret key used to encrypt flask cookies. WARNING: Ensure this is something secure you can generate a good secret key using `python -c 'import secrets; print(secrets.token_hex())'`.
- `OIDC_CLIENT_SECRETS`: Specifies the path to your client_secrets.json file or if you prefer, inline the entire JSON string.

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

The front-end config is loaded in [`craco.config.js`](craco.config.js). See
[`src/config/loadAccessConfig.js`](src/config/loadAccessConfig.js) for more details.

#### Backend Configuration

To override values on the back-end, modify these key-value pairs inside the `BACKEND` key in your custom config file.

| Name                      | Details                                                                                                                                                                                            | Example                                                                                |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------|
| `NAME_VALIDATION_PATTERN` | PCRE regex used for validating role, group, and tag names. Should not explicitly declare pattern boundaries: depending on context, may be used with or without a preceding `^` and a trailing `$`. | `[A-Z][A-Za-z0-9-]*`                                                                   |
| `NAME_VALIDATION_ERROR`   | Error message to display when a name does not match the validation pattern.                                                                                                                        | `Name must start with a capital letter and contain only letters, numbers, and hypens.` |

The back-end config is loaded in [`api/access_config.py`](api/access_config.py).

See [`api/views/schemas/core_schemas.py`](api/views/schemas/core_schemas.py) for details about how the pattern override
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
flask db upgrade
flask init <YOUR_OKTA_USER_EMAIL>
```

Visit [http://localhost:3000/](http://localhost:3000/) to view your running version of Access!

### Kubernetes Deployment and CronJobs

As Access is a web application packaged with Docker, it can easily be deployed to a Kubernetes cluster. We've included example Kubernetes yaml objects you can use to deploy Access in the [examples/kubernetes](https://github.com/discord/access/tree/main/examples/kubernetes) directory.

These examples include a Deployment, Service, Namespace, and Service Account object for serving the stateless web application. Additionally there are examples for deploying the `flask sync` and `flask notify` commands as cronjobs to periodically synchronize users, groups, and their memberships and send expiring access notifications respectively.

## Plugins

Access uses the [Python pluggy framework](https://pluggy.readthedocs.io/en/latest/) to allow for new functionality to be added to the system. Plugins are Python packages that are installed into the Access Docker container. For example, a notification plugin could add a new type of notification such as Email, SMS, or a Discord message for when new access requests are made and resolved.

### Creating a Plugin

Plugins in Access follow the conventions defined by the [Python pluggy framework](https://pluggy.readthedocs.io/en/latest/).

An example implementation of a notification plugin is included in [examples/plugins/notifications](https://github.com/discord/access/tree/main/examples/plugins/notifications), which can be extended to send messages using custom Python code. It implements the `NotificationPluginSpec` found in [notifications.py](https://github.com/discord/access/blob/main/api/plugins/notifications.py)

There's also an example implementation of a conditional access plugin in [examples/plugins/conditional_access](https://github.com/discord/access/tree/main/examples/plugins/conditional_access), which can be extended to conditionally approve or deny requests. It implements the `ConditionalAccessPluginSpec` found in [requests.py](https://github.com/discord/access/blob/main/api/plugins/conditional_access.py).

### Installing a Plugin in the Docker Container

Below is an example Dockerfile that would install the example notification plugin into the Access Docker container, which was built above using the top-level application [Dockerfile](https://github.com/discord/access/blob/main/Dockerfile). The plugin is installed into the `/app/plugins` directory and then installed using pip.

```Dockerfile
FROM access:latest

WORKDIR /app/plugins
ADD ./examples/plugins/ ./

RUN pip install ./notifications

WORKDIR /app
```

## TODO

Here are some of the features we're potentially planning to add to Access:

- A Group Lifecycle and User Lifecycle plugin framework
- Support for Google Groups and Github Teams via Group Lifecycle plugins
- Group (and Role) creation requests
- Role membership requests, so Role owners can request to add their Role to a Group
- OktaApp model with many-to-many relationship to App for automatically assigning AppGroups to Okta application tiles
- A webhook to synchronize group memberships and disabling users in real-time from Okta

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
