# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the CLI side of the adapter-health canary (Phase 5 /
P2 of the cc-adapter-integrity scope):

- D2: `auditk report` calls the (pure) `check_adapter_health` on the
  ingested session and, on breach, REFUSES to emit the post-mortem report:
  it prints the health failure and exits non-zero instead, unless `--force`
  is passed, in which case it proceeds and prints the report as normal.
- D3: a new `auditk doctor` subcommand runs the corpus-level invariant (D1)
  over a corpus root and prints the plan-anchor histogram
  (TodoWrite/TaskCreate/TaskUpdate counts, plus persisted-plan-store
  presence).

All fixtures are synthetic session JSONL files written under `tmp_path` --
never the real `~/.claude` corpus (read-only over that tree per the task's
constraints; these tests never touch it at all).

Every test in this module is expected to FAIL right now:
- the `report` tests fail because `--force` is not a recognised option yet
  (typer: "No such option '--force'") and/or because the CLI does not yet
  refuse on a health breach (current behaviour: always exits 0 and prints
  the report, health or not).
- the `doctor` tests fail because `doctor` is not a registered subcommand
  yet (typer: "No such command 'doctor'").

Production code (the exact CLI wiring, message wording, and doctor's exit
code semantics on breach) lands in the next (GREEN) phase after human
review of this RED phase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from auditk.cli import app

runner = CliRunner()

_REPORT_HEADER_MARKER = "# Session post-mortem"


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _assistant_tool_use(*tool_calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    content = [{"type": "tool_use", "name": name, "input": inp} for name, inp in tool_calls]
    return {"type": "assistant", "message": {"content": content}}


def _assistant_text(text: str) -> dict[str, Any]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _user_text(text: str = "please do the thing") -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}}


def _user_tool_results(n: int) -> dict[str, Any]:
    content = [{"type": "tool_result", "content": "ok"} for _ in range(n)]
    return {"type": "user", "message": {"content": content}}


def _untyped_record(record_type: str) -> dict[str, Any]:
    return {"type": record_type}


# --- `auditk report` health gate -----------------------------------------


def _breaching_session_events() -> list[dict[str, Any]]:
    """A single session that ingests fine (has substantive user/assistant
    events) but breaches the per-session unknown-record-type-share check:
    2 substantive records out of 6 total carry a genuinely UNRECOGNISED
    type (not in `KNOWN_RECORD_TYPES` -- see the RED-gate correction), i.e.
    4/6 = ~66.7% unknown, well over the 5% floor. No plan-anchor tool calls
    needed: this is a per-session check, which applies even to a single
    session (D1).

    Deliberately does NOT use known-benign types like `attachment`/`system`
    here -- those are in `KNOWN_RECORD_TYPES` by default and must NOT
    breach (see test_adapter_health.py's
    TestPerSessionUnknownRecordTypeShare for that coverage)."""
    return [
        _user_text("hello"),
        _assistant_text("hi there"),
        _untyped_record("totally-new-type-v9"),
        _untyped_record("totally-new-type-v9"),
        _untyped_record("another-unknown-type"),
        _untyped_record("another-unknown-type"),
    ]


def _healthy_session_events() -> list[dict[str, Any]]:
    return [
        _user_text("please add a CSV export endpoint"),
        _assistant_tool_use(("TaskCreate", {"subject": "add CSV export endpoint"})),
        _user_tool_results(1),
        _assistant_tool_use(("Bash", {"command": "echo hi"})),
        _user_tool_results(1),
    ]


