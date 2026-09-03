# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""CLI-level pin for P1b gap 1: `auditk ingest --strip-payloads` used to be
a silent no-op for every adapter except claude-code (`cli.py`'s `ingest`
`else` branch called `trace_adapter.ingest(events)` with no way to pass the
flag through at all -- see docs/adapters.md's former "Known contract gaps"
#2). This module pins the fix at the CLI boundary, not just the adapter
unit level:

- `--strip-payloads` genuinely redacts for langgraph and generic-otel now
  (`TestStripPayloadsActuallyRedacts`), proving the flag is no longer
  ignored for either.
- Omitting `--strip-payloads` still round-trips raw content
  (`TestWithoutStripPayloadsContentSurvives`) -- the flag must do
  something, not always redact regardless.
- An adapter with no redaction support at all makes `ingest` refuse
  loudly (non-zero exit, a clear message) rather than silently proceeding
  as if the flag had no effect (`TestUnsupportedAdapterRefusesLoudly`),
  simulated by monkeypatching a adapter into the registry with no
  matching redaction factory -- there is no real adapter shipped today
  that lacks this, so the registry is the only way to exercise the
  refusal path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from auditk.adapters import registry as adapter_registry
from auditk.cli import app

runner = CliRunner()


def _langgraph_checkpoints() -> list[dict[str, Any]]:
    return [
        {
            "config": {"configurable": {"thread_id": "thread-1"}},
            "checkpoint": {"id": "ck-1"},
            "metadata": {
                "step": 0,
                "source": "loop",
                "writes": {"run_sandbox_ls": {"result": "file1\nfile2", "cwd": "sandbox/"}},
            },
        }
    ]


def _otel_spans() -> list[dict[str, Any]]:
    return [
        {
            "span_id": "span-1",
            "trace_id": "trace-1",
            "parent_span_id": None,
            "name": "Bash",
            "start_time": "2026-01-01T00:00:00Z",
            "attributes": {
                "openinference.span.kind": "TOOL",
                "input.value": "ls sandbox/",
                "output.value": "file1\nfile2",
            },
        }
    ]


def _tool_call_step(trace: dict[str, Any]) -> dict[str, Any]:
    step = next(s for s in trace["steps"] if s["action"]["type"] == "tool_call")
    return step  # type: ignore[no-any-return]


class TestStripPayloadsActuallyRedacts:
    def test_langgraph_strip_payloads_redacts_writes(self, tmp_path: Path) -> None:
        in_file = tmp_path / "checkpoints.json"
        in_file.write_text(json.dumps(_langgraph_checkpoints()))
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "langgraph",
                "--in",
                str(in_file),
                "--out",
                str(out_file),
                "--strip-payloads",
            ],
        )

        assert result.exit_code == 0, result.output
        trace = json.loads(out_file.read_text())
        step = _tool_call_step(trace)
        assert step["action"]["payload"]["node"] == "run_sandbox_ls"
        assert step["action"]["payload"]["writes"]["redacted"] is True
        assert "file1" not in json.dumps(step["action"]["payload"]["writes"])

    def test_generic_otel_strip_payloads_redacts_input_output(self, tmp_path: Path) -> None:
        in_file = tmp_path / "spans.json"
        in_file.write_text(json.dumps(_otel_spans()))
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "generic-otel",
                "--in",
                str(in_file),
                "--out",
                str(out_file),
                "--strip-payloads",
            ],
        )

        assert result.exit_code == 0, result.output
        trace = json.loads(out_file.read_text())
        step = _tool_call_step(trace)
        assert step["action"]["payload"]["name"] == "Bash"
        assert step["action"]["payload"]["input"]["redacted"] is True
        assert step["action"]["payload"]["output"]["redacted"] is True
        assert "ls sandbox/" not in json.dumps(step["action"]["payload"])
        assert "file1" not in json.dumps(step["action"]["payload"])


class TestWithoutStripPayloadsContentSurvives:
    def test_langgraph_without_strip_payloads_keeps_raw_writes(self, tmp_path: Path) -> None:
        in_file = tmp_path / "checkpoints.json"
        in_file.write_text(json.dumps(_langgraph_checkpoints()))
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            ["ingest", "--adapter", "langgraph", "--in", str(in_file), "--out", str(out_file)],
        )

        assert result.exit_code == 0, result.output
        trace = json.loads(out_file.read_text())
        step = _tool_call_step(trace)
        assert step["action"]["payload"]["writes"] == {
            "result": "file1\nfile2",
            "cwd": "sandbox/",
        }

    def test_generic_otel_without_strip_payloads_keeps_raw_input_output(
        self, tmp_path: Path
    ) -> None:
        in_file = tmp_path / "spans.json"
        in_file.write_text(json.dumps(_otel_spans()))
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            ["ingest", "--adapter", "generic-otel", "--in", str(in_file), "--out", str(out_file)],
        )

        assert result.exit_code == 0, result.output
        trace = json.loads(out_file.read_text())
        step = _tool_call_step(trace)
        assert step["action"]["payload"]["input"] == "ls sandbox/"
        assert step["action"]["payload"]["output"] == "file1\nfile2"


class TestUnsupportedAdapterRefusesLoudly:
    def test_ingest_refuses_rather_than_silently_ignoring_strip_payloads(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """No shipped adapter lacks redaction support today -- registered in
        `_REGISTRY` but absent from `_FACTORIES` is exactly what a future
        adapter that genuinely can't redact would look like. Simulated here
        by monkeypatching the registry, since there is no real one to use.
        """
        from auditk.adapters.generic_otel import OtelTraceAdapter

        fake_registry = dict(adapter_registry._REGISTRY)
        fake_registry["fake-no-redact"] = OtelTraceAdapter()
        monkeypatch.setattr(adapter_registry, "_REGISTRY", fake_registry)
        # Deliberately NOT added to _FACTORIES -- this is the "can't redact"
        # adapter.

        in_file = tmp_path / "spans.json"
        in_file.write_text(json.dumps(_otel_spans()))
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "fake-no-redact",
                "--in",
                str(in_file),
                "--out",
                str(out_file),
                "--strip-payloads",
            ],
        )

        assert result.exit_code != 0
        assert not out_file.exists()
        assert "redaction" in result.output.lower() or "strip" in result.output.lower()

    def test_without_strip_payloads_the_same_adapter_still_works(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """The refusal is specific to `--strip-payloads`; an adapter with no
        redaction factory must still ingest normally without the flag."""
        from auditk.adapters.generic_otel import OtelTraceAdapter

        fake_registry = dict(adapter_registry._REGISTRY)
        fake_registry["fake-no-redact"] = OtelTraceAdapter()
        monkeypatch.setattr(adapter_registry, "_REGISTRY", fake_registry)

        in_file = tmp_path / "spans.json"
        in_file.write_text(json.dumps(_otel_spans()))
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "fake-no-redact",
                "--in",
                str(in_file),
                "--out",
                str(out_file),
            ],
        )

        assert result.exit_code == 0, result.output
        assert out_file.exists()
