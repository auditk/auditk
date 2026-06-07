"""Intent–enactment drift detector.

Deprecated entry-point — retained for backward compatibility.  The algorithm
has moved into pluggable Scorer implementations; this module delegates to a
configurable scorer key.
"""

from __future__ import annotations

from auditk.analysis.scorers import DEFAULT_SCORER, get_scorer
from auditk.schema import DriftReport, Trace


def compute_drift(trace: Trace, scorer_key: str | None = None) -> DriftReport:
    """Deprecated alias — delegates to the specified scorer."""
    key = scorer_key or DEFAULT_SCORER
    return get_scorer(key).score(trace)
