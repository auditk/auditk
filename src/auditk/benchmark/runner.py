# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark runner: multi-turn OpenAI-compatible tool-use loop.
Drives a model through a benchmark task using httpx, captures the full
message history, and emits a Trace via BenchmarkSessionAdapter.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import httpx
from anthropic import Anthropic

from auditk.benchmark.adapter import BenchmarkSessionAdapter
from auditk.benchmark.task import BenchmarkTask
from auditk.schema import Trace

_FIXTURE_DIR = (
    Path(__file__).parent.parent.parent.parent
    / "tests"
    / "fixtures"
    / "benchmark"
    / "inventory_service"
)
_READFILE_STUB = (
    "def main() -> None:\n"
    '    """Entry point for the audit runner."""\n'
    "    print('Starting audit...')\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)
_TOOL_SCHEMAS: dict[str, Any] = {
    "ReadFile": {
        "type": "function",
        "function": {
            "name": "ReadFile",
            "description": "Read a file from the fixture repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    "WriteFile": {
        "type": "function",
        "function": {
            "name": "WriteFile",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "TodoWrite": {
        "type": "function",
        "function": {
            "name": "TodoWrite",
            "description": "Record or update the sub-goal plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                                "priority": {"type": "string"},
                            },
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    "Report": {
        "type": "function",
        "function": {
            "name": "Report",
            "description": "Emit the final structured audit report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "line_range": {"type": "string"},
                                "problem": {"type": "string"},
                                "recommendation": {"type": "string"},
                            },
                        },
                    }
                },
                "required": ["issues"],
            },
        },
    },
}


class BenchmarkToolHarness:
    """Shared tool schemas and stub implementations for benchmark runners."""

    def _handle_tool(self, name: str, args: dict[str, Any]) -> str:
        if name == "ReadFile":
            path = args.get("path", "")
            filename = Path(path).name
            target = _FIXTURE_DIR / filename
            if target.exists():
                return target.read_text()
            return f"File not found: {filename}"
        elif name == "WriteFile":
            path = args.get("path", "unknown")
            return f"File {path} written successfully"
        elif name == "TodoWrite":
            todos = args.get("todos", [])
            active = [
                t for t in todos if t.get("status") in ("pending", "in_progress")
            ]
            return f"Todo list updated. {len(active)} active tasks. OK"
        elif name == "Report":
            return "Report received. Benchmark session complete."
        else:
            raise ValueError(f"Unknown tool: {name}")


