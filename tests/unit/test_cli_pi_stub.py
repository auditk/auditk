# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""CLI-level pin for the Pi adapter stub (P3): `--adapter pi` must produce
the clean, documented refusal message -- never an uncaught stack trace,
never a silent no-op -- from both `ingest` and `report`.

Before this module's fix, `cli.py`'s `ingest` command called
`trace_adapter.ingest(events)` OUTSIDE its `try: get_adapter(...)` block,
and `_ingest_generic_adapter_report` (used by `report`) had no error
handling around `get_adapter`/`ingest()` at all -- either would have let a
gated stub's `ValueError` propagate as an uncaught exception rather than
the clean "Error: ..." refusal every other adapter-lookup failure produces.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auditk.adapters.pi import PI_GATED_MESSAGE
from auditk.cli import app

runner = CliRunner()


def _write_native(tmp_path: Path) -> Path:
    in_file = tmp_path / "pi-session.json"
    in_file.write_text(json.dumps([{"type": "session", "version": 3, "id": "sess-1"}]))
    return in_file


class TestIngestPiStub:
    def test_refuses_with_the_documented_message(self, tmp_path: Path) -> None:
        in_file = _write_native(tmp_path)
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            ["ingest", "--adapter", "pi", "--in", str(in_file), "--out", str(out_file)],
        )

        assert result.exit_code != 0
        assert PI_GATED_MESSAGE in result.output
        assert "Traceback" not in result.output
        assert not out_file.exists()

    def test_refuses_the_same_way_with_strip_payloads(self, tmp_path: Path) -> None:
        in_file = _write_native(tmp_path)
        out_file = tmp_path / "trace.json"

        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "pi",
                "--in",
                str(in_file),
                "--out",
                str(out_file),
                "--strip-payloads",
            ],
        )

        assert result.exit_code != 0
        assert PI_GATED_MESSAGE in result.output
        assert "Traceback" not in result.output
        assert not out_file.exists()


class TestReportPiStub:
    def test_refuses_with_the_documented_message(self, tmp_path: Path) -> None:
        in_file = _write_native(tmp_path)

        result = runner.invoke(app, ["report", "--adapter", "pi", "--in", str(in_file)])

        assert result.exit_code != 0
        assert PI_GATED_MESSAGE in result.output
        assert "Traceback" not in result.output

    # `report --strip-payloads` itself doesn't exist yet at this point in
    # the branch history (see the `fix(cli): support --strip-payloads on
    # report` commit) -- its own pi-stub coverage is added alongside that
    # commit in tests/unit/test_cli_strip_payloads_generic.py, not here.
