"""Intent–enactment drift detector.

Deprecated entry-point — retained for backward compatibility.  The algorithm
has moved into pluggable Scorer implementations; this module delegates to the
default (jaccard) scorer.
"""

from __future__ import annotations

from auditk.analysis.scorers import DEFAULT_SCORER, get_scorer
from auditk.schema import DriftReport, Trace


def compute_drift(trace: Trace) -> DriftReport:
    """Deprecated alias — delegates to the default scorer."""
    return get_scorer(DEFAULT_SCORER).score(trace)
