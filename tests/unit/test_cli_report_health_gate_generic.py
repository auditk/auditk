# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""CLI-level pin for P1b gap 2: `auditk report`'s adapter-health canary
gate used to be wired ONLY for `--adapter claude-code` -- the `else`
branch called `trace_adapter.ingest(events)` directly, with no
`check_adapter_health` call at all, so a langgraph or generic-otel report
got no health check whatsoever (see docs/adapters.md's former "Known
contract gaps" #1).

This module pins the generic wiring at the CLI boundary:

- a langgraph/generic-otel session whose unknown-record-type share
  breaches its own `HealthDeclaration` makes `report` refuse, same as
  claude-code (`TestGenericAdapterHealthGate`);
- `--force` still overrides it, same as claude-code;
- an adapter with NO health declaration at all is reported as such
  (visibly, in the CLI output) rather than the check being silently
  skipped with no trace (`TestNoDeclarationIsVisible`), simulated via a
  monkeypatched registry since every shipped adapter has a declaration
  today.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from auditk.adapters import registry as adapter_registry
from auditk.cli import app

runner = CliRunner()

_REPORT_HEADER_MARKER = "# Session post-mortem"


def _langgraph_checkpoint(checkpoint_id: str, *, source: str = "loop") -> dict[str, Any]:
    return {
        "config": {"configurable": {"thread_id": "thread-1"}},
        "checkpoint": {"id": checkpoint_id},
        "metadata": {"step": 0, "source": source, "writes": {}},
    }


def _breaching_langgraph_checkpoints() -> list[dict[str, Any]]:
    # 4/5 checkpoints carry a `source` LangGraph has never emitted -- 80%
    # unknown, over the 5% floor (mirrors
    # tests/conformance/providers.py's `_lg_health_fixture`).
    return [
        _langgraph_checkpoint(f"ck-unknown-{i}", source="totally-new-conformance-source")
        for i in range(4)
    ] + [_langgraph_checkpoint("ck-known", source="loop")]


def _healthy_langgraph_checkpoints() -> list[dict[str, Any]]:
    return [_langgraph_checkpoint("ck-1", source="loop")]


class TestGenericAdapterHealthGate:
    def test_report_exits_nonzero_on_breaching_langgraph_session_without_force(
        self, tmp_path: Path
    ) -> None:
        in_file = tmp_path / "checkpoints.json"
        in_file.write_text(json.dumps(_breaching_langgraph_checkpoints()))

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "langgraph",
                "--in",
                str(in_file),
                "--no-policy-context",
            ],
        )

        assert result.exit_code != 0, result.output
        assert _REPORT_HEADER_MARKER not in result.output
        assert "health" in result.output.lower()

    def test_report_force_overrides_langgraph_breach(self, tmp_path: Path) -> None:
        in_file = tmp_path / "checkpoints.json"
        in_file.write_text(json.dumps(_breaching_langgraph_checkpoints()))

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "langgraph",
                "--in",
                str(in_file),
                "--force",
                "--no-policy-context",
            ],
        )

        assert result.exit_code == 0, result.output
        assert _REPORT_HEADER_MARKER in result.output

    def test_report_on_healthy_langgraph_session_needs_no_force(self, tmp_path: Path) -> None:
        in_file = tmp_path / "checkpoints.json"
        in_file.write_text(json.dumps(_healthy_langgraph_checkpoints()))

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "langgraph",
                "--in",
                str(in_file),
                "--no-policy-context",
            ],
        )

        assert result.exit_code == 0, result.output
        assert _REPORT_HEADER_MARKER in result.output


class TestNoDeclarationIsVisible:
    def test_adapter_with_no_health_declaration_says_so_rather_than_staying_silent(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        from auditk.adapters.generic_otel import OtelTraceAdapter

        fake_registry = dict(adapter_registry._REGISTRY)
        fake_registry["fake-no-declaration"] = OtelTraceAdapter()
        monkeypatch.setattr(adapter_registry, "_REGISTRY", fake_registry)
        # Deliberately NOT added to _HEALTH_DECLARATIONS.

        in_file = tmp_path / "spans.json"
        in_file.write_text(
            json.dumps(
                [
                    {
                        "span_id": "span-1",
                        "trace_id": "trace-1",
                        "parent_span_id": None,
                        "name": "root",
                        "start_time": "2026-01-01T00:00:00Z",
                        "attributes": {"openinference.span.kind": "AGENT"},
                    }
                ]
            )
        )

        result = runner.invoke(
            app,
            [
                "report",
                "--adapter",
                "fake-no-declaration",
                "--in",
                str(in_file),
                "--no-policy-context",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "no health declaration" in result.output.lower()
        assert _REPORT_HEADER_MARKER in result.output
