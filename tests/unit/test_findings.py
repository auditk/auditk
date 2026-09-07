# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the structural findings engine (auditk/analysis/findings.py).

Every test in this file is expected to FAIL against the current
``findings.py`` skeleton: the ``Severity``/``Finding``/``FindingsReport``/
``FindingsConfig`` models are real, but every predicate function and
``analyze_trace`` itself raise ``NotImplementedError``. They will start
passing once Phase 2 GREEN fills in the predicate bodies.

Plant map for tests/fixtures/claude_code/session_anomalies.jsonl (0-indexed
positions into ``trace.steps`` after ``ingest_claude_code_session``; uuid in
parens for cross-reference against the raw JSONL):

    step  1 (a1)          Write /work/proj/scratch_notes.md
                              -> abandoned-artifact (basename never recurs)
    steps 5,7,9,11 (a3-a6) Edit /work/proj/app.js, x4, no verify between
                              -> churn-burst
    step 13 (a7)          Edit /work/OTHER/secrets.yaml (outside cwd, not /tmp)
                              -> scope-escape
    step 15 (a8)          Bash "git commit -am ..." with no prior test/lint Bash
                              -> commit-without-tests
    step 19 (a10)         Bash "rm -rf /work/proj/build"
                              -> tripwire:destructive-rm
    steps 26,28,30 (u14,u15,u16) tool_result is_error=True, x3 consecutive
                              -> error-cluster
    step 31 (a16)         Task tool_use (adapter sets delegation_unobserved)
                              -> delegation-unobserved

Interleaved clean activity that must NOT trigger any rule: a Read of app.js
(step 3), a single Edit+Read verify pair on utils.js (steps 21/23), a
legitimate ``pytest`` Bash run placed AFTER the commit (step 17, so it must
not retroactively clear commit-without-tests for step 15), and a closing
narration utterance (step 33).

The negative control, tests/fixtures/claude_code/session_modern_taskcreate.jsonl,
is a clean session with none of the above and must yield zero HIGH/MEDIUM
findings — this is the false-positive guard and the most important test
in this file.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auditk.adapters.claude_code import ingest_claude_code_session
from auditk.analysis.findings import (
    FindingsConfig,
    Severity,
    analyze_trace,
    find_abandoned_artifacts,
    find_bash_tripwires,
    find_churn_bursts,
    find_commits_without_verify,
    find_error_clusters,
    find_test_edits_after_failed_run,
    find_unobserved_delegations,
    find_writes_outside_roots,
)
from auditk.schema import Action, ActionType, Actor, FlowType, Step, Trace

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_code"

PLANTED_RULE_IDS = {
    "scope-escape",
    "churn-burst",
    "commit-without-tests",
    "tripwire:destructive-rm",
    "error-cluster",
    "delegation-unobserved",
    "abandoned-artifact",
}


def _load_events(name: str) -> list[dict]:
    return [
        json.loads(line) for line in (_FIXTURES / name).read_text().splitlines() if line.strip()
    ]


@pytest.fixture
def anomalies_trace() -> Trace:
    return ingest_claude_code_session(_load_events("session_anomalies.jsonl"))


@pytest.fixture
def well_behaved_trace() -> Trace:
    return ingest_claude_code_session(_load_events("session_modern_taskcreate.jsonl"))


def _step_id(trace: Trace, index: int) -> str:
    return trace.steps[index].step_id


# --- Per-rule tests: fires on the planted anomaly, not spuriously elsewhere ---


