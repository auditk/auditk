"""Unit and integration tests for the NLIScorer (nli@0.2)."""

import os
from datetime import UTC, datetime
from typing import Any

import pytest

from auditk.analysis.protocols import Scorer
from auditk.analysis.scorers import get_scorer
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


# --- Protocol & identity ---


def test_nli_scorer_satisfies_protocol() -> None:
    from auditk.analysis.scorers.nli import NLIScorer

    scorer = NLIScorer(predictor=FakeNLIPredictor({}))
    assert isinstance(scorer, Scorer)
    assert scorer.method == "nli"
    assert scorer.method_version == "0.2"


# --- Scoring behaviour ---


def test_entailed_action_not_flagged() -> None:
    """An action entailed by the plan → FAITHFUL, not flagged, drift=0.0."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor({("goal", "action"): (0.1, 0.8, 0.1)})
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace([_make_step("s-1", "goal", {"text": "action"})])
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert report.flagged_steps == []
    assert report.method == "nli"
    assert report.method_version == "0.2"


def test_contradicting_action_flagged() -> None:
    """An action contradicting the plan → CONTRADICTION, flagged, drift=1.0."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor({("goal", "action"): (0.8, 0.1, 0.1)})
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace([_make_step("s-1", "goal", {"text": "action"})])
    report = scorer.score(trace)
    assert report.drift_score == 1.0
    assert "s-1" in report.flagged_steps


def test_neutral_action_not_in_flagged_steps() -> None:
    """A neutral action → NEUTRAL, not flagged, drift=0.0."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor({("goal", "action"): (0.1, 0.1, 0.8)})
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace([_make_step("s-1", "goal", {"text": "action"})])
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert "s-1" not in report.flagged_steps


def test_entailed_by_any_subgoal_is_faithful() -> None:
    """The step-1-of-5 fix: entailed by ANY sub-goal → FAITHFUL."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor(
        {
            ("sub-goal 1", "action"): (0.8, 0.1, 0.1),  # contradict
            ("sub-goal 2", "action"): (0.1, 0.8, 0.1),  # entail
        }
    )
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace([_make_step("s-1", "sub-goal 1\nsub-goal 2", {"text": "action"})])
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert "s-1" not in report.flagged_steps


def test_drift_score_is_contradiction_fraction() -> None:
    """1 contradiction of 4 scored steps → drift=0.25."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),
            ("goal-2", "act-2"): (0.1, 0.8, 0.1),
            ("goal-3", "act-3"): (0.1, 0.8, 0.1),
            ("goal-4", "act-4"): (0.8, 0.1, 0.1),
        }
    )
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace(
        [
            _make_step("s-1", "goal-1", {"text": "act-1"}),
            _make_step("s-2", "goal-2", {"text": "act-2"}),
            _make_step("s-3", "goal-3", {"text": "act-3"}),
            _make_step("s-4", "goal-4", {"text": "act-4"}),
        ]
    )
    report = scorer.score(trace)
    assert report.drift_score == 0.25
    assert report.flagged_steps == ["s-4"]


def test_no_scored_steps_returns_zero() -> None:
    """No declared_intent anywhere → 0.0 drift, no flagged steps."""
    from auditk.analysis.scorers.nli import NLIScorer

    scorer = NLIScorer(predictor=FakeNLIPredictor({}))
    trace = _make_trace(
        [
            _make_step("s-1", None, {"text": "no intent"}),
            _make_step("s-2", None, {"text": "also no intent"}),
        ]
    )
    report = scorer.score(trace)
    assert report.drift_score == 0.0
    assert report.flagged_steps == []


def test_nli_scorer_deterministic() -> None:
    """Two score() calls on the same trace produce identical reports."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor({("goal", "action"): (0.1, 0.8, 0.1)})
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace([_make_step("s-1", "goal", {"text": "action"})])
    report_a = scorer.score(trace)
    report_b = scorer.score(trace)
    assert report_a == report_b


# --- per_step population ---


