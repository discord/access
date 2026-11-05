# Access — Local development, testing, and migrations

Companion to `.claude/CLAUDE.md`. Read this when running the app locally, writing or running
tests, or creating database migrations.

The toolchain is `uv` (dependency + virtualenv management), `ruff` (lint/format), and `ty`
(Astral's type checker), all wrapped by a thin `Makefile`. There is no `tox`, `pip`,
`requirements.txt`, or `mypy` anymore — those were removed. See the README for full setup.

## Local development

`uv` reads the pinned interpreter from `.python-version` (**Python 3.13**) and downloads it
automatically — there's no manual `venv` step.

```bash
make dev          # uv sync — create .venv and install locked deps + the `access` CLI
make db-migrate   # apply migrations (alembic upgrade head) against the local sqlite DB
make db-init email=<you@example.com>   # seed an initial admin
make run-backend  # start the API
make shell        # access shell — REPL with the app context bootstrapped
```

To run a one-off command inside the environment, prefix it with `uv run` (e.g.
`uv run alembic ...`, `uv run pytest ...`). CI installs with `uv sync --locked` and fails if the
lockfile is out of sync (`uv sync --locked --check`).

## Testing, linting, type checking

```bash
make test      # ruff + ty + pytest (the full gate)
make pytest    # uv run pytest — the suite alone
make ruff      # uv run ruff check . && uv run ruff format --check .
make ty        # uv run ty check .  — don't skip; type annotations are load-bearing
```

Ruff and ty are configured in `pyproject.toml` (`[tool.ruff]`, `[tool.ty.*]`); pytest config is
in `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`, since the suite is async).

The pytest suite runs against **in-memory SQLite** by default (`sqlite+aiosqlite://`,
built from the models via `Base.metadata.create_all`, not migrations). To run against Postgres,
set `TEST_DATABASE_URI`, or use `make pytest-postgres`, which boots a disposable `postgres:16`
container. CI runs the suite twice — SQLite then Postgres.

Test data is created via factories in `tests/factories.py`. Don't write raw ORM inserts in
tests — use factories.

Frontend tests are vitest + React Testing Library: `npm test` watches, `npx vitest run` is the
one-shot CI form. Coverage is partial — a handful of suites under `src/`, concentrated on pure
helpers and form logic rather than page rendering — so treat an untested component as normal here
and add a suite alongside a change rather than assuming one exists to update.

`npm run check:engines` fails when the root `engines.node` admits a Node version that some
package in `package-lock.json` doesn't support; CI runs it in the `node-lint` job. When a
dependency bump raises its own floor, narrow `engines.node` to the versions every package
supports, then refresh the lockfile's root metadata with the npm version CI pins.

## Visual diff

`npm run visual-diff` renders every page in `src/App.tsx` on the current branch and on its merge
base with `origin/main`, then reports what changed. tsc and vitest don't render pages, so this is
how to check a change that could alter what users see without clicking through every page by
hand. Use it for:

- frontend dependency upgrades (MUI, MUI X, React, React Router, Emotion), and
- layout, theme, or shared-component changes that touch many pages at once.

It is opt-in and local, not a CI check. Nothing it produces is checked in: both sides render on
your machine in the same run, so there are no stored baselines and operating-system font
differences don't matter.

```bash
npm run visual-diff                        # diff against the merge base with origin/main
npm run visual-diff -- --base upstream/main  # diff against a different ref's merge base
npm run visual-diff -- --open              # serve the HTML report when done
```

