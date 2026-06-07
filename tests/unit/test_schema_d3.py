"""D3.1 — Schema additions: StepDrift, ScorerFingerprint, DriftReport additive fields."""

from __future__ import annotations

from auditk.analysis.taxonomy import TaxonomyLabel
from auditk.schema import DriftReport, ScorerFingerprint, StepDrift


def test_step_drift_exists_with_fields() -> None:
    """StepDrift must carry step_id, label, overturned_gate, and reasoning."""
    sd = StepDrift(
        step_id="s-1",
        label=TaxonomyLabel.FAITHFUL,
        overturned_gate=False,
        reasoning="Directly advances the declared sub-goal.",
    )
    assert sd.step_id == "s-1"
    assert sd.label == TaxonomyLabel.FAITHFUL
    assert sd.overturned_gate is False
    assert sd.reasoning == "Directly advances the declared sub-goal."


def test_scorer_fingerprint_exists_with_fields() -> None:
    """ScorerFingerprint must carry method, method_version, nli_model, nli_revision,
    judge_model, and judge_temperature."""
    fp = ScorerFingerprint(
        method="nli",
        method_version="0.2",
        nli_model="cross-encoder/nli-deberta-v3-small",
        nli_revision="fa2804872c3b4bd748f38c0185cc85775361e735",
        judge_model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        judge_temperature=0.0,
    )
    assert fp.method == "nli"
    assert fp.method_version == "0.2"
    assert fp.nli_model == "cross-encoder/nli-deberta-v3-small"
    assert fp.nli_revision == "fa2804872c3b4bd748f38c0185cc85775361e735"
    assert fp.judge_model == "accounts/fireworks/models/llama-v3p1-70b-instruct"
    assert fp.judge_temperature == 0.0


def test_drift_report_backward_compatible_no_new_fields() -> None:
    """DriftReport can still be constructed with only the original fields."""
    report = DriftReport(
        drift_score=0.0,
        drift_per_trace={"t-1": 0.0},
        flagged_steps=[],
        method="jaccard",
        method_version="0.1",
    )
    assert report.per_step is None
    assert report.scorer_fingerprint is None
    assert report.taxonomy_counts is None


def test_drift_report_with_new_fields() -> None:
    """DriftReport accepts the new additive-optional fields."""
    step_drift = StepDrift(
        step_id="s-1",
        label=TaxonomyLabel.FAITHFUL,
        overturned_gate=False,
        reasoning="ok",
    )
    fp = ScorerFingerprint(
        method="llm-judge",
        method_version="0.3",
    )
    report = DriftReport(
        drift_score=0.0,
        drift_per_trace={"t-1": 0.0},
        flagged_steps=[],
        method="llm-judge",
        method_version="0.3",
        per_step={"s-1": step_drift},
        scorer_fingerprint=fp,
        taxonomy_counts={"faithful": 1},
    )
    assert report.per_step is not None
    assert report.scorer_fingerprint is not None
    assert report.taxonomy_counts == {"faithful": 1}


def test_drift_report_defaults_all_new_fields_to_none() -> None:
    """When omitted, new additive-optional fields default to None."""
    report = DriftReport(
        drift_score=0.0,
        method="jaccard",
        method_version="0.1",
    )
    assert report.per_step is None
    assert report.scorer_fingerprint is None
    assert report.taxonomy_counts is None
