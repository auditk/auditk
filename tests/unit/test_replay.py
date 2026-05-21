"""Unit tests for counterfactual replay (T3.3)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from glasshouse_core.schema import (
    Action,
    ActionType,
    Actor,
    CounterfactualResult,
    FlowType,
    Outcome,
    Step,
    Trace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(step_id: str, trace_id: str, action_type: ActionType, payload: dict) -> Step:
    return Step(
        step_id=step_id,
        trace_id=trace_id,
        timestamp=datetime(2024, 1, 1, 0, 0, 0),
        actor=Actor.AGENT,
        action=Action(type=action_type, payload=payload),
    )


def _make_trace(trace_id: str, steps: list[Step]) -> Trace:
    return Trace(
        trace_id=trace_id,
        flow_type=FlowType.GENERIC,
        agent_config_ref="cfg-1",
        steps=steps,
        source_adapter="test",
        outcome=Outcome(status="success"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_divergence_returns_none_divergence_step() -> None:
    """Policy returning same action for every step -> no divergence."""
    steps = [
        _make_step("s0", "t-1", ActionType.UTTERANCE, {"text": "hello"}),
        _make_step("s1", "t-1", ActionType.TOOL_CALL, {"name": "search"}),
        _make_step("s2", "t-1", ActionType.STATE_TRANSITION, {"from": "a", "to": "b"}),
    ]
    trace = _make_trace("t-1", steps)

    # Policy always returns the same action as the original step
    result = _replay_same_policy(trace)

    assert result.divergence_step is None
    assert result.diff_summary == "[]"


def test_divergence_at_third_step() -> None:
    """Policy that diverges at step index 2 -> divergence_step == s2's step_id."""
    steps = [
        _make_step("s0", "t-1", ActionType.UTTERANCE, {"text": "hello"}),
        _make_step("s1", "t-1", ActionType.TOOL_CALL, {"name": "search"}),
        _make_step("s2", "t-1", ActionType.UTTERANCE, {"text": "original"}),
    ]
    trace = _make_trace("t-1", steps)

    def policy(step: Step) -> Action:
        if step.step_id == "s2":
            return Action(type=ActionType.UTTERANCE, payload={"text": "alternate"})
        return step.action

    from glasshouse_core.analysis.replay import replay

    result = replay(trace, policy)

    assert result.divergence_step == "s2"
    diffs = json.loads(result.diff_summary)
    assert len(diffs) == 1
    assert diffs[0]["step_id"] == "s2"


def test_divergence_at_all_steps() -> None:
    """Policy that changes all steps -> divergence_step is first step's id, len(diffs) == len(steps)."""
    steps = [
        _make_step("s0", "t-1", ActionType.UTTERANCE, {"text": "a"}),
        _make_step("s1", "t-1", ActionType.UTTERANCE, {"text": "b"}),
        _make_step("s2", "t-1", ActionType.UTTERANCE, {"text": "c"}),
    ]
    trace = _make_trace("t-1", steps)

    def always_different(step: Step) -> Action:
        return Action(type=ActionType.TOOL_CALL, payload={"name": "override"})

    from glasshouse_core.analysis.replay import replay

    result = replay(trace, always_different)

    assert result.divergence_step == "s0"
    diffs = json.loads(result.diff_summary)
    assert len(diffs) == 3


def test_single_step_no_divergence() -> None:
    """Single step trace, policy returns same action -> no divergence."""
    steps = [_make_step("s0", "t-1", ActionType.ENV_EFFECT, {"key": "val"})]
    trace = _make_trace("t-1", steps)

    result = _replay_same_policy(trace)

    assert result.divergence_step is None
    assert result.diff_summary == "[]"


def test_diff_summary_is_valid_json() -> None:
    """diff_summary is always parseable JSON."""
    steps = [
        _make_step("s0", "t-1", ActionType.UTTERANCE, {"x": 1}),
        _make_step("s1", "t-1", ActionType.TOOL_CALL, {}),
    ]
    trace = _make_trace("t-1", steps)

    def diverge_at_s1(step: Step) -> Action:
        if step.step_id == "s1":
            return Action(type=ActionType.STATE_TRANSITION, payload={"alt": True})
        return step.action

    from glasshouse_core.analysis.replay import replay

    result = replay(trace, diverge_at_s1)

    # Should not raise
    parsed = json.loads(result.diff_summary)
    assert isinstance(parsed, list)


def test_original_trace_id_set_correctly() -> None:
    """original_trace_id must match the provided trace's trace_id."""
    steps = [_make_step("s0", "my-trace-42", ActionType.UTTERANCE, {})]
    trace = _make_trace("my-trace-42", steps)

    result = _replay_same_policy(trace)

    assert result.original_trace_id == "my-trace-42"


# ---------------------------------------------------------------------------
# Helper: uses same policy (no divergence) to reduce repetition
# ---------------------------------------------------------------------------


def _replay_same_policy(trace: Trace) -> CounterfactualResult:
    from glasshouse_core.analysis.replay import replay

    return replay(trace, lambda step: step.action)
