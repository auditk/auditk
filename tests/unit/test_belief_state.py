# Copyright 2026 Matt Haiko and the glasshouse Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for glasshouse_core.analysis.belief_state.extract_belief_state."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from glasshouse_core.adapters.langgraph import ingest_checkpoints
from glasshouse_core.adapters.generic_otel import ingest_otel_spans
from glasshouse_core.analysis import extract_belief_state
from glasshouse_core.schema import (
    Action,
    ActionType,
    Actor,
    BeliefState,
    ContextRef,
    FlowType,
    Step,
    Trace,
)

_FIXTURES_LANGGRAPH = Path(__file__).parent.parent / "fixtures" / "langgraph"
_FIXTURES_OTEL = Path(__file__).parent.parent / "fixtures" / "otel"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    step_id: str,
    declared_intent: str | None = None,
    context_used: list[ContextRef] | None = None,
) -> Step:
    return Step(
        step_id=step_id,
        trace_id="trace-test-001",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        actor=Actor.AGENT,
        declared_intent=declared_intent,
        action=Action(type=ActionType.STATE_TRANSITION, payload={}),
        context_used=context_used or [],
    )


def _make_trace(steps: list[Step]) -> Trace:
    return Trace(
        trace_id="trace-test-001",
        flow_type=FlowType.GENERIC,
        agent_config_ref="test-agent",
        steps=steps,
        source_adapter="test",
    )


# ---------------------------------------------------------------------------
# Test 1: No declared_intent on any step
# ---------------------------------------------------------------------------


class TestNoIntent:
    def test_all_declared_goal_none(self) -> None:
        trace = _make_trace([
            _make_step("s1"),
            _make_step("s2"),
            _make_step("s3"),
        ])
        results = extract_belief_state(trace)
        assert all(bs.declared_goal is None for bs in results)

    def test_active_plan_empty(self) -> None:
        trace = _make_trace([_make_step("s1"), _make_step("s2")])
        results = extract_belief_state(trace)
        assert all(bs.active_plan == [] for bs in results)


# ---------------------------------------------------------------------------
# Test 2: declared_intent on step 2 only
# ---------------------------------------------------------------------------


class TestIntentOnSecondStep:
    def test_declared_goal_is_step2_intent(self) -> None:
        steps = [
            _make_step("s1", declared_intent=None),
            _make_step("s2", declared_intent="clarify_query"),
            _make_step("s3", declared_intent=None),
        ]
        trace = _make_trace(steps)
        results = extract_belief_state(trace)
        assert all(bs.declared_goal == "clarify_query" for bs in results)

    def test_active_plan_contains_only_step2(self) -> None:
        steps = [
            _make_step("s1", declared_intent=None),
            _make_step("s2", declared_intent="clarify_query"),
            _make_step("s3", declared_intent=None),
        ]
        trace = _make_trace(steps)
        results = extract_belief_state(trace)
        assert all(bs.active_plan == ["clarify_query"] for bs in results)


# ---------------------------------------------------------------------------
# Test 3: Intent on steps 1 and 3
# ---------------------------------------------------------------------------


class TestIntentOnFirstAndThird:
    def test_declared_goal_is_step1_intent(self) -> None:
        steps = [
            _make_step("s1", declared_intent="search_products"),
            _make_step("s2", declared_intent=None),
            _make_step("s3", declared_intent="confirm_order"),
        ]
        trace = _make_trace(steps)
        results = extract_belief_state(trace)
        assert all(bs.declared_goal == "search_products" for bs in results)

    def test_active_plan_is_both_intents_in_order(self) -> None:
        steps = [
            _make_step("s1", declared_intent="search_products"),
            _make_step("s2", declared_intent=None),
            _make_step("s3", declared_intent="confirm_order"),
        ]
        trace = _make_trace(steps)
        results = extract_belief_state(trace)
        assert all(bs.active_plan == ["search_products", "confirm_order"] for bs in results)


# ---------------------------------------------------------------------------
# Test 4: salient_facts from context_used
# ---------------------------------------------------------------------------