def test_nli_per_step_populated() -> None:
    """NLIScorer returns per_step with taxonomy labels and reasoning."""
    from auditk.analysis.scorers.nli import NLIScorer

    predictor = FakeNLIPredictor(
        {
            ("goal-1", "act-1"): (0.1, 0.8, 0.1),  # entail
            ("goal-2", "act-2"): (0.8, 0.1, 0.1),  # contradict
            ("goal-3", "act-3"): (0.1, 0.1, 0.8),  # neutral
        }
    )
    scorer = NLIScorer(predictor=predictor)
    trace = _make_trace(
        [
            _make_step("s-1", "goal-1", {"text": "act-1"}),
            _make_step("s-2", "goal-2", {"text": "act-2"}),
            _make_step("s-3", "goal-3", {"text": "act-3"}),
        ]
    )
    report = scorer.score(trace)
    assert report.per_step is not None
    assert len(report.per_step) == 3
    assert report.per_step["s-1"].label.value == "faithful"
    assert report.per_step["s-2"].label.value == "goal_deviation"
    assert report.per_step["s-3"].label.value == "neutral"
    assert "NLI" in report.per_step["s-1"].reasoning
    assert "NLI" in report.per_step["s-2"].reasoning
    assert "NLI" in report.per_step["s-3"].reasoning


# --- Decomposition ---


def test_decompose_matches_d1_join_delimiter() -> None:
    """D1 joins active todos on '\\n'; decompose must split on '\\n'."""
    from auditk.analysis.scorers.nli import decompose

    assert decompose("a\nb") == ["a", "b"]


def test_decompose_caps_subgoals_at_k() -> None:
    """Decompose caps at K=12 sub-goals to bound NLI calls."""
    from auditk.analysis.scorers.nli import decompose

    long_plan = "\n".join(f"goal-{i}" for i in range(20))
    result = decompose(long_plan)
    assert len(result) == 12


def test_decompose_empty_falls_back_to_whole() -> None:
    """Empty decomposition returns the whole plan text as a single sub-goal."""
    from auditk.analysis.scorers.nli import decompose

    assert decompose("single goal") == ["single goal"]


# --- Registry & packaging ---


def test_missing_nli_extra_raises_actionable_error() -> None:
    """get_scorer('nli@0.2') without the [nli] extra raises ImportError with install hint."""
    with pytest.raises(ImportError, match=r"auditk\[nli\]"):
        get_scorer("nli@0.2")


# --- Registry: model pinning (I1) ---


def test_nli_scorer_loads_deberta_model_with_local_files_only(monkeypatch) -> None:
    """I1: The NLI scorer must load the pinned deberta model with local_files_only=True."""
    from unittest.mock import patch

    import transformers.pipelines

    monkeypatch.setenv("RUN_NLI_MODEL", "1")

    captured: dict[str, Any] = {}

    def _mock_pipeline(task: str, **kwargs: Any) -> Any:
        captured.update(kwargs)

        class MockClassifier:
            def __call__(
                self, sequence: str, truncation: bool = True
            ) -> list[list[dict[str, Any]]]:
                return [[{"label": "entailment", "score": 0.9}]]

        return MockClassifier()

    from auditk.analysis.scorers import _load_nli_scorer

    with patch.object(transformers.pipelines, "pipeline", _mock_pipeline):
        _load_nli_scorer()
    assert captured.get("model") == "cross-encoder/nli-deberta-v3-small"
    assert captured.get("revision") == "fa2804872c3b4bd748f38c0185cc85775361e735"
    assert captured.get("local_files_only") is True


# --- Integration (real model) ---


@pytest.mark.skipif(not os.environ.get("RUN_NLI_MODEL"), reason="needs real NLI model")
def test_real_model_classifies_known_pair() -> None:
    """Integration: real model classifies a known entailment and contradiction pair."""
    pytest.importorskip("torch")
    pytest.importorskip("transformers")

    scorer = get_scorer("nli@0.2")
    trace = _make_trace(
        [
            _make_step("s-1", "The cat sat on the mat", {"text": "A cat is on a mat"}),
            _make_step("s-2", "The cat sat on the mat", {"text": "There is no cat"}),
        ]
    )
    report = scorer.score(trace)
    assert report.method == "nli"
    assert report.method_version == "0.2"
    # The first step should be entailed (not flagged)
    # The second step should be contradicted (flagged)
    assert "s-1" not in report.flagged_steps
    assert "s-2" in report.flagged_steps
