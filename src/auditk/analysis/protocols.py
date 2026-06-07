"""Pluggable scorer protocols for auditk.

Each Protocol defines a boundary that can be satisfied by any concrete class
(structural subtyping / PEP 544).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from auditk.analysis.taxonomy import RubricResult
from auditk.schema import DriftReport, Trace


@runtime_checkable
class Scorer(Protocol):
    """Compute an intent-enactment DriftReport from a single Trace.

    Every Scorer MUST expose a stable (method, method_version) identity; these
    are pinned into the signed evidence pack via DriftReport.
    """

    method: str
    method_version: str

    def score(self, trace: Trace) -> DriftReport: ...


class NLIPredictor(Protocol):
    """Three-valued NLI over (premise, hypothesis). Injected into NLIScorer so
    the scorer is testable without loading model weights."""

    def predict(self, premise: str, hypothesis: str) -> tuple[float, float, float]:
        """Return (p_contra, p_entail, p_neutral), summing to ~1.0."""
        ...


@runtime_checkable
class Judge(Protocol):
    """Adjudicate a single step that has been flagged by the NLI gate.

    A judge must be deterministic (temperature=0.0) and disclose its identity
    so that scorer fingerprints can be pinned into the signed evidence pack.
    """

    model_id: str
    temperature: float

    def adjudicate(
        self,
        step_id: str,
        declared_intent: str,
        action_text: str,
        gate_label: str,
    ) -> RubricResult:
        """Return a structured rubric result for the given step."""
        ...
