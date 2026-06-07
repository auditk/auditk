"""Red-phase tests for benchmark/runner.py.

Tests BenchmarkRunner: multi-turn OpenAI-compatible tool-use loop.
"""

import json
from unittest.mock import MagicMock

import pytest

from auditk.benchmark.runner import AnthropicBenchmarkRunner, BenchmarkRunner
from auditk.benchmark.task import BenchmarkTask, BENCHMARK_TASKS
from auditk.schema import Trace


def _task_message(task: BenchmarkTask) -> dict:
    return {
        "role": "user",
        "content": task.user_prompt,
        "session_id": task.task_id,
    }


# --- init ---


def test_runner_init_defaults() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    assert runner.api_key == "fake"
    assert runner.base_url == "http://test"
    assert runner.model_id == "test-model"
    assert runner.max_turns == 20


def test_runner_init_custom_max_turns() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model", max_turns=5
    )
    assert runner.max_turns == 5


# --- validation ---


def test_runner_run_raises_on_empty_api_key() -> None:
    runner = BenchmarkRunner(
        api_key="", base_url="http://test", model_id="test-model"
    )
    task = BENCHMARK_TASKS[0]
    with pytest.raises(ValueError, match="api_key"):
        runner.run(task)


# --- tool stubs ---


def test_runner_readfile_stub_returns_content() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    result = runner._handle_tool("ReadFile", {"path": "main.py"})
    assert isinstance(result, str)
    assert len(result) > 0


def test_runner_writefile_stub_returns_confirmation() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    result = runner._handle_tool(
        "WriteFile", {"path": "report.md", "content": "# Report"}
    )
    assert isinstance(result, str)
    assert "written" in result.lower() or "ok" in result.lower()


def test_runner_todowrite_stub_returns_ok() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    result = runner._handle_tool(
        "TodoWrite",
        {
            "todos": [
                {
                    "id": "1",
                    "content": "test",
                    "status": "pending",
                    "priority": "high",
                }
            ]
        },
    )
    assert isinstance(result, str)
    assert "ok" in result.lower()


def test_runner_report_stub_returns_confirmation() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    result = runner._handle_tool(
        "Report",
        {
            "issues": [
                {
                    "file": "main.py",
                    "line_range": "1-10",
                    "problem": "test",
                    "recommendation": "fix",
                }
            ]
        },
    )
    assert isinstance(result, str)
    assert "report" in result.lower() or "ok" in result.lower()


def test_runner_unknown_tool_raises() -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    with pytest.raises(ValueError, match="Unknown tool"):
        runner._handle_tool("UnknownTool", {})


# --- run loop ---


def test_runner_run_returns_trace_with_mocked_chat(monkeypatch) -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    task = BENCHMARK_TASKS[0]

    call_count = 0

    def fake_chat(messages, tools):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "tc-1",
                        "type": "function",
                        "function": {
                            "name": "TodoWrite",
                            "arguments": json.dumps(
                                {
                                    "todos": [
                                        {
                                            "id": "1",
                                            "content": "Read files",
                                            "status": "in_progress",
                                            "priority": "high",
                                        }
                                    ]
                                }
                            ),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "Done", "tool_calls": []}

    monkeypatch.setattr(runner, "_chat_completion", fake_chat)
    trace = runner.run(task)
    assert isinstance(trace, Trace)
    assert trace.source_adapter == "benchmark-api"
    assert len(trace.steps) > 0


def test_runner_run_respects_max_turns(monkeypatch) -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model", max_turns=3
    )
    task = BENCHMARK_TASKS[0]

    call_count = 0

    def fake_chat(messages, tools):
        nonlocal call_count
        call_count += 1
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"tc-{call_count}",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": json.dumps({"path": "main.py"}),
                    },
                }
            ],
        }

    monkeypatch.setattr(runner, "_chat_completion", fake_chat)
    trace = runner.run(task)
    assert isinstance(trace, Trace)
    # The chat completion should have been called at most max_turns times
    assert call_count <= 3


