"""Integration tests for the Claude Code session adapter.

Fixtures are synthetic (no real session data). When auditk-spec is present
(AUDITK_SPEC_PATH, default ../auditk-spec) the produced Trace is also
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
_SPEC_PATH = Path(os.environ.get("AUDITK_SPEC_PATH", "../auditk-spec"))
_SCHEMA_FILE = _SPEC_PATH / "spec" / "v0.1" / "trace.schema.json"


def _load(name: str) -> list[dict]:
    return [
        json.loads(line) for line in (_FIXTURES / name).read_text().splitlines() if line.strip()
    ]


def _validate_against_spec(trace) -> None:
    if not _SCHEMA_FILE.exists():
        pytest.skip(f"auditk-spec not found at {_SPEC_PATH}")
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
    assert tool_call.action.payload["input"] == {
        "redacted": True,
        "size": pytest.approx(len(str({"command": "ls sandbox/"})), abs=0),
    }


def test_intent_carried_across_separate_messages() -> None:
    trace = ingest_claude_code_session(_load("session-separate-intent.jsonl"))
    # user + assistant(text) + assistant(tool_call) + tool(env_effect) + assistant(utterance)
    assert len(trace.steps) == 5
    text_step = trace.steps[1]
    assert text_step.action.type == ActionType.UTTERANCE
    assert text_step.declared_intent == "I will list the directory contents."
    tool_call = trace.steps[2]
    assert tool_call.action.type == ActionType.TOOL_CALL
    assert tool_call.declared_intent == "I will list the directory contents."
    _validate_against_spec(trace)


def test_inline_narration_preferred_over_pending_intent() -> None:
    trace = ingest_claude_code_session(_load("session-prefer-inline.jsonl"))
    # user + assistant(text) + assistant(tool_call) + tool(env_effect)
    assert len(trace.steps) == 4
    tool_call = trace.steps[2]
    assert tool_call.action.type == ActionType.TOOL_CALL
    assert tool_call.declared_intent == "Actually, let me use ls instead."
    _validate_against_spec(trace)


def test_adapter_class_matches_function() -> None:
    events = _load("session-text-only.jsonl")
    via_class = ClaudeCodeTraceAdapter().ingest(events)
    via_func = ingest_claude_code_session(events)
    assert via_class.trace_id == via_func.trace_id
    assert len(via_class.steps) == len(via_func.steps)


def test_empty_session_raises() -> None:
    with pytest.raises(ValueError):
        ingest_claude_code_session([{"type": "system", "subtype": "init"}])


# --- Phase D1: standing plan from TodoWrite ---


STANDING_PLAN_TEXT = "Set up build script\nRun tests"


def test_todowrite_populates_standing_plan() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_standing.jsonl"))
    # user + TodoWrite + user + Bash + tool_result + Bash = 6 steps
    assert len(trace.steps) == 6
    bash_step = trace.steps[3]
    assert bash_step.action.type == ActionType.TOOL_CALL
    assert bash_step.declared_intent == STANDING_PLAN_TEXT
    _validate_against_spec(trace)


def test_standing_plan_attached_to_subsequent_action_intent() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_standing.jsonl"))
    bash_step = trace.steps[3]
    assert bash_step.action.type == ActionType.TOOL_CALL
    assert bash_step.declared_intent == STANDING_PLAN_TEXT


def test_standing_plan_carries_across_events() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_standing.jsonl"))
    # event a3 (Bash) occurs two assistant events after a1 (TodoWrite)
    later_bash = trace.steps[5]
    assert later_bash.action.type == ActionType.TOOL_CALL
    assert later_bash.declared_intent == STANDING_PLAN_TEXT


def test_narration_precedence_over_standing_plan() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_narration.jsonl"))
    # user + assistant(TodoWrite + Bash) + user + Bash = 5 steps
    assert len(trace.steps) == 5
    first_tool = trace.steps[1]
    assert first_tool.declared_intent == "I will set up the build and run tests."
    # second tool_use in the same message gets the standing plan (new behaviour)
    second_tool = trace.steps[2]
    assert second_tool.declared_intent == STANDING_PLAN_TEXT
    # next message after the narrated one also carries the standing plan
    next_bash = trace.steps[4]
    assert next_bash.declared_intent == STANDING_PLAN_TEXT


def test_completed_todos_clear_standing_plan() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_completed.jsonl"))
    # user + text + TodoWrite(completed) + user + Bash = 5 steps
    assert len(trace.steps) == 5
    # The TodoWrite itself should have None because the standing plan is cleared
    todo_step = trace.steps[2]
    assert todo_step.action.type == ActionType.TOOL_CALL
    assert todo_step.action.payload["name"] == "TodoWrite"
    assert todo_step.declared_intent is None
    # The subsequent Bash step should also have None
    bash_step = trace.steps[4]
    assert bash_step.action.type == ActionType.TOOL_CALL
    assert bash_step.declared_intent is None


def test_latest_todowrite_supersedes_previous() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_supersede.jsonl"))
    # user + TodoWrite(A,B) + user + TodoWrite(C) + user + Bash = 6 steps
    assert len(trace.steps) == 6
    bash_step = trace.steps[5]
    assert bash_step.declared_intent == "Deploy to staging"


def test_malformed_todowrite_ignored_keeps_previous() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_malformed.jsonl"))
    # user + TodoWrite(valid) + user + TodoWrite(malformed) + user + Bash = 6 steps
    assert len(trace.steps) == 6
    bash_step = trace.steps[5]
    assert bash_step.declared_intent == "Set up build script"


def test_todowrite_in_non_first_block_updates_plan() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_nonfirst.jsonl"))
    # user + assistant(Bash + TodoWrite) + user + Bash = 5 steps
    assert len(trace.steps) == 5
    # The TodoWrite is block i==1, so its own declared_intent follows precedence
    # but it should still update the standing plan for subsequent steps
    later_bash = trace.steps[4]
    assert later_bash.action.type == ActionType.TOOL_CALL
    assert later_bash.declared_intent == STANDING_PLAN_TEXT


def test_strip_payloads_redacts_todowrite_payload_but_keeps_intent() -> None:
    trace = ingest_claude_code_session(
        _load("session_todowrite_standing.jsonl"), strip_payloads=True
    )
    todo_step = trace.steps[1]
    assert todo_step.action.type == ActionType.TOOL_CALL
    assert todo_step.action.payload["name"] == "TodoWrite"
    assert todo_step.action.payload["input"] == {
        "redacted": True,
        "size": pytest.approx(115, abs=0),
    }
    # Downstream intent derived from standing plan is still present
    bash_step = trace.steps[3]
    assert bash_step.declared_intent == STANDING_PLAN_TEXT


def test_user_and_tool_result_steps_have_no_intent() -> None:
    trace = ingest_claude_code_session(_load("session_todowrite_standing.jsonl"))
    user_steps = [s for s in trace.steps if s.actor == Actor.USER]
    tool_steps = [s for s in trace.steps if s.actor == Actor.TOOL]
    for step in user_steps + tool_steps:
        assert step.declared_intent is None


def test_coverage_exceeds_threshold_on_fixture_session() -> None:
    trace = ingest_claude_code_session(_load("session_with_todos.jsonl"))
    covered = sum(1 for s in trace.steps if s.declared_intent)
    total = len(trace.steps)
    assert total >= 12, f"expected >=12 steps, got {total}"
    assert covered / total > 0.50, f"coverage {covered}/{total} = {covered / total:.2%}"
