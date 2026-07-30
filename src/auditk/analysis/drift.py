"""Intent-enactment drift detector.

Thin delegation layer: `compute_drift` is the stable public entry-point used
by `attest` and external callers.  Scoring logic lives in pluggable Scorer
implementations under `analysis/scorers/`; this module routes to the
configured scorer key.

Delegate (subagent) steps -- flattened into the parent trace by the
claude-code adapter (`adapters/claude_code.py`, P3 of the
cc-adapter-integrity work), each carrying `metadata["agent_id"]` -- are
excluded from the parent trace-level score computed here (D6): a delegate
is scored against its OWN brief, not the parent's declared intent, so
blending its steps into the parent's score would corrupt a number that
already has a documented false-positive history. The exclusion lives here,
once, rather than in each `Scorer` implementation, so no `Scorer` (current
or future) ever needs to know delegates exist; `attestation/pack.py`'s
`drift_metrics` inherits the fix with no changes of its own, since it only
ever calls `compute_drift`. Structural findings (`analysis/findings.py`)
are untouched by this: delegate steps stay fully visible there, only the
numeric drift score excludes them.
"""

from __future__ import annotations

from auditk.analysis.scorers import DEFAULT_SCORER, get_scorer
from auditk.schema import DriftReport, Step, Trace


def _is_delegate_step(step: Step) -> bool:
    """Whether `step` was flattened in from a subagent transcript (P3)."""
    return bool(step.metadata.get("agent_id"))


def _exclude_delegate_steps(trace: Trace) -> Trace:
    """A view of `trace` containing only its own (non-delegate) steps.

    Returns `trace` itself, unmodified, when there is nothing to exclude
    (the common case: no delegate steps at all) -- avoids an unnecessary
    copy and keeps `compute_drift(trace) is trace`-style identity checks
    working for traces that never had any delegates in the first place.
    """
    parent_steps = [s for s in trace.steps if not _is_delegate_step(s)]
    if len(parent_steps) == len(trace.steps):
        return trace
    return trace.model_copy(update={"steps": parent_steps})


def compute_drift(trace: Trace, scorer_key: str | None = None) -> DriftReport:
    """Score intent-enactment drift for a trace using the given scorer key.

    Delegate steps (`metadata["agent_id"]` set) are excluded before
    scoring (D6) -- see `compute_delegate_drift` for their own, separate
    per-agent figure.
    """
    key = scorer_key or DEFAULT_SCORER
    return get_scorer(key).score(_exclude_delegate_steps(trace))


def compute_delegate_drift(trace: Trace, scorer_key: str | None = None) -> dict[str, DriftReport]:
    """Per-delegate intent-enactment drift, keyed by `agent_id`.

    Each agent's `DriftReport` is computed over ONLY that agent's own
    steps (never blended with the parent's or another agent's) -- the
    delegate's brief is the relevant "intent" for its own actions, not the
    parent's. Returns `{}` when `trace` has no delegate steps at all.
    """
    key = scorer_key or DEFAULT_SCORER
    scorer = get_scorer(key)

    steps_by_agent: dict[str, list[Step]] = {}
    for step in trace.steps:
        agent_id = step.metadata.get("agent_id")
        if agent_id:
            steps_by_agent.setdefault(str(agent_id), []).append(step)

    return {
        agent_id: scorer.score(trace.model_copy(update={"steps": steps}))
        for agent_id, steps in steps_by_agent.items()
    }
