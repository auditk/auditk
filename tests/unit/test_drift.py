"""Unit tests for the intent–enactment drift detector (T3.2)."""

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


# ---------------------------------------------------------------------------
# Test 1: no declared_intent on any step → zero drift
# ---------------------------------------------------------------------------
def test_no_declared_intent_returns_zero_drift():
    steps = [
        _make_step("s-1", None, {"output": "hello world"}),
        _make_step("s-2", None, {"output": "another response"}),
    ]
    report = compute_drift(_make_trace(steps))
    assert isinstance(report, DriftReport)
    assert report.drift_score == 0.0
    assert report.flagged_steps == []
    assert report.drift_per_trace == {"t-1": 0.0}


# ---------------------------------------------------------------------------
# Test 2: perfectly-aligned trace → low drift (< 0.1)
# Tokens in declared_intent match tokens in str(payload) closely.
# Using key=value pairs so str(dict) tokens equal the intent tokens.
# ---------------------------------------------------------------------------
def test_aligned_trace_low_drift():
    # str({"order": "lookup"}) → "{'order': 'lookup'}" → tokens {"order", "lookup"}
    # intent tokens → {"order", "lookup"}  → Jaccard = 1.0, drift = 0.0
    steps = [
        _make_step("s-1", "order lookup", {"order": "lookup"}),
        _make_step("s-2", "retrieve customer", {"retrieve": "customer"}),
    ]
    report = compute_drift(_make_trace(steps))
    assert report.drift_score < 0.1


# ---------------------------------------------------------------------------
# Test 3: fully-drifted step → drift > 0.5, step flagged
# ---------------------------------------------------------------------------
def test_fully_drifted_step_flagged():
    steps = [
        _make_step(
            "s-1",
            "look up order",
            {"output": "completely different unrelated words"},
        )
    ]
    report = compute_drift(_make_trace(steps))
    assert report.drift_score > 0.5
    assert "s-1" in report.flagged_steps


# ---------------------------------------------------------------------------
# Test 4: mixed trace — some aligned, some drifted
# drift_score is between 0.1 and 0.9; only the drifted step is flagged
# ---------------------------------------------------------------------------
def test_mixed_trace_partial_drift():
    steps = [
        # aligned: tokens match exactly
        _make_step("s-1", "order lookup", {"order": "lookup"}),
        # drifted: no token overlap
        _make_step(
            "s-2",
            "retrieve payment",
            {"output": "completely unrelated jargon here"},
        ),
    ]
    report = compute_drift(_make_trace(steps))
    assert 0.1 < report.drift_score < 0.9
    assert "s-1" not in report.flagged_steps
    assert "s-2" in report.flagged_steps


# ---------------------------------------------------------------------------
# Test 5: method and method_version constants are correct
# ---------------------------------------------------------------------------
def test_drift_report_method_fields():
    steps = [_make_step("s-1", None, {})]
    report = compute_drift(_make_trace(steps))
    assert report.method == "plan-action-similarity"
    assert report.method_version == "0.1"


# ---------------------------------------------------------------------------
# Test 6: determinism — two calls on the same trace produce identical reports
# ---------------------------------------------------------------------------
def test_compute_drift_is_deterministic():
    steps = [
        _make_step("s-1", "order lookup", {"order": "lookup"}),
        _make_step(
            "s-2",
            "retrieve payment",
            {"output": "completely unrelated jargon here"},
        ),
    ]
    trace = _make_trace(steps)
    report_a = compute_drift(trace)
    report_b = compute_drift(trace)
    assert report_a == report_b


# ---------------------------------------------------------------------------
# Test 7: per_step populated by JaccardScorer
# ---------------------------------------------------------------------------
def test_jaccard_per_step_populated():
    steps = [
        _make_step("s-1", "order lookup", {"order": "lookup"}),
        _make_step(
            "s-2",
            "retrieve payment",
            {"output": "completely unrelated jargon here"},
        ),
    ]
    report = compute_drift(_make_trace(steps))
    assert report.per_step is not None
    assert "s-1" in report.per_step
    assert "s-2" in report.per_step
    assert report.per_step["s-1"].label.value == "faithful"
    assert report.per_step["s-2"].label.value == "goal_deviation"
    assert "Jaccard" in report.per_step["s-1"].reasoning
    assert "Jaccard" in report.per_step["s-2"].reasoning
