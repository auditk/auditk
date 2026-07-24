#!/usr/bin/env python3
# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Phase 1 (Track A) of the seeded-drift positive-control experiment.

docs/proposals/positive-control-experiment.md. Runs entirely over the 40
existing benchmark_results/<model>/<seed>/{trace.json,pack.json} artifacts —
no new benchmark runs, no LLM calls. For each session:

  1. Extract ground truth (`occurred`, offending `step_id`) via
     auditk.analysis.ground_truth, deterministically from trace.json.
  2. Read what auditk's pack.json already says about that step (or, for
     clean-control sessions, about the whole trace) — flagged label,
     gate-vs-judge routing.
  3. Cross-tabulate: detection rate (recall on positives, with Wilson 95%
     CI), false-positive rate on clean sessions, and gate-recall vs
     judge-recall attribution on the positive set.

Usage: python scripts/positive_control_phase1.py [--results-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auditk.analysis.ground_truth import (
    CLEAN_CONTROL_SEEDS,
    EXPECTED_LABELS,
    SEED_EXTRACTORS,
    GroundTruthResult,
    extract_ground_truth,
)
from auditk.schema import Trace

_DEFAULT_RESULTS_DIR = Path(__file__).parent.parent / "benchmark_results"
_ALL_SEEDS = sorted(SEED_EXTRACTORS) + sorted(CLEAN_CONTROL_SEEDS)


@dataclass(frozen=True)
class GateJudgeAttribution:
    """How the offending step was (or wasn't) routed through the pipeline."""

    scored: bool  # declared_intent was non-None → gate saw it at all
    gate_routed_to_judge: bool  # gate said "contradict" → became a judge candidate
    judged: bool  # an actual judge call happened (not gate-only, not budget-exhausted)
    final_label: str | None


@dataclass(frozen=True)
class SessionResult:
    model: str
    seed: str
    ground_truth: GroundTruthResult
    drift_score: float
    flagged_steps: tuple[str, ...]
    per_step: dict[str, dict[str, Any]]

    @property
    def occurred(self) -> bool:
        return self.ground_truth.occurred

    @property
    def session_flagged(self) -> bool:
        """Any step in the session flagged as drift — used for the FP-rate denominator."""
        return len(self.flagged_steps) > 0

    @property
    def offending_step_flagged(self) -> bool | None:
        """Was the specific ground-truth-offending step flagged? None if not applicable."""
        if not self.occurred or self.ground_truth.step_id is None:
            return None
        return self.ground_truth.step_id in self.flagged_steps

    @property
    def offending_label_matches_expected(self) -> bool | None:
        if not self.occurred or self.ground_truth.step_id is None:
            return None
        step = self.per_step.get(self.ground_truth.step_id)
        if step is None:
            return None
        expected = EXPECTED_LABELS.get(self.seed, frozenset())
        return step.get("label") in {label.value for label in expected}

    def gate_judge_attribution(self) -> GateJudgeAttribution | None:
        if not self.occurred or self.ground_truth.step_id is None:
            return None
        step = self.per_step.get(self.ground_truth.step_id)
        if step is None:
            return GateJudgeAttribution(False, False, False, None)
        reasoning = str(step.get("reasoning", ""))
        if reasoning.startswith("NLI gate: contradict (unjudged"):
            return GateJudgeAttribution(True, True, False, step.get("label"))
        if reasoning.startswith("NLI gate:"):
            return GateJudgeAttribution(True, False, False, step.get("label"))
        return GateJudgeAttribution(True, True, True, step.get("label"))


def _load_session(model_dir: Path, seed_dir: Path) -> SessionResult:
    trace = Trace.model_validate(json.loads((seed_dir / "trace.json").read_text()))
    pack = json.loads((seed_dir / "pack.json").read_text())
    drift = pack["drift_metrics"]

    seed = seed_dir.name
    if seed in CLEAN_CONTROL_SEEDS:
        gt = GroundTruthResult(False, None)
    else:
        gt = extract_ground_truth(seed, trace)

    return SessionResult(
        model=model_dir.name,
        seed=seed,
        ground_truth=gt,
        drift_score=drift["drift_score"],
        flagged_steps=tuple(drift.get("flagged_steps", [])),
        per_step=drift.get("per_step") or {},
    )


def load_all_sessions(results_dir: Path) -> list[SessionResult]:
    sessions = []
    for model_dir in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        for seed_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
            if not (seed_dir / "trace.json").exists():
                continue
            sessions.append(_load_session(model_dir, seed_dir))
    return sessions


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    """Wilson score 95% CI for a binomial proportion. Returns (point, lo, hi)."""
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    lo = (centre - spread) / denom
    hi = (centre + spread) / denom
    return (p, max(0.0, lo), min(1.0, hi))


def fmt_rate(k: int, n: int) -> str:
    if n == 0:
        return f"{k}/{n} (undefined — no denominator)"
    p, lo, hi = wilson_ci(k, n)
    return f"{k}/{n} = {p:.1%}  (95% CI {lo:.1%}–{hi:.1%})"


def print_report(sessions: list[SessionResult]) -> None:
    print(f"Loaded {len(sessions)} sessions from benchmark_results/\n")

    positives = [s for s in sessions if s.occurred]
    negatives = [s for s in sessions if not s.occurred]

    print("=== Ground truth ===")
    print(f"Ground-truth positives (occurred=True): {len(positives)} / {len(sessions)}")
    print(f"Clean sessions (occurred=False):        {len(negatives)} / {len(sessions)}\n")

    print("=== Per-seed occurrence ===")
    for seed in _ALL_SEEDS:
        seed_sessions = [s for s in sessions if s.seed == seed]
        n_occ = sum(1 for s in seed_sessions if s.occurred)
        tag = "clean control" if seed in CLEAN_CONTROL_SEEDS else "seeded condition"
        print(f"  {seed:22s} occurred {n_occ}/{len(seed_sessions)}  ({tag})")
    print()

    print("=== Detection rate (recall on positives) ===")
    det_flagged = sum(1 for s in positives if s.offending_step_flagged)
    print(f"Overall (any drift label on offending step): {fmt_rate(det_flagged, len(positives))}")
    label_match = sum(1 for s in positives if s.offending_label_matches_expected)
    print(f"Overall (expected taxonomy label):            {fmt_rate(label_match, len(positives))}")
    print("Per category:")
    for seed in sorted(SEED_EXTRACTORS):
        seed_pos = [s for s in positives if s.seed == seed]
        k = sum(1 for s in seed_pos if s.offending_step_flagged)
        print(f"  {seed:22s} {fmt_rate(k, len(seed_pos))}")
    print()

    print("=== False-positive rate (flagged / not-occurred) ===")
    fp = sum(1 for s in negatives if s.session_flagged)
    print(fmt_rate(fp, len(negatives)))
    if fp:
        print("Flagged-but-clean sessions:")
        for s in negatives:
            if s.session_flagged:
                print(
                    f"  {s.model}/{s.seed}: drift_score={s.drift_score:.4f} "
                    f"flagged={s.flagged_steps}"
                )
    print()

    print("=== Gate recall vs judge recall (on positives) ===")
    if not positives:
        print("N/A — zero ground-truth positives in the current 40 sessions.")
    else:
        for s in positives:
            attr = s.gate_judge_attribution()
            print(f"  {s.model}/{s.seed}: {attr}")
    print()

    print("=== Sanity check: known non-zero-scoring sessions ===")
    known = [("claude", "format-strict"), ("kimi", "distractor"), ("kimi", "priority-constraint")]
    for model, seed in known:
        match = next(s for s in sessions if s.model == model and s.seed == seed)
        print(
            f"  {model}/{seed}: occurred={match.occurred} "
            f"step_id={match.ground_truth.step_id} drift_score={match.drift_score:.4f} "
            f"flagged_steps={match.flagged_steps}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=_DEFAULT_RESULTS_DIR)
    args = parser.parse_args()
    sessions = load_all_sessions(args.results_dir)
    print_report(sessions)


if __name__ == "__main__":
    main()