def test_find_writes_outside_roots_flags_scope_escape(anomalies_trace: Trace) -> None:
    findings = find_writes_outside_roots(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "scope-escape"
    assert finding.severity == Severity.HIGH
    assert finding.step_ids == [_step_id(anomalies_trace, 13)]
    assert finding.evidence.get("file_path") == "/work/OTHER/secrets.yaml"


def test_find_writes_outside_roots_does_not_flag_well_behaved_session(
    well_behaved_trace: Trace,
) -> None:
    findings = find_writes_outside_roots(well_behaved_trace, FindingsConfig())
    assert findings == []


def test_find_churn_bursts_flags_repeated_same_file_edits(anomalies_trace: Trace) -> None:
    findings = find_churn_bursts(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "churn-burst"
    assert finding.severity == Severity.MEDIUM
    expected_steps = [_step_id(anomalies_trace, i) for i in (5, 7, 9, 11)]
    assert finding.step_ids == expected_steps
    assert finding.evidence.get("file_path") == "/work/proj/app.js"


def test_find_churn_bursts_does_not_flag_single_verified_edit(anomalies_trace: Trace) -> None:
    findings = find_churn_bursts(anomalies_trace, FindingsConfig())
    # The utils.js edit+read pair (step 21) is verified immediately by a Read
    # of the same file (step 23) and must never appear in a churn finding.
    utils_step_id = _step_id(anomalies_trace, 21)
    assert all(utils_step_id not in f.step_ids for f in findings)


def test_find_commits_without_verify_flags_commit_with_no_prior_test(
    anomalies_trace: Trace,
) -> None:
    findings = find_commits_without_verify(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "commit-without-tests"
    assert finding.severity == Severity.MEDIUM
    assert finding.step_ids == [_step_id(anomalies_trace, 15)]


def test_find_commits_without_verify_ignores_well_behaved_session(
    well_behaved_trace: Trace,
) -> None:
    # No git commit at all in the well-behaved fixture.
    findings = find_commits_without_verify(well_behaved_trace, FindingsConfig())
    assert findings == []


def test_find_bash_tripwires_flags_destructive_rm(anomalies_trace: Trace) -> None:
    findings = find_bash_tripwires(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "tripwire:destructive-rm"
    assert finding.severity == Severity.HIGH
    assert finding.step_ids == [_step_id(anomalies_trace, 19)]
    assert "rm -rf" in finding.evidence.get("command", "")


def test_find_bash_tripwires_ignores_well_behaved_session(well_behaved_trace: Trace) -> None:
    findings = find_bash_tripwires(well_behaved_trace, FindingsConfig())
    assert findings == []


def test_find_error_clusters_flags_three_consecutive_errors(anomalies_trace: Trace) -> None:
    findings = find_error_clusters(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "error-cluster"
    assert finding.severity == Severity.MEDIUM
    expected_steps = [_step_id(anomalies_trace, i) for i in (26, 28, 30)]
    assert finding.step_ids == expected_steps


def test_find_error_clusters_ignores_single_isolated_error(well_behaved_trace: Trace) -> None:
    # session_modern_taskcreate.jsonl has exactly one is_error=True tool_result
    # (the failed pytest run) — a lone error must not fire the cluster rule.
    findings = find_error_clusters(well_behaved_trace, FindingsConfig())
    assert findings == []


def test_find_unobserved_delegations_flags_task_tool_use(anomalies_trace: Trace) -> None:
    findings = find_unobserved_delegations(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "delegation-unobserved"
    assert finding.severity == Severity.INFO
    assert finding.step_ids == [_step_id(anomalies_trace, 31)]


def test_find_unobserved_delegations_ignores_well_behaved_session(
    well_behaved_trace: Trace,
) -> None:
    # TaskCreate/TaskUpdate are not delegation tools (only Task/Agent are);
    # the well-behaved fixture only ever uses TaskCreate/TaskUpdate.
    findings = find_unobserved_delegations(well_behaved_trace, FindingsConfig())
    assert findings == []


def test_find_abandoned_artifacts_flags_scratch_notes(anomalies_trace: Trace) -> None:
    findings = find_abandoned_artifacts(anomalies_trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "abandoned-artifact"
    assert finding.severity == Severity.LOW
    assert finding.step_ids == [_step_id(anomalies_trace, 1)]
    assert finding.evidence.get("basename") == "scratch_notes.md"


def test_find_abandoned_artifacts_ignores_well_behaved_session(
    well_behaved_trace: Trace,
) -> None:
    # No Write tool_call at all in the well-behaved fixture.
    findings = find_abandoned_artifacts(well_behaved_trace, FindingsConfig())
    assert findings == []


# --- Whole-report assembly ---


def test_analyze_trace_finds_all_planted_anomalies(anomalies_trace: Trace) -> None:
    report = analyze_trace(anomalies_trace)
    rule_ids = {f.rule_id for f in report.findings}
    normalized = {
        (rid if not rid.startswith("tripwire:") else "tripwire:destructive-rm") for rid in rule_ids
    }
    assert normalized == PLANTED_RULE_IDS, f"expected exactly the planted rule_ids, got {rule_ids}"


def test_well_behaved_session_has_no_high_or_medium_findings(well_behaved_trace: Trace) -> None:
    """False-positive guard: the primary reason this engine can be trusted.

    session_modern_taskcreate.jsonl is a clean, well-behaved session (no
    scope escapes, no churn, no un-tested commits, no tripwires, no error
    clusters, no delegation, no abandoned writes). analyze_trace must not
    invent findings against it.
    """
    report = analyze_trace(well_behaved_trace)
    high_or_medium = [f for f in report.findings if f.severity in (Severity.HIGH, Severity.MEDIUM)]
    assert high_or_medium == [], f"unexpected findings on clean session: {high_or_medium}"


def test_severity_counts_match_findings(anomalies_trace: Trace) -> None:
    report = analyze_trace(anomalies_trace)
    expected: dict[str, int] = {}
    for finding in report.findings:
        expected[finding.severity.value] = expected.get(finding.severity.value, 0) + 1
    assert report.severity_counts == expected
    assert sum(report.severity_counts.values()) == len(report.findings)


def test_not_checked_records_unrunnable_rules() -> None:
    """When a trace has no roots and no metadata['cwd'], scope-escape cannot
    run and must be recorded in `not_checked`, not silently reported as zero
    findings.
    """
    step = Step(
        step_id="s1",
        trace_id="t1",
        timestamp="2026-07-20T10:00:00Z",
        actor=Actor.AGENT,
        action=Action(
            type=ActionType.TOOL_CALL,
            payload={"name": "Edit", "input": {"file_path": "/anywhere/file.py"}},
        ),
    )
    trace = Trace(
        trace_id="t1",
        flow_type=FlowType.CODE,
        agent_config_ref="test:t1",
        steps=[step],
        source_adapter="test",
        metadata={},  # no cwd
    )
    report = analyze_trace(trace, FindingsConfig(roots=None))
    assert "scope-escape" in report.not_checked
    assert report.not_checked["scope-escape"]


# --- Regression: real-session false positives (session 193be0c2) ---


def _bash_step(step_id: str, command: str) -> Step:
    return Step(
        step_id=step_id,
        trace_id="t",
        timestamp=datetime(2026, 7, 1, tzinfo=UTC),
        actor=Actor.AGENT,
        action=Action(
            type=ActionType.TOOL_CALL,
            payload={"name": "Bash", "input": {"command": command}},
        ),
    )


def _bash_trace(*commands: str) -> Trace:
    steps = [_bash_step(f"s{i}", cmd) for i, cmd in enumerate(commands)]
    return Trace(
        trace_id="t",
        flow_type=FlowType.CODE,
        agent_config_ref="x",
        steps=steps,
        source_adapter="test",
    )


def test_inline_test_and_commit_is_not_flagged() -> None:
    # Real session 193be0c2: `npm test ... && git commit` runs verification in
    # the SAME command as the commit. The commit's own command must be scanned
    # for a verify pattern, else it is a commit-without-tests false positive.
    trace = _bash_trace("npm test 2>&1 | tail -8 && git add . && git commit -m 'x'")
    assert find_commits_without_verify(trace, FindingsConfig()) == []


def test_commit_with_no_verify_anywhere_still_flags() -> None:
    # Guard the fix does not silence the true positive.
    trace = _bash_trace("git add . && git commit -m 'x'")
    assert len(find_commits_without_verify(trace, FindingsConfig())) == 1


# --- test-edit-after-failed-run (Matt's pm-workflow test-integrity rule) ---
#
# Evidence base (2026-09-07 corpus sweep of 111 real claude-code sessions):
# the trace grammar this rule reads is a Bash tool_call whose paired
# env_effect (parent_step_id == the Bash step_id) carries the runner output
# in `tool_result` plus an optional `is_error` flag, followed by
# Edit/Write/NotebookEdit tool_calls carrying `input.file_path` and the edit
# text in `old_string`/`new_string` (Edit), `content` (Write) or
# `new_source` (NotebookEdit). Two false-positive traps observed in the real
# corpus are pinned as tests below: a green run whose pipeline exits
# non-zero (`is_error` true, output "330 passed"), and a test edit whose
# file is NOT the one named in the failure output.


_FAIL_OUTPUT = (
    "FAILED tests/test_widget.py::test_renders - AssertionError: expected 3 got 2\n"
    "1 failed, 4 passed in 0.51s"
)


def _call(step_id: str, name: str, tool_input: dict) -> Step:
    return Step(
        step_id=step_id,
        trace_id="t",
        timestamp=datetime(2026, 7, 1, tzinfo=UTC),
        actor=Actor.AGENT,
        action=Action(type=ActionType.TOOL_CALL, payload={"name": name, "input": tool_input}),
    )


def _result(step_id: str, parent_step_id: str, text: str, *, is_error: bool = False) -> Step:
    payload: dict = {"tool_result": text}
    if is_error:
        payload["is_error"] = True
    return Step(
        step_id=step_id,
        parent_step_id=parent_step_id,
        trace_id="t",
        timestamp=datetime(2026, 7, 1, tzinfo=UTC),
        actor=Actor.TOOL,
        action=Action(type=ActionType.ENV_EFFECT, payload=payload),
    )


def _steps_trace(*steps: Step) -> Trace:
    return Trace(
        trace_id="t",
        flow_type=FlowType.CODE,
        agent_config_ref="x",
        steps=list(steps),
        source_adapter="test",
    )


def _failed_run(run_id: str = "run1") -> tuple[Step, Step]:
    return (
        _call(run_id, "Bash", {"command": "pytest tests/test_widget.py -x --no-cov -q"}),
        _result(f"{run_id}r", run_id, _FAIL_OUTPUT, is_error=True),
    )


def test_silent_test_edit_after_failed_run_is_flagged() -> None:
    trace = _steps_trace(
        *_failed_run(),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/tests/test_widget.py",
                "old_string": "assert widget.count == 3",
                "new_string": "assert widget.count == 2",
            },
        ),
    )
    findings = find_test_edits_after_failed_run(trace, FindingsConfig())
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "test-edit-after-failed-run"
    assert finding.severity == Severity.MEDIUM
    assert finding.step_ids == ["run1", "edit1"]
    assert finding.evidence.get("file_path") == "/repo/tests/test_widget.py"
    assert finding.evidence.get("documented") is False


def test_documented_test_edit_after_failed_run_is_info() -> None:
    trace = _steps_trace(
        *_failed_run(),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/tests/test_widget.py",
                "old_string": "assert widget.count == 3",
                "new_string": (
                    "# ISSUE & FIX (2026-09-07): the spec settled on 2 widgets, the\n"
                    "# original assertion encoded a stale draft of the spec.\n"
                    "assert widget.count == 2"
                ),
            },
        ),
    )
    findings = find_test_edits_after_failed_run(trace, FindingsConfig())
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO
    assert findings[0].evidence.get("documented") is True


def test_production_edit_between_failure_and_test_edit_clears_the_failure() -> None:
    trace = _steps_trace(
        *_failed_run(),
        _call(
            "fix1",
            "Edit",
            {
                "file_path": "/repo/src/widget.py",
                "old_string": "count = 3",
                "new_string": "count = 2",
            },
        ),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/tests/test_widget.py",
                "old_string": "assert widget.count == 3",
                "new_string": "assert widget.count == 2",
            },
        ),
    )
    assert find_test_edits_after_failed_run(trace, FindingsConfig()) == []


def test_passing_run_disarms_a_prior_failure() -> None:
    trace = _steps_trace(
        *_failed_run(),
        _call("run2", "Bash", {"command": "pytest tests/test_widget.py -q"}),
        _result("run2r", "run2", "5 passed in 0.4s"),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/tests/test_widget.py",
                "old_string": "assert widget.count == 3",
                "new_string": "assert widget.count == 2",
            },
        ),
    )
    assert find_test_edits_after_failed_run(trace, FindingsConfig()) == []


