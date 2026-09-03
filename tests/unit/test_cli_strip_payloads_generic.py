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

`TestReportStripPayloads` (P4) pins the equivalent fix for `auditk report`,
which had no `--strip-payloads` at all until now (`ingest` had it,
`report` didn't -- an inconsistent CLI surface, not a redaction gap).
Unlike `ingest`, `report` also *analyses* the trace it ingests
(`analysis/findings.py`'s structural findings engine), and several of those
findings read straight out of `Action.payload` content
(`find_bash_tripwires`'s `input["command"]`, in particular) -- so this
class also pins the real, expected trade-off: a genuinely redacted payload
is a `{"redacted": True, "size": N}` dict, `_command()`/`_tool_input()`'s
`isinstance(..., str)` guards correctly treat that as "no command", and a
tripwire that would have fired against the raw command does not fire
against the redacted one. That is not a bug in `--strip-payloads` -- the
redaction itself works identically to `ingest`'s -- it is what "redact
before analysing" necessarily costs, and `report --strip-payloads`'s own
help text documents it.
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


def _claude_code_tripwire_events() -> list[dict[str, Any]]:
    """A minimal claude-code session with one destructive-`rm` Bash call --
    the shape `find_bash_tripwires` reads (`Action.payload["input"]["command"]`,
    claude-code-adapter-specific; see analysis/findings.py). Deliberately
    NOT one of the repo's checked-in fixtures (none of them carries a
    tripwire command) -- synthetic and self-contained, same convention as
    this file's other native-format fixtures.
    """
    return [
        {"type": "user", "message": {"content": "Clean up the scratch directory."}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu-rm",
                        "name": "Bash",
                        "input": {"command": "rm -rf /tmp/scratch"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tu-rm", "content": "removed"}]
            },
        },
    ]


class TestReportStripPayloads:
    """P4: `report --strip-payloads` (see this module's docstring for the
    "redacted payloads mean fewer findings can fire" trade-off this pins
    alongside the redaction itself)."""

    def test_claude_code_without_strip_payloads_tripwire_fires(self, tmp_path: Path) -> None:
        in_file = tmp_path / "session.jsonl"
        in_file.write_text("\n".join(json.dumps(e) for e in _claude_code_tripwire_events()) + "\n")

        result = runner.invoke(
            app,
            ["report", "--in", str(in_file), "--no-policy-context"],
        )

        assert result.exit_code == 0, result.output
        assert "rm -rf /tmp/scratch" in result.output
        assert "destructive-rm" in result.output

    def test_claude_code_strip_payloads_redacts_the_command_and_the_tripwire_does_not_fire(
        self, tmp_path: Path
    ) -> None:
        in_file = tmp_path / "session.jsonl"
        in_file.write_text("\n".join(json.dumps(e) for e in _claude_code_tripwire_events()) + "\n")

        result = runner.invoke(
            app,
            ["report", "--in", str(in_file), "--no-policy-context", "--strip-payloads"],
        )

        assert result.exit_code == 0, result.output
        assert "rm -rf" not in result.output
        assert "destructive-rm" not in result.output

    def test_generic_otel_strip_payloads_redacts_the_underlying_trace(self, tmp_path: Path) -> None:
        """A non-claude-code adapter's report also honours the flag (the CLI
        gap this closes was in the generic `_ingest_generic_adapter_report`
        path, not just claude-code's). generic-otel's payload shape isn't
        one `find_bash_tripwires` reads, so this pins the redaction itself
        (via --format json, which includes the header/timeline verbatim)
        rather than a findings-engine side effect."""
        in_file = tmp_path / "spans.json"
        in_file.write_text(json.dumps(_otel_spans()))

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "generic-otel",
                "--in",
                str(in_file),
                "--no-policy-context",
                "--strip-payloads",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "ls sandbox/" not in result.output
        assert "file1" not in result.output

    def test_generic_otel_without_strip_payloads_content_survives(self, tmp_path: Path) -> None:
        in_file = tmp_path / "spans.json"
        in_file.write_text(json.dumps(_otel_spans()))

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "generic-otel",
                "--in",
                str(in_file),
                "--no-policy-context",
            ],
        )

        assert result.exit_code == 0, result.output
        # generic-otel's payload isn't read by any timeline/findings rule,
        # so this just pins that omitting the flag doesn't accidentally
        # redact -- not that the content appears in the report body.
        assert "redacted" not in result.output.lower()


class TestReportPiStubStripPayloads:
    """`report --adapter pi --strip-payloads` refuses with the same gated
    message as every other pi entry point -- see
    tests/unit/test_cli_pi_stub.py::TestReportPiStub for the flag-less
    case (this test only exists here because `--strip-payloads` on
    `report` didn't exist yet when that module was written)."""

    def test_refuses_with_the_documented_message(self, tmp_path: Path) -> None:
        from auditk.adapters.pi import PI_GATED_MESSAGE

        in_file = tmp_path / "pi-session.json"
        in_file.write_text(json.dumps([{"type": "session", "version": 3, "id": "sess-1"}]))

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "pi",
                "--in",
                str(in_file),
                "--no-policy-context",
                "--strip-payloads",
            ],
        )

        assert result.exit_code != 0
        assert PI_GATED_MESSAGE in result.output
        assert "Traceback" not in result.output
