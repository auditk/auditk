"""Tests for TwoStageJudgeScorer (llm-judge@0.3, D3.4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from auditk.analysis.scorers.judge import TwoStageJudgeScorer
from auditk.analysis.taxonomy import RubricResult, TaxonomyLabel
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    FlowType,
    Outcome,
    Step,
    Trace,
)

_TS = datetime(2024, 1, 1, tzinfo=UTC)


class FakeNLIPredictor:
    """Returns scripted (p_contra, p_entail, p_neutral) distributions."""

    def __init__(self, mapping: dict[tuple[str, str], tuple[float, float, float]]):
        self.mapping = mapping

    def predict(self, premise: str, hypothesis: str) -> tuple[float, float, float]:
        return self.mapping.get((premise, hypothesis), (0.1, 0.1, 0.8))


class FakeJudge:
    """Scripted judge for unit tests."""

    model_id: str = "fake-judge"
    temperature: float = 0.0

    def __init__(self, verdicts: dict[str, RubricResult]) -> None:
        self.verdicts = verdicts
        self.calls: list[tuple[str, str, str, str]] = []

    def adjudicate(
        self,
        step_id: str,
        declared_intent: str,
        action_text: str,
        gate_label: str,
    ) -> RubricResult:
        self.calls.append((step_id, declared_intent, action_text, gate_label))
        return self.verdicts.get(
            step_id,
            RubricResult(
                label=TaxonomyLabel.FAITHFUL,
                confidence=1.0,
                reasoning="Default fake.",
            ),
        )


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
    action_type: ActionType = ActionType.UTTERANCE,
    trace_id: str = "t-1",
) -> Step:
    return Step(
        step_id=step_id,
        trace_id=trace_id,
        timestamp=_TS,
        actor=Actor.AGENT,
        declared_intent=declared_intent,
        action=Action(type=action_type, payload=payload),
    )


# --- Identity ---


def test_scorer_identity() -> None:
    predictor = FakeNLIPredictor({})
    judge = FakeJudge({})
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    assert scorer.method == "llm-judge"
    assert scorer.method_version == "0.3"


# --- Candidate policy ---


def test_only_contradiction_candidates_reach_judge() -> None:
    """Faithful and neutral steps must not be sent to the judge."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),  # entail → faithful
            ("goal-2", "act-2"): (0.1, 0.1, 0.8),  # neutral
            ("goal-3", "act-3"): (0.8, 0.1, 0.1),  # contradict → judge
        }
    )
    judge = FakeJudge({})
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace(
        [
            _make_step("s-1", "goal-1", {"text": "act-1"}),
            _make_step("s-2", "goal-2", {"text": "act-2"}),
            _make_step("s-3", "goal-3", {"text": "act-3"}),
        ]
    )
    scorer.score(trace)
    assert len(judge.calls) == 1
    assert judge.calls[0][0] == "s-3"


# --- Overturn ---


def test_judge_overturn_removes_from_drift() -> None:
    """Judge returns faithful for a contradiction → step not flagged, drift=0.0."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.FAITHFUL,
                confidence=1.0,
                reasoning="Actually fine.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert "s-1" not in report.flagged_steps
    assert report.per_step is not None
    assert report.per_step["s-1"].label == TaxonomyLabel.FAITHFUL
    assert report.per_step["s-1"].overturned_gate is True


def test_judge_overturn_to_benign_elaboration() -> None:
    """Judge returns benign_elaboration for a contradiction → step not flagged."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.BENIGN_ELABORATION,
                confidence=1.0,
                reasoning="Instrumental sub-step.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert "s-1" not in report.flagged_steps
    assert report.per_step["s-1"].label == TaxonomyLabel.BENIGN_ELABORATION
    assert report.per_step["s-1"].overturned_gate is True


# --- Drift labels confirmed ---


