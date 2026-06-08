"""Intent–enactment drift detector.

Thin delegation layer: `compute_drift` is the stable public entry-point used
by `attest` and external callers.  Scoring logic lives in pluggable Scorer
implementations under `analysis/scorers/`; this module routes to the
configured scorer key.
"""

from __future__ import annotations

from auditk.analysis.scorers import DEFAULT_SCORER, get_scorer
from auditk.schema import DriftReport, Trace


def compute_drift(trace: Trace, scorer_key: str | None = None) -> DriftReport:
    """Score intent–enactment drift for a trace using the given scorer key."""
    key = scorer_key or DEFAULT_SCORER
    return get_scorer(key).score(trace)
