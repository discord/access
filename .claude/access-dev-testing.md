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

## Changed-line coverage gate

Every PR must cover at least **90%** of the Python lines it changes, or the `python-test` job in
`ci.yml` fails. The gate measures only the lines the PR touches, not the repo total, so it never
asks an author to backfill tests for code they didn't change.

How it works: both pytest runs (SQLite, then Postgres) record coverage with `pytest-cov` into one
data file, so a line exercised on only one engine counts. `diff-cover` then compares that against
`git diff origin/<base>...HEAD` (the PR's own changes since it branched).

- **What is measured** — `api/` and `examples/plugins/` (`[tool.coverage.run] source` in
  `pyproject.toml`). The example plugins count because they run in the default pytest scope and are
  the code operators copy. Tests, `migrations/` (covered by the `db-migrations.yml` workflow, not
  pytest), and every non-Python file are ignored, so a PR touching none of `api/` or
  `examples/plugins/` passes with "No lines with coverage information in this diff."
- **Renames and moves** — `git diff` detects renames, so a pure rename or a file move adds no
  changed lines. Whitespace-only changes (re-indenting a block) are ignored too. Code moved *within
  or between files* still shows as added lines, so moving an untested function makes it count.
- **Threshold** — `[tool.diff_cover] fail_under` in `pyproject.toml`. 90 rather than 100 leaves room
  for the odd defensive branch that's not worth a test; anything lower lets a PR land with a whole
  untested branch. `if TYPE_CHECKING:`, `@overload`, and `raise NotImplementedError` are excluded
  (`[tool.coverage.report] exclude_also`).
- **Escape hatch** — a `# pragma: no cover` comment excludes a line or block. Use it for genuinely
  unreachable code, not to dodge a test; it shows up in review either way.

**Reading the report.** The job summary of the `python-test` run (the run's **Summary** page)
carries a "Diff Coverage" table: one row per changed measured file, with its percentage and the
line numbers that never ran. The same uncovered lines are annotated as warnings on the PR's
**Files changed** tab, and the step log prints them too. A line listed there is one no test
executed; write a test that reaches it, or remove the line if nothing can.

To check before pushing (against the SQLite suite only; includes uncommitted changes):

```bash
make diff-cover                                   # compares against origin/main
make diff-cover COMPARE_BRANCH=origin/my-base     # a stacked branch's base
```

The frontend has no coverage gate. Its vitest suites cover a handful of helpers and forms, not page
rendering, so a changed-line gate there would block most UI PRs on scaffolding the suite doesn't
have yet (query-client, router, and theme providers for component tests).

Frontend tests are vitest + React Testing Library: `npm test` watches, `npx vitest run` is the
one-shot CI form. Coverage is partial — a handful of suites under `src/`, concentrated on pure
helpers and form logic rather than page rendering — so treat an untested component as normal here
and add a suite alongside a change rather than assuming one exists to update.

`npm run check:engines` fails when the root `engines.node` admits a Node version that some
package in `package-lock.json` doesn't support; CI runs it in the `node-lint` job. When a
dependency bump raises its own floor, narrow `engines.node` to the versions every package
supports, then refresh the lockfile's root metadata with the npm version CI pins.

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
