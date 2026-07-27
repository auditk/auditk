# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for CLAUDE.md policy-context discovery.

Covers ``auditk.analysis.policy_context.discover_policy_context`` (pure
filesystem cascade discovery) and its wiring into
``auditk.analysis.report`` (``ReportModel.policy_context`` /
``render_markdown``'s "Policy context" section) and the ``auditk report``
CLI command's ``--no-policy-context`` flag.

All filesystem cases use ``tmp_path`` for both the "home" and "project"
sides of the cascade — this module must never read the real user's
``~/.claude/CLAUDE.md``. Any test that exercises the CLI's default
(discovery-on) path additionally monkeypatches ``$HOME`` to an isolated
``tmp_path`` directory, since the ``auditk report``/``auditk rules init``
commands do not expose a ``--home`` override and would otherwise fall back
to the real ``Path.home()``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auditk.analysis.policy_context import PolicyDoc, discover_policy_context
from auditk.analysis.report import build_report, render_markdown
from auditk.cli import app
from auditk.schema import Action, ActionType, Actor, FlowType, Step, Trace

runner = CliRunner()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- discover_policy_context --------------------------------------------


def test_discover_policy_context_empty_when_nothing_present(tmp_path: Path) -> None:
    home = tmp_path / "home"
    start = tmp_path / "start"
    home.mkdir()
    start.mkdir()

    assert discover_policy_context(start, home=home) == []


def test_discover_policy_context_full_cascade_order_and_scopes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    sub = repo / "sub"

    _write(home / ".claude" / "CLAUDE.md", "# Global policy\nglobal content")
    _write(repo / "CLAUDE.md", "# Repo policy\nrepo content")
    _write(sub / ".claude" / "CLAUDE.md", "# Sub policy\nsub content")
    _write(repo / ".claude" / "rules" / "x.md", "# Rule X\nrule content")

    docs = discover_policy_context(sub, home=home)

    assert [(d.scope, d.title) for d in docs] == [
        ("global", "Global policy"),
        ("project", "Sub policy"),
        ("project", "Repo policy"),
        ("rules", "Rule X"),
    ]
    assert [Path(d.path) for d in docs] == [
        home / ".claude" / "CLAUDE.md",
        sub / ".claude" / "CLAUDE.md",
        repo / "CLAUDE.md",
        repo / ".claude" / "rules" / "x.md",
    ]


def test_discover_policy_context_sorts_multiple_rules_files_by_name(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()

    _write(repo / ".claude" / "rules" / "zzz.md", "# Z\n")
    _write(repo / ".claude" / "rules" / "aaa.md", "# A\n")

    docs = discover_policy_context(repo, home=home)

    rule_docs = [d for d in docs if d.scope == "rules"]
    assert [Path(d.path).name for d in rule_docs] == ["aaa.md", "zzz.md"]


def test_discover_policy_context_dedups_when_start_dir_is_home(tmp_path: Path) -> None:
    # When start_dir's cascade walk reaches the same file already picked up
    # as the GLOBAL doc (e.g. a project living directly under $HOME), it
    # must not be listed twice.
    home = tmp_path / "home"
    _write(home / ".claude" / "CLAUDE.md", "# Global\n")

    docs = discover_policy_context(home, home=home)

    assert len(docs) == 1
    assert docs[0].scope == "global"


def test_discover_policy_context_title_none_when_no_heading(tmp_path: Path) -> None:
    home = tmp_path / "home"
    start = tmp_path / "start"
    home.mkdir()
    _write(start / "CLAUDE.md", "no heading here\njust some text\n## not an h1 either\n")

    docs = discover_policy_context(start, home=home)

    assert len(docs) == 1
    assert docs[0].title is None


def test_discover_policy_context_only_includes_existing_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    start = tmp_path / "start"
    home.mkdir()
    start.mkdir()
    # A CLAUDE.md one level up that does NOT exist should simply be skipped,
    # not raise.
    docs = discover_policy_context(start, home=home)
    assert docs == []


# --- report integration ---------------------------------------------------


def _minimal_trace() -> Trace:
    step = Step(
        step_id="s1",
        trace_id="t1",
        timestamp=datetime(2026, 7, 1, tzinfo=UTC),
        actor=Actor.USER,
        action=Action(type=ActionType.UTTERANCE, payload={"text": "hello"}),
    )
    return Trace(
        trace_id="t1",
        flow_type=FlowType.CODE,
        agent_config_ref="test:t1",
        steps=[step],
        source_adapter="test",
        metadata={},
    )


def test_build_report_carries_policy_context() -> None:
    from auditk.analysis.findings import FindingsReport

    docs = [PolicyDoc(path="/x/CLAUDE.md", scope="project", title="Some policy")]
    report_model = build_report(_minimal_trace(), FindingsReport(), policy_context=docs)

    assert report_model.policy_context == docs


def test_build_report_defaults_policy_context_to_empty_list() -> None:
    from auditk.analysis.findings import FindingsReport

    report_model = build_report(_minimal_trace(), FindingsReport())

    assert report_model.policy_context == []


def test_render_markdown_includes_policy_context_section() -> None:
    from auditk.analysis.findings import FindingsReport

    docs = [
        PolicyDoc(path="/home/x/.claude/CLAUDE.md", scope="global", title="Global rules"),
        PolicyDoc(path="/repo/CLAUDE.md", scope="project", title=None),
    ]
    report_model = build_report(_minimal_trace(), FindingsReport(), policy_context=docs)
    markdown = render_markdown(report_model)

    assert "## Policy context" in markdown
    assert "/home/x/.claude/CLAUDE.md" in markdown
    assert "Global rules" in markdown
    assert "/repo/CLAUDE.md" in markdown
    assert "(no heading)" in markdown


def test_render_markdown_policy_context_empty_case() -> None:
    from auditk.analysis.findings import FindingsReport

    report_model = build_report(_minimal_trace(), FindingsReport())
    markdown = render_markdown(report_model)

    assert (
        "No CLAUDE.md policy files were discovered for this session's working directory."
        in markdown
    )


# --- CLI: `auditk report` policy-context wiring ---------------------------

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_code"
_CLEAN_FIXTURE = _FIXTURES / "session_modern_taskcreate.jsonl"


def test_cli_report_discovers_policy_context_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))

    session_dir = tmp_path / "session-cwd"
    _write(session_dir / "CLAUDE.md", "# My project rules\nsome content\n")

    session_file = tmp_path / "session.jsonl"
    events = _load_events(_CLEAN_FIXTURE)
    for event in events:
        if isinstance(event.get("cwd"), str):
            event["cwd"] = str(session_dir)
    _write_jsonl(session_file, events)

    result = runner.invoke(
        app,
        ["report", "--adapter", "claude-code", "--in", str(session_file), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    import json as _json

    parsed = _json.loads(result.output)
    assert "policy_context" in parsed
    assert any(doc["path"] == str(session_dir / "CLAUDE.md") for doc in parsed["policy_context"])


def test_cli_report_no_policy_context_flag_skips_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))

    session_dir = tmp_path / "session-cwd"
    _write(session_dir / "CLAUDE.md", "# My project rules\nsome content\n")

    session_file = tmp_path / "session.jsonl"
    events = _load_events(_CLEAN_FIXTURE)
    for event in events:
        if isinstance(event.get("cwd"), str):
            event["cwd"] = str(session_dir)
    _write_jsonl(session_file, events)

    result = runner.invoke(
        app,
        [
            "report",
            "--adapter",
            "claude-code",
            "--in",
            str(session_file),
            "--format",
            "json",
            "--no-policy-context",
        ],
    )

    assert result.exit_code == 0, result.output
    import json as _json

    parsed = _json.loads(result.output)
    assert parsed["policy_context"] == []


def _load_events(path: Path) -> list[dict]:
    import json

    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, events: list[dict]) -> None:
    import json

    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_policy_doc_is_pydantic_model() -> None:
    doc = PolicyDoc(path="/a/CLAUDE.md", scope="project", title="Hi")
    assert doc.model_dump()["scope"] == "project"
