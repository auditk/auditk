# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Adapter-health canary (Phase 5 of docs/proposals/session-postmortem-reporting.md).

Finding A of that proposal: the claude-code adapter parses a private,
explicitly unstable format (Claude Code's own docs warn the JSONL layout
"changes between versions"), and it failed *silently* when the harness
renamed its plan-tracking tool (``TodoWrite`` -> ``TaskCreate``/
``TaskUpdate``). A drift score computed on top of a dead parser is worse
than no score at all -- it looks like a real number.

This module is the self-check that catches the next such rename before it
produces a confidently wrong report. Two kinds of invariant, both pure
functions of already-parsed data (no I/O, no clock, no model, never
raises):

- **Corpus-level** (D1): across a corpus of >= ``min_corpus_size`` sessions,
  at least one known plan anchor -- a ``TodoWrite``/``TaskCreate``/
  ``TaskUpdate`` tool call, or a persisted plan-store directory -- must
  appear somewhere. Zero occurrences of every known anchor across that many
  sessions is what a dead parser looks like; below the threshold there
  isn't enough signal to tell "no anchors" apart from "small/light
  corpus", so the check does not run at all.
- **Per-session** (cheap, no magic number, always run regardless of corpus
  size): (a) a session's tool-call / tool-result counts should balance,
  after excluding calls still legitimately in flight when the transcript
  capture simply ends (see ``_trailing_in_flight_call_count`` -- a real
  "clean" fixture used across several other test modules ends exactly this
  way, which is how the naive "raw counts must be equal, no exceptions"
  version of this check was caught false-positiving during GREEN-phase
  implementation and corrected); (b) the share of a session's records whose
  ``type`` is outside ``KNOWN_RECORD_TYPES`` should not exceed
  ``max_unhandled_type_share``.

On ``KNOWN_RECORD_TYPES``: real corpus data shows a Claude Code parent
transcript contains far more record types than ``user``/``assistant`` --
``last-prompt`` and ``queue-operation`` alone are each >5% of records in a
typical session. Treating "not user/assistant" as "unhandled" would
false-positive a dead-parser breach on essentially every healthy session.
The check's actual intent is narrower: catch a *genuinely new, unrecognised*
record type appearing at significant share -- itself a format-change
signal, the same kind of event that silently broke the TodoWrite anchor.
So ``KNOWN_RECORD_TYPES`` is a single-source-of-truth, deliberately
generous allow-list of every type observed to date; a share of records
outside it is what fires the breach. A new type crossing the threshold and
firing IS the canary working as designed: a human triages it and, once
confirmed benign, adds it here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The canary's known-benign set (see module docstring). Seeded with every
# record `type` observed across the local corpus so it does not false-fire
# today. A type outside this set crossing `max_unhandled_type_share` is a
# format-change signal, not routine chatter -- add it here once a human has
# confirmed it's benign noise rather than a broken/renamed parse path.
KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {
        "assistant",
        "user",
        "last-prompt",
        "queue-operation",
        "system",
        "mode",
        "attachment",
        "ai-title",
        "custom-title",
        "permission-mode",
        "file-history-snapshot",
        "pr-link",
        "agent-name",
        "file-history-delta",
        "bridge-session",
        "frame-link",
        "summary",
    }
)

# Tool names that anchor the adapter's "standing plan": the legacy
# TodoWrite anchor and the modern TaskCreate/TaskUpdate pair. Mirrors the
# dispatch in `adapters/claude_code.py:_apply_tool_anchor` and
# `scripts/corpus_stats.py:_PLAN_ANCHOR_TOOLS` (kept here rather than
# imported from either: neither exports it as public API, and `scripts/`
# is not an installable package -- see the module docstring's note on
# `KNOWN_RECORD_TYPES` for the same constraint). Public because `auditk
# doctor` (cli.py) needs it to print the plan-anchor histogram.
PLAN_ANCHOR_TOOL_NAMES = ("TodoWrite", "TaskCreate", "TaskUpdate")

DEFAULT_MIN_CORPUS_SIZE = 20
DEFAULT_MAX_UNHANDLED_TYPE_SHARE = 0.05


@dataclass
class SessionHealthInput:
    """One session's raw parsed JSONL event dicts, plus what's known about
    its persisted plan store, for `check_adapter_health` to evaluate.

    `session_id` is used only to label breach messages; when absent, the
    session's position in the input list is used instead.
    """

    events: list[dict[str, Any]]
    session_id: str | None = None
    has_plan_store: bool = False


@dataclass
class AdapterHealth:
    """Result of `check_adapter_health`: never an exception, never a score."""

    ok: bool
    breaches: list[str] = field(default_factory=list)


def _tool_use_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    """`tool_use` content blocks of an `assistant` event, or [] otherwise.

    Mirrors the shape `adapters/claude_code.py` and `scripts/corpus_stats.py`
    already read (`message.content[*].type == "tool_use"`); malformed or
    absent `message`/`content` shapes are treated as "no tool calls" rather
    than raising, matching this module's own never-raises contract.
    """
    if event.get("type") != "assistant":
        return []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def _tool_result_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    """`tool_result` content blocks of a `user` event, or [] otherwise."""
    if event.get("type") != "user":
        return []
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"]