class TestSalientFacts:
    def test_salient_facts_contains_identifier_excerpt_pairs(self) -> None:
        refs = [
            ContextRef(source="retrieval", identifier="doc-001", excerpt="First excerpt"),
            ContextRef(source="retrieval", identifier="doc-002", excerpt="Second excerpt"),
        ]
        steps = [_make_step("s1", context_used=refs)]
        trace = _make_trace(steps)
        results = extract_belief_state(trace)
        assert results[0].salient_facts == {
            "doc-001": "First excerpt",
            "doc-002": "Second excerpt",
        }

    def test_empty_context_gives_empty_salient_facts(self) -> None:
        trace = _make_trace([_make_step("s1")])
        results = extract_belief_state(trace)
        assert results[0].salient_facts == {}

    def test_salient_facts_none_excerpt_stored_as_none(self) -> None:
        refs = [ContextRef(source="kb", identifier="item-99", excerpt=None)]
        steps = [_make_step("s1", context_used=refs)]
        trace = _make_trace(steps)
        results = extract_belief_state(trace)
        assert results[0].salient_facts == {"item-99": None}


# ---------------------------------------------------------------------------
# Test 5: Return list length == len(trace.steps)
# ---------------------------------------------------------------------------


class TestReturnLength:
    @pytest.mark.parametrize("n", [0, 1, 3, 7])
    def test_length_matches_step_count(self, n: int) -> None:
        trace = _make_trace([_make_step(f"s{i}") for i in range(n)])
        results = extract_belief_state(trace)
        assert len(results) == n

    def test_returns_belief_state_objects(self) -> None:
        trace = _make_trace([_make_step("s1"), _make_step("s2")])
        results = extract_belief_state(trace)
        assert all(isinstance(bs, BeliefState) for bs in results)


# ---------------------------------------------------------------------------
# Fixture-based tests using realistic Traces
# ---------------------------------------------------------------------------


class TestWithLangGraphFixture:
    """Use the intent fixture which has declared_intent on step 1."""

    def test_declared_goal_from_fixture(self) -> None:
        raw = json.loads((_FIXTURES_LANGGRAPH / "checkpoint-list-with-intent.json").read_text())
        trace = ingest_checkpoints(raw)
        results = extract_belief_state(trace)
        # Step index 1 has intent "in_scope"; that becomes the declared_goal for all
        assert all(bs.declared_goal == "in_scope" for bs in results)

    def test_active_plan_from_fixture(self) -> None:
        raw = json.loads((_FIXTURES_LANGGRAPH / "checkpoint-list-with-intent.json").read_text())
        trace = ingest_checkpoints(raw)
        results = extract_belief_state(trace)
        # Only steps[1] and steps[2] have intent="in_scope" (both non-None)
        non_none_intents = [s.declared_intent for s in trace.steps if s.declared_intent is not None]
        assert all(bs.active_plan == non_none_intents for bs in results)

    def test_result_count_matches_step_count(self) -> None:
        raw = json.loads((_FIXTURES_LANGGRAPH / "checkpoint-list-with-intent.json").read_text())
        trace = ingest_checkpoints(raw)
        results = extract_belief_state(trace)
        assert len(results) == len(trace.steps)


class TestWithOtelFixture:
    """Use the retrieval fixture which has ContextRef entries."""

    def test_salient_facts_populated_from_retrieval_span(self) -> None:
        raw = json.loads((_FIXTURES_OTEL / "span-list-agent-with-retrieval.json").read_text())
        trace = ingest_otel_spans(raw)
        results = extract_belief_state(trace)
        # retriever001 step has two context refs; find it in results
        retriever_step_idx = next(
            i for i, s in enumerate(trace.steps) if s.context_used
        )
        assert results[retriever_step_idx].salient_facts  # non-empty

    def test_result_count_matches_span_count(self) -> None:
        raw = json.loads((_FIXTURES_OTEL / "span-list-agent-with-retrieval.json").read_text())
        trace = ingest_otel_spans(raw)
        results = extract_belief_state(trace)
        assert len(results) == len(trace.steps)
