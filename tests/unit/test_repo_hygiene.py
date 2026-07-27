# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Repo-hygiene gate: shipped source carries nothing machine/user-specific.

auditk is a public, open-core library. A user's own rules and paths must never
be baked into shipped code — they live in gitignored local rulesets instead
(see docs/rules.md). This test scans everything under ``src/auditk`` and fails
if a shipped file hardcodes an absolute home path or a private project/identity
token. The standard copyright header (which names a contributor) is exempt.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent.parent / "src" / "auditk"

# Absolute user-home paths never belong in shipped code — use Path.home() /
# relative discovery instead.
_FORBIDDEN_PATH = re.compile(r"/home/|/Users/[^/\s]")

# Private project / identity tokens that must not leak into the generic tool.
_FORBIDDEN_TOKENS = ("lesportif", "narwhal", "bossyk", "nmg-toolbox")


def _shipped_files() -> list[Path]:
    return sorted(
        p
        for ext in ("*.py", "*.yaml", "*.yml")
        for p in _SRC.rglob(ext)
        if "__pycache__" not in p.parts
    )


def _significant_lines(path: Path) -> list[tuple[int, str]]:
    # Skip the copyright/SPDX header lines, which legitimately name a contributor.
    return [
        (i, line)
        for i, line in enumerate(path.read_text().splitlines(), start=1)
        if "Copyright" not in line and "SPDX-License-Identifier" not in line
    ]


def test_no_shipped_file_hardcodes_a_home_path() -> None:
    offenders = [
        f"{path}:{lineno}: {line.strip()}"
        for path in _shipped_files()
        for lineno, line in _significant_lines(path)
        if _FORBIDDEN_PATH.search(line)
    ]
    assert not offenders, "shipped source hardcodes an absolute home path:\n" + "\n".join(offenders)


def test_no_shipped_file_contains_a_private_token() -> None:
    offenders = [
        f"{path}:{lineno}: {line.strip()}"
        for path in _shipped_files()
        for lineno, line in _significant_lines(path)
        for token in _FORBIDDEN_TOKENS
        if token.lower() in line.lower()
    ]
    assert not offenders, "shipped source contains a private token:\n" + "\n".join(offenders)


def test_default_ruleset_ships_no_explicit_roots() -> None:
    text = (_SRC / "analysis" / "rules.default.yaml").read_text()
    assert "roots: auto" in text
    assert "/home/" not in text and ".claude" not in text


def test_hygiene_scan_actually_covers_source() -> None:
    # Guard against the scan silently matching nothing (e.g. a moved src tree).
    files = _shipped_files()
    assert len(files) > 20
    assert any(p.name == "findings.py" for p in files)