class BenchmarkRunner(BenchmarkToolHarness):
    """Drive a model through a benchmark task via an OpenAI-compatible endpoint."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_id: str,
        *,
        max_turns: int = 20,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model_id = model_id
        self.max_turns = max_turns

    def _build_tools(self, tool_names: list[str]) -> list[dict[str, Any]]:
        return [_TOOL_SCHEMAS[name] for name in tool_names if name in _TOOL_SCHEMAS]

    def _chat_completion(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        # Strip any non-standard fields from messages before sending
        clean_messages = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in (
                "role", "content", "tool_calls", "tool_call_id", "name"
            )}
            clean_messages.append(clean)
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_id,
                "messages": clean_messages,
                "tools": tools,
                "temperature": 0,
                "max_tokens": 4096,
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        return cast(dict[str, Any], data["choices"][0]["message"])

    def run(self, task: BenchmarkTask) -> Trace:
        if not self.api_key:
            raise ValueError("api_key is required")
        tools = self._build_tools(task.tools)
        # Keep session_id in messages for adapter but strip before API calls
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": task.system_prompt,
            },
            {
                "role": "user",
                "content": task.user_prompt,
                "session_id": task.task_id,
            },
        ]
        for _ in range(self.max_turns):
            response = self._chat_completion(messages, tools)
            messages.append(response)
            tool_calls = response.get("tool_calls", [])
            if not tool_calls:
                break
            should_break = False
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    args = json.loads(raw_args)
                except (json.JSONDecodeError, KeyError):
                    args = {}
                result = self._handle_tool(name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": result,
                    }
                )
                if name == "Report":
                    should_break = True
            if should_break:
                break
        adapter = BenchmarkSessionAdapter()
        return adapter.ingest(messages)


class AnthropicBenchmarkRunner(BenchmarkToolHarness):
    """Drive a model through a benchmark task via the Anthropic Messages API."""

    def __init__(
        self,
        api_key: str,
        model_id: str,
        *,
        max_turns: int = 20,
    ) -> None:
        self.api_key = api_key
        self.model_id = model_id
        self.max_turns = max_turns
        self.client = Anthropic(api_key=api_key)

    def _build_tools(self, tool_names: list[str]) -> list[dict[str, Any]]:
        anthropic_tools: list[dict[str, Any]] = []
        for name in tool_names:
            if name not in _TOOL_SCHEMAS:
                continue
            schema = _TOOL_SCHEMAS[name]
            func = schema.get("function", {})
            anthropic_tools.append(
                {
                    "type": "custom",
                    "name": func.get("name"),
                    "description": func.get("description"),
                    "input_schema": func.get("parameters"),
                }
            )
        return anthropic_tools

    def _to_openai_assistant_message(self, anthropic_msg: Any) -> dict[str, Any]:
        content = ""
        tool_calls: list[dict[str, Any]] = []
        for block in anthropic_msg.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input),
                        },
                    }
                )
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }

    def _to_anthropic_messages(
        self, openai_msgs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        anthropic_msgs: list[dict[str, Any]] = []
        i = 0
        while i < len(openai_msgs):
            msg = openai_msgs[i]
            role = msg.get("role")
            if role == "system":
                i += 1
                continue
            elif role == "user":
                anthropic_msgs.append(
                    {"role": "user", "content": msg.get("content", "")}
                )
                i += 1
            elif role == "assistant":
                content_blocks: list[dict[str, Any]] = []
                text_content = msg.get("content", "")
                if text_content:
                    content_blocks.append({"type": "text", "text": text_content})
                for tc in msg.get("tool_calls", []):
                    func = tc.get("function", {})
                    raw_args = func.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id"),
                            "name": func.get("name"),
                            "input": args,
                        }
                    )
                anthropic_msgs.append({"role": "assistant", "content": content_blocks})
                i += 1
            elif role == "tool":
                tool_results: list[dict[str, Any]] = []
                while (
                    i < len(openai_msgs)
                    and openai_msgs[i].get("role") == "tool"
                ):
                    tool_msg = openai_msgs[i]
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_msg.get("tool_call_id"),
                            "content": tool_msg.get("content", ""),
                        }
                    )
                    i += 1
                anthropic_msgs.append({"role": "user", "content": tool_results})
            else:
                i += 1
        return anthropic_msgs

    def _call_anthropic(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> Any:
        return self.client.messages.create(
            model=self.model_id,
            messages=cast(Any, messages),
            tools=cast(Any, tools),
            system=system,
            max_tokens=4096,
            temperature=0,
        )

    def run(self, task: BenchmarkTask) -> Trace:
        if not self.api_key:
            raise ValueError("api_key is required")
        tools = self._build_tools(task.tools)
        openai_history: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": task.system_prompt,
            },
            {
                "role": "user",
                "content": task.user_prompt,
                "session_id": task.task_id,
            },
        ]
        for _ in range(self.max_turns):
            anthropic_messages = self._to_anthropic_messages(openai_history)
            response = self._call_anthropic(
                anthropic_messages, tools, task.system_prompt
            )
            openai_assistant = self._to_openai_assistant_message(response)
            openai_history.append(openai_assistant)
            tool_calls = openai_assistant.get("tool_calls", [])
            if not tool_calls:
                break
            should_break = False
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "")
                try:
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    args = json.loads(raw_args)
                except (json.JSONDecodeError, KeyError):
                    args = {}
                result = self._handle_tool(name, args)
                openai_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": result,
                    }
                )
                if name == "Report":
                    should_break = True
            if should_break:
                break
        adapter = BenchmarkSessionAdapter()
        return adapter.ingest(openai_history)