def test_runner_run_terminates_on_report(monkeypatch) -> None:
    runner = BenchmarkRunner(
        api_key="fake", base_url="http://test", model_id="test-model"
    )
    task = BENCHMARK_TASKS[0]

    call_count = 0

    def fake_chat(messages, tools):
        nonlocal call_count
        call_count += 1
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {
                        "name": "Report",
                        "arguments": json.dumps({"issues": []}),
                    },
                }
            ],
        }

    monkeypatch.setattr(runner, "_chat_completion", fake_chat)
    trace = runner.run(task)
    assert isinstance(trace, Trace)
    # Should have stopped after the Report tool (1 turn only)
    assert call_count == 1


# --- AnthropicBenchmarkRunner ---


# --- init ---


def test_anthropic_runner_init_defaults() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    assert runner.api_key == "fake"
    assert runner.model_id == "test-model"
    assert runner.max_turns == 20


def test_anthropic_runner_init_custom_max_turns() -> None:
    runner = AnthropicBenchmarkRunner(
        api_key="fake", model_id="test-model", max_turns=5
    )
    assert runner.max_turns == 5


# --- validation ---


def test_anthropic_runner_run_raises_on_empty_api_key() -> None:
    runner = AnthropicBenchmarkRunner(api_key="", model_id="test-model")
    task = BENCHMARK_TASKS[0]
    with pytest.raises(ValueError, match="api_key"):
        runner.run(task)


# --- tool stubs (inherited/composed) ---


def test_anthropic_runner_handle_tool_readfile() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    result = runner._handle_tool("ReadFile", {"path": "main.py"})
    assert isinstance(result, str)
    assert len(result) > 0


def test_anthropic_runner_handle_tool_unknown_raises() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    with pytest.raises(ValueError, match="Unknown tool"):
        runner._handle_tool("UnknownTool", {})


# --- conversion ---


def test_anthropic_to_openai_assistant_text_only() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(type="text", text="Done")]
    fake_msg.stop_reason = "end_turn"
    msg = runner._to_openai_assistant_message(fake_msg)
    assert msg["role"] == "assistant"
    assert msg["content"] == "Done"
    assert msg["tool_calls"] == []


def test_anthropic_to_openai_assistant_with_tool_use() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    fake_msg = MagicMock()
    fake_block = MagicMock()
    fake_block.type = "tool_use"
    fake_block.id = "toolu_01"
    fake_block.name = "TodoWrite"
    fake_block.input = {"todos": [{"id": "1", "content": "test"}]}
    fake_msg.content = [fake_block]
    fake_msg.stop_reason = "tool_use"
    msg = runner._to_openai_assistant_message(fake_msg)
    assert msg["role"] == "assistant"
    assert msg["content"] == ""
    assert len(msg["tool_calls"]) == 1
    assert msg["tool_calls"][0]["id"] == "toolu_01"
    assert msg["tool_calls"][0]["type"] == "function"
    assert msg["tool_calls"][0]["function"]["name"] == "TodoWrite"
    assert (
        json.loads(msg["tool_calls"][0]["function"]["arguments"])
        == {"todos": [{"id": "1", "content": "test"}]}
    )