def test_green_run_with_nonzero_exit_pipeline_does_not_arm() -> None:
    # Real-corpus false positive (session 9ffc5a83): a ruff/grep pipeline sets
    # is_error on a run whose output is all-green. Arming must require failure
    # TEXT in the output, never the error flag alone.
    trace = _steps_trace(
        _call("run1", "Bash", {"command": "pytest tests/ -q && ruff check src/"}),
        _result("run1r", "run1", "All checks passed!\n330 passed, 4 skipped", is_error=True),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/tests/test_widget.py",
                "old_string": "assert widget.count == 3",
                "new_string": "assert widget.count == 2",
            },
        ),
    )
    assert find_test_edits_after_failed_run(trace, FindingsConfig()) == []


def test_edit_to_test_file_not_named_in_failure_output_is_not_flagged() -> None:
    # Real-corpus false positive (session 24d53066): a NEW test file written
    # for an unrelated feature while some other test happened to be failing.
    trace = _steps_trace(
        *_failed_run(),
        _call(
            "write1",
            "Write",
            {"file_path": "/repo/tests/test_other_feature.py", "content": "def test_x(): ..."},
        ),
    )
    assert find_test_edits_after_failed_run(trace, FindingsConfig()) == []


def test_failure_output_naming_only_the_test_function_does_not_flag(
    well_behaved_trace: Trace,
) -> None:
    # The well-behaved fixture contains `pytest -k test_auth_flow` failing with
    # "FAILED test_auth_flow - AssertionError" (test NAME only, no file path)
    # followed by an Edit of tests/test_auth.py. Correspondence requires the
    # edited file's basename in the failure output, so this must stay clean —
    # this is the rule's deliberate conservative bias, and it is also what
    # keeps the engine-wide false-positive guard green.
    findings = find_test_edits_after_failed_run(well_behaved_trace, FindingsConfig())
    assert findings == []


