# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark session adapter for auditk.

Converts a list of OpenAI-compatible message dicts (from the benchmark runner)
into a normalised Trace. Reuses the D1 standing-plan logic from claude_code.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from auditk.adapters.claude_code import (
    PlanState,
    _maybe_redact,
    _update_standing_plan,
)
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    FlowType,
    Step,
    Trace,
)


def _make_step(
    trace_id: str,
    actor: Actor,
    action: Action,
    declared_intent: str | None,
    parent_step_id: str | None = None,
    step_counter: list[int] | None = None,
) -> Step:
    if step_counter is None:
        step_counter = [0]
    step_counter[0] += 1
    return Step(
        step_id=f"{trace_id}-step-{step_counter[0]}",
        parent_step_id=parent_step_id,
        trace_id=trace_id,
        timestamp=datetime.now(UTC),
        actor=actor,
        declared_intent=declared_intent,
        action=action,
    )


def _extract_todo_blocks(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert OpenAI-format TodoWrite tool_calls into claude_code blocks."""
    blocks: list[dict[str, Any]] = []
    for tc in tool_calls:
        func = tc.get("function")
        if isinstance(func, dict) and func.get("name") == "TodoWrite":
            try:
                args = json.loads(str(func["arguments"]))
                blocks.append({"name": "TodoWrite", "input": args})
            except (json.JSONDecodeError, KeyError):
                pass
    return blocks


def _build_assistant_steps(
    msg: dict[str, Any],
    trace_id: str,
    plan: PlanState,
    step_counter: list[int],
    tool_call_id_to_step_id: dict[str, str],
    strip: bool,
) -> tuple[list[Step], PlanState]:
    content = msg.get("content") or ""
    tool_calls = msg.get("tool_calls", [])

    blocks = _extract_todo_blocks(tool_calls)
    plan = _update_standing_plan(blocks, plan)
    standing_plan = plan.standing_plan
    new_plan = PlanState(
        pending_intent=None, standing_plan=standing_plan, created_tasks=plan.created_tasks
    )

    steps: list[Step] = []
    if not tool_calls:
        action = Action(type=ActionType.UTTERANCE, payload={"text": content})
        intent = content if content else standing_plan
        steps.append(_make_step(trace_id, Actor.AGENT, action, intent, step_counter=step_counter))
        return steps, new_plan

    for i, tc in enumerate(tool_calls):
        func = tc.get("function")
        name = func.get("name", "") if isinstance(func, dict) else ""
        try:
            args = json.loads(str(func["arguments"])) if isinstance(func, dict) else {}
        except (json.JSONDecodeError, KeyError):
            args = {}

        raw_payload = {"name": name, "input": args}
        payload = _maybe_redact(raw_payload, strip)

        action = Action(type=ActionType.TOOL_CALL, payload=payload)
        intent = content if i == 0 and content else standing_plan
        step = _make_step(trace_id, Actor.AGENT, action, intent, step_counter=step_counter)
        steps.append(step)
        tool_call_id_to_step_id[str(tc.get("id", ""))] = step.step_id

    return steps, new_plan


def _build_tool_step(
    msg: dict[str, Any],
    trace_id: str,
    step_counter: list[int],
    tool_call_id_to_step_id: dict[str, str],
    strip: bool,
) -> Step:
    content = msg.get("content") or ""
    raw_payload = {"tool_result": content}
    payload = _maybe_redact(raw_payload, strip)

    action = Action(type=ActionType.ENV_EFFECT, payload=payload)
    parent_id = tool_call_id_to_step_id.get(str(msg.get("tool_call_id", "")))
    return _make_step(
        trace_id,
        Actor.TOOL,
        action,
        None,
        parent_step_id=parent_id,
        step_counter=step_counter,
    )


class BenchmarkSessionAdapter:
    """Convert benchmark API message history into a Trace."""

    def __init__(self, strip_payloads: bool = False) -> None:
        self.strip_payloads = strip_payloads

    def ingest(self, raw: Any) -> Trace:
        messages = raw
        if not messages:
            raise ValueError("session contains no messages")

        trace_id = str(messages[0].get("session_id") or "unknown")
        steps: list[Step] = []
        plan = PlanState()
        tool_call_id_to_step_id: dict[str, str] = {}
        step_counter: list[int] = [0]

        for msg in messages:
            role = msg.get("role")

            if role == "user":
                content = msg.get("content") or ""
                action = Action(type=ActionType.UTTERANCE, payload={"text": content})
                steps.append(
                    _make_step(trace_id, Actor.USER, action, None, step_counter=step_counter)
                )

            elif role == "assistant":
                new_steps, plan = _build_assistant_steps(
                    msg,
                    trace_id,
                    plan,
                    step_counter,
                    tool_call_id_to_step_id,
                    self.strip_payloads,
                )
                steps.extend(new_steps)

            elif role == "tool":
                steps.append(
                    _build_tool_step(
                        msg, trace_id, step_counter, tool_call_id_to_step_id, self.strip_payloads
                    )
                )

        return Trace(
            trace_id=trace_id,
            flow_type=FlowType.CODE,
            agent_config_ref=f"benchmark-api:{trace_id}",
            steps=steps,
            source_adapter="benchmark-api",
        )
