# shellcheck shell=bash
# Dev shell hook for flake.nix. NPM_VERSION and PYTHON are set by the flake.
set -e

# nixpkgs nodejs bundles an older npm than the repo requires, so install the
# pinned npm into a repo-local prefix instead of the user's global one.
export NPM_CONFIG_PREFIX="$PWD/.nix-npm-global"
export PATH="$NPM_CONFIG_PREFIX/bin:$PATH"
mkdir -p "$NPM_CONFIG_PREFIX"
current_npm="$(npm --version 2>/dev/null || echo 0.0.0)"
if [ "$current_npm" != "$NPM_VERSION" ]; then
  echo "Installing npm@$NPM_VERSION into $NPM_CONFIG_PREFIX (was $current_npm)..."
  npm install -g "npm@$NPM_VERSION" >/dev/null 2>&1 ||
    echo "warning: could not install npm@$NPM_VERSION; using $current_npm" >&2
fi

# Create the venv that `make dev` and the run targets expect.
if [ ! -d venv ]; then
  echo "Creating Python venv (run 'make dev' to install deps)..."
  "$PYTHON" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

set +e

cat <<'BANNER'

  ACCESS dev shell.

  First-time setup:
    make dev        # install pinned Python deps into ./venv + editable install
    npm install     # install frontend deps into ./node_modules
    # create a .env with CURRENT_OKTA_USER_EMAIL / OKTA_* / DATABASE_URI (see README)

  Common targets:
    make run             # backend (uvicorn) + frontend (vite) together
    make run-backend     # uvicorn --reload
    make run-frontend    # vite dev server
    make test            # ruff + mypy + pytest
    make pytest-postgres # pytest against a disposable postgres:16 (needs docker)
    make db-migrate      # alembic upgrade head

BANNER
