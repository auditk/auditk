"""Deterministic counterfactual replay for agent traces (T3.3)."""

from __future__ import annotations

import json
from collections.abc import Callable

from glasshouse_core.schema import Action, CounterfactualResult, Step, Trace


def replay(
    original_trace: Trace,
    alternate_policy: Callable[[Step], Action],
) -> CounterfactualResult:
    """Deterministic substitution replay.

    For each Step in original_trace:
    - Ask alternate_policy(step) for the action it would have taken
    - If alternate_policy returns the same action.type AND payload: no divergence at this step
    - If different: record as a divergence entry
    - divergence_step = step_id of the FIRST diverging step (None if no divergence)
    - diff_summary = JSON string of list of {step_id, original_action, alternate_action}
      for diverging steps only; "[]" if none
    """
    divergences: list[tuple[Step, Action]] = []

    for step in original_trace.steps:
        alt = alternate_policy(step)
        if not _actions_equal(step.action, alt):
            divergences.append((step, alt))

    divergence_step = divergences[0][0].step_id if divergences else None
    diff_summary = _build_diff_summary(divergences)

    return CounterfactualResult(
        original_trace_id=original_trace.trace_id,
        counterfactual_policy="alternate_policy",
        diff_summary=diff_summary,
        divergence_step=divergence_step,
    )


def _actions_equal(a: Action, b: Action) -> bool:
    return a.type == b.type and a.payload == b.payload


def _build_diff_summary(divergences: list[tuple[Step, Action]]) -> str:
    return json.dumps(
        [
            {
                "step_id": step.step_id,
                "original_action": {
                    "type": step.action.type.value,
                    "payload": step.action.payload,
                },
                "alternate_action": {
                    "type": alt.type.value,
                    "payload": alt.payload,
                },
            }
            for step, alt in divergences
        ]
    )