def _session_anchor_tool_names(events: list[dict[str, Any]]) -> set[str]:
    """Which known plan-anchor tool names this session actually calls."""
    found: set[str] = set()
    for event in events:
        for block in _tool_use_blocks(event):
            name = block.get("name")
            if name in PLAN_ANCHOR_TOOL_NAMES:
                found.add(str(name))
    return found


def _session_tool_call_result_counts(events: list[dict[str, Any]]) -> tuple[int, int]:
    calls = sum(len(_tool_use_blocks(event)) for event in events)
    results = sum(len(_tool_result_blocks(event)) for event in events)
    return calls, results


def _session_unknown_type_share(
    events: list[dict[str, Any]], handled_record_types: frozenset[str]
) -> tuple[float, int, int]:
    """(share, unknown_count, total_count) of records whose `type` is
    outside `handled_record_types`. (0.0, 0, 0) for an empty session."""
    total = len(events)
    if total == 0:
        return 0.0, 0, 0
    unknown = sum(
        1
        for event in events
        if isinstance(event, dict) and event.get("type") not in handled_record_types
    )
    return unknown / total, unknown, total


def _session_label(index: int, session: SessionHealthInput) -> str:
    return session.session_id if session.session_id is not None else f"session[{index}]"


def _trailing_in_flight_call_count(events: list[dict[str, Any]]) -> int:
    """Tool_use calls in the trailing run of `assistant` events for which no
    `user` event appears afterward anywhere in this session.

    A transcript capture legitimately ends mid-call: the harness is killed,
    the user closes the session, or the capture is simply truncated, all
    before the last tool_result is ever written. That is normal and is not
    the "adapter dropped a result" signal this check exists to catch --
    real corpus fixtures show exactly this shape (see the GREEN-phase
    report for the concrete example this was discovered against). Only a
    tool_use call that some LATER event skipped past without ever
    delivering its result -- i.e. one that is NOT part of this trailing,
    still-in-flight run -- counts as a genuine pairing breach below.
    """
    trailing = 0
    for event in reversed(events):
        if event.get("type") == "user":
            break
        if event.get("type") == "assistant":
            trailing += len(_tool_use_blocks(event))
    return trailing


def _check_session(
    index: int,
    session: SessionHealthInput,
    *,
    max_unhandled_type_share: float,
    handled_record_types: frozenset[str],
) -> list[str]:
    """The two cheap, corpus-size-independent per-session structural checks."""
    label = _session_label(index, session)
    breaches: list[str] = []

    calls, results = _session_tool_call_result_counts(session.events)
    if calls != results:
        trailing = _trailing_in_flight_call_count(session.events)
        unresolved = (calls - results) - trailing
        if unresolved > 0:
            breaches.append(
                f"{label}: tool-call/tool-result mismatch: {calls} tool-call(s) vs "
                f"{results} tool-result(s) ({unresolved} unresolved beyond the "
                f"{trailing} still-in-flight trailing call(s))"
            )

    share, unknown, total = _session_unknown_type_share(session.events, handled_record_types)
    if total > 0 and share > max_unhandled_type_share:
        breaches.append(
            f"{label}: unhandled/unknown record type share {share:.1%} "
            f"({unknown}/{total} records) exceeds the {max_unhandled_type_share:.0%} threshold"
        )
    return breaches


def _corpus_has_any_anchor(sessions: list[SessionHealthInput]) -> bool:
    """Whether ANY session in the corpus shows ANY known plan anchor --
    a persisted plan store, or a TodoWrite/TaskCreate/TaskUpdate call."""
    for session in sessions:
        if session.has_plan_store or _session_anchor_tool_names(session.events):
            return True
    return False


def check_adapter_health(
    sessions: list[SessionHealthInput],
    *,
    min_corpus_size: int = DEFAULT_MIN_CORPUS_SIZE,
    max_unhandled_type_share: float = DEFAULT_MAX_UNHANDLED_TYPE_SHARE,
    handled_record_types: frozenset[str] = KNOWN_RECORD_TYPES,
) -> AdapterHealth:
    """Evaluate the adapter-health canary over `sessions`.

    Pure and never raises (D2): any per-session evaluation that somehow
    still errors on malformed input is caught and turned into its own
    breach message rather than propagating, so a caller always gets an
    `AdapterHealth` back, never an exception.

    The corpus-level dead-anchor invariant (D1) only runs once
    ``len(sessions) >= min_corpus_size``; the per-session checks always
    run, regardless of corpus size (including on a single session, which is
    how ``auditk report`` uses this for its one ingested session).
    """
    breaches: list[str] = []

    for index, session in enumerate(sessions):
        try:
            breaches.extend(
                _check_session(
                    index,
                    session,
                    max_unhandled_type_share=max_unhandled_type_share,
                    handled_record_types=handled_record_types,
                )
            )
        except Exception as exc:  # noqa: BLE001 - D2: this function never raises.
            breaches.append(
                f"{_session_label(index, session)}: health check could not evaluate this "
                f"session ({type(exc).__name__}: {exc})"
            )

    if len(sessions) >= min_corpus_size and not _corpus_has_any_anchor(sessions):
        breaches.append(
            "corpus-level: 0 known plan anchor (TodoWrite/TaskCreate/TaskUpdate/persisted "
            f"plan store) occurrences across {len(sessions)} sessions (need >= {min_corpus_size})"
        )

    return AdapterHealth(ok=not breaches, breaches=breaches)
