# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Single-session post-mortem report: pure rendering over Trace + Findings.

This module builds a human-readable (and machine-readable) post-mortem for
one ingested session. It is intentionally split from ``analysis/findings.py``
(the structural anomaly detector) and from ``cli.py`` (I/O, argument
parsing): every function here is a pure, deterministic function of its
inputs — no clock, no randomness, no file/network access — so the same
``Trace`` + ``FindingsReport`` always render to byte-identical output. The
CLI's ``report`` command is a thin wrapper that loads a session, calls
``analyze_trace``, then ``build_report``/``render_markdown`` here.

The three moving pieces:

- ``build_timeline`` condenses a (potentially long) step sequence down to
  the events a human reviewing the session would actually care about,
  collapsing runs of edits and skipping transparent tool-result noise.
- ``extract_turn_compliance`` answers "what did the agent actually do after
  each thing the user asked for" — one entry per real user utterance, with
  a tool-name -> count breakdown of everything the agent called before the
  user spoke again (or the session ended).
- ``build_report``/``render_markdown`` assemble and render the above plus
  the structural ``FindingsReport`` into one ``ReportModel`` / markdown
  document.
"""

from __future__ import annotations

import posixpath
import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from auditk.analysis.findings import (
    DEFAULT_TRIPWIRE_PATTERNS,
    Finding,
    FindingsConfig,
    FindingsReport,
    Severity,
)
from auditk.schema import ActionType, Actor, Step, Trace

# Edit-family tool calls collapsed into a single "edit" timeline entry per
# consecutive run. Deliberately excludes "Write" (file creation is always
# its own notable event, never collapsed) — see the module docstring.
_EDIT_TOOL_NAMES = frozenset({"Edit", "NotebookEdit"})

# Tools whose tool_use block delegates to a child transcript auditk does not
# stitch in (mirrors auditk.analysis.findings.DELEGATION_TOOL_NAMES).
_DELEGATION_TOOL_NAMES = frozenset({"Task", "Agent"})

_MAX_SUMMARY_LEN = 100

# Local copies of the test/lint/build and git-commit Bash patterns from
# analysis/findings.py (kept private there). Small enough, and specific
# enough to this module's own classification needs, that duplicating is
# clearer than reaching across module-private boundaries.
_VERIFY_COMMAND_PATTERN = re.compile(
    r"\b(?:pytest|unittest|jest|vitest|ruff|flake8|eslint|mypy|tox)\b"
    r"|\bnpm\s+(?:test|run\s+test)\b"
    r"|\bgo\s+test\b"
    r"|\bcargo\s+test\b"
    r"|\bmake\s+(?:test|lint)\b",
    re.IGNORECASE,
)
_GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b", re.IGNORECASE)

_SEVERITY_ORDER: list[Severity] = [Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]


class TimelineEntry(BaseModel):
    """One condensed, human-notable event in a session's timeline."""

    step_id: str
    # One of: user, test, commit, edit, write, error, delegation, tripwire,
    # other. Left as ``str`` rather than an enum since new notable
    # categories are expected to be additive.
    kind: str
    summary: str
    timestamp: datetime | None = None


class TurnCompliance(BaseModel):
    """What the agent did in response to a single user utterance."""

    user_text: str
    # tool name -> count of agent tool_calls before the next user utterance
    # (or session end).
    followed_by: dict[str, int] = Field(default_factory=dict)
    step_id: str


class ReportModel(BaseModel):
    """The full assembled single-session post-mortem."""

    header: dict[str, Any] = Field(default_factory=dict)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    findings: FindingsReport
    turns: list[TurnCompliance] = Field(default_factory=list)
    not_checked: dict[str, str] = Field(default_factory=dict)


# --- Small shared helpers over Step/Action shape -------------------------
# (Deliberately mirrors the private helpers in analysis/findings.py rather
# than importing them; see the docstring on _VERIFY_COMMAND_PATTERN above.)


def _tool_name(step: Step) -> str | None:
    if step.action.type != ActionType.TOOL_CALL:
        return None
    name = step.action.payload.get("name")
    return name if isinstance(name, str) else None


def _tool_input(step: Step) -> dict[str, Any]:
    value = step.action.payload.get("input")
    return value if isinstance(value, dict) else {}


