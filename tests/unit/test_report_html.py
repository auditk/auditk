# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the HTML post-mortem renderer (auditk report --format html).

``render_html`` sits beside ``render_markdown`` over the same
``ReportModel`` — same sections, same determinism guarantee, one
self-contained HTML document (no external assets, so it travels over email
the way the evidence pack does). Traces reuse the same synthetic fixtures
as ``test_report.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auditk.adapters.claude_code import ingest_claude_code_session
from auditk.analysis.findings import analyze_trace
from auditk.analysis.report import (
    build_report,
    render_html,
)
from auditk.cli import app
from auditk.schema import Trace

FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_code"
ANOMALIES_FIXTURE = FIXTURES / "session_anomalies.jsonl"
PI_FIXTURE = Path(__file__).parent.parent / "fixtures" / "pi" / "session-rich.jsonl"

runner = CliRunner()


def _load_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _trace(path: Path) -> Trace:
    return ingest_claude_code_session(_load_events(path))


def _report_model(path: Path = ANOMALIES_FIXTURE):
    trace = _trace(path)
    findings = analyze_trace(trace)
    return build_report(trace, findings)


# --- render_html: document shape ----------------------------------------


def test_render_html_is_a_complete_html_document() -> None:
    html = render_html(_report_model())
    lowered = html.lower()
    assert lowered.startswith("<!doctype html>")
    assert "<html" in lowered and "</html>" in lowered
    assert "<title>" in lowered


def test_render_html_has_all_section_headings() -> None:
    html = render_html(_report_model())
    for heading in (
        "Session post-mortem",
        "Summary",
        "Timeline",
        "Findings",
        "Instruction compliance",
        "Not checked",
    ):
        assert heading in html, f"missing section heading: {heading}"


def test_render_html_contains_header_fields() -> None:
    model = _report_model()
    html = render_html(model)
    assert str(model.header.get("sessionId")) in html
    assert "Tool calls" in html


def test_render_html_is_deterministic() -> None:
    model = _report_model()
    assert render_html(model) == render_html(model)


# --- self-containment ----------------------------------------------------


def test_render_html_is_self_contained_no_external_assets() -> None:
    """Strict CSP-style property: nothing in the document may reach out to
    any host — no external scripts, stylesheets, images, or fonts."""
    html = render_html(_report_model())
    lowered = html.lower()
    for marker in ('src="http', "src='http", 'href="http', "href='http", "@import", "url(http"):
        assert marker not in lowered, f"external asset reference found: {marker}"


def test_render_html_has_no_script_tags() -> None:
    """The report is a static document; script-free by construction."""
    html = render_html(_report_model())
    assert "<script" not in html.lower()


# --- escaping ------------------------------------------------------------


def test_render_html_escapes_session_content() -> None:
    """Session-derived text is untrusted: markup in a user turn or finding
    must be escaped, never interpolated as live HTML."""
    model = _report_model()
    hostile = '<script>alert("xss")</script><img src=x onerror=y>'
    model.header["cwd"] = hostile
    if model.timeline:
        model.timeline[0].summary = hostile
    html = render_html(model)
    assert "<script>alert(" not in html
    assert "<img src=x" not in html
    assert "&lt;script&gt;" in html


# --- findings severity is visible ----------------------------------------


def test_render_html_shows_finding_severities_and_rules() -> None:
    model = _report_model()  # anomalies fixture: has HIGH/MEDIUM findings
    html = render_html(model)
    for severity in ("HIGH", "MEDIUM"):
        assert severity in html
    # Each finding's rule id appears so the HTML is as auditable as the md.
    assert any(f.rule_id in html for f in model.findings.findings)


# --- CLI dispatch ---------------------------------------------------------


def test_cli_report_format_html_writes_html(tmp_path: Path) -> None:
    out = tmp_path / "report.html"
    result = runner.invoke(
        app,
        [
            "report",
            "--adapter",
            "pi",
            "--in",
            str(PI_FIXTURE),
            "--no-policy-context",
            "--format",
            "html",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    content = out.read_text()
    assert content.lower().startswith("<!doctype html>")
    assert "01a07bca-7505-72d4-81eb-73bffd067779" in content


def test_cli_report_format_html_stdout() -> None:
    result = runner.invoke(
        app,
        [
            "report",
            "--adapter",
            "pi",
            "--in",
            str(PI_FIXTURE),
            "--no-policy-context",
            "--format",
            "html",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "<!doctype html>" in result.output.lower()


def test_cli_report_unknown_format_lists_html_among_choices() -> None:
    result = runner.invoke(
        app,
        [
            "report",
            "--adapter",
            "pi",
            "--in",
            str(PI_FIXTURE),
            "--no-policy-context",
            "--format",
            "docx",
        ],
    )
    assert result.exit_code != 0
    assert "html" in result.output
