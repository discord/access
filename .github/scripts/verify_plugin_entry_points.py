"""Assert the example plugins registered inside a built Access image.

Run *inside* the image (see .github/workflows/docker-build.yml), which is the
point: `uv pip install` targeting the wrong interpreter is a silent failure that
surfaces only at runtime as "Registered 0 plugins", so the entry points have to
be queried by the image's own Python rather than inferred from a zero exit code
on the build. Uses importlib.metadata only — no DB, Okta, or config needed.

Expects every INSTALL_*_PLUGIN build arg to have been set to "true". Locally:

    uv run python .github/scripts/verify_plugin_entry_points.py

(which exits 1 against a normal dev venv, where only the audit logger is
installed — that is the intended negative result, not a bug.)
"""

import importlib.metadata as md
import sys

# Entry point group -> names expected once every INSTALL_* arg is on.
#
# Note examples/plugins/notifications and notifications_slack both publish the
# distribution "access-notifications" with an entry point named "notifications",
# so enabling both build args yields one, not two: the later install (slack)
# replaces the earlier one. Both install branches still run, which is what the
# image build is covering here.
EXPECTED = {
    "access_app_group_lifecycle": {"audit_logger", "google_group_manager"},
    "access_conditional_access": {"conditional_access"},
    "access_metrics_reporter": {"metrics_reporter"},
    "access_notifications": {"notifications"},
    "access.commands": {"health"},
}


def main() -> int:
    missing_any = False
    for group, expected in EXPECTED.items():
        found = {ep.name for ep in md.entry_points(group=group)}
        print(f"{group}: {sorted(found)}")
        if missing := expected - found:
            print(f"  MISSING from {group}: {sorted(missing)}", file=sys.stderr)
            missing_any = True

    if missing_any:
        print("\nFAILED: expected plugin entry points are not registered", file=sys.stderr)
        return 1

    print(f"\nOK: all expected entry points registered across {len(EXPECTED)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
