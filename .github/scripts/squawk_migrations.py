"""Lint the Postgres SQL of changed Alembic migrations with squawk.

Each migration is rendered offline for the postgresql dialect, exactly as
`alembic upgrade <down_revision>:<revision> --sql` would print it, so no
database is needed. squawk then lints the rendered SQL against the repo's
`.squawk.toml`, which documents the rules turned off for Access's scale.

A migration whose warning is understood and accepted can say so in its own
source, next to the code a reviewer is reading:

    # squawk-ignore-file ban-drop-column, renaming-column

The script copies that line to the top of the rendered SQL as squawk's
`-- squawk-ignore-file` comment. With no rule names it silences every rule for
that migration.

Usage (install squawk first; see .github/requirements/squawk.txt):

    uv run python .github/scripts/squawk_migrations.py --base origin/main
    uv run python .github/scripts/squawk_migrations.py migrations/versions/<file>.py

Exits with squawk's status: 0 when the SQL is clean or no migration changed.
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SQUAWK_CONFIG = REPO_ROOT / ".squawk.toml"

# Offline rendering never connects; the URL only selects the dialect.
RENDER_DATABASE_URI = "postgresql://render@localhost/render"

IGNORE_MARKER = re.compile(r"^\s*#\s*squawk-ignore-file\b(?P<rules>.*)$", re.MULTILINE)


def _changed_migrations(base: str) -> list[Path]:
    """Return the migration files added, modified, or renamed since `base`."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD", "--", "migrations/versions/*.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [REPO_ROOT / line for line in result.stdout.splitlines() if line]


def _render_sql(paths: list[Path], out_dir: Path) -> list[Path]:
    """Render each migration's upgrade SQL into `out_dir`, one file per migration."""
    # env.py reads the URL from settings, which read DATABASE_URI once on import.
    os.environ["DATABASE_URI"] = RENDER_DATABASE_URI
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(REPO_ROOT / "alembic.ini"))
    scripts = {Path(script.path).resolve(): script for script in ScriptDirectory.from_config(config).walk_revisions()}

    rendered = []
    for path in paths:
        script = scripts.get(path.resolve())
        if script is None:
            print(f"Skipping {path.relative_to(REPO_ROOT)}: not an Alembic revision.")
            continue
        # A merge revision has several parents; rendering from the first one
        # also lints the other branch's revisions, which is harmless.
        down = script.down_revision
        if isinstance(down, tuple):
            down = down[0]
        buffer = io.StringIO()
        config.output_buffer = buffer
        command.upgrade(config, f"{down or 'base'}:{script.revision}", sql=True)

        header = "".join(f"-- squawk-ignore-file{m['rules']}\n" for m in IGNORE_MARKER.finditer(path.read_text()))
        sql_path = out_dir / f"{path.stem}.sql"
        sql_path.write_text(header + buffer.getvalue())
        rendered.append(sql_path)
    return rendered


def main() -> int:
    """Render the selected migrations and run squawk over them."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("migrations", nargs="*", type=Path, help="migration files to lint")
    parser.add_argument("--base", help="lint the migrations changed between this git ref and HEAD")
    args = parser.parse_args()
    if bool(args.migrations) == bool(args.base):
        parser.error("pass migration files or --base, but not both")

    paths = [p.resolve() for p in args.migrations] if args.migrations else _changed_migrations(args.base)
    if not paths:
        print("No migrations changed; nothing for squawk to lint.")
        return 0

    squawk = shutil.which("squawk")
    if squawk is None:
        print("squawk is not on PATH; install it from .github/requirements/squawk.txt.", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        sql_files = _render_sql(paths, out_dir)
        if not sql_files:
            print("No Alembic revisions among the changed files; nothing for squawk to lint.")
            return 0
        for sql_file in sql_files:
            print(f"::group::Rendered SQL: {sql_file.name}\n{sql_file.read_text()}::endgroup::", flush=True)
        # Run from the output directory so squawk reports `<migration>.sql`
        # rather than a temporary path.
        result = subprocess.run(
            [squawk, "--config", str(SQUAWK_CONFIG), *(f.name for f in sql_files)],
            cwd=out_dir,
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