def test_openai_to_anthropic_messages() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    openai_msgs = [
        {"role": "system", "content": "sys"},
        {
            "role": "user",
            "content": "hello",
            "session_id": "s1",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {
                        "name": "ReadFile",
                        "arguments": json.dumps({"path": "x.py"}),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc1", "content": "file content"},
    ]
    anthropic_msgs = runner._to_anthropic_messages(openai_msgs)
    # system message should be dropped
    assert len(anthropic_msgs) == 3
    assert anthropic_msgs[0]["role"] == "user"
    assert anthropic_msgs[0]["content"] == "hello"
    # assistant with tool_use
    assert anthropic_msgs[1]["role"] == "assistant"
    assert anthropic_msgs[1]["content"][0]["type"] == "tool_use"
    assert anthropic_msgs[1]["content"][0]["name"] == "ReadFile"
    # tool result
    assert anthropic_msgs[2]["role"] == "user"
    assert anthropic_msgs[2]["content"][0]["type"] == "tool_result"
    assert anthropic_msgs[2]["content"][0]["tool_use_id"] == "tc1"
    assert anthropic_msgs[2]["content"][0]["content"] == "file content"


# --- schema translation ---


def test_anthropic_build_tools_converts_openai_schemas() -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    tools = runner._build_tools(["ReadFile", "WriteFile"])
    assert len(tools) == 2
    assert tools[0]["type"] == "custom"
    assert tools[0]["name"] == "ReadFile"
    assert "input_schema" in tools[0]
    assert tools[0]["input_schema"]["type"] == "object"
    # No outer "function" wrapper
    assert "function" not in tools[0]


# --- run loop ---


def test_anthropic_runner_run_returns_trace_with_mocked_chat(monkeypatch) -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    task = BENCHMARK_TASKS[0]

    call_count = 0

    def fake_chat(messages, tools, system):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            fake_msg = MagicMock()
            fake_block = MagicMock()
            fake_block.type = "tool_use"
            fake_block.id = "tc-1"
            fake_block.name = "TodoWrite"
            fake_block.input = {
                "todos": [
                    {
                        "id": "1",
                        "content": "Read files",
                        "status": "in_progress",
                        "priority": "high",
                    }
                ]
            }
            fake_msg.content = [fake_block]
            fake_msg.stop_reason = "tool_use"
            return fake_msg
        fake_msg = MagicMock()
        fake_msg.content = [MagicMock(type="text", text="Done")]
        fake_msg.stop_reason = "end_turn"
        return fake_msg

    monkeypatch.setattr(runner, "_call_anthropic", fake_chat)
    trace = runner.run(task)
    assert isinstance(trace, Trace)
    assert trace.source_adapter == "benchmark-api"
    assert len(trace.steps) > 0


def test_anthropic_runner_run_respects_max_turns(monkeypatch) -> None:
    runner = AnthropicBenchmarkRunner(
        api_key="fake", model_id="test-model", max_turns=3
    )
    task = BENCHMARK_TASKS[0]

    call_count = 0

    def fake_chat(messages, tools, system):
        nonlocal call_count
        call_count += 1
        fake_msg = MagicMock()
        fake_block = MagicMock()
        fake_block.type = "tool_use"
        fake_block.id = f"tc-{call_count}"
        fake_block.name = "ReadFile"
        fake_block.input = {"path": "main.py"}
        fake_msg.content = [fake_block]
        fake_msg.stop_reason = "tool_use"
        return fake_msg

    monkeypatch.setattr(runner, "_call_anthropic", fake_chat)
    trace = runner.run(task)
    assert isinstance(trace, Trace)
    assert call_count <= 3


def test_anthropic_runner_run_terminates_on_report(monkeypatch) -> None:
    runner = AnthropicBenchmarkRunner(api_key="fake", model_id="test-model")
    task = BENCHMARK_TASKS[0]

    call_count = 0

    def fake_chat(messages, tools, system):
        nonlocal call_count
        call_count += 1
        fake_msg = MagicMock()
        fake_block = MagicMock()
        fake_block.type = "tool_use"
        fake_block.id = "tc-1"
        fake_block.name = "Report"
        fake_block.input = {"issues": []}
        fake_msg.content = [fake_block]
        fake_msg.stop_reason = "tool_use"
        return fake_msg

    monkeypatch.setattr(runner, "_call_anthropic", fake_chat)
    trace = runner.run(task)
    assert isinstance(trace, Trace)
    assert call_count == 1
