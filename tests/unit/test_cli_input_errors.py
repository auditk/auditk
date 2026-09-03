# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests: the CLI's input-file loading must refuse, not raise.

The adapter layer already follows the refuse-don't-raise contract
(docs/adapters.md): a bad adapter or gated stub surfaces a clean one-line
``Error: ...`` message and exit 1. But the file-read/JSON-parse step that
runs *before* adapter dispatch in ``report`` and ``ingest`` does not: a
non-JSON input file (the docs/external-testing.md quickstart audience's
most likely first mistake, e.g. ``--in README.md``) escapes as a raw
``json.JSONDecodeError`` traceback, and a missing file as a raw
``FileNotFoundError``.

These tests pin the contract for both commands x both failure modes
(non-JSON content, missing file), plus the ``.jsonl`` line-parse variant:
exit code 1, a single ``Error: `` line on output, and no traceback
leaking through. The adapter is irrelevant (parsing precedes dispatch),
so they run with the default/claude-code adapter and stay green for every
adapter, including gated stubs like ``pi``.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from auditk.cli import app

runner = CliRunner()


def _assert_clean_refusal(output: str) -> None:
    """The whole point: one ``Error: `` line, not a 48-line rich traceback."""
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected a single error line, got:\n{output}"
    assert lines[0].startswith("Error: "), output
    assert "Traceback" not in output


class TestReportInputErrors:
    def test_non_json_file_refuses_cleanly(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "README.md"
        bad_file.write_text("# Not JSON at all\n\nJust some markdown.\n")

        result = runner.invoke(app, ["report", "--in", str(bad_file), "--no-policy-context"])

        assert result.exit_code == 1, result.output
        _assert_clean_refusal(result.output)

    def test_missing_file_refuses_cleanly(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app, ["report", "--in", str(tmp_path / "no-such.json"), "--no-policy-context"]
        )

        assert result.exit_code == 1, result.output
        _assert_clean_refusal(result.output)

    def test_invalid_jsonl_line_refuses_cleanly(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "session.jsonl"
        bad_file.write_text('{"type": "user", "message": {"content": "hi"}}\nnot json\n')

        result = runner.invoke(app, ["report", "--in", str(bad_file), "--no-policy-context"])

        assert result.exit_code == 1, result.output
        _assert_clean_refusal(result.output)


class TestIngestInputErrors:
    def test_non_json_file_refuses_cleanly(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "README.md"
        bad_file.write_text("# Not JSON at all\n")

        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "claude-code",
                "--in",
                str(bad_file),
                "--out",
                str(tmp_path / "trace.json"),
            ],
        )

        assert result.exit_code == 1, result.output
        _assert_clean_refusal(result.output)
        assert not (tmp_path / "trace.json").exists()

    def test_missing_file_refuses_cleanly(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "ingest",
                "--adapter",
                "claude-code",
                "--in",
                str(tmp_path / "no-such.jsonl"),
                "--out",
                str(tmp_path / "trace.json"),
            ],
        )

        assert result.exit_code == 1, result.output
        _assert_clean_refusal(result.output)
