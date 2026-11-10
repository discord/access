"""Tests for check_operator_strings."""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_operator_strings as check
import pytest

CONFIG = """
[[pattern]]
id = "operator-name"
regex = '(?<![a-z])discord(app)?(?![a-z])'
ignore_case = true
description = "names the operator"

[[pattern]]
id = "release-pipeline"
regex = 'discord-access-prd'
description = "names a release-pipeline value"

[[exclude]]
path = "carve-out.yml"
reason = "sanctioned"

[[allow]]
paths = ["*"]
patterns = ["operator-name"]
match = 'discord/access\\b'
reason = "repo slug"
"""


def scan(files: dict[str, str], config_text: str = CONFIG) -> tuple[list[check.Hit], check.Config]:
    """Scan ``files`` with ``config_text`` and return the hits and the config."""
    config = check.load_config(config_text)
    return check.scan_files(files, config), config


def test_flags_operator_name_in_any_case_and_inside_identifiers() -> None:
    hits, _ = scan({"a.py": "x = 'DISCORD'\nallow_discord_access = 1\nDiscordApp\n"})
    assert [(h.line_number, h.pattern_id) for h in hits] == [
        (1, "operator-name"),
        (2, "operator-name"),
        (3, "operator-name"),
    ]


def test_does_not_flag_words_that_merely_contain_the_name() -> None:
    hits, _ = scan({"a.py": "discordant\nconcordiscord\n"})
    assert hits == []


def test_reports_each_pattern_that_matches() -> None:
    hits, _ = scan({"a.yml": "project: discord-access-prd\n"})
    assert sorted(h.pattern_id for h in hits) == ["operator-name", "release-pipeline"]


def test_excluded_path_is_not_scanned() -> None:
    hits, _ = scan({"carve-out.yml": "discord-access-prd\n"})
    assert hits == []


def test_allowlist_covers_only_its_own_span() -> None:
    """A slug on the line must not excuse a different mention beside it."""
    hits, _ = scan({"README.md": "see github.com/discord/access and discord-access-prd\n"})
    assert sorted(h.pattern_id for h in hits) == ["operator-name", "release-pipeline"]
    assert all("discord-access-prd" in h.text for h in hits)


def test_allowlist_is_scoped_to_its_patterns() -> None:
    config = (
        CONFIG
        + """
[[allow]]
paths = ["docs.md"]
patterns = ["operator-name"]
reason = "documents the carve-out"
"""
    )
    hits, _ = scan({"docs.md": "discord-access-prd\n"}, config)
    assert [h.pattern_id for h in hits] == ["release-pipeline"]


def test_allowlist_is_scoped_to_its_paths() -> None:
    config = (
        CONFIG
        + """
[[allow]]
paths = ["docs/*"]
patterns = ["operator-name"]
reason = "docs only"
"""
    )
    hits, _ = scan({"docs/a/b.md": "Discord\n", "api/x.py": "Discord\n"}, config)
    assert [h.path for h in hits] == ["api/x.py"]


def test_unused_allow_entry_is_reported() -> None:
    config = (
        CONFIG
        + """
[[allow]]
paths = ["gone.md"]
patterns = ["operator-name"]
reason = "stale"
"""
    )
    _, loaded = scan({"README.md": "github.com/discord/access\n"}, config)
    assert [e.reason for e in check.unused_allow_entries(loaded)] == ["stale"]


@pytest.mark.parametrize(
    "entry",
    [
        '[[allow]]\npaths = ["*"]\npatterns = ["operator-name"]\n',
        '[[allow]]\npaths = ["*"]\npatterns = ["no-such-pattern"]\nreason = "r"\n',
        '[[exclude]]\npath = "x"\n',
    ],
    ids=["allow-without-reason", "unknown-pattern", "exclude-without-reason"],
)
def test_rejects_malformed_entries(entry: str) -> None:
    with pytest.raises(check.ConfigError):
        check.load_config(CONFIG + "\n" + entry)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_main_scans_tracked_text_files_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    _git(tmp_path, "init", "--quiet")
    (tmp_path / "tracked.txt").write_text("hello\nDiscord\n")
    (tmp_path / "binary.bin").write_bytes(b"\0Discord")
    (tmp_path / "untracked.txt").write_text("Discord\n")
    _git(tmp_path, "add", "tracked.txt", "binary.bin")

    assert check.main(["--config", str(config), "--root", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "tracked.txt:2: [operator-name] Discord" in out
    assert "binary.bin" not in out
    assert "untracked.txt" not in out

    (tmp_path / "tracked.txt").write_text("github.com/discord/access\n")
    assert check.main(["--config", str(config), "--root", str(tmp_path)]) == 0


def test_repository_config_loads() -> None:
    """The checked-in config parses and every allowlist names known patterns."""
    check.load_config(check.DEFAULT_CONFIG.read_text())
