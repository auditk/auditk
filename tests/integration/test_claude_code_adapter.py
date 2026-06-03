"""Integration tests for the Claude Code session adapter.

Fixtures are synthetic (no real session data). When glasshouse-spec is present
(GLASSHOUSE_SPEC_PATH, default ../glasshouse-spec) the produced Trace is also
validated against the normative trace.schema.json.
"""

import json
import os
from pathlib import Path

import jsonschema
import pytest

from auditk.adapters.claude_code import (
    ClaudeCodeTraceAdapter,
    ingest_claude_code_session,
)
from auditk.schema import ActionType, Actor

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_code"
_SPEC_PATH = Path(os.environ.get("GLASSHOUSE_SPEC_PATH", "../glasshouse-spec"))
_SCHEMA_FILE = _SPEC_PATH / "spec" / "v0.1" / "trace.schema.json"


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (_FIXTURES / name).read_text().splitlines() if line.strip()]


def _validate_against_spec(trace) -> None:
    if not _SCHEMA_FILE.exists():
        pytest.skip(f"glasshouse-spec not found at {_SPEC_PATH}")
    schema = json.loads(_SCHEMA_FILE.read_text())
    jsonschema.validate(instance=trace.model_dump(mode="json"), schema=schema)


def test_text_only_session_yields_two_steps() -> None:
    trace = ingest_claude_code_session(_load("session-text-only.jsonl"))
    assert trace.source_adapter == "claude-code"
    assert trace.flow_type.value == "code"
    assert len(trace.steps) == 2
    assert trace.steps[0].actor == Actor.USER
    assert trace.steps[1].actor == Actor.AGENT
    assert trace.steps[1].action.type == ActionType.UTTERANCE
    _validate_against_spec(trace)


def test_tool_use_session_maps_tool_call_and_env_effect() -> None:
    trace = ingest_claude_code_session(_load("session-tool-use.jsonl"))
    # user + assistant(tool_call) + tool(env_effect) + assistant(utterance)
    assert len(trace.steps) == 4
    tool_call = trace.steps[1]
    assert tool_call.action.type == ActionType.TOOL_CALL
    assert tool_call.action.payload["name"] == "Bash"
    assert tool_call.declared_intent == "I will list the directory contents."
    result = trace.steps[2]
    assert result.actor == Actor.TOOL
    assert result.action.type == ActionType.ENV_EFFECT
    _validate_against_spec(trace)


def test_intent_action_session_expands_multiple_tool_uses() -> None:
    trace = ingest_claude_code_session(_load("session-intent-action.jsonl"))
    # user + assistant(2 tool_calls) + tool(2 env_effects) + assistant(utterance)
    assert len(trace.steps) == 6
    first_call, second_call = trace.steps[1], trace.steps[2]
    assert first_call.action.type == ActionType.TOOL_CALL
    assert second_call.action.type == ActionType.TOOL_CALL
    # Narration attaches to the first tool_use only.
    assert first_call.declared_intent == "I will create the factorial function and then add a test."
    assert second_call.declared_intent is None
    # Second step chains its parent to the first step within the same event.
    assert second_call.parent_step_id == first_call.step_id
    _validate_against_spec(trace)


def test_strip_payloads_redacts_tool_input() -> None:
    trace = ingest_claude_code_session(_load("session-tool-use.jsonl"), strip_payloads=True)
    tool_call = trace.steps[1]
    assert tool_call.action.payload["input"] == {"redacted": True, "size": pytest.approx(len(str({"command": "ls sandbox/"})), abs=0)}


def test_adapter_class_matches_function() -> None:
    events = _load("session-text-only.jsonl")
    via_class = ClaudeCodeTraceAdapter().ingest(events)
    via_func = ingest_claude_code_session(events)
    assert via_class.trace_id == via_func.trace_id
    assert len(via_class.steps) == len(via_func.steps)


def test_empty_session_raises() -> None:
    with pytest.raises(ValueError):
        ingest_claude_code_session([{"type": "system", "subtype": "init"}])
