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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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


_SUBSTANTIVE_TYPES = ("user", "assistant")
_REDACTION_KEY = "redacted"


def ingest_claude_code_session(
    events: list[dict[str, Any]],
    strip_payloads: bool = False,
) -> Trace:
    """Convert a list of Claude Code session event dicts into a Trace.

    Raises:
        ValueError: If no substantive (user/assistant) events are present.
    """
    substantive = [e for e in events if e.get("type") in _SUBSTANTIVE_TYPES]
    if not substantive:
        raise ValueError("session contains no user/assistant events")

    session_id = str(substantive[0].get("sessionId") or "unknown")
    steps: list[Step] = []
    plan = PlanState()
    for event in substantive:
        new_steps, plan = _event_to_steps(event, session_id, strip_payloads, plan)
        steps.extend(new_steps)

    return Trace(
        trace_id=session_id,
        flow_type=FlowType.CODE,
        agent_config_ref=f"claude-code:{session_id}",
        steps=steps,
        source_adapter="claude-code",
    )


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
        and todo.get("status") in ("pending", "in_progress")
    ]
    if not active:
        return None
    return "\n".join(active)


def _update_standing_plan(blocks: list[dict[str, Any]], current: str | None) -> str | None:
    for block in blocks:
        todos = _extract_todos(block)
        if todos is not None:
            current = _active_goal_text(todos)
    return current


def _assistant_steps(
    event: dict[str, Any],
    trace_id: str,
    strip: bool,
    plan: PlanState,
) -> tuple[list[Step], PlanState]:
    content = _content(event)
    narration = _join_text(content)
    tool_uses = [b for b in content if _block_type(b) == "tool_use"]
    standing_plan = _update_standing_plan(tool_uses, plan.standing_plan)

    if not tool_uses:
        if narration:
            standing_plan = narration
        action = Action(type=ActionType.UTTERANCE, payload={"text": narration})
        intent = narration if narration else standing_plan
        step = _make_step(event, 0, trace_id, Actor.AGENT, intent, action)
        new_plan = PlanState(
            pending_intent=narration if narration else plan.pending_intent,
            standing_plan=standing_plan,
        )
        return [step], new_plan

    steps: list[Step] = []
    for i, block in enumerate(tool_uses):
        action = Action(
            type=ActionType.TOOL_CALL,
            payload={"name": block.get("name"), "input": _maybe_redact(block.get("input"), strip)},
        )
        intent = narration if i == 0 and narration else standing_plan
        steps.append(_make_step(event, i, trace_id, Actor.AGENT, intent, action, prev=steps))
    return steps, PlanState(pending_intent=None, standing_plan=standing_plan)


def _user_steps(event: dict[str, Any], trace_id: str, strip: bool) -> list[Step]:
    content = event.get("message", {}).get("content") if event.get("message") else None
    tool_results = (
        [b for b in content if _block_type(b) == "tool_result"] if isinstance(content, list) else []
    )
    if tool_results:
        steps: list[Step] = []
        for i, block in enumerate(tool_results):
            action = Action(
                type=ActionType.ENV_EFFECT,
                payload={"tool_result": _maybe_redact(block.get("content"), strip)},
            )
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
) -> Step:
    uuid = str(event.get("uuid") or f"{trace_id}-{index}")
    step_id = uuid if index == 0 else f"{uuid}-{index}"
    if index == 0:
        parent = event.get("parentUuid")
        parent_step_id = str(parent) if parent else None
    else:
        parent_step_id = prev[-1].step_id if prev else None
    return Step(
        step_id=step_id,
        parent_step_id=parent_step_id,
        trace_id=trace_id,
        timestamp=_parse_ts(event.get("timestamp")),
        actor=actor,
        declared_intent=declared_intent or None,
        action=action,
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
