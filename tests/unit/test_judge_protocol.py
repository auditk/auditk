"""D3.1 — Judge protocol contract tests."""

from __future__ import annotations

from typing import Protocol, get_type_hints

from auditk.analysis.protocols import Judge
from auditk.analysis.taxonomy import RubricResult, TaxonomyLabel


class _FakeJudge:
    """Minimal concrete type that satisfies the Judge protocol."""

    model_id: str = "fake-judge"
    temperature: float = 0.0

    def adjudicate(
        self,
        step_id: str,
        declared_intent: str,
        action_text: str,
        gate_label: str,
    ) -> RubricResult:
        return RubricResult(
            label=TaxonomyLabel.FAITHFUL,
            confidence=1.0,
            reasoning="Fake judge says ok.",
        )


class _BadJudgeNoModelId:
    """Missing model_id — should fail structural check."""

    temperature: float = 0.0

    def adjudicate(
        self,
        step_id: str,
        declared_intent: str,
        action_text: str,
        gate_label: str,
    ) -> RubricResult:
        return RubricResult(
            label=TaxonomyLabel.FAITHFUL,
            confidence=1.0,
            reasoning="ok",
        )


class _BadJudgeNoAdjudicate:
    """Missing adjudicate method — should fail structural check."""

    model_id: str = "fake"
    temperature: float = 0.0


def test_judge_is_protocol() -> None:
    """Judge must be a runtime-checkable Protocol."""
    assert isinstance(Judge, type)
    assert issubclass(type(Judge), type(Protocol))


def test_judge_exposes_model_id_attribute() -> None:
    """Judge protocol must declare a model_id: str attribute."""
    hints = get_type_hints(Judge)
    assert "model_id" in hints
    assert hints["model_id"] is str


def test_judge_exposes_temperature_attribute() -> None:
    """Judge protocol must declare a temperature: float attribute."""
    hints = get_type_hints(Judge)
    assert "temperature" in hints
    assert hints["temperature"] is float


def test_judge_adjudicate_signature() -> None:
    """Judge.adjudicate must accept (step_id, declared_intent, action_text, gate_label)
    and return a RubricResult."""
    method_hints = get_type_hints(Judge.adjudicate)
    expected_args = {"step_id": str, "declared_intent": str, "action_text": str, "gate_label": str}
    for arg, expected_type in expected_args.items():
        assert arg in method_hints, f"Missing adjudicate parameter: {arg}"
        assert method_hints[arg] is expected_type, f"adjudicate.{arg} is not {expected_type}"

    assert "return" in method_hints
    assert method_hints["return"] is RubricResult


def test_fake_judge_satisfies_protocol() -> None:
    """A conforming implementation should be recognized as a Judge at runtime."""
    fake = _FakeJudge()
    assert isinstance(fake, Judge)


def test_bad_judge_missing_model_id_fails() -> None:
    """Missing model_id should not satisfy the protocol."""
    bad = _BadJudgeNoModelId()
    assert not isinstance(bad, Judge)


def test_bad_judge_missing_adjudicate_fails() -> None:
    """Missing adjudicate should not satisfy the protocol."""
    bad = _BadJudgeNoAdjudicate()
    assert not isinstance(bad, Judge)
