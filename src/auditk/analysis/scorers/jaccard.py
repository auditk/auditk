"""Jaccard-based intent–enactment scorer (plan-action-similarity@0.1).

Deprecated — retained as the default baseline scorer for backward compatibility.
Algorithm: Jaccard similarity between declared_intent tokens and
str(action.payload) tokens, then drift_score = 1 - mean(s_i).
"""

from __future__ import annotations

import re
from statistics import mean

from auditk.schema import DriftReport, Trace

_METHOD = "plan-action-similarity"
_METHOD_VERSION = "0.1"
_FLAG_THRESHOLD = 0.3


def _tokenise(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


class JaccardScorer:
    """Deprecated baseline scorer: symmetric lexical overlap between intent and action.

    Retained as the default scorer for backward compatibility. New implementations
    should use the Scorer protocol and register under a method-specific key.
    """

    method = _METHOD
    method_version = _METHOD_VERSION

    def score(self, trace: Trace) -> DriftReport:
        scores: list[float] = []
        flagged: list[str] = []

        for step in trace.steps:
            if step.declared_intent is None:
                continue

            intent_tokens = _tokenise(step.declared_intent)
            payload_tokens = _tokenise(str(step.action.payload))
            s_i = _jaccard(intent_tokens, payload_tokens)
            scores.append(s_i)

            if s_i < _FLAG_THRESHOLD:
                flagged.append(step.step_id)

        drift_score = 0.0 if not scores else 1.0 - mean(scores)

        return DriftReport(
            drift_score=drift_score,
            drift_per_trace={trace.trace_id: drift_score},
            flagged_steps=flagged,
            method=_METHOD,
            method_version=_METHOD_VERSION,
        )