def test_goal_deviation_flagged() -> None:
    """Judge confirms B4 → goal_deviation, flagged, drift=1.0."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="Works against plan.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.drift_score == 1.0
    assert "s-1" in report.flagged_steps
    assert report.per_step is not None
    assert report.per_step["s-1"].label == TaxonomyLabel.GOAL_DEVIATION
    assert report.per_step["s-1"].overturned_gate is False


def test_instruction_noncompliance_flagged() -> None:
    """Judge confirms B3 → instruction_noncompliance, flagged, drift=1.0."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE,
                confidence=1.0,
                reasoning="Violates constraint.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.drift_score == 1.0
    assert "s-1" in report.flagged_steps
    assert report.per_step["s-1"].label == TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE


def test_undeclared_goal_flagged() -> None:
    """Judge confirms B5 → undeclared_goal, flagged, drift=1.0."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.UNDECLARED_GOAL,
                confidence=1.0,
                reasoning="Pursues off-plan goal.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.drift_score == 1.0
    assert "s-1" in report.flagged_steps
    assert report.per_step["s-1"].label == TaxonomyLabel.UNDECLARED_GOAL


# --- Drift score ---


def test_drift_score_with_multiple_steps() -> None:
    """3 steps: 1 faithful, 1 goal_deviation, 1 neutral → drift=1/3."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),  # entail
            ("goal-2", "act-2"): (0.8, 0.1, 0.1),  # contradict → goal_deviation
            ("goal-3", "act-3"): (0.1, 0.1, 0.8),  # neutral
        }
    )
    judge = FakeJudge(
        {
            "s-2": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="Bad.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace(
        [
            _make_step("s-1", "goal-1", {"text": "act-1"}),
            _make_step("s-2", "goal-2", {"text": "act-2"}),
            _make_step("s-3", "goal-3", {"text": "act-3"}),
        ]
    )
    report = scorer.score(trace)
    assert report.drift_score == pytest.approx(1.0 / 3.0)
    assert report.flagged_steps == ["s-2"]


def test_no_scored_steps_returns_zero() -> None:
    predictor = FakeNLIPredictor({})
    judge = FakeJudge({})
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", None, {"text": "no intent"})])
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert report.flagged_steps == []
    assert report.per_step == {}


# --- per_step population ---


def test_per_step_populated_for_all_scored_steps() -> None:
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),  # entail
        }
    )
    judge = FakeJudge({})
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.per_step is not None
    assert "s-1" in report.per_step
    assert report.per_step["s-1"].label == TaxonomyLabel.FAITHFUL
    assert report.per_step["s-1"].overturned_gate is False


# --- taxonomy_counts ---


