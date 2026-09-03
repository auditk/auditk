# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Hermes-agent session adapter for auditk.

Converts a Hermes-agent session -- a list of row dicts from its SQLite
``messages`` table (``~/.hermes/state.db``, ``schema_version`` 16 as of this
adapter, confirmed against a live local install), ordered by ``timestamp``
-- into a normalised ``Trace``. Format discovered by reading the writer
source (``hermes_state.py``, ``tools/delegate_tool.py``, ``tools/todo_tool.py``
in the sibling ``hermes-agent`` checkout), not guessed from data alone.

Format notes
------------
Framing: one row per turn/event, flat (no JSONL, no nested tree). The
canonical columns this adapter reads: ``session_id``, ``role`` (one of
``user``/``assistant``/``tool``/``session_meta`` -- the full set observed on
a live corpus), ``content`` (text, nullable), ``tool_call_id`` (a ``tool``
row's own id, joining back to the call it resolves), ``tool_calls`` (a
JSON-encoded **string** on an ``assistant`` row -- OpenAI-style
``[{"id"/"call_id": ..., "type": "function", "function": {"name": ...,
"arguments": "<json string>"}}, ...]``, genuinely double-JSON-encoded as
persisted), ``tool_name`` (a ``tool`` row's own tool name), ``timestamp``
(Unix epoch float), ``reasoning``/``reasoning_content`` (the model's own
chain-of-thought text, when the backend returns one), ``id`` (row primary
key), and ``active`` (``0`` marks a row *rewound* out of the live
transcript -- ``hermes_state.py`` itself excludes ``active=0`` rows from
its own default reads, so this adapter does the same rather than inventing
a new convention).

Ids / pairing: **real**, unlike LangGraph/generic-otel. A ``tool_calls``
entry's ``id`` (equivalently ``call_id`` -- identical in every sample seen)
is later echoed back verbatim as the resolving ``tool`` row's own
``tool_call_id``. Unlike Claude Code (where a `tool_result` lives inside a
`user` event's own content blocks), Hermes gives tool results their own
first-class ``role="tool"`` row -- no digging through a parent event's
content list required.

Declared intent: three-tier precedence, directly mirroring Claude Code's
(see ``claude_code.py``'s module docstring) because Hermes' own schema
happens to carry a genuine analogue of each tier:

1. Inline narration -- an ``assistant`` row's own ``content`` text.
2. ``reasoning_content`` (falling back to ``reasoning`` if that is absent
   but the other is present -- both were identical in every real sample
   seen) -- Hermes' own explicit chain-of-thought column, the same
   "weaker, unverified proxy" caveat as Claude Code's ``thinking`` blocks.
3. The standing plan, anchored by calls to Hermes' own ``todo`` tool (see
   ``tools/todo_tool.py``'s ``TodoStore``) -- the direct analogue of Claude
   Code's ``TodoWrite``/``TaskCreate``/``TaskUpdate``. Unlike Claude Code's
   ``TaskUpdate`` (which must *guess* a task's position from a bare
   ``taskId`` -- see ``claude_code.py:_resolve_task_index``), Hermes' own
   todo items always carry an explicit, agent-chosen ``id`` and the tool's
   real ``merge`` flag (``false`` = full replace, ``true`` = update-by-id/
   append -- ``TodoStore.write``), so no positional guesswork is needed
   here at all.

Delegation: Hermes has a real subagent-delegation concept (the
``delegate_task`` tool, ``tools/delegate_tool.py``), and a delegated child
*does* get its own session row (``sessions.parent_session_id`` plus a
``model_config._delegate_from`` marker set at creation -- confirmed by
reading ``hermes_state.py``). But -- confirmed by reading
``delegate_task``'s own return-value construction end to end -- **no id
anywhere joins a specific ``delegate_task`` tool call to the specific child
session(s) it spawned**: the JSON returned to the parent
(``{"results": [{"task_index", "status", "summary", "model", "tokens",
...}], "total_duration_seconds"}``) never includes a child ``session_id``,
and a single batched call can spawn several children that all share the
same ``parent_session_id`` with nothing to disambiguate which child came
from which task index at the session-row level. This is the genuine
delegation-linkage analogue of LangGraph/generic-otel's "no call/result
id-pairing concept" -- except here it is Hermes' call/result pairing that
*is* real (see above) while it is the *cross-session delegation join*
specifically that has no id to key off. Per docs/adapters.md's "No
fabricated pairings" rule, this adapter therefore never attempts subagent-
transcript stitching (no analogue of Claude Code's
``_ingest_subagent_transcript``): every ``delegate_task`` ``TOOL_CALL``
step is marked ``metadata["delegation_unobserved"] = True``
unconditionally, and that marker can never be cleared from a single
``ingest()`` call -- there is no evidence available within one session's
own message list that could ever clear it.

Parent linkage: the ``messages`` table has no per-row parent-link column
at all (no analogue of Claude Code's ``parentUuid`` or LangGraph's
``parent_config``) -- the only structural chaining this adapter can
honestly populate is the within-message chain across an ``assistant``
row's own multiple ``tool_calls`` entries (mirroring
``claude_code.py:_assistant_steps``' chaining of multiple ``tool_use``
blocks in one event).

Version marker: ``hermes_state.py``'s own ``SCHEMA_VERSION`` (16 at the
time this adapter was written, confirmed via ``PRAGMA``/the live
``schema_version`` table on a real ``state.db``). This adapter reads a
level of the schema (four ``messages`` columns, one flat row shape) stable
enough that it is not pinned to an exact schema version, but Hermes is an
active project (a compression-triggered ``parent_session_id`` chain and
delegate-child tagging were both added by "v16" migrations per
``hermes_state.py``'s own comments) -- format churn here is a real risk,
noted for whoever maintains this next.

Redaction pass-through
-----------------------
Set ``strip_payloads=True`` to redact a ``TOOL_CALL`` step's ``input`` and
an ``ENV_EFFECT`` step's ``tool_result`` via the shared, adapter-generic
post-ingest pass (``auditk.adapters.redaction.redact_trace``) -- this
adapter is new, so it reaches for that shared mechanism rather than
reimplementing Claude Code's own inline ``_maybe_redact`` (see
docs/adapters.md's "Two redaction mechanisms, by design"). ``UTTERANCE``
payloads (narration, the model's own response) are deliberately left
untouched, the same convention every other shipped adapter follows.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from auditk.adapters.health import HealthDeclaration
from auditk.adapters.redaction import ContentKeysByActionType, redact_trace
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    FlowType,
    Step,
    Trace,
)

# Every `role` value observed on a live Hermes corpus (`select distinct role
# from messages`) -- the direct analogue of Claude Code's KNOWN_RECORD_TYPES.
# A `role` outside this set is exactly the format-drift signal the health
# canary's unknown-record-type-share check exists to catch.
KNOWN_RECORD_TYPES: frozenset[str] = frozenset({"user", "assistant", "tool", "session_meta"})

# `session_meta` rows are session-level bookkeeping, not a turn -- excluded
# from step construction the same way Claude Code's own `_SUBSTANTIVE_TYPES`
# is a strict subset of its `KNOWN_RECORD_TYPES` (both live in this module
# for the same "known but not step-worthy" reason).
_SUBSTANTIVE_ROLES = ("user", "assistant", "tool")

_PLAN_ANCHOR_TOOL_NAME = "todo"
_PLAN_ACTIVE_STATUSES = ("pending", "in_progress")

# `delegate_task` is Hermes' only delegation tool (`tools/delegate_tool.py`,
# `toolsets.py`'s "delegation" toolset) -- see the module docstring for why
# every call to it is marked `delegation_unobserved` unconditionally.
_DELEGATION_TOOL_NAMES = frozenset({"delegate_task"})

# Redaction pass-through (see module docstring): a TOOL_CALL step's `input`
# (the parsed tool_calls[].function.arguments) and an ENV_EFFECT step's
# `tool_result` (a `tool` row's own `content`) are the only content-bearing
# payload keys this adapter produces. UTTERANCE is deliberately absent.
REDACTION_CONTENT_KEYS: ContentKeysByActionType = {
    ActionType.TOOL_CALL: frozenset({"input"}),
    ActionType.ENV_EFFECT: frozenset({"tool_result"}),
}


@dataclass
class PlanState:
    """Standing-plan state threaded across `_assistant_steps` calls.

    `todos` is keyed by the todo tool's own agent-chosen item id (see the
    module docstring: unlike Claude Code's `TaskUpdate`, no positional
    guesswork is needed here since Hermes' own todo items always carry a
    real id), in insertion order -- `dict` preserves that in Python 3.7+.
    """

    standing_plan: str | None = None
    todos: dict[str, dict[str, Any]] = field(default_factory=dict)


def _is_active(record: dict[str, Any]) -> bool:
    """A row is active unless its own `active` column is falsy/zero.

    Mirrors `hermes_state.py`'s own default-read behaviour: "Rewound
    (active=0) rows are excluded by default." A missing `active` key
    (older export, or a caller that only supplied the columns it had) is
    treated as active, matching the DB's own `NOT NULL DEFAULT 1`.
    """
    value = record.get("active")
    return value is None or bool(value)


def _parse_tool_calls(record: dict[str, Any]) -> list[dict[str, Any]]:
    """An `assistant` row's `tool_calls`, decoded from its real on-disk
    shape (a JSON-encoded string), or `[]` if absent/malformed.

    Defensive by construction (never raises): an unparseable or
    unexpectedly-typed `tool_calls` value degrades to "no tool calls" for
    that row, which falls through to a plain UTTERANCE step rather than
    crashing -- this is what lets malformed input be processed best-effort
    per docs/adapters.md's "malformed-input" section.
    """
    raw = record.get("tool_calls")
    if not raw:
        return []
    if isinstance(raw, list):
        calls = raw
    elif isinstance(raw, str):
        try:
            calls = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    else:
        return []
    if not isinstance(calls, list):
        return []
    return [c for c in calls if isinstance(c, dict)]


def _tool_call_id(call: dict[str, Any]) -> str | None:
    """A tool_calls entry's own id -- `id` and `call_id` were identical in
    every real sample seen; `id` is preferred, `call_id` is the fallback."""
    value = call.get("id") or call.get("call_id")
    return str(value) if value else None


def _tool_call_name(call: dict[str, Any]) -> str | None:
    function = call.get("function")
    name = function.get("name") if isinstance(function, dict) else None
    return str(name) if name else None


def _tool_call_arguments(call: dict[str, Any]) -> Any:
    """A tool_calls entry's `function.arguments`, decoded from its real
    on-disk shape (a JSON-encoded string nested inside the already-decoded
    `tool_calls` list) into a dict when possible.

    Falls back to the raw string if it doesn't parse as JSON, rather than
    raising -- the same best-effort discipline as `_parse_tool_calls`.
    """
    function = call.get("function")
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(arguments, str):
        try:
            return json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
    return arguments


def _active_todos_text(todos: dict[str, dict[str, Any]]) -> str | None:
    active = [
        str(t["content"])
        for t in todos.values()
        if t.get("content") and t.get("status") in _PLAN_ACTIVE_STATUSES
    ]
    if not active:
        return None
    return "\n".join(active)


def _apply_todo_call(call: dict[str, Any], plan: PlanState) -> PlanState:
    """Fold one `todo` tool call into the standing plan, honouring the real
    `merge` semantics `tools/todo_tool.py:TodoStore.write` implements:
    `merge=False` (the default) replaces the list outright; `merge=True`
    updates existing items by id and appends new ones. Malformed
    arguments (not a dict, no `todos` list) leave `plan` unchanged rather
    than raising.
    """
    arguments = _tool_call_arguments(call)
    if not isinstance(arguments, dict):
        return plan
    todos_arg = arguments.get("todos")
    if not isinstance(todos_arg, list):
        return plan
    merge = bool(arguments.get("merge"))
    items: dict[str, dict[str, Any]] = dict(plan.todos) if merge else {}
    for entry in todos_arg:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id")
        if not item_id:
            continue
        item_id = str(item_id)
        existing = items.get(item_id, {})
        content = entry.get("content", existing.get("content"))
        status = entry.get("status", existing.get("status"))
        items[item_id] = {"content": content, "status": status}
    return PlanState(standing_plan=_active_todos_text(items), todos=items)


def _update_standing_plan(tool_calls: list[dict[str, Any]], plan: PlanState) -> PlanState:
    for call in tool_calls:
        if _tool_call_name(call) == _PLAN_ANCHOR_TOOL_NAME:
            plan = _apply_todo_call(call, plan)
    return plan


def _narration_text(record: dict[str, Any]) -> str | None:
    content = record.get("content")
    if not isinstance(content, str):
        return None
    text = content.strip()
    return text or None


def _thinking_text(record: dict[str, Any]) -> str | None:
    """Declared-intent text from `reasoning_content` (preferred) or
    `reasoning` -- Hermes' own chain-of-thought columns, a weaker,
    unverified proxy for intent than explicit narration (see module
    docstring), exactly the same caveat `claude_code.py:_join_thinking`
    documents for `thinking` blocks."""
    value = record.get("reasoning_content") or record.get("reasoning")
    if not value:
        return None
    text = str(value).strip()
    return text or None


def _parse_ts(raw: Any) -> datetime:
    if raw is None:
        return datetime.now(UTC)
    try:
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (TypeError, ValueError, OSError):
        return datetime.now(UTC)


def _record_id(record: dict[str, Any], record_index: int, trace_id: str) -> str:
    row_id = record.get("id")
    return str(row_id) if row_id is not None else f"{trace_id}-{record_index}"


def _step_id(record: dict[str, Any], record_index: int, sub_index: int, trace_id: str) -> str:
    """Mirrors `claude_code.py:_step_id`: the row's own id (or a
    trace-relative fallback) for the first step it produces, with `-{i}`
    appended for the i-th additional step chained off the same row (one
    `assistant` row's multiple `tool_calls` entries)."""
    base = _record_id(record, record_index, trace_id)
    return base if sub_index == 0 else f"{base}-{sub_index}"


def _user_step(record: dict[str, Any], record_index: int, trace_id: str) -> Step:
    text = _narration_text(record) or ""
    action = Action(type=ActionType.UTTERANCE, payload={"text": text})
    return Step(
        step_id=_step_id(record, record_index, 0, trace_id),
        parent_step_id=None,
        trace_id=trace_id,
        timestamp=_parse_ts(record.get("timestamp")),
        actor=Actor.USER,
        declared_intent=None,
        action=action,
        metadata={},
    )


def _tool_step(record: dict[str, Any], record_index: int, trace_id: str) -> Step:
    """A `role="tool"` row -> one ENV_EFFECT step.

    Unlike Claude Code's `tool_result.is_error`, Hermes' `messages` schema
    has no first-class error flag on a tool-result row at all -- any
    success/failure signal (e.g. a `terminal` tool's own `exit_code` in its
    JSON-shaped `content`) is embedded, tool-specific, and not something
    this adapter parses out (that would mean assuming a per-tool content
    schema Hermes itself does not declare) -- a real, documented gap, not
    an oversight.
    """
    payload = {"tool_result": record.get("content")}
    action = Action(type=ActionType.ENV_EFFECT, payload=payload)
    return Step(
        step_id=_step_id(record, record_index, 0, trace_id),
        parent_step_id=None,
        trace_id=trace_id,
        timestamp=_parse_ts(record.get("timestamp")),
        actor=Actor.TOOL,
        declared_intent=None,
        action=action,
        metadata={},
    )


def _assistant_steps(
    record: dict[str, Any],
    record_index: int,
    trace_id: str,
    plan: PlanState,
) -> tuple[list[Step], PlanState]:
    tool_calls = _parse_tool_calls(record)
    narration = _narration_text(record)
    thinking = _thinking_text(record)
    plan = _update_standing_plan(tool_calls, plan)
    standing_plan = plan.standing_plan

    if not tool_calls:
        # Explicit signals (narration, then thinking) win over the standing
        # plan and become it going forward -- mirrors
        # claude_code.py:_assistant_steps' precedence exactly (see module
        # docstring).
        message_intent = narration or thinking
        if message_intent:
            standing_plan = message_intent
        action = Action(type=ActionType.UTTERANCE, payload={"text": narration})
        intent = message_intent if message_intent else standing_plan
        step = Step(
            step_id=_step_id(record, record_index, 0, trace_id),
            parent_step_id=None,
            trace_id=trace_id,
            timestamp=_parse_ts(record.get("timestamp")),
            actor=Actor.AGENT,
            declared_intent=intent,
            action=action,
            metadata={},
        )
        return [step], PlanState(standing_plan=standing_plan, todos=plan.todos)

    steps: list[Step] = []
    message_intent = narration or thinking
    for i, call in enumerate(tool_calls):
        name = _tool_call_name(call)
        arguments = _tool_call_arguments(call)
        action = Action(type=ActionType.TOOL_CALL, payload={"name": name, "input": arguments})
        intent = message_intent if i == 0 and message_intent else standing_plan
        metadata: dict[str, Any] = {}
        if name in _DELEGATION_TOOL_NAMES:
            # Never clearable from a single ingest() call -- see the module
            # docstring's "Delegation" section: no id anywhere joins this
            # call to the child session(s) it spawned.
            metadata["delegation_unobserved"] = True
        steps.append(
            Step(
                step_id=_step_id(record, record_index, i, trace_id),
                parent_step_id=None if i == 0 else steps[-1].step_id,
                trace_id=trace_id,
                timestamp=_parse_ts(record.get("timestamp")),
                actor=Actor.AGENT,
                declared_intent=intent,
                action=action,
                metadata=metadata,
            )
        )
    # Narration/thinking on a tool-issuing message is transient (feeds only
    # the first step above), not a standing-plan update -- matches
    # claude_code.py's PlanState(pending_intent=None, ...) on this branch.
    return steps, PlanState(standing_plan=standing_plan, todos=plan.todos)


def _first_session_id(substantive: list[dict[str, Any]]) -> str:
    value = substantive[0].get("session_id")
    return str(value) if value else "unknown"


def ingest_hermes_session(records: list[dict[str, Any]]) -> Trace:
    """Convert a list of Hermes `messages`-table row dicts into a Trace.

    Args:
        records: The session's message rows, in `timestamp` order (a
            `SELECT * FROM messages WHERE session_id = ? ORDER BY
            timestamp` export is the natural producer of this shape).

    Raises:
        ValueError: If `records` is empty, or contains no active
            user/assistant/tool row at all (mirrors
            `claude_code.py:ingest_claude_code_session`'s "no substantive
            events" refusal).
    """
    if not records:
        raise ValueError("hermes session must not be empty")

    try:
        active_records = [r for r in records if isinstance(r, dict) and _is_active(r)]
        substantive = [r for r in active_records if r.get("role") in _SUBSTANTIVE_ROLES]
        if not substantive:
            raise ValueError("hermes session contains no active user/assistant/tool records")

        session_id = _first_session_id(substantive)
        steps: list[Step] = []
        plan = PlanState()
        for index, record in enumerate(substantive):
            role = record.get("role")
            if role == "assistant":
                new_steps, plan = _assistant_steps(record, index, session_id, plan)
                steps.extend(new_steps)
            elif role == "user":
                steps.append(_user_step(record, index, session_id))
            else:  # role == "tool"
                steps.append(_tool_step(record, index, session_id))
    except (KeyError, TypeError, AttributeError) as exc:
        # Refuse cleanly on malformed session data rather than letting an
        # opaque, undocumented KeyError/TypeError/AttributeError leak from
        # deep inside the per-row helpers above.
        raise ValueError(f"malformed Hermes session data: {exc}") from exc

    return Trace(
        trace_id=session_id,
        flow_type=FlowType.GENERIC,
        agent_config_ref=f"hermes:{session_id}",
        steps=steps,
        source_adapter="hermes",
    )


# Health-canary declaration (docs/adapters.md's "The health canary").
# Unlike LangGraph/generic-otel, Hermes supports ALL THREE sub-checks: real
# call/result ids (see module docstring), a real record-type vocabulary
# (`role`, empirically confirmed against a live corpus), and a real
# plan-anchor tool (`todo`).


def _hermes_record_type(record: dict[str, Any]) -> str | None:
    if not isinstance(record, dict):
        return None
    role = record.get("role")
    return str(role) if role is not None else None


def _hermes_call_ids(record: dict[str, Any]) -> list[str | None]:
    """The id of every tool_calls entry on one `assistant` row."""
    if not isinstance(record, dict) or record.get("role") != "assistant":
        return []
    return [_tool_call_id(c) for c in _parse_tool_calls(record)]


def _hermes_result_ref_ids(record: dict[str, Any]) -> list[str | None]:
    """The `tool_call_id` one `tool` row claims to resolve.

    Always a length-1 list for a `tool` row (it always reports exactly one
    result), `[None]` if `tool_call_id` itself is missing -- mirrors
    `claude_code.py:_cc_result_ref_ids`' "count the record, note the
    missing id" behaviour rather than dropping it from the count entirely.
    """
    if not isinstance(record, dict) or record.get("role") != "tool":
        return []
    value = record.get("tool_call_id")
    return [str(value) if value else None]


def _hermes_pairing_boundary(record: dict[str, Any]) -> bool:
    """A `user` row is a hard trailing-in-flight boundary for the id-less
    fallback, mirroring `claude_code.py`'s own choice: a new user turn is
    the clearest "the previous call round is over" signal Hermes' own
    session shape carries."""
    return isinstance(record, dict) and record.get("role") == "user"


def _hermes_call_names(record: dict[str, Any]) -> list[str]:
    if not isinstance(record, dict) or record.get("role") != "assistant":
        return []
    names: list[str] = []
    for call in _parse_tool_calls(record):
        name = _tool_call_name(call)
        if name:
            names.append(name)
    return names


HERMES_HEALTH_DECLARATION = HealthDeclaration(
    name="hermes",
    record_type=_hermes_record_type,
    known_record_types=KNOWN_RECORD_TYPES,
    call_ids=_hermes_call_ids,
    result_ref_ids=_hermes_result_ref_ids,
    pairing_boundary=_hermes_pairing_boundary,
    call_names=_hermes_call_names,
    anchor_tool_names=frozenset({_PLAN_ANCHOR_TOOL_NAME}),
)


class HermesTraceAdapter:
    """Structural TraceAdapter for Hermes-agent session message-row lists.

    Set ``strip_payloads=True`` to redact TOOL_CALL/ENV_EFFECT steps'
    content-bearing payload keys via the shared post-ingest redaction pass
    (``auditk.adapters.redaction.redact_trace`` -- see module docstring).
    """

    def __init__(self, strip_payloads: bool = False) -> None:
        self.strip_payloads = strip_payloads

    def ingest(self, raw: Any) -> Trace:
        trace = ingest_hermes_session(raw)
        if self.strip_payloads:
            trace = redact_trace(trace, REDACTION_CONTENT_KEYS)
        return trace
