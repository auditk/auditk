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


def test_step_drift_severity_and_evidence_default() -> None:
    """severity/evidence are additive-optional: omitting them still constructs
    (packs built before these fields existed must still validate)."""
    sd = StepDrift(
        step_id="s-1",
        label=TaxonomyLabel.FAITHFUL,
        overturned_gate=False,
        reasoning="ok",
    )
    assert sd.severity == "LOW"
    assert sd.evidence == "n/a"


def test_step_drift_severity_and_evidence_explicit() -> None:
    """severity/evidence carry through when a judge supplies them — mirrors
    RubricResult in analysis/taxonomy.py."""
    sd = StepDrift(
        step_id="s-1",
        label=TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE,
        overturned_gate=False,
        reasoning="Violated the declared constraint.",
        severity="HIGH",
        evidence="rm -rf /data",
    )
    assert sd.severity == "HIGH"
    assert sd.evidence == "rm -rf /data"


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


def test_drift_report_carries_step_severity_and_evidence() -> None:
    """severity/evidence set on a StepDrift survive nesting inside DriftReport.per_step
    (the RubricResult -> StepDrift -> DriftReport leg of the thread)."""
    step_drift = StepDrift(
        step_id="s-1",
        label=TaxonomyLabel.GOAL_DEVIATION,
        overturned_gate=False,
        reasoning="Pursued a different objective than declared.",
        severity="MEDIUM",
        evidence="wrote to a different file than declared",
    )
    report = DriftReport(
        drift_score=1.0,
        drift_per_trace={"t-1": 1.0},
        flagged_steps=["s-1"],
        method="llm-judge",
        method_version="0.3",
        per_step={"s-1": step_drift},
    )
    assert report.per_step is not None
    assert report.per_step["s-1"].severity == "MEDIUM"
    assert report.per_step["s-1"].evidence == "wrote to a different file than declared"

    # DriftReport is itself nested under EvidencePack.drift_metrics — round-trip
    # through JSON the way a pack is written/read to confirm the fields survive
    # (de)serialisation, not just in-memory construction.
    round_tripped = DriftReport.model_validate_json(report.model_dump_json())
    assert round_tripped.per_step is not None
    assert round_tripped.per_step["s-1"].severity == "MEDIUM"
    assert round_tripped.per_step["s-1"].evidence == "wrote to a different file than declared"


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
