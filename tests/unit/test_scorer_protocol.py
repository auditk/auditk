"""Unit tests for the Scorer protocol and registry (Sub-phase A)."""

import pytest

from auditk.analysis.drift import compute_drift
from auditk.analysis.protocols import Scorer
from auditk.analysis.scorers import DEFAULT_SCORER, available, get_scorer
from auditk.schema import Action, ActionType, Actor, DriftReport, FlowType, Outcome, Step, Trace

_TS = __import__("datetime").datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc)


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
    trace_id: str = "t-1",
) -> Step:
    return Step(
        step_id=step_id,
        trace_id=trace_id,
        timestamp=_TS,
        actor=Actor.AGENT,
        declared_intent=declared_intent,
        action=Action(type=ActionType.UTTERANCE, payload=payload),
    )


def test_jaccard_scorer_satisfies_protocol() -> None:
    scorer = get_scorer("plan-action-similarity@0.1")
    assert isinstance(scorer, Scorer)
    assert scorer.method == "plan-action-similarity"
    assert scorer.method_version == "0.1"


def test_registry_resolves_jaccard_key() -> None:
    scorer = get_scorer("plan-action-similarity@0.1")
    report = scorer.score(_make_trace([_make_step("s-1", None, {})]))
    assert isinstance(report, DriftReport)
    assert "plan-action-similarity@0.1" in available()


def test_registry_unknown_key_raises() -> None:
    with pytest.raises(KeyError) as exc_info:
        get_scorer("unknown@9.9")
    message = str(exc_info.value)
    assert "unknown@9.9" in message
    assert "plan-action-similarity@0.1" in message


def test_compute_drift_delegates_to_default_scorer() -> None:
    trace = _make_trace([_make_step("s-1", None, {})])
    via_shim = compute_drift(trace)
    via_registry = get_scorer(DEFAULT_SCORER).score(trace)
    assert via_shim == via_registry
