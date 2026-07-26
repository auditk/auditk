# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Claude Code session adapter for auditk.

Converts a Claude Code session — a list of JSONL event dicts as persisted under
``~/.claude/projects/<encoded-path>/<session-uuid>.jsonl`` — into a normalised
``Trace``. The session is a tree of events linked by ``parentUuid``; each
substantive ``user``/``assistant`` event expands into one or more Steps.

Set ``strip_payloads=True`` to redact tool inputs and tool-result contents,
keeping names, types, and timing. Use that mode when building evidence packs
from real sessions that may contain sensitive code.

Declared-intent anchor
-----------------------
The adapter tracks a "standing plan" — a best-effort summary of what the
agent says it is still working on — that flows forward across steps until
something more specific narrows it. Three sources feed a single step's
``declared_intent``, in precedence order:

1. Inline narration (an assistant ``text`` block in the same message).
2. A ``thinking`` block in the same message (a weaker, unverified proxy for
   intent than explicit narration — reasoning traces are not commitments).
3. The standing plan itself (unchanged from the most recent anchor update).

The standing plan is anchored by, in order of authority:

- The persisted plan store (``plan_tasks``), when supplied by the caller —
  this is the harness's own source of truth for the active task list.
- ``TaskCreate``/``TaskUpdate`` tool calls in the transcript (the modern
  harness's task-tracking tool pair).
- ``TodoWrite`` tool calls (the legacy harness's task-tracking tool, kept
  for back-compat with older sessions).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auditk.schema import (
    Action,
    ActionType,
    Actor,
    FlowType,
    Step,
    Trace,
)


@dataclass
class PlanState:
    pending_intent: str | None = None
    standing_plan: str | None = None
    # Transcript-reconstructed TaskCreate/TaskUpdate task list, in creation
    # order. TaskCreate calls carry no id, so this is a best-effort
    # reconstruction — see `_resolve_task_index`.
    created_tasks: list[dict[str, Any]] = field(default_factory=list)


_SUBSTANTIVE_TYPES = ("user", "assistant")
_REDACTION_KEY = "redacted"
_PLAN_ACTIVE_STATUSES = ("pending", "in_progress")
_DELEGATION_TOOL_NAMES = frozenset({"Task", "Agent"})
_TRACE_METADATA_FIELDS = ("cwd", "gitBranch", "version", "sessionId")


def ingest_claude_code_session(
    events: list[dict[str, Any]],
    strip_payloads: bool = False,
    *,
    plan_tasks: list[dict[str, Any]] | None = None,
) -> Trace:
    """Convert a list of Claude Code session event dicts into a Trace.

    Args:
        events: The session's JSONL event dicts, in file order.
        strip_payloads: Redact tool inputs/results, keeping names and shape.
        plan_tasks: Task dicts from the harness's persisted plan store (see
            `load_plan_tasks`), if available. When given, the active tasks
            (status ``pending``/``in_progress``) seed the standing plan
            before the transcript is walked; transcript ``TaskCreate``/
            ``TaskUpdate``/``TodoWrite`` calls still update it from there.

    Raises:
        ValueError: If no substantive (user/assistant) events are present.
    """
    substantive = [e for e in events if e.get("type") in _SUBSTANTIVE_TYPES]
    if not substantive:
        raise ValueError("session contains no user/assistant events")

    session_id = str(substantive[0].get("sessionId") or "unknown")
    steps: list[Step] = []
    plan = PlanState(standing_plan=_plan_active_text(plan_tasks) if plan_tasks else None)
    for event in substantive:
        new_steps, plan = _event_to_steps(event, session_id, strip_payloads, plan)
        steps.extend(new_steps)

    return Trace(
        trace_id=session_id,
        flow_type=FlowType.CODE,
        agent_config_ref=f"claude-code:{session_id}",
        steps=steps,
        source_adapter="claude-code",
        metadata=_trace_metadata(substantive[0]),
    )


def load_plan_tasks(session_dir: Path) -> list[dict[str, Any]]:
    """Read a harness plan store directory into a list of task dicts.

    Each task is persisted as its own ``<n>.json`` file under
    ``session_dir``; this loads them sorted by numeric filename stem (the
    harness's own ordering). Returns an empty list if the directory does not
    exist. Malformed or non-numeric-stem files are skipped rather than
    raising, since this is best-effort forensic tooling reading files a
    third-party harness controls, not a producer we can validate at write
    time.
    """
    if not session_dir.is_dir():
        return []
    task_files = sorted(
        (p for p in session_dir.glob("*.json") if p.stem.isdigit()),
        key=lambda p: int(p.stem),
    )
    tasks: list[dict[str, Any]] = []
    for task_file in task_files:
        try:
            data = json.loads(task_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            tasks.append(data)
    return tasks


def _trace_metadata(first_event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: first_event[key] for key in _TRACE_METADATA_FIELDS if first_event.get(key) is not None
    }


def _event_to_steps(
    event: dict[str, Any],
    trace_id: str,
    strip: bool,
    plan: PlanState,
) -> tuple[list[Step], PlanState]:
    if event.get("type") == "assistant":
        return _assistant_steps(event, trace_id, strip, plan)
    return _user_steps(event, trace_id, strip), plan


def _extract_todos(block: dict[str, Any]) -> list[dict[str, Any]] | None:
    if block.get("name") != "TodoWrite":
        return None
    input_data = block.get("input")
    if not isinstance(input_data, dict):
        return None
    todos = input_data.get("todos")
    if not isinstance(todos, list):
        return None
    return todos


def _active_goal_text(todos: list[dict[str, Any]]) -> str | None:
    active = [
        str(todo["content"])
        for todo in todos
        if isinstance(todo, dict)
        and todo.get("content")
        and todo.get("status") in _PLAN_ACTIVE_STATUSES
    ]
    if not active:
        return None
    return "\n".join(active)


def _plan_active_text(tasks: list[dict[str, Any]]) -> str | None:
    """Standing-plan text from a list of ``{subject, status}`` task dicts.

    Shared shape for the persisted plan store (`plan_tasks`) and the
    transcript-reconstructed ``TaskCreate``/``TaskUpdate`` task list.
    """
    active = [
        str(task["subject"])
        for task in tasks
        if isinstance(task, dict)
        and task.get("subject")
        and task.get("status") in _PLAN_ACTIVE_STATUSES
    ]
    if not active:
        return None
    return "\n".join(active)


def _resolve_task_index(task_id: Any, count: int) -> int | None:
    """Best-effort mapping from a ``TaskUpdate`` ``taskId`` to a position in
    the transcript-order ``created_tasks`` list.

    Known limitation: ``TaskCreate`` calls carry no id in their tool_use
    input, so the adapter cannot observe the harness's true task
    identifiers. This assumes 1-based creation order (``taskId "2"`` is the
    second ``TaskCreate`` seen so far), which holds for well-behaved
    sessions but will misattribute updates if the harness ever creates or
    completes tasks out of that order, or if tasks from a prior session
    persist into this one's numbering. The persisted plan store
    (``plan_tasks`` / `load_plan_tasks`) is authoritative and should be
    preferred over this reconstruction whenever it is available.
    """
    try:
        idx = int(str(task_id)) - 1
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < count else None


def _apply_tool_anchor(block: dict[str, Any], plan: PlanState) -> PlanState:
    """Dispatch a single tool_use block to whichever intent anchor it feeds.

    ``TodoWrite`` is the legacy anchor (kept for back-compat with older
    sessions); ``TaskCreate``/``TaskUpdate`` are the modern harness's
    replacement. Both funnel into the same ``standing_plan`` field so
    downstream declared_intent resolution doesn't need to know which one
    produced it — the next harness rename only needs a new branch here.
    """
    name = block.get("name")
    if name == "TodoWrite":
        todos = _extract_todos(block)
        if todos is None:
            return plan
        return PlanState(
            pending_intent=plan.pending_intent,
            standing_plan=_active_goal_text(todos),
            created_tasks=plan.created_tasks,
        )
    if name == "TaskCreate":
        input_data = block.get("input")
        subject = input_data.get("subject") if isinstance(input_data, dict) else None
        if not subject:
            return plan
        created_tasks = [*plan.created_tasks, {"subject": str(subject), "status": "pending"}]
        return PlanState(
            pending_intent=plan.pending_intent,
            standing_plan=_plan_active_text(created_tasks),
            created_tasks=created_tasks,
        )
    if name == "TaskUpdate":
        input_data = block.get("input")
        if not isinstance(input_data, dict):
            return plan
        status = input_data.get("status")
        idx = _resolve_task_index(input_data.get("taskId"), len(plan.created_tasks))
        if idx is None or not status:
            return plan
        created_tasks = list(plan.created_tasks)
        created_tasks[idx] = {**created_tasks[idx], "status": str(status)}
        return PlanState(
            pending_intent=plan.pending_intent,
            standing_plan=_plan_active_text(created_tasks),
            created_tasks=created_tasks,
        )
    return plan


def _update_standing_plan(blocks: list[dict[str, Any]], plan: PlanState) -> PlanState:
    for block in blocks:
        plan = _apply_tool_anchor(block, plan)
    return plan


def _assistant_steps(
    event: dict[str, Any],
    trace_id: str,
    strip: bool,
    plan: PlanState,
) -> tuple[list[Step], PlanState]:
    content = _content(event)
    narration = _join_text(content)
    thinking = _join_thinking(content)
    tool_uses = [b for b in content if _block_type(b) == "tool_use"]
    plan = _update_standing_plan(tool_uses, plan)
    standing_plan = plan.standing_plan

    if not tool_uses:
        # Explicit signals (narration, then thinking) win over the standing
        # plan and become it going forward; see module docstring precedence.
        message_intent = narration or thinking
        if message_intent:
            standing_plan = message_intent
        action = Action(type=ActionType.UTTERANCE, payload={"text": narration})
        intent = message_intent if message_intent else standing_plan
        step = _make_step(event, 0, trace_id, Actor.AGENT, intent, action)
        new_plan = PlanState(
            pending_intent=message_intent if message_intent else plan.pending_intent,
            standing_plan=standing_plan,
            created_tasks=plan.created_tasks,
        )
        return [step], new_plan

    steps: list[Step] = []
    message_intent = narration if narration else thinking
    for i, block in enumerate(tool_uses):
        action = Action(
            type=ActionType.TOOL_CALL,
            payload={"name": block.get("name"), "input": _maybe_redact(block.get("input"), strip)},
        )
        intent = message_intent if i == 0 and message_intent else standing_plan
        step_metadata = (
            {"delegation_unobserved": True} if block.get("name") in _DELEGATION_TOOL_NAMES else None
        )
        steps.append(
            _make_step(
                event, i, trace_id, Actor.AGENT, intent, action, prev=steps, metadata=step_metadata
            )
        )
    return steps, PlanState(
        pending_intent=None, standing_plan=standing_plan, created_tasks=plan.created_tasks
    )


def _user_steps(event: dict[str, Any], trace_id: str, strip: bool) -> list[Step]:
    content = event.get("message", {}).get("content") if event.get("message") else None
    tool_results = (
        [b for b in content if _block_type(b) == "tool_result"] if isinstance(content, list) else []
    )
    if tool_results:
        steps: list[Step] = []
        for i, block in enumerate(tool_results):
            payload: dict[str, Any] = {"tool_result": _maybe_redact(block.get("content"), strip)}
            if block.get("is_error"):
                payload["is_error"] = True
            action = Action(type=ActionType.ENV_EFFECT, payload=payload)
            steps.append(_make_step(event, i, trace_id, Actor.TOOL, None, action, prev=steps))
        return steps

    text = (
        content
        if isinstance(content, str)
        else _join_text(content if isinstance(content, list) else [])
    )
    action = Action(type=ActionType.UTTERANCE, payload={"text": text})
    return [_make_step(event, 0, trace_id, Actor.USER, None, action)]


def _make_step(
    event: dict[str, Any],
    index: int,
    trace_id: str,
    actor: Actor,
    declared_intent: str | None,
    action: Action,
    prev: list[Step] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Step:
    uuid = str(event.get("uuid") or f"{trace_id}-{index}")
    step_id = uuid if index == 0 else f"{uuid}-{index}"
    if index == 0:
        parent = event.get("parentUuid")
        parent_step_id = str(parent) if parent else None
    else:
        parent_step_id = prev[-1].step_id if prev else None
    step_metadata: dict[str, Any] = {}
    permission_mode = event.get("permissionMode")
    if permission_mode:
        step_metadata["permission_mode"] = permission_mode
    if metadata:
        step_metadata.update(metadata)
    return Step(
        step_id=step_id,
        parent_step_id=parent_step_id,
        trace_id=trace_id,
        timestamp=_parse_ts(event.get("timestamp")),
        actor=actor,
        declared_intent=declared_intent or None,
        action=action,
        metadata=step_metadata,
    )


def _content(event: dict[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message") or {}
    content = message.get("content")
    return content if isinstance(content, list) else []


def _block_type(block: Any) -> str | None:
    return block.get("type") if isinstance(block, dict) else None


def _join_text(content: list[dict[str, Any]]) -> str:
    texts = [b.get("text", "") for b in content if _block_type(b) == "text"]
    return "\n".join(t for t in texts if t).strip()


def _join_thinking(content: list[dict[str, Any]]) -> str:
    """Declared-intent text from `thinking` blocks in an assistant message.

    Reasoning traces are a weaker proxy for declared intent than explicit
    narration (a model may think through options it doesn't commit to), so
    callers should prefer `_join_text` and only fall back to this.
    """
    texts = [b.get("thinking", "") for b in content if _block_type(b) == "thinking"]
    return "\n".join(t for t in texts if t).strip()


def _maybe_redact(value: Any, strip: bool) -> Any:
    if not strip:
        return value
    return {_REDACTION_KEY: True, "size": len(str(value)) if value is not None else 0}


def _parse_ts(raw: Any) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


class ClaudeCodeTraceAdapter:
    """Structural TraceAdapter for Claude Code session JSONL event lists."""

    def __init__(self, strip_payloads: bool = False) -> None:
        self.strip_payloads = strip_payloads

    def ingest(self, raw: Any) -> Trace:
        return ingest_claude_code_session(raw, strip_payloads=self.strip_payloads)