It prints the pages whose screenshots changed, the pages that are new on the branch, and the
errors that appear only on the branch: uncaught exceptions, `console.error` output (React and MUI
warnings included; the frontend is built in development mode so they aren't stripped), failed
`/api/*` requests, and pages that render the not-found or error page. It exits 1 only when the
branch has new errors. A screenshot diff alone exits 0, since it may be the point of the change.
The Playwright HTML report under `.visual-diff/report/` shows the expected, actual, and diff
images for each changed page; `.visual-diff/summary.json` has the full error text and
`.visual-diff/logs/` has every build, backend, and Playwright log.

How it works (`scripts/visual-diff/`):

- **The base runs in a temporary `git worktree`** of `git merge-base HEAD <base>`, installed with
  `npm ci --ignore-scripts` so husky can't rewrite the shared `core.hooksPath`. The current branch
  is built from the working tree as it stands, uncommitted changes included. Both builds go to
  temporary directories, so the checkout's `build/` is untouched.
- **Each side gets its own SQLite database, backend, and static server on free ports.** The
  backends run in a virtualenv built from that side's `uv.lock` without dev dependencies, from a
  scratch directory, with a minimal environment, so neither the checkout's `.env` nor plugins
  installed in its `.venv` leak in. A dev server already running on 3000 or 6060 is left alone.
- **Both sides are seeded by `seed.py` from the current branch** with fixed ids
  (`fixtures.json`) and timestamps offset from midnight UTC of the run. The browser clock is frozen
  relative to the same point, so relative dates ("in 5 days") match. Nothing calls Okta.
- **One spec, `pages.spec.ts` from the current branch, renders both sides.** The base run writes
  the screenshots and the branch run compares against them. A route the branch adds renders the
  not-found page on the base, so no expectation is written and the page reports as new rather than
  changed. The spec fails the run when `src/App.tsx` has a `<Route>` it has no case for, so add
  a case (and seed whatever it needs) when you add a page.
- **The base's install and build are cached by commit**, and virtualenvs by lockfile, under
  `${XDG_CACHE_HOME:-~/.cache}/access-visual-diff`. The first run against a new merge base takes
  about 35 seconds on a laptop with warm npm and uv caches; later runs take about 25. The first
  run on a machine also downloads Playwright's Chromium.
- Ctrl-C stops every process it started and removes the worktree and temporary files.

Up to 100 differing pixels per page count as noise: two launches of the same headless Chromium
still antialias a few glyphs a shade differently. Any real change to spacing, layout, or color
moves far more than that.

## The generated API client must not drift

`src/api/` is generated from the backend's OpenAPI spec and is checked in. The
`openapi-client-drift` CI job regenerates it and fails if the result differs, so **any change to a
Pydantic response/request schema needs the client regenerated in the same commit** — including a
single new optional field. Never hand-edit `apiSchemas.ts` or `apiComponents.ts`.

Locally, `npm run codegen` reads the spec from a running dev backend at port 6060. To regenerate
without one (and exactly as CI does), dump the spec offline first:

```bash
uv run python -c "
import json
from api.app import create_app
with open('openapi.json', 'w') as f:
    json.dump(create_app(testing=True).openapi(), f)
"
OPENAPI_SPEC_FILE=openapi.json npm run codegen && npx prettier --write 'src/api/**/*.{ts,tsx}'
```

`create_app(testing=True)` needs no DB or env. The prettier pass matches what the pre-commit hook
does, so skipping it shows up as spurious drift.

## Database migrations

```bash
make db-revision msg="<message>"   # generate a new migration (alembic revision --autogenerate)
make db-migrate                    # apply migrations (alembic upgrade head)
make db-downgrade                  # alembic downgrade -1
make db-current / make db-history  # inspect state
```

(These wrap `uv run alembic ...`.) Migrations live in `migrations/versions/`. After generating
one, review the file before applying — autogenerate is good but not perfect.

CI exercises the migrations against **both** Postgres and SQLite on every PR: it enforces a
single migration head, applies `alembic upgrade head` from empty, runs `alembic check` (the
models and migrations must be in sync — a model change without a matching migration fails
here), and verifies the newest migration is reversible (`alembic downgrade -1` then re-upgrade).
So a new migration must apply cleanly on both engines and ship a working `downgrade()`.
Dialect-specific DDL (e.g. Postgres native enum types, which don't exist on SQLite) must guard
on `op.get_bind().dialect.name`.

## Okta sandbox

For local dev against a real Okta instance, use an Okta preview/sandbox org (e.g. an
`*.oktapreview.com` tenant) rather than production. Ask your Okta administrator to set up an
account if you don't have one. Use `OKTA_DOMAIN` to set your sandbox/preview org domain
and `OKTA_API_TOKEN` to set an API token minted in that sandbox org for Access to use.
