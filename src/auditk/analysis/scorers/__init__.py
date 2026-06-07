"""Scorer registry — mirrors adapters/registry.py.

String keys are "{method}@{method_version}".  Base install is light; the
nli@0.2 scorer is loaded lazily via a factory so importing the registry
never imports torch/transformers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from auditk.analysis.scorers.jaccard import JaccardScorer

if TYPE_CHECKING:
    from auditk.analysis.protocols import Scorer

__all__ = ["DEFAULT_SCORER", "get_scorer", "available"]

DEFAULT_SCORER = "plan-action-similarity@0.1"

_REGISTRY: dict[str, JaccardScorer] = {
    DEFAULT_SCORER: JaccardScorer(),
}


def get_scorer(key: str = DEFAULT_SCORER) -> "Scorer":
    if key not in _REGISTRY:
        raise KeyError(
            f"Unknown scorer {key!r}. Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]


def available() -> list[str]:
    return sorted(_REGISTRY)
