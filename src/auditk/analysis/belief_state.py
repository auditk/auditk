# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Belief-state extractor — v0.1 heuristic method.

Infers a BeliefState for every Step in a Trace using only the information
already present in the trace (no I/O, no randomness).
"""

from __future__ import annotations

from auditk.schema import BeliefState, ContextRef, Step, Trace


def _first_declared_intent(steps: list[Step]) -> str | None:
    for step in steps:
        if step.declared_intent is not None:
            return step.declared_intent
    return None


def _active_plan(steps: list[Step]) -> list[str]:
    return [s.declared_intent for s in steps if s.declared_intent is not None]


def _salient_facts(context_used: list[ContextRef]) -> dict[str, str | None]:
    return {ref.identifier: ref.excerpt for ref in context_used}


def extract_belief_state(trace: Trace) -> list[BeliefState]:
    """Infer a BeliefState for every Step in the trace.

    v0.1-heuristic method:
    - declared_goal: first non-null declared_intent in the trace (same for all steps)
    - active_plan: list of all declared_intent strings in trace order (skip Nones)
    - salient_facts: dict of ContextRef.identifier -> excerpt for each step
    - confidence: None
    """
    declared_goal = _first_declared_intent(trace.steps)
    active_plan = _active_plan(trace.steps)

    return [
        BeliefState(
            declared_goal=declared_goal,
            active_plan=active_plan,
            salient_facts=_salient_facts(step.context_used),
            confidence=None,
        )
        for step in trace.steps
    ]
