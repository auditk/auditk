"""Generic OpenTelemetry / OpenInference adapter for auditk.

Converts a list of OTel span dicts (OpenInference GenAI semconv) into a Trace.
No OTel SDK dependency is required — spans are processed as plain dicts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from auditk.schema import (
    Action,
    ActionType,
    Actor,
    ContextRef,
    FlowType,
    Step,
    Trace,
)


def _infer_flow_type(span_kind: str) -> FlowType:
    """Map openinference.span.kind to FlowType."""
    upper = span_kind.upper()
    if upper.startswith("VOICE") or upper == "AUDIO":
        return FlowType.VOICE
    if upper.startswith("BROWSER") or upper == "COMPUTER_USE":
        return FlowType.BROWSER
    return FlowType.GENERIC


def _infer_actor(span_kind: str) -> Actor:
    """Map openinference.span.kind to Actor."""
    if span_kind in ("TOOL", "RETRIEVER"):
        return Actor.TOOL
    return Actor.AGENT


def _infer_action(span_kind: str, span_name: str, attrs: dict[str, Any]) -> Action:
    """Map span kind + attributes to an Action."""
    input_val: str | None = attrs.get("input.value")
    output_val: str | None = attrs.get("output.value")

    if span_kind == "LLM":
        return Action(
            type=ActionType.UTTERANCE,
            payload={"input": input_val, "output": output_val},
        )
    if span_kind in ("TOOL", "RETRIEVER"):
        return Action(
            type=ActionType.TOOL_CALL,
            payload={"name": span_name, "input": input_val, "output": output_val},
        )
    if span_kind in ("AGENT", "CHAIN"):
        return Action(type=ActionType.STATE_TRANSITION, payload={"node": span_name})
    return Action(type=ActionType.UTTERANCE, payload={})


def _parse_timestamp(ts: Any) -> datetime:
    """Parse ISO-8601 string or Unix nanoseconds integer into an aware datetime."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1_000_000_000, tz=UTC)
    iso = str(ts).replace("Z", "+00:00")
    return datetime.fromisoformat(iso)


def _parse_context_refs(docs_attr: str | list[Any]) -> list[ContextRef]:
    """Parse retrieval.documents attribute into ContextRef list."""
    docs: list[Any] = json.loads(docs_attr) if isinstance(docs_attr, str) else docs_attr
    refs: list[ContextRef] = []
    for i, doc in enumerate(docs):
        if isinstance(doc, dict):
            identifier = str(doc.get("document.id", i))
            content: str = doc.get("document.content", "") or ""
        else:
            identifier = str(i)
            content = str(doc)
        refs.append(
            ContextRef(
                source="retrieval",
                identifier=identifier,
                excerpt=content[:100] if content else None,
            )
        )
    return refs


def _span_to_step(span: dict[str, Any], trace_id: str) -> Step:
    """Convert a single OTel span dict to a Step."""
    attrs: dict[str, Any] = span.get("attributes") or {}
    span_kind: str = attrs.get("openinference.span.kind", "")

    raw_intent: str | None = attrs.get("input.value")
    declared_intent = raw_intent[:200] if raw_intent else None

    context_used: list[ContextRef] = []
    if "retrieval.documents" in attrs:
        context_used = _parse_context_refs(attrs["retrieval.documents"])

    parent_id: str | None = span.get("parent_span_id") or None

    return Step(
        step_id=span["span_id"],
        parent_step_id=parent_id,
        trace_id=trace_id,
        timestamp=_parse_timestamp(span["start_time"]),
        actor=_infer_actor(span_kind),
        declared_intent=declared_intent,
        action=_infer_action(span_kind, span["name"], attrs),
        context_used=context_used,
        belief_state=None,
    )


def _find_root_span(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first span without a parent; fall back to spans[0]."""
    for span in spans:
        if not span.get("parent_span_id"):
            return span
    return spans[0]


def ingest_otel_spans(spans: list[dict[str, Any]]) -> Trace:
    """Convert a list of OTel span dicts (OpenInference semconv) into a Trace.

    Args:
        spans: Non-empty list of span dicts.

    Returns:
        A fully-populated Trace with one Step per span.

    Raises:
        ValueError: If spans is empty.
    """
    if not spans:
        raise ValueError("spans must be non-empty")

    root = _find_root_span(spans)
    root_attrs: dict[str, Any] = root.get("attributes") or {}
    trace_id: str = root["trace_id"]

    flow_type = _infer_flow_type(root_attrs.get("openinference.span.kind", ""))
    agent_config_ref: str = root_attrs.get("session.id") or "unknown"
    tenant_id: str | None = root_attrs.get("user.id") or None

    steps = [_span_to_step(span, trace_id) for span in spans]

    return Trace(
        trace_id=trace_id,
        tenant_id=tenant_id,
        flow_type=flow_type,
        agent_config_ref=agent_config_ref,
        steps=steps,
        source_adapter="generic-otel",
    )


class OtelTraceAdapter:
    """Structural implementation of TraceAdapter for OTel/OpenInference spans."""

    def ingest(self, raw: Any) -> Trace:
        return ingest_otel_spans(raw)
