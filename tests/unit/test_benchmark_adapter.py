"""Red-phase tests for benchmark/adapter.py.

Tests BenchmarkSessionAdapter: OpenAI-compatible message dicts → Trace.
"""

from datetime import datetime

import pytest

from auditk.benchmark.adapter import BenchmarkSessionAdapter
from auditk.schema import (
    ActionType,
    Actor,
    FlowType,
)


@pytest.fixture
def adapter() -> BenchmarkSessionAdapter:
    return BenchmarkSessionAdapter()


def _make_todo_write_args(todos: list[dict]) -> str:
    import json

    return json.dumps({"todos": todos})


def _make_messages_with_todo() -> list[dict]:
    return [
        {
            "role": "user",
            "content": "Audit the codebase.",
            "session_id": "sess-001",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "TodoWrite",
                        "arguments": _make_todo_write_args(
                            [
                                {
                                    "id": "t1",
                                    "content": "Read all source files",
                                    "status": "in_progress",
                                    "priority": "high",
                                }
                            ]
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": "OK",
        },
    ]


# --- basic shape ---


def test_adapter_ingest_empty_raises(adapter: BenchmarkSessionAdapter) -> None:
    with pytest.raises(ValueError, match="no messages"):
        adapter.ingest([])


def test_adapter_trace_has_correct_metadata(adapter: BenchmarkSessionAdapter) -> None:
    messages = [{"role": "user", "content": "Hello", "session_id": "sess-001"}]
    trace = adapter.ingest(messages)
    assert trace.source_adapter == "benchmark-api"
    assert trace.flow_type == FlowType.CODE
    assert trace.trace_id == "sess-001"


def test_adapter_trace_id_defaults_when_no_session_id(
    adapter: BenchmarkSessionAdapter,
) -> None:
    messages = [{"role": "user", "content": "Hello"}]
    trace = adapter.ingest(messages)
    assert trace.trace_id is not None
    assert isinstance(trace.trace_id, str)


# --- user message ---


def test_user_message_becomes_utterance_step(adapter: BenchmarkSessionAdapter) -> None:
    messages = [{"role": "user", "content": "Audit the codebase."}]
    trace = adapter.ingest(messages)
    assert len(trace.steps) == 1
    step = trace.steps[0]
    assert step.actor == Actor.USER
    assert step.action.type == ActionType.UTTERANCE
    assert step.action.payload.get("text") == "Audit the codebase."


# --- assistant tool call ---


def test_assistant_tool_call_becomes_tool_call_step(
    adapter: BenchmarkSessionAdapter,
) -> None:
    messages = [
        {"role": "user", "content": "Audit."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": '{"path": "main.py"}',
                    },
                }
            ],
        },
    ]
    trace = adapter.ingest(messages)
    assert len(trace.steps) == 2  # user + tool_call
    tool_step = trace.steps[1]
    assert tool_step.actor == Actor.AGENT
    assert tool_step.action.type == ActionType.TOOL_CALL
    assert tool_step.action.payload["name"] == "ReadFile"
    assert tool_step.action.payload["input"]["path"] == "main.py"


# --- assistant utterance ---


def test_assistant_without_tools_becomes_utterance_step(
    adapter: BenchmarkSessionAdapter,
) -> None:
    messages = [
        {"role": "user", "content": "Audit."},
        {"role": "assistant", "content": "I'll start now."},
    ]
    trace = adapter.ingest(messages)
    assert len(trace.steps) == 2
    utterance = trace.steps[1]
    assert utterance.actor == Actor.AGENT
    assert utterance.action.type == ActionType.UTTERANCE
    assert utterance.action.payload.get("text") == "I'll start now."


# --- tool result ---


def test_tool_result_becomes_env_effect_step(adapter: BenchmarkSessionAdapter) -> None:
    messages = [
        {"role": "user", "content": "Audit."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": '{"path": "main.py"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": "file contents",
        },
    ]
    trace = adapter.ingest(messages)
    assert len(trace.steps) == 3
    result_step = trace.steps[2]
    assert result_step.actor == Actor.TOOL
    assert result_step.action.type == ActionType.ENV_EFFECT
    assert result_step.action.payload.get("tool_result") == "file contents"


# --- standing plan / declared intent ---


