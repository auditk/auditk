# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""CLAUDE.md policy-context discovery for the single-session post-mortem.

``discover_policy_context`` answers a narrower question than
``auditk.analysis.ruleset.load_ruleset``: not "what findings-engine config
applies", but "what natural-language policy documents (CLAUDE.md files and
their ``.claude/rules/*.md`` companions) were in effect for this session's
working directory, so a human reviewing the post-mortem can see them
alongside the structural findings."

Two independent sources are checked, mirroring the layout Claude Code itself
uses:

- GLOBAL: ``<home>/.claude/CLAUDE.md`` — a user-wide policy file, not tied to
  any one project.
- CASCADE: walking from the session's working directory up to the
  filesystem root, at each directory checking for a ``CLAUDE.md``, a
  ``.claude/CLAUDE.md``, and any ``.claude/rules/*.md`` files.

This module is pure filesystem inspection — no subprocess, no network, and
no opinion about *content* (that's ``auditk.analysis.rules_scaffold``). A
missing file at any candidate location is silently skipped; nothing here
ever raises for an absent CLAUDE.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

_RULES_SUBDIR = Path(".claude") / "rules"
_HEADING_PATTERN = re.compile(r"^#[ \t]+(\S.*)$")


class PolicyDoc(BaseModel):
    """One discovered CLAUDE.md-family policy file."""

    path: str
    # One of "global", "project", "rules".
    scope: str
    title: str | None = None


def _extract_title(path: Path) -> str | None:
    """The text of the first markdown `# heading` (h1 only) in `path`, or None.

    Robust to unreadable files and to files with no heading at all — both
    return None rather than raising, since a missing/malformed title should
    never block discovery of the file itself.
    """
    try:
        text = path.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        match = _HEADING_PATTERN.match(line.rstrip())
        if match:
            return match.group(1).strip()
    return None


def discover_policy_context(start_dir: Path, *, home: Path | None = None) -> list[PolicyDoc]:
    """The CLAUDE.md-family policy docs in effect for `start_dir`.

    `home` defaults to ``Path.home()``. Returns an empty list when nothing is
    found. Order is deterministic: the GLOBAL doc first (if present), then
    the CASCADE walk from `start_dir` upward to the filesystem root —
    most-specific directory first — checking ``CLAUDE.md``, then
    ``.claude/CLAUDE.md``, then any ``.claude/rules/*.md`` (sorted by name)
    at each level. Entries are deduplicated by resolved path: if the GLOBAL
    doc is also reachable via the cascade walk (e.g. a project living
    directly under `home`), it is listed once, as GLOBAL.
    """
    resolved_home = home if home is not None else Path.home()

    docs: list[PolicyDoc] = []
    seen: set[Path] = set()

    def _add(path: Path, scope: str) -> None:
        if not path.is_file():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        docs.append(PolicyDoc(path=str(path), scope=scope, title=_extract_title(path)))

    _add(resolved_home / ".claude" / "CLAUDE.md", "global")

    current = start_dir.resolve()
    while True:
        _add(current / "CLAUDE.md", "project")
        _add(current / ".claude" / "CLAUDE.md", "project")
        rules_dir = current / _RULES_SUBDIR
        if rules_dir.is_dir():
            for rule_file in sorted(rules_dir.glob("*.md")):
                _add(rule_file, "rules")
        parent = current.parent
        if parent == current:
            break
        current = parent

    return docs
