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
  capture simply ends. Where every call/result carries an id (real Claude
  Code transcripts do: ``tool_use.id`` / ``tool_result.tool_use_id``),
  results are paired to calls by id and "in flight" gets an exact
  definition (see ``_unresolved_call_count_by_id``); id-less input (a real
  "clean" fixture used across several other test modules ends exactly this
  way) falls back to the coarser, type/count-only
  ``_trailing_in_flight_call_count`` heuristic -- which is how the naive
  "raw counts must be equal, no exceptions" version of this check was
  caught false-positiving during GREEN-phase implementation and corrected,
  and later how a doctor false breach on trailing *parallel* calls (two
  calls, one resolved, capture truncated before the other's result) was
  caught and fixed by adding id-matching on top; (b) the share of a
  session's records whose ``type`` is outside ``KNOWN_RECORD_TYPES`` should
  not exceed ``max_unhandled_type_share``.

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
class SubagentHealthInput:
    """One `agent-*.jsonl` candidate found under a session's `subagents/`
    directory, for `check_adapter_health` to evaluate -- successfully
    loadable by `load_subagent_transcripts` or not.

    `has_meta`/`has_tool_use_id` model the two failure modes
    `load_subagent_transcripts` (`adapters/claude_code.py`, P3) currently
    tolerates silently rather than raising: an `agent-*.jsonl` with no
    sidecar `.meta.json` at all, or a `.meta.json` present but missing the
    `toolUseId` join key. Both are a broken `subagents/` layout -- the same
    kind of format drift the parent-transcript canary already exists to
    catch, just one directory level deeper.
    """

    agent_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    has_meta: bool = True
    has_tool_use_id: bool = True


@dataclass
class SessionHealthInput:
    """One session's raw parsed JSONL event dicts, plus what's known about
    its persisted plan store and its subagent (delegate) transcripts, for
    `check_adapter_health` to evaluate.

    `session_id` is used only to label breach messages; when absent, the
    session's position in the input list is used instead. `subagents`
    defaults to `[]`, so any existing caller/test built before P4 (which
    never mentions subagents at all) is completely unaffected.
    """

    events: list[dict[str, Any]]
    session_id: str | None = None
    has_plan_store: bool = False
    subagents: list[SubagentHealthInput] = field(default_factory=list)


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


def _tool_use_id(block: dict[str, Any]) -> str | None:
    """The `id` of a `tool_use` block (`"toolu_..."` in real transcripts),
    or None if absent/falsy -- which of the pairing strategies below
    applies is decided entirely by whether every call/result in a session
    has one of these, so this returning None for even one block is what
    triggers the id-less fallback in `_unresolved_call_count_by_id`."""
    value = block.get("id")
    return str(value) if value else None


def _tool_result_ref_id(block: dict[str, Any]) -> str | None:
    """The `tool_use_id` a `tool_result` block claims to resolve, or None
    if absent/falsy."""
    value = block.get("tool_use_id")
    return str(value) if value else None


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

    ID-LESS FALLBACK ONLY as of the id-matched pairing fix: this coarse,
    type/count-only heuristic can't tell "two trailing parallel calls, one
    resolved" apart from "an orphaned call followed by an unrelated,
    separately-resolved one" -- both reduce to the same
    `[assistant(call), assistant(call), user(N results)]` shape (see
    `_unresolved_call_count_by_id`'s docstring for the concrete case this
    caused). `_check_session` only reaches for this function when
    `_unresolved_call_count_by_id` returns None, i.e. the session's
    tool_use/tool_result blocks don't reliably carry ids to match by.
    """
    trailing = 0
    for event in reversed(events):
        if event.get("type") == "user":
            break
        if event.get("type") == "assistant":
            trailing += len(_tool_use_blocks(event))
    return trailing


def _unresolved_call_count_by_id(events: list[dict[str, Any]]) -> int | None:
    """Genuine (non-trailing) unresolved tool_use call count, pairing each
    call to its result by id -- or None if this session can't be reliably
    id-matched (any tool_use or tool_result block is missing its id), which
    tells `_check_session` to fall back to `_trailing_in_flight_call_count`'s
    coarser, count-only heuristic instead.

    This is the fix for a real doctor false breach: a live session's tail
    was `assistant(tool_use: WebSearch), assistant(tool_use: WebFetch),
    user(tool_result for the WebSearch call)` -- two trailing parallel
    calls, one resolved, capture truncated before the second's result
    arrived. `_trailing_in_flight_call_count` walks in reverse and stops at
    the FIRST `user` event, so it excused nothing here (that event wasn't
    an unbroken run of `assistant` events) and the still-open WebFetch call
    false-breached. But nothing in `_trailing_in_flight_call_count`'s
    type/count-only view can tell that shape apart from a genuinely
    orphaned call followed by an unrelated, separately-resolved one --
    both are `[assistant(call), assistant(call), user(1 result)]`. Real
    transcripts carry enough to resolve the ambiguity: `tool_use` blocks
    have `id`, `tool_result` blocks have `tool_use_id` naming which call
    they resolve (confirmed against a live Claude Code session's raw
    JSONL). Once results are matched to calls by id rather than by
    position/count, "trailing in-flight" gets an exact definition: a call
    is excused iff it is unresolved AND no call issued at-or-after it ever
    gets a tool_result anywhere in the transcript -- i.e. the maximal
    SUFFIX of the issuance-ordered call list that is entirely unresolved.
    Any unresolved call before that suffix has a LATER call that DID get
    resolved, meaning the capture plainly kept going after it -- a genuine,
    mid-transcript pairing breach, not truncation.
    """
    call_ids: list[str | None] = [
        _tool_use_id(block) for event in events for block in _tool_use_blocks(event)
    ]
    if not call_ids or any(call_id is None for call_id in call_ids):
        return None

    result_ref_ids: list[str | None] = [
        _tool_result_ref_id(block) for event in events for block in _tool_result_blocks(event)
    ]
    if any(ref_id is None for ref_id in result_ref_ids):
        return None

    resolved_ids = set(result_ref_ids)
    unresolved_flags = [call_id not in resolved_ids for call_id in call_ids]

    trailing_excused = 0
    for is_unresolved in reversed(unresolved_flags):
        if not is_unresolved:
            break
        trailing_excused += 1

    return sum(unresolved_flags) - trailing_excused


def _check_subagent(
    label: str,
    subagent: SubagentHealthInput,
    *,
    max_unhandled_type_share: float,
    handled_record_types: frozenset[str],
) -> list[str]:
    """Breach(es) for one subagent transcript candidate.

    A broken `subagents/` layout (no `meta.json`, or a `meta.json` missing
    `toolUseId`) is checked first and, if present, is the ONLY breach
    reported for this subagent -- there's no reliable joined identity left
    to also run a content check against, and reporting both would just be
    noise on top of the one real signal. Only when the layout itself is
    clean does the SAME unknown-record-type-share check already used for
    parent transcripts run against this subagent's own events.
    """
    if not subagent.has_meta:
        return [
            f"{label}: subagent {subagent.agent_id} has no meta.json "
            "(broken subagents/ layout -- load_subagent_transcripts would skip it)"
        ]
    if not subagent.has_tool_use_id:
        return [
            f"{label}: subagent {subagent.agent_id} has a meta.json but it is missing "
            "toolUseId (broken subagents/ layout -- cannot join it to its parent Task step)"
        ]

    share, unknown, total = _session_unknown_type_share(subagent.events, handled_record_types)
    if total > 0 and share > max_unhandled_type_share:
        return [
            f"{label}: subagent {subagent.agent_id} unhandled/unknown record type share "
            f"{share:.1%} ({unknown}/{total} records) exceeds the "
            f"{max_unhandled_type_share:.0%} threshold"
        ]
    return []


def _check_session(
    index: int,
    session: SessionHealthInput,
    *,
    max_unhandled_type_share: float,
    handled_record_types: frozenset[str],
) -> list[str]:
    """The cheap, corpus-size-independent per-session structural checks:
    (a) tool-call/tool-result pairing, (b) unknown-record-type share, both
    over the parent transcript's own events, and (c) the same checks
    (layout + unknown-type-share) over each of this session's subagent
    transcripts, if any."""
    label = _session_label(index, session)
    breaches: list[str] = []

    calls, results = _session_tool_call_result_counts(session.events)
    if calls != results:
        unresolved = _unresolved_call_count_by_id(session.events)
        if unresolved is None:
            # Id-less fallback (see _unresolved_call_count_by_id's
            # docstring): can't pair by id, so fall back to the coarser
            # type/count-only trailing heuristic.
            trailing = _trailing_in_flight_call_count(session.events)
            unresolved = (calls - results) - trailing
        else:
            trailing = (calls - results) - unresolved
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

    for subagent in session.subagents:
        breaches.extend(
            _check_subagent(
                label,
                subagent,
                max_unhandled_type_share=max_unhandled_type_share,
                handled_record_types=handled_record_types,
            )
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