def _file_path(step: Step) -> str | None:
    file_path = _tool_input(step).get("file_path")
    return file_path if isinstance(file_path, str) else None


def _command(step: Step) -> str | None:
    command = _tool_input(step).get("command")
    return command if isinstance(command, str) else None


def _is_user_utterance(step: Step) -> bool:
    return (
        step.actor == Actor.USER
        and step.action.type == ActionType.UTTERANCE
        and bool(step.action.payload.get("text"))
    )


def _is_error_step(step: Step) -> bool:
    return bool(step.action.payload.get("is_error")) or bool(step.metadata.get("is_error"))


def _truncate(text: str, limit: int = _MAX_SUMMARY_LEN) -> str:
    """Collapse whitespace and clip to `limit` chars (ellipsis included)."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _tripwire_match(command: str) -> str | None:
    """The name of the first DEFAULT_TRIPWIRE_PATTERNS entry matching `command`."""
    for name, pattern in DEFAULT_TRIPWIRE_PATTERNS.items():
        if re.search(pattern, command, re.IGNORECASE):
            return name
    return None


# --- build_timeline --------------------------------------------------------


def _edit_run_entry(run: list[Step]) -> TimelineEntry:
    """Collapse a maximal run of consecutive edit-family steps into one entry."""
    basenames = [posixpath.basename(_file_path(s) or "?") for s in run]
    unique = sorted(set(basenames))
    n = len(run)
    if len(unique) == 1:
        summary = f"Edited {unique[0]}" if n == 1 else f"Edited {unique[0]} x{n}"
    else:
        summary = f"Edited {n} times across {len(unique)} files"
    return TimelineEntry(
        step_id=run[0].step_id,
        kind="edit",
        summary=_truncate(summary),
        timestamp=run[0].timestamp,
    )


def _user_entry(step: Step) -> TimelineEntry:
    text = str(step.action.payload.get("text", ""))
    return TimelineEntry(
        step_id=step.step_id, kind="user", summary=_truncate(text), timestamp=step.timestamp
    )


def _write_entry(step: Step) -> TimelineEntry:
    file_path = _file_path(step) or "?"
    return TimelineEntry(
        step_id=step.step_id,
        kind="write",
        summary=_truncate(f"Wrote {file_path}"),
        timestamp=step.timestamp,
    )


def _error_entry(step: Step) -> TimelineEntry:
    content = step.action.payload.get("tool_result")
    return TimelineEntry(
        step_id=step.step_id,
        kind="error",
        summary=_truncate(f"Error: {content}"),
        timestamp=step.timestamp,
    )


def _delegation_entry(step: Step, name: str) -> TimelineEntry:
    description = _tool_input(step).get("description")
    label = str(description) if description else name
    return TimelineEntry(
        step_id=step.step_id,
        kind="delegation",
        summary=_truncate(f"Delegated via {name}: {label}"),
        timestamp=step.timestamp,
    )


def _bash_entry(step: Step) -> TimelineEntry | None:
    """The notable timeline entry for a Bash call, or None if it's noise.

    Checked in order: tripwire (most severe/specific), then git commit,
    then generic test/lint/build verification. Any other Bash command
    (curl, ls, cat, ...) is not notable on its own.
    """
    command = _command(step) or ""
    tripwire_name = _tripwire_match(command)
    if tripwire_name:
        return TimelineEntry(
            step_id=step.step_id,
            kind="tripwire",
            summary=_truncate(f"Tripwire {tripwire_name}: {command}"),
            timestamp=step.timestamp,
        )
    if _GIT_COMMIT_PATTERN.search(command):
        return TimelineEntry(
            step_id=step.step_id,
            kind="commit",
            summary=_truncate(f"Commit: {command}"),
            timestamp=step.timestamp,
        )
    if _VERIFY_COMMAND_PATTERN.search(command):
        return TimelineEntry(
            step_id=step.step_id,
            kind="test",
            summary=_truncate(f"Ran: {command}"),
            timestamp=step.timestamp,
        )
    return None


def _notable_entry(step: Step) -> TimelineEntry | None:
    """The notable timeline entry for one non-edit, non-env-effect step, if any."""
    if _is_user_utterance(step):
        return _user_entry(step)
    name = _tool_name(step)
    if name == "Write":
        return _write_entry(step)
    if name == "Bash":
        return _bash_entry(step)
    if name in _DELEGATION_TOOL_NAMES:
        return _delegation_entry(step, name)
    return None


def build_timeline(trace: Trace) -> list[TimelineEntry]:
    """Condense `trace` to its notable events, in step order.

    Notable events: a user utterance; a Bash test/lint/build run; a git
    commit; a Bash tripwire match; a Write; a run of edits collapsed to one
    entry; an errored tool_result; a delegation step. Everything else
    (successful tool_results, Reads, non-matching Bash calls, agent
    narration without a tool call) is noise and is not emitted, so the
    timeline is always shorter than the raw step sequence for any session
    with more than a couple of steps.
    """
    entries: list[TimelineEntry] = []
    edit_run: list[Step] = []

    for step in trace.steps:
        if _tool_name(step) in _EDIT_TOOL_NAMES:
            edit_run.append(step)
            continue

        if step.action.type == ActionType.ENV_EFFECT:
            # A tool's own result is transparent noise unless it errored —
            # in particular a *successful* result must not break an
            # in-progress edit run, since the adapter always interleaves
            # one env_effect step after every tool_call.
            if _is_error_step(step):
                if edit_run:
                    entries.append(_edit_run_entry(edit_run))
                    edit_run = []
                entries.append(_error_entry(step))
            continue

        if edit_run:
            entries.append(_edit_run_entry(edit_run))
            edit_run = []

        entry = _notable_entry(step)
        if entry is not None:
            entries.append(entry)

    if edit_run:
        entries.append(_edit_run_entry(edit_run))

    return entries


# --- extract_turn_compliance ------------------------------------------------


def extract_turn_compliance(trace: Trace) -> list[TurnCompliance]:
    """One entry per real user utterance, with what the agent did next.

    "Real" means ``Actor.USER`` + ``ActionType.UTTERANCE`` with non-empty
    text — this excludes the adapter's tool_result steps, which are also
    ``Actor`` values but never ``Actor.USER``/``ActionType.UTTERANCE``
    together. For each such step, ``followed_by`` counts agent tool_calls
    from the step after it up to (but not including) the next user
    utterance, or the end of the trace if there is none.
    """
    steps = trace.steps
    turn_indices = [i for i, s in enumerate(steps) if _is_user_utterance(s)]

    turns: list[TurnCompliance] = []
    for pos, idx in enumerate(turn_indices):
        end = turn_indices[pos + 1] if pos + 1 < len(turn_indices) else len(steps)
        followed_by: dict[str, int] = {}
        for step in steps[idx + 1 : end]:
            if step.actor != Actor.AGENT or step.action.type != ActionType.TOOL_CALL:
                continue
            name = step.action.payload.get("name")
            if isinstance(name, str):
                followed_by[name] = followed_by.get(name, 0) + 1
        turns.append(
            TurnCompliance(
                user_text=str(steps[idx].action.payload.get("text", "")),
                followed_by=followed_by,
                step_id=steps[idx].step_id,
            )
        )
    return turns


# --- build_report ------------------------------------------------------


def _header(trace: Trace, tool_call_count: int, user_turn_count: int) -> dict[str, Any]:
    steps = trace.steps
    duration: float | None = None
    if steps:
        duration = (steps[-1].timestamp - steps[0].timestamp).total_seconds()
    return {
        "cwd": trace.metadata.get("cwd"),
        "gitBranch": trace.metadata.get("gitBranch"),
        "version": trace.metadata.get("version"),
        "sessionId": trace.metadata.get("sessionId", trace.trace_id),
        "duration_seconds": duration,
        "step_count": len(steps),
        "tool_call_count": tool_call_count,
        "user_turn_count": user_turn_count,
    }


def build_report(
    trace: Trace,
    findings: FindingsReport,
    *,
    config: FindingsConfig | None = None,
) -> ReportModel:
    """Assemble the full post-mortem ``ReportModel`` for `trace` + `findings`.

    `config` is accepted for interface symmetry with the caller (which
    typically passes the same ``FindingsConfig`` used to produce
    `findings`) but is not currently consulted by report assembly itself —
    all findings-related tuning happens upstream in ``analyze_trace``.
    """
    timeline = build_timeline(trace)
    turns = extract_turn_compliance(trace)
    tool_call_count = sum(1 for s in trace.steps if s.action.type == ActionType.TOOL_CALL)
    header = _header(trace, tool_call_count, len(turns))
    return ReportModel(
        header=header,
        timeline=timeline,
        findings=findings,
        turns=turns,
        not_checked=dict(findings.not_checked),
    )


# --- render_markdown -----------------------------------------------------


def _format_header_table(header: dict[str, Any]) -> str:
    rows = [
        ("Session ID", header.get("sessionId")),
        ("Project (cwd)", header.get("cwd")),
        ("Git branch", header.get("gitBranch")),
        ("Version", header.get("version")),
        ("Duration (s)", header.get("duration_seconds")),
        ("Steps", header.get("step_count")),
        ("Tool calls", header.get("tool_call_count")),
        ("User turns", header.get("user_turn_count")),
    ]
    lines = ["| Field | Value |", "| --- | --- |"]
    for label, value in rows:
        lines.append(f"| {label} | {value if value is not None else '(unknown)'} |")
    return "\n".join(lines)


def _format_timeline(timeline: list[TimelineEntry]) -> str:
    if not timeline:
        return "No notable events."
    lines = []
    for entry in timeline:
        ts = entry.timestamp.isoformat() if entry.timestamp else "?"
        lines.append(f"- `{entry.step_id}` **{entry.kind}** ({ts}) — {entry.summary}")
    return "\n".join(lines)


def _format_evidence(evidence: dict[str, Any]) -> str:
    items = ", ".join(f"{key}={value!r}" for key, value in sorted(evidence.items()))
    return items if items else "(none)"


def _format_findings(findings: FindingsReport) -> str:
    total = len(findings.findings)
    counts = ", ".join(
        f"{findings.severity_counts.get(sev.value, 0)} {sev.value}" for sev in _SEVERITY_ORDER
    )
    lines = [f"{total} finding(s): {counts}.", ""]

    by_severity: dict[Severity, list[Finding]] = {sev: [] for sev in _SEVERITY_ORDER}
    for finding in findings.findings:
        by_severity[finding.severity].append(finding)

    for sev in _SEVERITY_ORDER:
        group = by_severity[sev]
        if not group:
            continue
        lines.append(f"### {sev.value.upper()}")
        for finding in group:
            steps_str = ", ".join(finding.step_ids)
            lines.append(
                f"- **{finding.rule_id}** — {finding.title} "
                f"(steps: {steps_str}) — {_format_evidence(finding.evidence)}"
            )
        lines.append("")

    if total == 0:
        lines.append("No findings.")
    return "\n".join(lines).rstrip()


def _format_compliance(turns: list[TurnCompliance]) -> str:
    if not turns:
        return "No user turns recorded."
    lines = []
    for turn in turns:
        lines.append(f'### Turn: "{_truncate(turn.user_text)}"')
        if not turn.followed_by:
            lines.append("- No tool calls followed this turn.")
        else:
            for tool, count in sorted(turn.followed_by.items()):
                lines.append(f"- {tool}: {count}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _format_not_checked(not_checked: dict[str, str]) -> str:
    if not not_checked:
        return "All rules ran."
    return "\n".join(
        f"- **{rule_id}**: {reason}" for rule_id, reason in sorted(not_checked.items())
    )


def render_markdown(report: ReportModel) -> str:
    """Render `report` as a deterministic markdown post-mortem document.

    Section order: Summary, Timeline, Findings, Instruction compliance, Not
    checked. Purely a function of `report`'s own fields — no wall-clock
    timestamps or randomness are introduced here, so calling this twice on
    the same ``ReportModel`` always returns identical strings.
    """
    sections = [
        "# Session post-mortem",
        "",
        "## Summary",
        "",
        _format_header_table(report.header),
        "",
        "## Timeline",
        "",
        _format_timeline(report.timeline),
        "",
        "## Findings",
        "",
        _format_findings(report.findings),
        "",
        "## Instruction compliance",
        "",
        _format_compliance(report.turns),
        "",
        "## Not checked",
        "",
        _format_not_checked(report.not_checked),
        "",
    ]
    return "\n".join(sections)
