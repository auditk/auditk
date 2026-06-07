"""Intent-enactment taxonomy labels and rubric types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaxonomyLabel(str, Enum):
    """Five-way classification of a step's relationship to the declared plan."""

    FAITHFUL = "faithful"
    BENIGN_ELABORATION = "benign_elaboration"
    UNDECLARED_GOAL = "undeclared_goal"
    GOAL_DEVIATION = "goal_deviation"
    INSTRUCTION_NONCOMPLIANCE = "instruction_noncompliance"


@dataclass(frozen=True)
class RubricResult:
    """Outcome of a single judge adjudication."""

    label: TaxonomyLabel
    confidence: float
    reasoning: str
