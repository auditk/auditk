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


class BenchmarkRunner:
    """Drive a model through a benchmark task."""

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

    def _chat_completion(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_id,
                "messages": messages,
                "tools": tools,
                "temperature": 0,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        return cast(dict[str, Any], data["choices"][0]["message"])

    def run(self, task: BenchmarkTask) -> Trace:
        if not self.api_key:
            raise ValueError("api_key is required")

        tools = self._build_tools(task.tools)

        messages = [
            {
                "role": "system",
                "content": task.system_prompt,
                "session_id": task.task_id,
            },
            {"role": "user", "content": task.user_prompt},
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
