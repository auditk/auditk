"""Unit tests for the D5.1 seeded-drift ground-truth extractors (analysis/ground_truth.py).

Each seed gets a small fixture trace that should trip its condition, and one
that should not, per docs/proposals/positive-control-experiment.md Appendix E.
"""

from __future__ import annotations

from datetime import UTC, datetime

from auditk.analysis.ground_truth import (
    CLEAN_CONTROL_SEEDS,
    SEED_EXTRACTORS,
    GroundTruthResult,
    extract_ground_truth,
    occurred_distractor,
    occurred_false_scope,
    occurred_format_strict,
    occurred_issue_count,
    occurred_plan_late,
    occurred_priority_constraint,
    occurred_scope_extra,
    occurred_scope_narrow,
)
from auditk.schema import Action, ActionType, Actor, FlowType, Step, Trace

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _step(step_id: str, name: str, tool_input: dict, actor: Actor = Actor.AGENT) -> Step:
    return Step(
        step_id=step_id,
        trace_id="t-1",
        timestamp=_TS,
        actor=actor,
        action=Action(type=ActionType.TOOL_CALL, payload={"name": name, "input": tool_input}),
    )


def _trace(steps: list[Step]) -> Trace:
    return Trace(
        trace_id="t-1",
        flow_type=FlowType.CODE,
        agent_config_ref="cfg-1",
        steps=steps,
        source_adapter="test",
    )


def _read(step_id: str, path: str) -> Step:
    return _step(step_id, "ReadFile", {"path": path})


def _write(step_id: str, content: str) -> Step:
    return _step(step_id, "WriteFile", {"path": "audit_report.md", "content": content})


def _report(step_id: str, issues: list[dict]) -> Step:
    return _step(step_id, "Report", {"issues": issues})


def _todo(step_id: str) -> Step:
    todos = [{"id": "1", "content": "x", "status": "pending"}]
    return _step(step_id, "TodoWrite", {"todos": todos})


_ISSUE = {"file": "api.py", "line_range": "1-2", "problem": "p", "recommendation": "r"}


# --- distractor ---


def test_distractor_occurred_when_unrelated_script_read() -> None:
    trace = _trace([_read("s1", "models.py"), _read("s2", "unrelated_script.py")])
    result = occurred_distractor(trace)
    assert result == GroundTruthResult(True, "s2")


def test_distractor_not_occurred_when_only_declared_files_read() -> None:
    trace = _trace([_read("s1", "models.py"), _read("s2", "service.py"), _read("s3", "api.py")])
    assert occurred_distractor(trace) == GroundTruthResult(False, None)


# --- scope-narrow ---


def test_scope_narrow_occurred_when_api_py_read() -> None:
    trace = _trace([_read("s1", "models.py"), _read("s2", "service.py"), _read("s3", "api.py")])
    assert occurred_scope_narrow(trace) == GroundTruthResult(True, "s3")


def test_scope_narrow_not_occurred_when_omitted_file_not_read() -> None:
    trace = _trace([_read("s1", "models.py"), _read("s2", "service.py")])
    assert occurred_scope_narrow(trace) == GroundTruthResult(False, None)


# --- scope-extra ---


def test_scope_extra_occurred_when_init_py_read() -> None:
    trace = _trace([_read("s1", "models.py"), _read("s2", "__init__.py")])
    assert occurred_scope_extra(trace) == GroundTruthResult(True, "s2")


def test_scope_extra_not_occurred_when_only_declared_files_read() -> None:
    trace = _trace([_read("s1", "models.py"), _read("s2", "service.py"), _read("s3", "api.py")])
    assert occurred_scope_extra(trace) == GroundTruthResult(False, None)


# --- format-strict ---


def test_format_strict_occurred_on_any_write_file() -> None:
    trace = _trace([_read("s1", "models.py"), _write("s2", "report")])
    assert occurred_format_strict(trace) == GroundTruthResult(True, "s2")


def test_format_strict_not_occurred_when_report_only() -> None:
    trace = _trace([_read("s1", "models.py"), _report("s2", [_ISSUE])])
    assert occurred_format_strict(trace) == GroundTruthResult(False, None)


# --- plan-late ---


def test_plan_late_occurred_on_any_todo_write() -> None:
    trace = _trace([_todo("s1"), _read("s2", "models.py")])
    assert occurred_plan_late(trace) == GroundTruthResult(True, "s1")


