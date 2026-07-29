# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for P4 part 1 (D6) of the cc-adapter-integrity work:
docs/proposals/session-postmortem-reporting.md Phase 5/6 follow-on.

P3 flattened delegate (subagent) steps into the parent trace, each carrying
`metadata["agent_id"]`. That is correct for structural findings (P3's
acceptance gate: `analysis/findings.py` predicates must see delegate
steps) but WRONG for the trace-level intent-enactment drift score: a
delegate is scored against its OWN brief, not the parent's declared
intent, so blending its steps into the parent's `compute_drift` result
would silently corrupt a number that already has a documented
false-positive history (Finding B in the proposal). D6 (RESOLVED, not
re-opened here):

- The parent trace-level drift score EXCLUDES steps carrying
  `metadata["agent_id"]` -- a trace containing delegate steps must yield
  the SAME drift score as the identical trace with those steps removed.
- A separate PER-AGENT drift figure must be computable, scoped to just
  that agent's own steps, keyed by agent_id.
- The structural findings engine must NOT regress: delegate steps stay
  fully visible to `analysis/findings.py` predicates. Only the numeric
  drift SCORE excludes them.

REAL SURFACE LOCATED (per the P4 kickoff's instruction to find it rather
than assume): drift is actually computed by `auditk.analysis.drift.
compute_drift(trace, scorer_key) -> DriftReport`, which is a thin
delegation layer routing to a pluggable `Scorer` (`JaccardScorer` today,
`analysis/scorers/`) and is the one thing `attestation/pack.py` calls to
populate an evidence pack's `drift_metrics`. `analysis/report.py`
(`auditk report`'s ReportModel/build_report) does NOT currently compute or
surface any drift score at all -- confirmed by grep, not assumed; the
proposal deliberately keeps semantic drift out of the structural
post-mortem ("a complementary, weaker signal, not a replacement",
findings.py's own module docstring). This module's tests are therefore
written against `analysis/drift.py`, the real surface, not against
`analysis/report.py`.

FLAGGED DESIGN DECISIONS (proposed here, not silently committed -- for
review before GREEN):

(a) Per-agent drift reporting surface: a NEW function
    `compute_delegate_drift(trace, scorer_key=None) -> dict[str, DriftReport]`
    living alongside `compute_drift` in `analysis/drift.py`, keyed by
    `agent_id`, each value a full `DriftReport` (mirroring `compute_drift`'s
    own return shape, so existing DriftReport consumers/tooling need no new
    type to understand). Proposed over adding a field to `ReportModel`
    (analysis/report.py) or to `DriftReport` itself: `auditk report` has no
    drift concept today and this phase doesn't ask it to grow one, and
    bolting a `per_agent` dict onto `DriftReport` would force every OTHER
    caller of `compute_drift` (attestation/pack.py, any future one) to
    reason about a field that's only meaningful when delegates exist. A
    small standalone function callers can opt into is the minimal-footprint
    choice.

(b) Where the exclusion happens: inside `compute_drift` itself (the "thin
    delegation layer"), by building a delegate-free VIEW of the trace
    (`trace.model_copy(update={"steps": ...})`) before handing it to
    whichever `Scorer` is configured -- NOT inside each `Scorer`
    implementation. Proposed because there are already three scorer
    implementations (`JaccardScorer`, `NLIScorer`, the two-stage judge
    scorer) and more are pluggable; duplicating an `agent_id` filter into
    every current and future `Scorer.score()` is exactly the kind of
    drift-prone duplication this whole cc-adapter-integrity effort exists
    to eliminate. Centralizing it in `compute_drift` means no `Scorer`
    implementation ever needs to know delegates exist.

Every fixture below is built directly from `auditk.schema` model
constructors (same convention as the existing `tests/unit/test_drift.py`),
not through the claude-code adapter -- this module tests the scoring
layer, not ingestion (already covered by P3's own tests).

Every test in this module is expected to FAIL right now: the
`compute_delegate_drift` tests fail with an `ImportError` (the function
does not exist yet); the exclusion tests fail because `compute_drift`
today scores every step, delegate or not, so a trace with delegate steps
currently produces a DIFFERENT (not equal) drift score than the same trace
without them. Production code lands in the next (GREEN) phase, after
human review of this RED phase.
"""

from __future__ import annotations

from datetime import UTC, datetime

from auditk.analysis.drift import compute_drift
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    DriftReport,
    FlowType,
    Outcome,
    Step,
    Trace,
)

_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _make_trace(steps: list[Step], trace_id: str = "t-1") -> Trace:
    return Trace(
        trace_id=trace_id,
        flow_type=FlowType.GENERIC,
        agent_config_ref="cfg-1",
        steps=steps,
        source_adapter="test",
        outcome=Outcome(status="success"),
    )


def _make_step(
    step_id: str,
    declared_intent: str | None,
    payload: dict,
    *,
    trace_id: str = "t-1",
    agent_id: str | None = None,
) -> Step:
    return Step(
        step_id=step_id,
        trace_id=trace_id,
        timestamp=_TS,
        actor=Actor.AGENT,
        declared_intent=declared_intent,
        action=Action(type=ActionType.UTTERANCE, payload=payload),
        metadata={"agent_id": agent_id} if agent_id else {},
    )


# A step whose declared_intent and payload share no tokens at all -- always
# scores as fully drifted (drift ~1.0) under the default Jaccard scorer,
# regardless of what else is in the trace.
def _heavily_drifted_step(step_id: str, *, agent_id: str | None = None) -> Step:
    return _make_step(
        step_id,
        "investigate and repair the subsystem",
        {"output": "zzz qqq xxx unrelated placeholder tokens"},
        agent_id=agent_id,
    )


# A step whose declared_intent and payload tokens match closely -- always
# scores as well-aligned (drift ~0.0).
def _well_aligned_step(step_id: str, *, agent_id: str | None = None) -> Step:
    return _make_step(step_id, "order lookup", {"order": "lookup"}, agent_id=agent_id)


# --- D6(a): the parent drift score excludes delegate steps ---------------


class TestParentDriftScoreExcludesDelegateSteps:
    def test_drift_score_identical_with_and_without_delegate_steps(self) -> None:
        parent_steps = [
            _well_aligned_step("p-1"),
            _make_step("p-2", "retrieve payment", {"output": "completely unrelated jargon here"}),
        ]
        # A heavily-drifted delegate step: if blended into the parent score,
        # this would visibly change drift_score. It must not.
        delegate_steps = [_heavily_drifted_step("d-1", agent_id="AAA")]

        report_without_delegates = compute_drift(_make_trace(parent_steps))
        report_with_delegates = compute_drift(_make_trace(parent_steps + delegate_steps))

        assert report_with_delegates.drift_score == report_without_delegates.drift_score

    def test_delegate_step_never_appears_in_flagged_steps(self) -> None:
        trace = _make_trace(
            [_well_aligned_step("p-1"), _heavily_drifted_step("d-1", agent_id="AAA")]
        )
        report = compute_drift(trace)
        assert "d-1" not in report.flagged_steps

    def test_delegate_step_never_appears_in_per_step(self) -> None:
        trace = _make_trace(
            [_well_aligned_step("p-1"), _heavily_drifted_step("d-1", agent_id="AAA")]
        )
        report = compute_drift(trace)
        assert report.per_step is not None
        assert "d-1" not in report.per_step
        assert "p-1" in report.per_step

    def test_trace_of_only_delegate_steps_yields_zero_drift(self) -> None:
        # No parent steps at all to score against -- exclusion must not
        # crash, and must fall back to the scorer's own "nothing to score"
        # behaviour (JaccardScorer: 0.0), not silently fall through to
        # scoring the delegate steps after all.
        trace = _make_trace(
            [
                _heavily_drifted_step("d-1", agent_id="AAA"),
                _heavily_drifted_step("d-2", agent_id="AAA"),
            ]
        )
        report = compute_drift(trace)
        assert report.drift_score == 0.0
        assert report.flagged_steps == []

    def test_multiple_agents_all_excluded_from_parent_score(self) -> None:
        parent_steps = [_well_aligned_step("p-1")]
        delegate_steps = [
            _heavily_drifted_step("d-aaa-1", agent_id="AAA"),
            _heavily_drifted_step("d-bbb-1", agent_id="BBB"),
        ]
        report_with = compute_drift(_make_trace(parent_steps + delegate_steps))
        report_without = compute_drift(_make_trace(parent_steps))
        assert report_with.drift_score == report_without.drift_score


# --- D6(b): a separate per-agent drift figure, keyed by agent_id ---------


class TestComputeDelegateDrift:
    def test_returns_dict_keyed_by_agent_id(self) -> None:
        from auditk.analysis.drift import compute_delegate_drift

        trace = _make_trace(
            [
                _well_aligned_step("p-1"),
                _heavily_drifted_step("d-aaa-1", agent_id="AAA"),
                _well_aligned_step("d-bbb-1", agent_id="BBB"),
            ]
        )

        result = compute_delegate_drift(trace)

        assert isinstance(result, dict)
        assert set(result) == {"AAA", "BBB"}
        assert all(isinstance(v, DriftReport) for v in result.values())

    def test_per_agent_score_reflects_that_agents_own_alignment(self) -> None:
        from auditk.analysis.drift import compute_delegate_drift

        trace = _make_trace(
            [
                _well_aligned_step("p-1"),
                _heavily_drifted_step("d-aaa-1", agent_id="AAA"),
                _well_aligned_step("d-bbb-1", agent_id="BBB"),
            ]
        )

        result = compute_delegate_drift(trace)

        assert result["AAA"].drift_score > 0.5
        assert result["BBB"].drift_score < 0.1

    def test_per_agent_score_is_isolated_not_blended_across_agents(self) -> None:
        # If AAA's two steps were blended with BBB's, AAA's score would
        # differ from scoring AAA's steps alone in a standalone trace.
        from auditk.analysis.drift import compute_delegate_drift

        aaa_steps = [
            _well_aligned_step("d-aaa-1", agent_id="AAA"),
            _heavily_drifted_step("d-aaa-2", agent_id="AAA"),
        ]
        bbb_steps = [_heavily_drifted_step("d-bbb-1", agent_id="BBB")]

        combined_trace = _make_trace([*aaa_steps, *bbb_steps])
        isolated_aaa_trace = _make_trace(aaa_steps)

        combined_result = compute_delegate_drift(combined_trace)
        isolated_report = compute_drift(isolated_aaa_trace)

        assert combined_result["AAA"].drift_score == isolated_report.drift_score

    def test_per_agent_score_never_includes_parent_steps(self) -> None:
        from auditk.analysis.drift import compute_delegate_drift

        # The parent step is heavily drifted; AAA's own step is
        # well-aligned. If the parent step leaked into AAA's computation,
        # AAA's score would rise well above what its own step alone
        # produces.
        parent_step = _heavily_drifted_step("p-1")
        aaa_step = _well_aligned_step("d-aaa-1", agent_id="AAA")

        trace = _make_trace([parent_step, aaa_step])
        result = compute_delegate_drift(trace)

        assert result["AAA"].drift_score < 0.1

    def test_empty_dict_when_no_delegate_steps_present(self) -> None:
        from auditk.analysis.drift import compute_delegate_drift

        trace = _make_trace([_well_aligned_step("p-1")])
        assert compute_delegate_drift(trace) == {}


# --- Regression guard: structural findings must still see delegate steps -


class TestFindingsVisibilityDoesNotRegress:
    """P3's acceptance gate must hold even after this phase's drift-scoring
    change: `analysis/findings.py` is untouched by D6 (it doesn't call
    `compute_drift` at all), so this already passes today -- included as an
    explicit, named regression guard rather than left implicit."""

    def test_delegate_edit_step_still_flagged_by_scope_escape_finding(self) -> None:
        from auditk.analysis.findings import FindingsConfig, find_writes_outside_roots

        delegate_edit = Step(
            step_id="d-edit-1",
            trace_id="t-1",
            timestamp=_TS,
            actor=Actor.AGENT,
            declared_intent="fix the bug",
            action=Action(
                type=ActionType.TOOL_CALL,
                payload={
                    "name": "Edit",
                    "input": {
                        "file_path": "/outside-scope/evil.py",
                        "old_string": "a",
                        "new_string": "b",
                    },
                },
            ),
            metadata={"agent_id": "AAA"},
        )
        trace = _make_trace([delegate_edit])
        config = FindingsConfig(roots=["/work/allowed-project"])

        findings = find_writes_outside_roots(trace, config)

        assert any(f.step_ids == ["d-edit-1"] for f in findings)
