"""Guard the example plugins' dependency declarations against the app's own pins.

Example plugins are installed *into the Access venv*, on top of the dependency set
`uv sync --locked` resolved — by the Dockerfile's `install_plugin` helper, by
`make install-plugins`, and by the plugin-install steps in `.github/workflows/ci.yml`.

The examples declare their deps in `install_requires` or `[project] dependencies`,
but those two install paths still read a `requirements.txt` when one is present, so
all three are checked here for the benefit of any plugin that reintroduces one.

So a plugin requirement that *excludes* the version the app pins does not fail loudly;
it silently re-resolves that package in the app's venv. A plugin pinning
`pluggy==1.5.0` while `pyproject.toml` pins `pluggy==1.6.0` downgrades the running
app's pluggy, and `uv sync --locked --check` won't catch it because the change lands
after the sync.

These tests assert the inverse: for any package the app pins exactly, every example
plugin's declared specifier must *admit* that version.
"""

import ast
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "examples" / "plugins"


def _app_pinned_versions() -> dict[str, str]:
    """Map canonical package name -> version, for deps pyproject pins with a bare `==`."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    pinned = {}
    for raw in pyproject["project"]["dependencies"]:
        req = Requirement(raw)
        specs = list(req.specifier)
        if len(specs) == 1 and specs[0].operator == "==":
            pinned[canonicalize_name(req.name)] = specs[0].version
    return pinned


def _setup_py_requirements(setup_py: Path) -> list[str]:
    """Pull the `install_requires` list out of a setup.py without executing it."""
    tree = ast.parse(setup_py.read_text())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setup"):
            continue
        for kw in node.keywords:
            if kw.arg == "install_requires":
                return ast.literal_eval(kw.value)
    return []


def _pyproject_requirements(pyproject: Path) -> list[str]:
    """Pull `[project] dependencies` out of a plugin's pyproject.toml."""
    return tomllib.loads(pyproject.read_text())["project"].get("dependencies", [])


def _requirements_txt_requirements(requirements: Path) -> list[str]:
    parsed = []
    for line in requirements.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        # Skip blanks and pip flag lines (-r, -e, --index-url, ...).
        if not line or line.startswith("-"):
            continue
        parsed.append(line)
    return parsed


def _declaration_files() -> list[Path]:
    files = (
        sorted(PLUGINS_DIR.glob("*/setup.py"))
        + sorted(PLUGINS_DIR.glob("*/pyproject.toml"))
        + sorted(PLUGINS_DIR.glob("*/requirements.txt"))
    )
    assert files, f"no example plugin dependency declarations found under {PLUGINS_DIR}"
    return files


@pytest.mark.parametrize("declaration", _declaration_files(), ids=lambda p: str(p.relative_to(PLUGINS_DIR)))
def test_example_plugin_admits_app_pinned_versions(declaration: Path) -> None:
    """No example plugin may exclude a version the app pins exactly."""
    pinned = _app_pinned_versions()

    if declaration.name == "setup.py":
        raw_requirements = _setup_py_requirements(declaration)
    elif declaration.name == "pyproject.toml":
        raw_requirements = _pyproject_requirements(declaration)
    else:
        raw_requirements = _requirements_txt_requirements(declaration)

    for raw in raw_requirements:
        req = Requirement(raw)
        app_version = pinned.get(canonicalize_name(req.name))
        if app_version is None:
            # Not a package the app itself pins; the plugin owns it outright.
            continue
        assert req.specifier.contains(app_version, prereleases=True), (
            f"{declaration.relative_to(REPO_ROOT)} declares {raw!r}, which excludes the "
            f"version pyproject.toml pins ({req.name}=={app_version}). Installing this "
            f"plugin would silently re-resolve {req.name} in the Access venv. Use a "
            f"compatible range (e.g. '{req.name}>=1.5,<2') instead of an exact pin."
        )
