"""Red-phase tests for benchmark/result.py.

Tests the BenchmarkResult dataclass and its fields.
"""

import pytest

from auditk.benchmark.result import BenchmarkResult
from auditk.benchmark.task import BenchmarkTask, BENCHMARK_TASKS
from auditk.schema import Trace, FlowType, ActionType, Actor, Action, Step
from datetime import datetime, UTC


def _make_dummy_trace() -> Trace:
    return Trace(
        trace_id="test-trace",
        flow_type=FlowType.CODE,
        agent_config_ref="test-config",
        steps=[
            Step(
                step_id="s1",
                trace_id="test-trace",
                timestamp=datetime.now(UTC),
                actor=Actor.USER,
                action=Action(type=ActionType.UTTERANCE, payload={"text": "hello"}),
            )
        ],
        source_adapter="benchmark-api",
    )


def test_benchmark_result_all_fields() -> None:
    task = BENCHMARK_TASKS[0]
    trace = _make_dummy_trace()
    result = BenchmarkResult(
        model_id="claude-sonnet-4-6",
        task=task,
        trace=trace,
        evidence_pack_path="benchmarks/sessions/claude-s1/evidence-pack.json",
        completion_fraction=1.0,
        drift_score=0.05,
    )
    assert result.model_id == "claude-sonnet-4-6"
    assert result.task == task
    assert result.trace == trace
    assert result.evidence_pack_path == "benchmarks/sessions/claude-s1/evidence-pack.json"
    assert result.completion_fraction == 1.0
    assert result.drift_score == 0.05


def test_benchmark_result_optional_fields_none() -> None:
    task = BENCHMARK_TASKS[0]
    trace = _make_dummy_trace()
    result = BenchmarkResult(
        model_id="kimi-k2p6",
        task=task,
        trace=trace,
        evidence_pack_path=None,
        completion_fraction=0.6,
        drift_score=None,
    )
    assert result.evidence_pack_path is None
    assert result.drift_score is None
    assert result.completion_fraction == 0.6


def test_benchmark_result_drift_score_is_optional() -> None:
    task = BENCHMARK_TASKS[0]
    trace = _make_dummy_trace()
    result = BenchmarkResult(
        model_id="minimax-m2.7",
        task=task,
        trace=trace,
        evidence_pack_path="pack.json",
        completion_fraction=0.4,
        drift_score=None,
    )
    assert result.drift_score is None


def test_benchmark_result_evidence_pack_path_is_optional() -> None:
    task = BENCHMARK_TASKS[0]
    trace = _make_dummy_trace()
    result = BenchmarkResult(
        model_id="deepseek-v4-pro",
        task=task,
        trace=trace,
        evidence_pack_path=None,
        completion_fraction=0.2,
        drift_score=0.15,
    )
    assert result.evidence_pack_path is None


def test_benchmark_result_completion_fraction_zero() -> None:
    task = BENCHMARK_TASKS[0]
    trace = _make_dummy_trace()
    result = BenchmarkResult(
        model_id="test-model",
        task=task,
        trace=trace,
        evidence_pack_path=None,
        completion_fraction=0.0,
        drift_score=0.0,
    )
    assert result.completion_fraction == 0.0


def test_benchmark_result_trace_source_adapter() -> None:
    task = BENCHMARK_TASKS[0]
    trace = _make_dummy_trace()
    result = BenchmarkResult(
        model_id="test-model",
        task=task,
        trace=trace,
        evidence_pack_path=None,
        completion_fraction=1.0,
        drift_score=0.0,
    )
    assert result.trace.source_adapter == "benchmark-api"
