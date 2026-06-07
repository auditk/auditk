"""Tests for the taxonomy module (D3.1 Task 1)."""

from __future__ import annotations

from auditk.analysis.taxonomy import RubricResult, TaxonomyLabel


def test_taxonomy_label_enum_values() -> None:
    """All five taxonomy labels must exist with exact string values."""
    assert TaxonomyLabel.FAITHFUL == "faithful"
    assert TaxonomyLabel.BENIGN_ELABORATION == "benign_elaboration"
    assert TaxonomyLabel.UNDECLARED_GOAL == "undeclared_goal"
    assert TaxonomyLabel.GOAL_DEVIATION == "goal_deviation"
    assert TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE == "instruction_noncompliance"


def test_rubric_result_dataclass_exists() -> None:
    """RubricResult must be a dataclass with label, confidence, and reasoning fields."""
    result = RubricResult(
        label=TaxonomyLabel.FAITHFUL,
        confidence=0.95,
        reasoning="Directly advances the declared sub-goal.",
    )
    assert result.label == TaxonomyLabel.FAITHFUL
    assert result.confidence == 0.95
    assert result.reasoning == "Directly advances the declared sub-goal."


def test_rubric_result_fields_are_typed() -> None:
    """RubricResult fields must have the correct types."""
    from dataclasses import fields

    rubric_fields = {f.name: f.type for f in fields(RubricResult)}
    assert rubric_fields["label"] == "TaxonomyLabel"
    assert rubric_fields["confidence"] == "float"
    assert rubric_fields["reasoning"] == "str"
