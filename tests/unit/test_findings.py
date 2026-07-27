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
