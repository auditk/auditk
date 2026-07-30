# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Canonical corpus-walking primitives for the local Claude Code session
corpus (default ``~/.claude/projects`` / ``~/.claude/tasks``).

Single source of truth for two consumers that both need to walk the same
on-disk layout: ``scripts/corpus_stats.py`` (read-only reporting on the
corpus, invoked directly as a script) and ``auditk doctor``
(``cli.py``, the adapter-health corpus-level invariant). Before this
module existed, both duplicated the same discovery/parsing logic
independently, because ``scripts/`` is not part of the installed package
(see ``pyproject.toml``'s ``packages = ["src/auditk"]``) and so cannot be
imported *from* — this module lives on the importable side instead, and
``scripts/corpus_stats.py`` imports it (a script run from a repo checkout
can always import the installed/checked-out package; only the reverse
direction fails).

Read-only and offline throughout: nothing here ever writes to a path it is
given, and no network or model call is made.

On-disk layout trap (see ``docs/proposals/session-postmortem-reporting.md``
Phase 6's correction): a session's parent transcript ``<uuid>.jsonl`` is a
SIBLING of the ``<uuid>/`` directory that holds ``subagents/`` — both live
directly under ``<root>/<project-slug>/``. A naive ``**/*.jsonl`` glob from
the project directory finds both parent and subagent transcripts with no
way to tell them apart by path shape alone; ``discover_sessions`` below
resolves the sibling directory explicitly instead.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / ".claude" / "projects"
DEFAULT_TASKS_ROOT = Path.home() / ".claude" / "tasks"


@dataclass(frozen=True)
class SessionPaths:
    """A parent transcript plus its sibling session directory, if any."""

    session_id: str
    project_slug: str
    transcript: Path
    session_dir: Path | None


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file into a list of parsed dict records.

    Lines that are blank, not valid JSON, or not a JSON object are skipped
    rather than raising — this is best-effort forensic tooling reading files
    a third-party harness controls, not a producer we can validate at write
    time (same rationale as `adapters.claude_code.load_plan_tasks`).
    """
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def discover_sessions(root: Path) -> list[SessionPaths]:
    """Find parent `<uuid>.jsonl` transcripts under `root/<project-slug>/`.

    Pairs each transcript with its sibling `<uuid>/` directory (which may
    hold `subagents/`) when one exists on disk.
    """
    if not root.is_dir():
        return []
    sessions: list[SessionPaths] = []
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for transcript in sorted(project_dir.glob("*.jsonl")):
            session_id = transcript.stem
            sibling_dir = project_dir / session_id
            sessions.append(
                SessionPaths(
                    session_id=session_id,
                    project_slug=project_dir.name,
                    transcript=transcript,
                    session_dir=sibling_dir if sibling_dir.is_dir() else None,
                )
            )
    return sessions


def discover_subagent_transcripts(session_dir: Path) -> list[Path]:
    """Find `agent-*.jsonl` subagent transcripts under a session's sibling dir."""
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.is_dir():
        return []
    return sorted(subagents_dir.glob("agent-*.jsonl"))


def has_plan_store(tasks_root: Path, session_id: str) -> bool:
    """Whether the persisted plan store `<tasks_root>/<session_id>/` exists
    and holds at least one task file."""
    session_task_dir = tasks_root / session_id
    if not session_task_dir.is_dir():
        return False
    return any(session_task_dir.glob("*.json"))


def count_record_types(records: Iterable[dict[str, Any]]) -> Counter[str]:
    """Pure: count the `type` field across an iterable of parsed records."""
    counts: Counter[str] = Counter()
    for record in records:
        record_type = record.get("type")
        if record_type is not None:
            counts[str(record_type)] += 1
    return counts


def count_tool_calls(
    records: Iterable[dict[str, Any]], names: Iterable[str] | None = None
) -> Counter[str]:
    """Pure: count `tool_use` block names inside `assistant` message records.

    If `names` is given, only those tool names are counted; otherwise every
    tool_use block's name is counted. Non-assistant records and malformed
    message/content shapes are ignored rather than raising.
    """
    wanted = set(names) if names is not None else None
    counts: Counter[str] = Counter()
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if name is None:
                continue
            name_str = str(name)
            if wanted is not None and name_str not in wanted:
                continue
            counts[name_str] += 1
    return counts
