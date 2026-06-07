"""Scorer registry — mirrors adapters/registry.py.

String keys are "{method}@{method_version}".  Base install is light; the
nli@0.2 scorer is loaded lazily via a factory so importing the registry
never imports torch/transformers.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

from auditk.analysis.scorers.jaccard import JaccardScorer

if TYPE_CHECKING:
    from auditk.analysis.protocols import Scorer

__all__ = ["DEFAULT_SCORER", "get_scorer", "available"]

DEFAULT_SCORER = "plan-action-similarity@0.1"

_REGISTRY: dict[str, JaccardScorer] = {
    DEFAULT_SCORER: JaccardScorer(),
}

_NLI_INSTALL_HINT = (
    "The nli@0.2 scorer requires the [nli] extra. Install with: pip install auditk[nli]"
)


class _TransformersNLIPredictor:
    """Real NLI predictor wrapping a pre-created transformers pipeline."""

    def __init__(self, classifier: Any) -> None:
        self._classifier = classifier

    def predict(self, premise: str, hypothesis: str) -> tuple[float, float, float]:
        sequence = f"{premise} </s></s> {hypothesis}"
        raw_result: Any = self._classifier(sequence, truncation=True)
        result = cast(list[list[dict[str, Any]]], raw_result)
        scores: dict[str, float] = {str(item["label"]): float(item["score"]) for item in result[0]}
        return (
            scores.get("contradiction", 0.0),
            scores.get("entailment", 0.0),
            scores.get("neutral", 0.0),
        )


def _load_nli_scorer() -> Scorer:
    """Load the nli@0.2 scorer with a real model predictor."""
    try:
        import transformers
    except ImportError as exc:
        raise ImportError(_NLI_INSTALL_HINT) from exc

    if not os.environ.get("RUN_NLI_MODEL"):
        raise ImportError(_NLI_INSTALL_HINT)

    from auditk.analysis.scorers.nli import NLIScorer

    classifier = transformers.pipeline(
        "text-classification",
        model="cross-encoder/nli-deberta-v3-small",
        revision="fa2804872c3b4bd748f38c0185cc85775361e735",
        device="cpu",
        top_k=None,
        local_files_only=True,
    )
    return NLIScorer(predictor=_TransformersNLIPredictor(classifier))


def get_scorer(key: str = DEFAULT_SCORER) -> Scorer:
    if key == "nli@0.2":
        return _load_nli_scorer()
    if key not in _REGISTRY:
        raise KeyError(f"Unknown scorer {key!r}. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[key]


def available() -> list[str]:
    return sorted(_REGISTRY)