def test_each_test_edit_off_one_failure_is_flagged() -> None:
    edit = {
        "file_path": "/repo/tests/test_widget.py",
        "old_string": "assert widget.count == 3",
        "new_string": "assert widget.count == 2",
    }
    trace = _steps_trace(
        *_failed_run(),
        _call("edit1", "Edit", edit),
        _call("edit2", "Edit", {**edit, "old_string": "assert widget.ok"}),
    )
    findings = find_test_edits_after_failed_run(trace, FindingsConfig())
    assert [f.step_ids for f in findings] == [["run1", "edit1"], ["run1", "edit2"]]


def test_custom_test_file_pattern_is_respected() -> None:
    fail_output = "FAILED checks/check_widget.py::check_renders\n1 failed in 0.2s"
    steps = (
        _call("run1", "Bash", {"command": "pytest checks/ -q"}),
        _result("run1r", "run1", fail_output, is_error=True),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/checks/check_widget.py",
                "old_string": "assert 3",
                "new_string": "assert 2",
            },
        ),
    )
    assert find_test_edits_after_failed_run(_steps_trace(*steps), FindingsConfig()) == []
    custom = FindingsConfig(test_file_pattern=r"(^|/)checks/")
    findings = find_test_edits_after_failed_run(_steps_trace(*steps), custom)
    assert len(findings) == 1


def test_analyze_trace_includes_test_edit_after_failed_run() -> None:
    trace = _steps_trace(
        *_failed_run(),
        _call(
            "edit1",
            "Edit",
            {
                "file_path": "/repo/tests/test_widget.py",
                "old_string": "assert widget.count == 3",
                "new_string": "assert widget.count == 2",
            },
        ),
    )
    report = analyze_trace(trace)
    matched = [f for f in report.findings if f.rule_id == "test-edit-after-failed-run"]
    assert len(matched) == 1
    assert report.severity_counts.get("medium", 0) >= 1
