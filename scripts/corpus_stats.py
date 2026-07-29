#!/usr/bin/env python3
# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only corpus statistics for the local Claude Code session corpus.

`docs/proposals/session-postmortem-reporting.md` (Phase 6) asserts, without a
documented methodology or script, that the local corpus contains "105
subagent transcripts across 32 sessions" carrying "2,521 Bash, 1,105 Read,
516 Edit, 192 Write" delegate tool calls. This script makes that claim
reproducible: it walks a corpus root (default ``~/.claude/projects``) and
reports session counts, per-record-``type`` counts, the plan-anchor tool
histogram (``TodoWrite``/``TaskCreate``/``TaskUpdate``) plus persisted
plan-store presence, and delegate (subagent) tool-call counts by tool.

Read-only and offline: no file under the corpus root is ever written to, and
no network or model call is made.

On-disk layout trap (see the proposal's Phase 6 correction): a session's
parent transcript ``<uuid>.jsonl`` is a SIBLING of the ``<uuid>/`` directory
that holds ``subagents/`` — both live directly under
``<root>/<project-slug>/``. A naive ``**/*.jsonl`` glob from the project
directory finds both parent and subagent transcripts with no way to tell
them apart by path shape alone; this module discovers the sibling directory
explicitly instead.

Usage:
    python scripts/corpus_stats.py [--root PATH] [--tasks-root PATH] [--json]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path.home() / ".claude" / "projects"
DEFAULT_TASKS_ROOT = Path.home() / ".claude" / "tasks"

# Tool names that anchor the adapter's "standing plan" (see
# adapters/claude_code.py module docstring): the legacy TodoWrite anchor and
# the modern TaskCreate/TaskUpdate pair.
_PLAN_ANCHOR_TOOLS = ("TodoWrite", "TaskCreate", "TaskUpdate")


@dataclass(frozen=True)
class SessionPaths:
    """A parent transcript plus its sibling session directory, if any."""

    session_id: str
    project_slug: str
    transcript: Path
    session_dir: Path | None


@dataclass
class CorpusStats:
    session_count: int
    record_type_counts: Counter[str] = field(default_factory=Counter)
    plan_anchor_counts: Counter[str] = field(default_factory=Counter)
    sessions_with_plan_store: int = 0
    subagent_transcript_count: int = 0
    subagent_record_count: int = 0
    sessions_with_subagents: int = 0
    delegate_tool_counts: Counter[str] = field(default_factory=Counter)
    unreadable_transcripts: list[str] = field(default_factory=list)


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


def compute_corpus_stats(
    root: Path = DEFAULT_ROOT, tasks_root: Path = DEFAULT_TASKS_ROOT
) -> CorpusStats:
    """Orchestrate discovery + pure counting into a single `CorpusStats`.

    Read-only: every path under `root` and `tasks_root` is only ever opened
    for reading.
    """
    sessions = discover_sessions(root)
    stats = CorpusStats(session_count=len(sessions))
    subagent_transcripts: list[Path] = []

    for session in sessions:
        try:
            records = iter_jsonl(session.transcript)
        except OSError:
            stats.unreadable_transcripts.append(str(session.transcript))
            continue

        stats.record_type_counts.update(count_record_types(records))
        stats.plan_anchor_counts.update(count_tool_calls(records, _PLAN_ANCHOR_TOOLS))
        if has_plan_store(tasks_root, session.session_id):
            stats.sessions_with_plan_store += 1

        if session.session_dir is None:
            continue
        found = discover_subagent_transcripts(session.session_dir)
        if found:
            stats.sessions_with_subagents += 1
            subagent_transcripts.extend(found)

    for transcript in subagent_transcripts:
        try:
            records = iter_jsonl(transcript)
        except OSError:
            stats.unreadable_transcripts.append(str(transcript))
            continue
        stats.subagent_record_count += len(records)
        stats.delegate_tool_counts.update(count_tool_calls(records))

    stats.subagent_transcript_count = len(subagent_transcripts)
    return stats


def stats_to_dict(stats: CorpusStats) -> dict[str, Any]:
    return {
        "session_count": stats.session_count,
        "record_type_counts": dict(stats.record_type_counts),
        "plan_anchor_counts": dict(stats.plan_anchor_counts),
        "sessions_with_plan_store": stats.sessions_with_plan_store,
        "subagent_transcript_count": stats.subagent_transcript_count,
        "subagent_record_count": stats.subagent_record_count,
        "sessions_with_subagents": stats.sessions_with_subagents,
        "delegate_tool_counts": dict(stats.delegate_tool_counts),
        "unreadable_transcripts": list(stats.unreadable_transcripts),
    }


def format_report(stats: CorpusStats) -> str:
    lines = [
        f"session count: {stats.session_count}",
        "",
        "record type counts (parent transcripts):",
    ]
    for type_name, count in stats.record_type_counts.most_common():
        lines.append(f"  {type_name:<20} {count}")

    lines.append("")
    lines.append("plan-anchor tool calls (parent transcripts):")
    for tool in _PLAN_ANCHOR_TOOLS:
        lines.append(f"  {tool:<12} {stats.plan_anchor_counts.get(tool, 0)}")
    lines.append(f"  sessions with persisted plan store: {stats.sessions_with_plan_store}")

    lines.append("")
    lines.append("subagent (delegate) transcripts:")
    lines.append(f"  subagent transcript count: {stats.subagent_transcript_count}")
    lines.append(f"  subagent record count: {stats.subagent_record_count}")
    lines.append(f"  sessions containing subagent transcripts: {stats.sessions_with_subagents}")
    lines.append("  delegate tool-call counts:")
    for tool, count in stats.delegate_tool_counts.most_common():
        lines.append(f"    {tool:<12} {count}")

    if stats.unreadable_transcripts:
        lines.append("")
        lines.append(f"unreadable transcripts (skipped): {len(stats.unreadable_transcripts)}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Corpus root to walk (default: ~/.claude/projects). Read-only.",
    )
    parser.add_argument(
        "--tasks-root",
        type=Path,
        default=DEFAULT_TASKS_ROOT,
        help="Persisted plan store root (default: ~/.claude/tasks). Read-only.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a formatted table.",
    )
    args = parser.parse_args(argv)

    stats = compute_corpus_stats(args.root, args.tasks_root)
    if args.json:
        print(json.dumps(stats_to_dict(stats), indent=2, sort_keys=True))
    else:
        print(format_report(stats))


if __name__ == "__main__":
    main()
