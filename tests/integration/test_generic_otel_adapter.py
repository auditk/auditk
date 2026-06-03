"""Integration tests for the generic OTel / OpenInference adapter.

Each test loads a fixture file, calls ingest_otel_spans, and validates:
- Returns a Trace with correct fields
- source_adapter == "generic-otel"
- len(trace.steps) == len(spans)
- Trace validates against spec/v0.1/trace.schema.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from auditk.adapters.generic_otel import OtelTraceAdapter, ingest_otel_spans
from auditk.schema import Trace

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "otel"
_SPEC_PATH = Path(os.environ.get("GLASSHOUSE_SPEC_PATH", "../auditk-spec"))
_TRACE_SCHEMA_PATH = _SPEC_PATH / "spec" / "v0.1" / "trace.schema.json"


def _load_fixture(name: str) -> list[dict[str, Any]]:
    with (FIXTURES_DIR / name).open() as fh:
        return json.load(fh)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def trace_schema() -> dict[str, Any]:
    if not _TRACE_SCHEMA_PATH.exists():
        pytest.skip(f"Spec schema not found at {_TRACE_SCHEMA_PATH}")
    with _TRACE_SCHEMA_PATH.open() as fh:
        return json.load(fh)  # type: ignore[no-any-return]


def _assert_valid_trace(spans: list[dict[str, Any]], schema: dict[str, Any]) -> Trace:
    """Shared assertions: type, source_adapter, step count, schema validity."""
    trace = ingest_otel_spans(spans)
    assert isinstance(trace, Trace)
    assert trace.source_adapter == "generic-otel"
    assert len(trace.steps) == len(spans)
    jsonschema.validate(instance=trace.model_dump(mode="json"), schema=schema)
    return trace


def test_llm_chain_fixture(trace_schema: dict[str, Any]) -> None:
    """CHAIN root + LLM child + TOOL child — flow_type generic, correct actor mapping."""
    spans = _load_fixture("span-list-llm-chain.json")
    trace = _assert_valid_trace(spans, trace_schema)

    assert trace.flow_type.value == "generic"
    assert trace.agent_config_ref == "session-xyz-42"
    assert trace.tenant_id == "tenant-corp-1"

    # LLM span → utterance action, AGENT actor
    llm_step = next(s for s in trace.steps if s.step_id == "llm001")
    assert llm_step.action.type.value == "utterance"
    assert llm_step.actor.value == "agent"

    # TOOL span → tool_call action, TOOL actor
    tool_step = next(s for s in trace.steps if s.step_id == "tool001")
    assert tool_step.action.type.value == "tool_call"
    assert tool_step.actor.value == "tool"
    assert tool_step.action.payload["name"] == "get_account_balance"


def test_agent_with_retrieval_fixture(trace_schema: dict[str, Any]) -> None:
    """AGENT root + RETRIEVER child with retrieval.documents → ContextRef list."""
    spans = _load_fixture("span-list-agent-with-retrieval.json")
    trace = _assert_valid_trace(spans, trace_schema)

    assert trace.agent_config_ref == "session-rag-77"
    assert trace.tenant_id == "tenant-demo"

    retriever_step = next(s for s in trace.steps if s.step_id == "retriever001")
    assert retriever_step.actor.value == "tool"
    assert len(retriever_step.context_used) == 2
    assert retriever_step.context_used[0].source == "retrieval"
    assert retriever_step.context_used[0].identifier == "doc-policy-001"
    assert retriever_step.context_used[1].identifier == "doc-policy-002"
    # excerpt is truncated to 100 chars
    assert retriever_step.context_used[0].excerpt is not None
    assert len(retriever_step.context_used[0].excerpt) <= 100


def test_minimal_fixture(trace_schema: dict[str, Any]) -> None:
    """Single span with no attributes — defaults applied correctly."""
    spans = _load_fixture("span-list-minimal.json")
    trace = _assert_valid_trace(spans, trace_schema)

    assert trace.flow_type.value == "generic"
    assert trace.agent_config_ref == "unknown"
    assert trace.tenant_id is None

    step = trace.steps[0]
    assert step.step_id == "min001"
    assert step.parent_step_id is None
    assert step.declared_intent is None
    assert step.context_used == []
    assert step.belief_state is None


def test_otel_adapter_class(trace_schema: dict[str, Any]) -> None:
    """OtelTraceAdapter.ingest() produces same result as ingest_otel_spans()."""
    spans = _load_fixture("span-list-llm-chain.json")
    adapter = OtelTraceAdapter()
    result = adapter.ingest(spans)

    assert isinstance(result, Trace)
    assert result.source_adapter == "generic-otel"
    assert len(result.steps) == len(spans)
    jsonschema.validate(instance=result.model_dump(mode="json"), schema=trace_schema)

    # Must match the function directly
    direct = ingest_otel_spans(spans)
    assert result.model_dump(mode="json") == direct.model_dump(mode="json")


def test_empty_spans_raises() -> None:
    """ingest_otel_spans([]) must raise ValueError, not silently return."""
    with pytest.raises(ValueError, match="non-empty"):
        ingest_otel_spans([])
