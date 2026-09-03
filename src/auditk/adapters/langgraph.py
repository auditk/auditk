# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""LangGraph checkpoint adapter for auditk.

Converts a list of serialised LangGraph checkpoint dicts into a normalised Trace.
Checkpoint dicts must follow the serialised CheckpointTuple shape (not live objects).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from auditk.adapters.redaction import ContentKeysByActionType, redact_trace
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    FlowType,
    Step,
    Trace,
)

# Redaction pass-through (P1b gap 1): a LangGraph TOOL_CALL step's payload
# is `{"node": node_name, "writes": node_writes}` (see `_classify_action`)
# -- `node` is a graph-defined name (structural, survives), `writes` is
# the node's actual output and the only place sensitive content can live.
# UTTERANCE (the `respond` node's own AI message) is deliberately NOT
# listed here, mirroring Claude Code's own redaction: narration survives,
# only tool-call-shaped content is stripped.
REDACTION_CONTENT_KEYS: ContentKeysByActionType = {
    ActionType.TOOL_CALL: frozenset({"writes"}),
}


def _get_thread_id(checkpoint_dict: dict[str, Any]) -> str:
    return str(checkpoint_dict["config"]["configurable"]["thread_id"])


def _get_parent_step_id(checkpoint_dict: dict[str, Any]) -> str | None:
    parent_config = checkpoint_dict.get("parent_config")
    if not parent_config:
        return None
    parent_ck_id = parent_config.get("configurable", {}).get("checkpoint_id")
    return f"ck-{parent_ck_id}" if parent_ck_id else None


def _extract_ai_messages(node_writes: Any) -> list[dict[str, Any]]:
    if not isinstance(node_writes, dict):
        return []
    messages = node_writes.get("messages", [])
    if not isinstance(messages, list):
        return []
    return [m for m in messages if isinstance(m, dict) and m.get("type") == "ai"]


def _classify_action(metadata: dict[str, Any]) -> Action:
    """Derive an Action from checkpoint metadata.writes."""
    writes: dict[str, Any] = metadata.get("writes") or {}
    if not writes:
        return Action(type=ActionType.STATE_TRANSITION, payload={"node": "unknown"})

    node_name = next(iter(writes))
    node_writes = writes[node_name]

    ai_messages = _extract_ai_messages(node_writes)
    if node_name == "respond" or ai_messages:
        output = ai_messages[-1]["content"] if ai_messages else str(node_writes)
        return Action(
            type=ActionType.UTTERANCE,
            payload={"node": node_name, "output": output},
        )

    has_messages = isinstance(node_writes, dict) and "messages" in node_writes
    if not has_messages:
        return Action(
            type=ActionType.TOOL_CALL,
            payload={"node": node_name, "writes": node_writes},
        )

    return Action(type=ActionType.STATE_TRANSITION, payload={"node": node_name})


def _get_declared_intent(checkpoint: dict[str, Any]) -> str | None:
    channel_values = checkpoint.get("channel_values") or {}
    intent = channel_values.get("intent")
    return str(intent) if intent is not None else None


def _build_step(
    checkpoint_dict: dict[str, Any],
    thread_id: str,
    base_time: datetime,
) -> Step:
    checkpoint = checkpoint_dict["checkpoint"]
    metadata: dict[str, Any] = checkpoint_dict.get("metadata") or {}
    step_index: int = metadata.get("step", 0)

    return Step(
        step_id=f"ck-{checkpoint['id']}",
        parent_step_id=_get_parent_step_id(checkpoint_dict),
        trace_id=thread_id,
        timestamp=base_time + timedelta(seconds=step_index),
        actor=Actor.AGENT,
        declared_intent=_get_declared_intent(checkpoint),
        action=_classify_action(metadata),
        context_used=[],
        belief_state=None,
    )


def ingest_checkpoints(checkpoints: list[dict[str, Any]]) -> Trace:
    """Convert a list of serialised LangGraph checkpoint dicts into a Trace."""
    if not checkpoints:
        raise ValueError("checkpoints list must not be empty")

    try:
        thread_id = _get_thread_id(checkpoints[0])
        base_time = datetime.now(UTC)
        steps = [_build_step(ck, thread_id, base_time) for ck in checkpoints]
    except (KeyError, TypeError, AttributeError) as exc:
        # Refuse cleanly on malformed checkpoint data (missing `config`/
        # `checkpoint`/`configurable.thread_id`, or a non-dict entry) rather
        # than letting an opaque, undocumented KeyError/TypeError/AttributeError
        # leak from deep inside `_get_thread_id`/`_build_step`.
        raise ValueError(f"malformed LangGraph checkpoint data: {exc}") from exc

    return Trace(
        trace_id=thread_id,
        flow_type=FlowType.GENERIC,
        agent_config_ref=f"langgraph:{thread_id}",
        steps=steps,
        source_adapter="langgraph",
    )


class LangGraphTraceAdapter:
    """Adapter class wrapping ingest_checkpoints for protocol compliance.

    Set ``strip_payloads=True`` to redact TOOL_CALL steps' ``writes``
    payload via the shared post-ingest redaction pass
    (`auditk.adapters.redaction.redact_trace`) -- the LangGraph half of
    closing P1b's gap 1 (redaction pass-through was Claude-Code-only).
    """

    def __init__(self, strip_payloads: bool = False) -> None:
        self.strip_payloads = strip_payloads

    def ingest(self, raw: Any) -> Trace:
        trace = ingest_checkpoints(raw)
        if self.strip_payloads:
            trace = redact_trace(trace, REDACTION_CONTENT_KEYS)
        return trace
