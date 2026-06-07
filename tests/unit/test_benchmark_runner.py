"""Red-phase tests for benchmark/runner.py.

Tests BenchmarkRunner: multi-turn OpenAI-compatible tool-use loop.
"""

import json
from unittest.mock import MagicMock

import pytest

from auditk.benchmark.runner import BenchmarkRunner
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
