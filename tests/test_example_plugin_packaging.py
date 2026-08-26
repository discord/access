"""Static checks that the example plugins are installable *alongside each other*.

Every example under ``examples/plugins/`` is an independent distribution, and
the Dockerfile exposes each as its own ``INSTALL_*_PLUGIN`` build arg, so any
subset can be enabled in one image — including two examples that extend the
same hook (pluggy calls every registered implementation, so that is a supported
configuration, not a conflict).

Nothing about installing two distributions that claim the same distribution
name, top-level module, or entry point fails loudly: ``uv pip install`` of the
second one simply overwrites the first's ``.dist-info`` and module file, and the
image silently ends up with one plugin where the operator asked for two. That
happened to ``notifications`` and ``notifications_slack``, which both shipped as
``access-notifications``/``notifications.py``. These tests parse the packaging
declarations without importing or executing them, so the collision is caught
here rather than in a built image.
"""

import ast
import tomllib
from pathlib import Path
from typing import Any

import pytest

EXAMPLE_PLUGINS_DIR = Path(__file__).resolve().parents[1] / "examples" / "plugins"

# Examples declare their packaging either way; both are read into the same shape
# below so a plugin can't drop out of the collision checks by switching form.
DECLARATION_FILES = sorted(EXAMPLE_PLUGINS_DIR.glob("*/setup.py")) + sorted(
    EXAMPLE_PLUGINS_DIR.glob("*/pyproject.toml")
)


def _setup_kwargs(setup_py: Path) -> dict[str, Any]:
    """Return the literal keyword arguments of the ``setup()`` call in ``setup_py``."""
    tree = ast.parse(setup_py.read_text(), filename=str(setup_py))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "setup":
            continue
        return {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords if kw.arg is not None}
    raise AssertionError(f"No setup() call found in {setup_py}")


def _pyproject_kwargs(pyproject: Path) -> dict[str, Any]:
    """Return a ``setup()``-shaped view of a plugin's ``pyproject.toml``."""
    parsed = tomllib.loads(pyproject.read_text())
    setuptools_table = parsed.get("tool", {}).get("setuptools", {})

    kwargs: dict[str, Any] = {
        "name": parsed["project"]["name"],
        "packages": setuptools_table.get("packages", []),
        "py_modules": setuptools_table.get("py-modules", []),
        # Normalize {group: {name: target}} onto setup.py's ["name=target"] form.
        "entry_points": {
            group: [f"{name}={target}" for name, target in specs.items()]
            for group, specs in parsed["project"].get("entry-points", {}).items()
        },
    }

    # Auto-discovery would leave the top-level collision check with nothing to
    # compare, i.e. passing without checking anything. Require it be spelled out.
    assert kwargs["packages"] or kwargs["py_modules"], (
        f"{pyproject} must declare [tool.setuptools] packages or py-modules explicitly, "
        "so the top-level collision check below has something to compare."
    )
    return kwargs


def _declaration_kwargs(declaration: Path) -> dict[str, Any]:
    if declaration.name == "setup.py":
        return _setup_kwargs(declaration)
    return _pyproject_kwargs(declaration)


def _top_level_names(kwargs: dict[str, Any]) -> list[str]:
    """Top-level importable names the distribution installs into site-packages."""
    modules = list(kwargs.get("py_modules", []))
    # Subpackages ("a.b") don't collide on their own; only the root does.
    packages = [pkg.split(".")[0] for pkg in kwargs.get("packages", [])]
    return modules + packages


def _entry_points(kwargs: dict[str, Any]) -> list[tuple[str, str]]:
    """``(group, name)`` pairs declared by the distribution."""
    pairs = []
    for group, specs in kwargs.get("entry_points", {}).items():
        for spec in specs:
            pairs.append((group, spec.split("=")[0].strip()))
    return pairs


def test_example_plugins_are_discovered() -> None:
    """Guard against the collision tests below passing vacuously."""
    assert len(DECLARATION_FILES) > 1, f"Expected several example plugins under {EXAMPLE_PLUGINS_DIR}"


@pytest.mark.parametrize(
    ("extract", "what"),
    [
        (lambda kwargs: [kwargs["name"]], "distribution name"),
        (_top_level_names, "top-level module/package"),
        (_entry_points, "entry point"),
    ],
    ids=["distribution_name", "top_level_module", "entry_point"],
)
def test_example_plugins_do_not_collide(extract: Any, what: str) -> None:
    owners: dict[Any, str] = {}
    collisions = []
    for declaration in DECLARATION_FILES:
        plugin = declaration.parent.name
        for value in extract(_declaration_kwargs(declaration)):
            if value in owners:
                collisions.append(f"{what} {value!r} claimed by both {owners[value]} and {plugin}")
            owners[value] = plugin

    assert not collisions, (
        "Example plugins must be installable into the same environment; "
        "whichever is installed last would silently win:\n  " + "\n  ".join(collisions)
    )