def test_taxonomy_counts() -> None:
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),  # entail
            ("goal-2", "act-2"): (0.8, 0.1, 0.1),  # contradict → goal_deviation
        }
    )
    judge = FakeJudge(
        {
            "s-2": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="Bad.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace(
        [
            _make_step("s-1", "goal-1", {"text": "act-1"}),
            _make_step("s-2", "goal-2", {"text": "act-2"}),
        ]
    )
    report = scorer.score(trace)
    assert report.taxonomy_counts is not None
    assert report.taxonomy_counts["faithful"] == 1
    assert report.taxonomy_counts["goal_deviation"] == 1


# --- scorer_fingerprint ---


def test_scorer_fingerprint_populated() -> None:
    predictor = FakeNLIPredictor({})
    judge = FakeJudge({})
    scorer = TwoStageJudgeScorer(
        predictor=predictor,
        judge=judge,
        nli_model="nli-model",
        nli_revision="nli-rev",
    )
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.scorer_fingerprint is not None
    fp = report.scorer_fingerprint
    assert fp.method == "llm-judge"
    assert fp.method_version == "0.3"
    assert fp.nli_model == "nli-model"
    assert fp.nli_revision == "nli-rev"
    assert fp.judge_model == "fake-judge"
    assert fp.judge_temperature == 0.0


# --- max_judge_calls budget ---


def test_max_judge_calls_budget() -> None:
    """Only the first max_judge_calls contradiction candidates are adjudicated."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
            ("goal-2", "act-2"): (0.9, 0.05, 0.05),  # contradict (stronger)
            ("goal-3", "act-3"): (0.7, 0.2, 0.1),  # contradict (weaker)
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="",
            ),
            "s-2": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="",
            ),
            "s-3": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge, max_judge_calls=2)
    trace = _make_trace(
        [
            _make_step("s-1", "goal-1", {"text": "act-1"}),
            _make_step("s-2", "goal-2", {"text": "act-2"}),
            _make_step("s-3", "goal-3", {"text": "act-3"}),
        ]
    )
    report = scorer.score(trace)
    # Highest contra probability is s-2 (0.9), then s-1 (0.8), then s-3 (0.7)
    assert len(judge.calls) == 2
    called_step_ids = [c[0] for c in judge.calls]
    assert "s-2" in called_step_ids
    assert "s-1" in called_step_ids
    assert "s-3" not in called_step_ids

    # Unjudged contradiction remains flagged as drift (conservative)
    assert report.drift_score == 1.0
    assert len(report.flagged_steps) == 3

    # s-3 was never sent to the judge (budget exhausted) — it still gets a
    # conservative non-faithful severity default, not the schema's bare "LOW".
    assert report.per_step is not None
    assert report.per_step["s-3"].severity == "MEDIUM"
    assert report.per_step["s-3"].evidence == "n/a"


# --- Determinism ---


def test_deterministic_with_deterministic_fake_judge() -> None:
    """Two score() calls on the same trace produce identical reports."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.GOAL_DEVIATION,
                confidence=1.0,
                reasoning="Bad.",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report_a = scorer.score(trace)
    report_b = scorer.score(trace)
    assert report_a == report_b


# --- severity / evidence threading (RubricResult -> StepDrift) ---


def test_judge_severity_and_evidence_thread_into_step_drift() -> None:
    """severity/evidence returned by the judge (RubricResult) land unchanged on
    the corresponding StepDrift — not the schema defaults."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.8, 0.1, 0.1),  # contradict
        }
    )
    judge = FakeJudge(
        {
            "s-1": RubricResult(
                label=TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE,
                confidence=0.81,
                reasoning="Violated the declared constraint.",
                severity="HIGH",
                evidence="wrote to /etc/passwd",
            ),
        }
    )
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert report.per_step is not None
    step_drift = report.per_step["s-1"]
    assert step_drift.label == TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE
    assert step_drift.severity == "HIGH"
    assert step_drift.evidence == "wrote to /etc/passwd"


def test_gate_only_steps_keep_severity_and_evidence_defaults() -> None:
    """Steps resolved by the NLI gate alone (no judge call) keep the schema's
    LOW/n/a defaults — there is no RubricResult to thread through."""
    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),  # entail -> faithful, no judge call
        }
    )
    judge = FakeJudge({})
    scorer = TwoStageJudgeScorer(predictor=predictor, judge=judge)
    trace = _make_trace([_make_step("s-1", "goal-1", {"text": "act-1"})])
    report = scorer.score(trace)
    assert len(judge.calls) == 0
    assert report.per_step is not None
    assert report.per_step["s-1"].severity == "LOW"
    assert report.per_step["s-1"].evidence == "n/a"


# --- Registry ---


def test_registry_lazy_loads_llm_judge_scorer(monkeypatch) -> None:
    """get_scorer('llm-judge@0.3') lazy-loads without import-time side effects."""
    from auditk.analysis.scorers import get_scorer

    # Ensure RUN_JUDGE_MODEL is not set so it refuses to load
    monkeypatch.delenv("RUN_JUDGE_MODEL", raising=False)
    with pytest.raises(ImportError, match=r"llm-judge"):
        get_scorer("llm-judge@0.3")
