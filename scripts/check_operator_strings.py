#!/usr/bin/env python3
"""Flag operator-specific strings in tracked files.

Access is open source and run by many operators, so upstream code must not
carry one operator's infrastructure: GCP projects, registry paths, service
accounts, CI secrets, corporate email addresses, or private repositories. This
script scans every file ``git ls-files`` reports against the patterns in
``operator_strings.toml`` and fails when a match is neither inside an excluded
path nor covered by an allowlist entry.

Every exclusion and allowlist entry carries a reason, and an allowlist entry
that no longer matches anything fails the check too, so the list cannot rot.

Uses only the standard library so CI can run it without installing the
project's dependencies.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).with_name("operator_strings.toml")

# Bytes inspected when deciding whether a file is binary.
_BINARY_SNIFF_BYTES = 8192


@dataclass(frozen=True)
class Pattern:
    """A named regex for one shape of operator-specific string."""

    id: str
    regex: re.Pattern[str]
    description: str


@dataclass
class AllowEntry:
    """Permission for specific matches of specific patterns in specific files.

    ``match`` must cover the whole matched span, so an entry for one mention on
    a line does not also excuse a different mention on the same line.
    """

    paths: list[str]
    patterns: list[str]
    match: re.Pattern[str] | None
    reason: str
    used: bool = field(default=False, compare=False)

    def allows(self, path: str, pattern_id: str, line: str, start: int, end: int) -> bool:
        """Return whether this entry covers the match at ``line[start:end]``."""
        if pattern_id not in self.patterns:
            return False
        if not any(fnmatch.fnmatchcase(path, glob) for glob in self.paths):
            return False
        if self.match is None:
            return True
        return any(m.start() <= start and end <= m.end() for m in self.match.finditer(line))


@dataclass(frozen=True)
class Config:
    """Patterns, excluded paths, and allowlist entries loaded from TOML."""

    patterns: list[Pattern]
    exclude: list[str]
    allow: list[AllowEntry]


@dataclass(frozen=True)
class Hit:
    """One unallowlisted match of a pattern."""

    path: str
    line_number: int
    pattern_id: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_number}: [{self.pattern_id}] {self.text}"


class ConfigError(ValueError):
    """Raised when the TOML config is malformed."""


def _require(table: Mapping[str, object], key: str, where: str) -> object:
    """Return ``table[key]``, raising ``ConfigError`` naming ``where`` if absent."""
    if key not in table or table[key] in ("", []):
        raise ConfigError(f"{where} is missing required key {key!r}")
    return table[key]


def load_config(text: str) -> Config:
    """Parse the TOML config.

    Args:
        text: The TOML document.

    Returns:
        The parsed ``Config``.

    Raises:
        ConfigError: If an entry lacks a required key (every exclusion and
            allowlist entry needs a ``reason``) or names an unknown pattern.
    """
    data = tomllib.loads(text)

    patterns: list[Pattern] = []
    for i, raw in enumerate(data.get("pattern", [])):
        where = f"pattern[{i}]"
        pattern_id = str(_require(raw, "id", where))
        flags = re.IGNORECASE if raw.get("ignore_case", False) else 0
        patterns.append(
            Pattern(
                id=pattern_id,
                regex=re.compile(str(_require(raw, "regex", where)), flags),
                description=str(_require(raw, "description", where)),
            )
        )
    known_ids = {p.id for p in patterns}

    exclude: list[str] = []
    for i, raw in enumerate(data.get("exclude", [])):
        where = f"exclude[{i}]"
        _require(raw, "reason", where)
        exclude.append(str(_require(raw, "path", where)))

    allow: list[AllowEntry] = []
    for i, raw in enumerate(data.get("allow", [])):
        where = f"allow[{i}]"
        paths = _require(raw, "paths", where)
        pattern_ids = _require(raw, "patterns", where)
        if not isinstance(paths, list) or not isinstance(pattern_ids, list):
            raise ConfigError(f"{where}: 'paths' and 'patterns' must be lists")
        unknown = set(pattern_ids) - known_ids
        if unknown:
            raise ConfigError(f"{where} names unknown patterns: {sorted(unknown)}")
        match = raw.get("match")
        allow.append(
            AllowEntry(
                paths=[str(p) for p in paths],
                patterns=[str(p) for p in pattern_ids],
                match=re.compile(match) if match else None,
                reason=str(_require(raw, "reason", where)),
            )
        )

    return Config(patterns=patterns, exclude=exclude, allow=allow)


def is_excluded(path: str, exclude: Iterable[str]) -> bool:
    """Return whether ``path`` matches any excluded glob."""
    return any(fnmatch.fnmatchcase(path, glob) for glob in exclude)


def scan_text(path: str, text: str, config: Config) -> list[Hit]:
    """Return the unallowlisted hits in one file's text.

    Marks every allowlist entry that excuses a match as used.
    """
    hits: list[Hit] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in config.patterns:
            for m in pattern.regex.finditer(line):
                allowed = False
                for entry in config.allow:
                    if entry.allows(path, pattern.id, line, m.start(), m.end()):
                        entry.used = True
                        allowed = True
                if not allowed:
                    hits.append(Hit(path, line_number, pattern.id, line.strip()))
    return hits


def scan_files(files: Mapping[str, str], config: Config) -> list[Hit]:
    """Return the unallowlisted hits across ``files`` (path to text)."""
    hits: list[Hit] = []
    for path in sorted(files):
        if not is_excluded(path, config.exclude):
            hits.extend(scan_text(path, files[path], config))
    return hits


def unused_allow_entries(config: Config) -> list[AllowEntry]:
    """Return allowlist entries that excused nothing in the last scan."""
    return [entry for entry in config.allow if not entry.used]


def read_tracked_files(root: Path) -> dict[str, str]:
    """Return the text of every tracked, non-binary file under ``root``."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode()
    files: dict[str, str] = {}
    for path in filter(None, listing.split("\0")):
        full = root / path
        if not full.is_file():
            # Tracked but deleted in the working tree, or a submodule.
            continue
        data = full.read_bytes()
        if b"\0" in data[:_BINARY_SNIFF_BYTES]:
            continue
        try:
            files[path] = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return files


def main(argv: list[str] | None = None) -> int:
    """Run the check; return 0 when clean and 1 when anything needs attention."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="pattern and allowlist TOML")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root to scan")
    args = parser.parse_args(argv)

    config = load_config(args.config.read_text())
    hits = scan_files(read_tracked_files(args.root), config)
    unused = unused_allow_entries(config)

    descriptions = {p.id: p.description for p in config.patterns}
    for hit in hits:
        print(f"::error file={hit.path},line={hit.line_number}::{descriptions[hit.pattern_id]}")
        print(hit)
    for entry in unused:
        print(f"Allowlist entry matches nothing; remove it: paths={entry.paths} reason={entry.reason!r}")

    if hits:
        print(
            f"\n{len(hits)} operator-specific string(s) found. Access is open source and run by many "
            "operators: move operator-specific values into the operator's own repo or a plugin. If a "
            f"mention is legitimate, add an [[allow]] entry with a reason to {args.config.name}."
        )
    if hits or unused:
        return 1
    print("No operator-specific strings found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
