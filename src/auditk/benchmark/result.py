# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark result data model.

Captures the outcome of one model-run on one benchmark task,
including the raw trace, attestation path, and computed metrics.
"""

from __future__ import annotations

from dataclasses import dataclass

from auditk.benchmark.task import BenchmarkTask
from auditk.schema import Trace


@dataclass
class BenchmarkResult:
    model_id: str
    task: BenchmarkTask
    trace: Trace
    evidence_pack_path: str | None
    completion_fraction: float
    drift_score: float | None
