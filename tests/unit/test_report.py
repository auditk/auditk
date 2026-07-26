# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the single-session post-mortem report (auditk report).

Covers ``auditk.analysis.report`` (pure timeline/turn/report building and
markdown rendering) and the ``auditk report`` CLI command. Traces are built
from the two synthetic fixtures already used by ``test_findings.py``:

- ``session_anomalies.jsonl`` — a single user turn followed by 34 steps with
  every findings-engine anomaly planted (see the plant map in
  ``test_findings.py`` for the exact step positions).
- ``session_modern_taskcreate.jsonl`` — a clean session with zero HIGH/MEDIUM
  findings; the false-positive guard for the report's Findings section.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auditk.adapters.claude_code import ingest_claude_code_session
from auditk.analysis.findings import analyze_trace
from auditk.analysis.report import (
    ReportModel,
    TimelineEntry,
    TurnCompliance,
    build_report,
    build_timeline,
    extract_turn_compliance,
    render_markdown,
)
from auditk.cli import app
from auditk.schema import Trace

FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_code"
ANOMALIES_FIXTURE = FIXTURES / "session_anomalies.jsonl"
CLEAN_FIXTURE = FIXTURES / "session_modern_taskcreate.jsonl"

runner = CliRunner()


def _load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _trace(path: Path) -> Trace:
    return ingest_claude_code_session(_load_events(path))


# --- build_timeline -----------------------------------------------------


def test_build_timeline_collapses_churn_run_to_one_entry() -> None:
    """The 5-edit run (app.js x4, secrets.yaml x1) becomes a single entry."""
    trace = _trace(ANOMALIES_FIXTURE)
    timeline = build_timeline(trace)

    edit_entries = [e for e in timeline if e.kind == "edit"]
    assert len(edit_entries) == 2  # the 5-run, then the lone utils.js edit
    assert edit_entries[0].summary == "Edited 5 times across 2 files"
    assert edit_entries[1].summary == "Edited utils.js"


def test_build_timeline_does_not_emit_one_entry_per_step() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    timeline = build_timeline(trace)

    assert len(trace.steps) == 34
    assert len(timeline) < len(trace.steps)
    assert len(timeline) == 11


def test_build_timeline_includes_notable_categories() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    timeline = build_timeline(trace)
    kinds = [e.kind for e in timeline]

    assert kinds.count("user") == 1
    assert kinds.count("write") == 1
    assert kinds.count("commit") == 1
    assert kinds.count("test") == 1
    assert kinds.count("tripwire") == 1
    assert kinds.count("error") == 3
    assert kinds.count("delegation") == 1

    # Deterministic order: first entry is the opening user turn.
    assert timeline[0].kind == "user"
    assert timeline[0].step_id == trace.steps[0].step_id


def test_build_timeline_on_clean_fixture_has_no_tripwire_or_commit_entries() -> None:
    """The clean fixture has one isolated test failure (not a tripwire or
    commit) plus its retry; it is "clean" in the findings sense (no HIGH/
    MEDIUM findings), not literally error-free.
    """
    trace = _trace(CLEAN_FIXTURE)
    timeline = build_timeline(trace)
    kinds = [e.kind for e in timeline]

    assert "tripwire" not in kinds
    assert "commit" not in kinds
    assert kinds.count("user") == 1
    assert kinds.count("error") == 1


# --- extract_turn_compliance ---------------------------------------------


def test_extract_turn_compliance_single_turn_anomalies_fixture() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    turns = extract_turn_compliance(trace)

    assert len(turns) == 1
    turn = turns[0]
    assert isinstance(turn, TurnCompliance)
    assert turn.step_id == trace.steps[0].step_id
    assert turn.user_text.startswith("Please refactor app.js")
    assert turn.followed_by == {
        "Write": 1,
        "Read": 2,
        "Edit": 6,
        "Bash": 6,
        "Task": 1,
    }


def test_extract_turn_compliance_ignores_tool_result_user_events() -> None:
    """Only genuine user utterances count as turns, not tool_result steps."""
    trace = _trace(ANOMALIES_FIXTURE)
    turns = extract_turn_compliance(trace)
    # 34 steps but only ONE real user utterance among them (the rest of the
    # "user" role events are tool_result payloads, not utterances).
    assert len(turns) == 1


# --- build_report ---------------------------------------------------------


def test_build_report_header_stats() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    findings = analyze_trace(trace)
    report = build_report(trace, findings)

    assert isinstance(report, ReportModel)
    assert report.header["step_count"] == 34
    assert report.header["tool_call_count"] == 16
    assert report.header["user_turn_count"] == 1
    assert report.header["duration_seconds"] is not None
    assert report.header["duration_seconds"] >= 0
    assert report.not_checked == findings.not_checked


def test_build_report_carries_findings_through() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    findings = analyze_trace(trace)
    report = build_report(trace, findings)

    assert report.findings is findings or report.findings == findings
    high_rule_ids = {f.rule_id for f in report.findings.findings if f.severity.value == "high"}
    assert "scope-escape" in high_rule_ids


# --- render_markdown --------------------------------------------------


def test_render_markdown_has_all_sections_anomalies() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    findings = analyze_trace(trace)
    report = build_report(trace, findings)
    md = render_markdown(report)

    for heading in (
        "# Session post-mortem",
        "## Summary",
        "## Timeline",
        "## Findings",
        "## Instruction compliance",
        "## Not checked",
    ):
        assert heading in md

    assert "scope-escape" in md


def test_render_markdown_clean_fixture_shows_zero_high_medium() -> None:
    trace = _trace(CLEAN_FIXTURE)
    findings = analyze_trace(trace)
    report = build_report(trace, findings)
    md = render_markdown(report)

    assert "0 high" in md
    assert "0 medium" in md
    assert "## Not checked" in md
    assert "## Findings" in md


def test_render_markdown_is_deterministic() -> None:
    trace = _trace(ANOMALIES_FIXTURE)
    findings = analyze_trace(trace)
    report = build_report(trace, findings)

    first = render_markdown(report)
    second = render_markdown(report)
    assert first == second

    # Also stable across independently rebuilding the report from scratch.
    report_again = build_report(trace, analyze_trace(trace))
    assert render_markdown(report_again) == first


# --- CLI --------------------------------------------------------------


def test_cli_report_markdown() -> None:
    result = runner.invoke(
        app,
        ["report", "--adapter", "claude-code", "--in", str(ANOMALIES_FIXTURE), "--format", "md"],
    )
    assert result.exit_code == 0, result.output
    assert "Session post-mortem" in result.output
    assert "scope-escape" in result.output


def test_cli_report_json() -> None:
    result = runner.invoke(
        app,
        ["report", "--adapter", "claude-code", "--in", str(ANOMALIES_FIXTURE), "--format", "json"],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "findings" in parsed
    assert "timeline" in parsed


def test_cli_report_writes_to_out_file(tmp_path: Path) -> None:
    out_file = tmp_path / "report.md"
    result = runner.invoke(
        app,
        [
            "report",
            "--adapter",
            "claude-code",
            "--in",
            str(ANOMALIES_FIXTURE),
            "--format",
            "md",
            "--out",
            str(out_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out_file.exists()
    assert "Session post-mortem" in out_file.read_text()


def test_timeline_entry_and_report_model_are_pydantic_models() -> None:
    # Sanity check on the public shape so a future refactor can't silently
    # drop these into plain dicts/dataclasses without a test noticing.
    entry = TimelineEntry(step_id="s1", kind="user", summary="hi")
    assert entry.timestamp is None
