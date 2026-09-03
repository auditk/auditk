# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the LangGraph checkpoint adapter.

Loads each fixture, converts via ingest_checkpoints, and asserts:
  - Returns a valid Trace
  - source_adapter == "langgraph"
  - step count matches checkpoint count
  - Result validates against spec/v0.1/trace.schema.json
  - fixture-specific structural assertions (declared_intent, tool_call)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import jsonschema
import pytest

from auditk.adapters.langgraph import LangGraphTraceAdapter, ingest_checkpoints
from auditk.schema import ActionType, Trace

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "langgraph"
_SPEC_PATH = Path(os.environ.get("AUDITK_SPEC_PATH", "../auditk-spec"))
_TRACE_SCHEMA_FILE = _SPEC_PATH / "spec" / "v0.1" / "trace.schema.json"


def _load_fixture(name: str) -> list[dict]:  # type: ignore[type-arg]
    return json.loads((_FIXTURES_DIR / name).read_text())  # type: ignore[return-value]


@pytest.fixture(scope="module")
def trace_schema() -> dict:  # type: ignore[type-arg]
    if not _TRACE_SCHEMA_FILE.exists():
        pytest.skip(
            f"auditk-spec not found at {_SPEC_PATH}; "
            "set AUDITK_SPEC_PATH to run schema-validation tests."
        )
    return json.loads(_TRACE_SCHEMA_FILE.read_text())  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Simple fixture: 2 checkpoints, human → AI response
# ---------------------------------------------------------------------------


class TestSimpleCheckpointList:
    def test_returns_trace(self) -> None:
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-simple.json"))
        assert isinstance(trace, Trace)

    def test_source_adapter(self) -> None:
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-simple.json"))
        assert trace.source_adapter == "langgraph"

    def test_step_count_matches_checkpoints(self) -> None:
        checkpoints = _load_fixture("checkpoint-list-simple.json")
        trace = ingest_checkpoints(checkpoints)
        assert len(trace.steps) == len(checkpoints)

    def test_validates_against_schema(self, trace_schema: dict) -> None:  # type: ignore[type-arg]
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-simple.json"))
        jsonschema.validate(instance=trace.model_dump(mode="json"), schema=trace_schema)

    def test_agent_config_ref_includes_thread_id(self) -> None:
        checkpoints = _load_fixture("checkpoint-list-simple.json")
        trace = ingest_checkpoints(checkpoints)
        thread_id = checkpoints[0]["config"]["configurable"]["thread_id"]
        assert trace.agent_config_ref == f"langgraph:{thread_id}"


# ---------------------------------------------------------------------------
# Intent fixture: 3 checkpoints, intent populated at step 1
# ---------------------------------------------------------------------------


class TestWithIntentCheckpointList:
    def test_declared_intent_populated_at_step_1(self) -> None:
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-with-intent.json"))
        assert trace.steps[1].declared_intent is not None

    def test_step_count_matches_checkpoints(self) -> None:
        checkpoints = _load_fixture("checkpoint-list-with-intent.json")
        trace = ingest_checkpoints(checkpoints)
        assert len(trace.steps) == len(checkpoints)

    def test_validates_against_schema(self, trace_schema: dict) -> None:  # type: ignore[type-arg]
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-with-intent.json"))
        jsonschema.validate(instance=trace.model_dump(mode="json"), schema=trace_schema)

    def test_first_step_declared_intent_is_none(self) -> None:
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-with-intent.json"))
        assert trace.steps[0].declared_intent is None


# ---------------------------------------------------------------------------
# Tool call fixture: 3 checkpoints, tool call at step 1
# ---------------------------------------------------------------------------


class TestToolCallCheckpointList:
    def test_any_step_is_tool_call(self) -> None:
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-tool-call.json"))
        assert any(step.action.type == ActionType.TOOL_CALL for step in trace.steps)

    def test_step_count_matches_checkpoints(self) -> None:
        checkpoints = _load_fixture("checkpoint-list-tool-call.json")
        trace = ingest_checkpoints(checkpoints)
        assert len(trace.steps) == len(checkpoints)

    def test_validates_against_schema(self, trace_schema: dict) -> None:  # type: ignore[type-arg]
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-tool-call.json"))
        jsonschema.validate(instance=trace.model_dump(mode="json"), schema=trace_schema)

    def test_last_step_is_utterance(self) -> None:
        trace = ingest_checkpoints(_load_fixture("checkpoint-list-tool-call.json"))
        assert trace.steps[-1].action.type == ActionType.UTTERANCE


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------


class TestLangGraphTraceAdapter:
    def test_adapter_satisfies_protocol(self) -> None:
        checkpoints = _load_fixture("checkpoint-list-simple.json")
        adapter = LangGraphTraceAdapter()
        trace = adapter.ingest(checkpoints)
        assert isinstance(trace, Trace)
        assert trace.source_adapter == "langgraph"

    def test_empty_checkpoints_raises(self) -> None:
        adapter = LangGraphTraceAdapter()
        with pytest.raises(ValueError, match="empty"):
            adapter.ingest([])

    def test_malformed_checkpoint_missing_config_raises_value_error(self) -> None:
        """A checkpoint dict missing `config` must refuse via a documented
        ValueError, not leak a raw KeyError from `_get_thread_id`."""
        adapter = LangGraphTraceAdapter()
        with pytest.raises(ValueError, match="malformed"):
            adapter.ingest([{"checkpoint": {"id": "c1"}, "metadata": {}}])