class TestReportHealthGate:
    def test_report_exits_nonzero_on_breaching_session_without_force(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session.jsonl"
        _write_jsonl(session_file, _breaching_session_events())

        result = runner.invoke(app, ["report", "--in", str(session_file), "--no-policy-context"])

        assert result.exit_code != 0, result.output

    def test_report_does_not_print_a_report_on_breach_without_force(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session.jsonl"
        _write_jsonl(session_file, _breaching_session_events())

        result = runner.invoke(app, ["report", "--in", str(session_file), "--no-policy-context"])

        # A 0.813-style score/report that means "the adapter is blind" is
        # worse than no number at all (Phase 5 rationale) -- the normal
        # report body must not appear.
        assert _REPORT_HEADER_MARKER not in result.output

    def test_report_breach_message_mentions_adapter_health(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session.jsonl"
        _write_jsonl(session_file, _breaching_session_events())

        result = runner.invoke(app, ["report", "--in", str(session_file), "--no-policy-context"])

        assert "health" in result.output.lower()

    def test_report_force_overrides_breach_and_prints_report(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session.jsonl"
        _write_jsonl(session_file, _breaching_session_events())

        result = runner.invoke(
            app, ["report", "--in", str(session_file), "--force", "--no-policy-context"]
        )

        assert result.exit_code == 0, result.output
        assert _REPORT_HEADER_MARKER in result.output

    def test_report_force_json_format_also_proceeds(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session.jsonl"
        _write_jsonl(session_file, _breaching_session_events())

        result = runner.invoke(
            app,
            [
                "report",
                "--in",
                str(session_file),
                "--force",
                "--format",
                "json",
                "--no-policy-context",
            ],
        )

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert "header" in payload

    def test_report_on_healthy_session_needs_no_force(self, tmp_path: Path) -> None:
        session_file = tmp_path / "session.jsonl"
        _write_jsonl(session_file, _healthy_session_events())

        result = runner.invoke(app, ["report", "--in", str(session_file), "--no-policy-context"])

        assert result.exit_code == 0, result.output
        assert _REPORT_HEADER_MARKER in result.output


# --- `auditk doctor` ------------------------------------------------------


def _build_corpus(
    root: Path,
    n_sessions: int,
    *,
    anchor_tool: str | None = "TaskCreate",
    project_slug: str = "-tmp-demo-project",
) -> None:
    """Write `n_sessions` synthetic session transcripts under
    `root/<project_slug>/<session-id>.jsonl`, mirroring the on-disk layout
    `scripts/corpus_stats.py` already discovers (see its module docstring
    for the parent-transcript-is-a-sibling-of-its-dir layout trap -- not
    exercised here since these sessions have no subagents)."""
    project_dir = root / project_slug
    for i in range(n_sessions):
        session_id = f"session-{i:04d}"
        events: list[dict[str, Any]] = [_user_text(f"turn {i}")]
        if anchor_tool is not None:
            events.append(_assistant_tool_use((anchor_tool, {"subject": f"task {i}"})))
            events.append(_user_tool_results(1))
        events.append(_assistant_tool_use(("Bash", {"command": "echo hi"})))
        events.append(_user_tool_results(1))
        _write_jsonl(project_dir / f"{session_id}.jsonl", events)


class TestDoctorSubcommand:
    def test_doctor_is_a_registered_command(self) -> None:
        # GREEN-phase update (Test Integrity Rule): at RED this asserted
        # "doctor" was ABSENT from --help, which was correct before this
        # phase's production code landed. Now that `doctor` is a real
        # `@app.command()` in cli.py, that assumption is (by design)
        # invalidated -- the test's premise changed, not its subject, so the
        # assertion is flipped rather than deleted.
        result = runner.invoke(app, ["--help"])
        assert "doctor" in result.output

    def test_doctor_exits_zero_and_prints_histogram_for_healthy_corpus(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "projects"
        tasks_root = tmp_path / "tasks"
        _build_corpus(root, 20, anchor_tool="TaskCreate")

        result = runner.invoke(
            app, ["doctor", "--root", str(root), "--tasks-root", str(tasks_root)]
        )

        assert result.exit_code == 0, result.output
        assert "TaskCreate" in result.output
        assert "TodoWrite" in result.output
        assert "TaskUpdate" in result.output

    def test_doctor_histogram_counts_match_the_corpus(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        tasks_root = tmp_path / "tasks"
        _build_corpus(root, 20, anchor_tool="TaskCreate")

        result = runner.invoke(
            app, ["doctor", "--root", str(root), "--tasks-root", str(tasks_root)]
        )

        assert "20" in result.output

    def test_doctor_reports_breach_for_anchor_free_corpus(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        tasks_root = tmp_path / "tasks"
        _build_corpus(root, 20, anchor_tool=None)

        result = runner.invoke(
            app, ["doctor", "--root", str(root), "--tasks-root", str(tasks_root)]
        )

        # Design choice flagged for human review at the RED gate: doctor's
        # exit code on a corpus-level breach is not specified by D3. This
        # test asserts the stricter, CI-friendly reading (non-zero exit) --
        # see the report-back "design ambiguity" note.
        assert result.exit_code != 0
        assert "breach" in result.output.lower() or "unhealthy" in result.output.lower()

    def test_doctor_healthy_corpus_reports_ok(self, tmp_path: Path) -> None:
        root = tmp_path / "projects"
        tasks_root = tmp_path / "tasks"
        _build_corpus(root, 20, anchor_tool="TaskCreate")

        result = runner.invoke(
            app, ["doctor", "--root", str(root), "--tasks-root", str(tasks_root)]
        )

        assert "ok" in result.output.lower() or "healthy" in result.output.lower()