def test_todo_write_updates_standing_plan(adapter: BenchmarkSessionAdapter) -> None:
    messages = _make_messages_with_todo()
    trace = adapter.ingest(messages)
    # user + TodoWrite tool_call + tool_result = 3 steps
    assert len(trace.steps) == 3
    todo_step = trace.steps[1]
    assert todo_step.actor == Actor.AGENT
    assert todo_step.action.type == ActionType.TOOL_CALL
    assert todo_step.action.payload["name"] == "TodoWrite"
    # declared_intent should be the active goal text from the TodoWrite
    assert "Read all source files" in (todo_step.declared_intent or "")


def test_readfile_after_todo_carries_declared_intent(
    adapter: BenchmarkSessionAdapter,
) -> None:
    messages = [
        {
            "role": "user",
            "content": "Audit.",
            "session_id": "sess-002",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "TodoWrite",
                        "arguments": _make_todo_write_args(
                            [
                                {
                                    "id": "t1",
                                    "content": "Read all source files",
                                    "status": "in_progress",
                                    "priority": "high",
                                }
                            ]
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-1", "content": "OK"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-2",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": '{"path": "main.py"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-2", "content": "def main(): ..."},
    ]
    trace = adapter.ingest(messages)
    # user + TodoWrite + tool_result + ReadFile + tool_result = 5 steps
    assert len(trace.steps) == 5
    read_step = trace.steps[3]
    assert read_step.actor == Actor.AGENT
    assert read_step.action.type == ActionType.TOOL_CALL
    assert "Read all source files" in (read_step.declared_intent or "")


def test_multiple_todo_writes_update_plan(adapter: BenchmarkSessionAdapter) -> None:
    messages = [
        {"role": "user", "content": "Audit.", "session_id": "sess-003"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "TodoWrite",
                        "arguments": _make_todo_write_args(
                            [
                                {
                                    "id": "t1",
                                    "content": "Plan phase",
                                    "status": "in_progress",
                                    "priority": "high",
                                }
                            ]
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-1", "content": "OK"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-2",
                    "type": "function",
                    "function": {
                        "name": "TodoWrite",
                        "arguments": _make_todo_write_args(
                            [
                                {
                                    "id": "t1",
                                    "content": "Plan phase",
                                    "status": "completed",
                                    "priority": "high",
                                },
                                {
                                    "id": "t2",
                                    "content": "Execute phase",
                                    "status": "in_progress",
                                    "priority": "high",
                                },
                            ]
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-2", "content": "OK"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-3",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": '{"path": "x.py"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc-3", "content": "..."},
    ]
    trace = adapter.ingest(messages)
    # user + TodoWrite + result + TodoWrite + result + ReadFile + result = 7
    assert len(trace.steps) == 7
    read_step = trace.steps[5]
    # The second TodoWrite should have updated the standing plan
    assert "Execute phase" in (read_step.declared_intent or "")
    assert "Plan phase" not in (read_step.declared_intent or "")


# --- step relationships ---


def test_tool_result_step_has_parent_tool_call_step(
    adapter: BenchmarkSessionAdapter,
) -> None:
    messages = [
        {"role": "user", "content": "Audit."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": '{"path": "main.py"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": "file contents",
        },
    ]
    trace = adapter.ingest(messages)
    tool_call_step = trace.steps[1]
    tool_result_step = trace.steps[2]
    assert tool_result_step.parent_step_id == tool_call_step.step_id


# --- timestamp ---


def test_all_steps_have_reasonable_timestamps(adapter: BenchmarkSessionAdapter) -> None:
    messages = [{"role": "user", "content": "Hi"}]
    trace = adapter.ingest(messages)
    for step in trace.steps:
        assert isinstance(step.timestamp, datetime)


# --- strip_payloads ---


def test_strip_payloads_redacts_tool_input(adapter: BenchmarkSessionAdapter) -> None:
    adapter_strip = BenchmarkSessionAdapter(strip_payloads=True)
    messages = [
        {"role": "user", "content": "Audit."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": '{"path": "secret.py"}',
                    },
                }
            ],
        },
    ]
    trace = adapter_strip.ingest(messages)
    tool_step = trace.steps[1]
    payload = tool_step.action.payload
    assert "redacted" in payload
    assert "secret.py" not in str(payload)