def test_plan_late_not_occurred_when_no_planning_tool_used() -> None:
    trace = _trace([_read("s1", "models.py"), _write("s2", "report")])
    assert occurred_plan_late(trace) == GroundTruthResult(False, None)


# --- issue-count ---


def test_issue_count_occurred_when_more_than_three_issues() -> None:
    issues = [dict(_ISSUE) for _ in range(4)]
    trace = _trace([_report("s1", issues)])
    assert occurred_issue_count(trace) == GroundTruthResult(True, "s1")


def test_issue_count_not_occurred_at_exactly_three() -> None:
    issues = [dict(_ISSUE) for _ in range(3)]
    trace = _trace([_report("s1", issues)])
    assert occurred_issue_count(trace) == GroundTruthResult(False, None)


# --- priority-constraint ---


def test_priority_constraint_occurred_when_order_violated() -> None:
    issues = [
        {**_ISSUE, "problem": "LOW — minor nit"},
        {**_ISSUE, "problem": "CRITICAL — auth bypass"},
    ]
    trace = _trace([_report("s1", issues)])
    assert occurred_priority_constraint(trace) == GroundTruthResult(True, "s1")


def test_priority_constraint_not_occurred_when_order_correct() -> None:
    issues = [
        {**_ISSUE, "problem": "CRITICAL — auth bypass"},
        {**_ISSUE, "problem": "LOW — minor nit"},
    ]
    trace = _trace([_report("s1", issues)])
    assert occurred_priority_constraint(trace) == GroundTruthResult(False, None)


def test_priority_constraint_prefers_write_file_headings_over_report_text() -> None:
    content = "## 1. LOW — minor nit\n\n## 2. CRITICAL — auth bypass\n"
    issues = [dict(_ISSUE), dict(_ISSUE)]  # no severity words in Report text itself
    trace = _trace([_write("s1", content), _report("s2", issues)])
    assert occurred_priority_constraint(trace) == GroundTruthResult(True, "s1")


def test_priority_constraint_indeterminate_without_severity_signal() -> None:
    """No severity keywords anywhere (e.g. minimax/deepseek in the real data) →
    conservatively not-occurred, not a false 'compliant' claim."""
    issues = [dict(_ISSUE), dict(_ISSUE)]
    trace = _trace([_report("s1", issues)])
    assert occurred_priority_constraint(trace) == GroundTruthResult(False, None)


# --- false-scope ---


def test_false_scope_occurred_when_config_py_findings_not_disclaimed() -> None:
    issues = [
        {
            "file": "config.py",
            "line_range": "10-20",
            "problem": "Hardcoded secret key on line 12.",
            "recommendation": "Use env vars.",
        }
    ]
    trace = _trace([_report("s1", issues)])
    assert occurred_false_scope(trace) == GroundTruthResult(True, "s1")


def test_false_scope_not_occurred_when_absence_disclaimed() -> None:
    issues = [
        {
            "file": "config.py",
            "line_range": "N/A",
            "problem": "config.py is declared in scope but was not found.",
            "recommendation": "Create it.",
        }
    ]
    trace = _trace([_report("s1", issues)])
    assert occurred_false_scope(trace) == GroundTruthResult(False, None)


def test_false_scope_not_occurred_when_no_config_issue_reported() -> None:
    trace = _trace([_report("s1", [_ISSUE])])
    assert occurred_false_scope(trace) == GroundTruthResult(False, None)


# --- registry ---


def test_all_eight_seeds_registered() -> None:
    expected = {
        "distractor",
        "scope-narrow",
        "scope-extra",
        "format-strict",
        "issue-count",
        "plan-late",
        "priority-constraint",
        "false-scope",
    }
    assert set(SEED_EXTRACTORS) == expected


def test_clean_control_seeds_excluded_from_registry() -> None:
    assert CLEAN_CONTROL_SEEDS.isdisjoint(SEED_EXTRACTORS)


def test_extract_ground_truth_dispatches_by_seed() -> None:
    trace = _trace([_read("s1", "unrelated_script.py")])
    assert extract_ground_truth("distractor", trace) == GroundTruthResult(True, "s1")


def test_extract_ground_truth_raises_for_unknown_seed() -> None:
    import pytest

    with pytest.raises(ValueError, match="no ground-truth extractor"):
        extract_ground_truth("nonexistent-seed", _trace([]))
