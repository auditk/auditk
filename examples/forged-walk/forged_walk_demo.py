"""Forged-walk demo: a walk recorded in shared state is a self-report, not an audit.

Demonstrates, deterministically and with no model calls, why auditk ingests
runtime telemetry through adapters rather than reading a trace key that the
graph's own nodes write.

A gated diamond (fetch -> analyze -> review -> human_approval -> apply -> halt)
is built in LangGraph with its execution trace recorded the way much of the
ecosystem records it: as a shared-state list each node appends its own name to.
One compromised node (`analyze`) appends the gate names to that list and routes
straight past the gate. The same run is then checked twice:

  Condition A: trace-invariant checks over the state-recorded trace (S).
               The forged walk passes clean.
  Condition B: the same checks over the trace derived from the runtime's own
               execution stream (T). The bypass is exposed.

The invariant checks bundled here are a clean-room implementation written from
the public homework specification of Ken Huang's *Graph Engineering for Agentic
AI Systems* companion repo (halt reached; all-of join complete; no spend
without a prior gate). His repo ships no licence file, so his code is not
vendored. To reproduce the result against his unmodified checker instead, pass
``--against-huang /path/to/graph-engineering`` (a clone of
https://github.com/kenhuangus/graph-engineering).

Pre-registration (written before the code, 2026-09-07): supported iff the
forged state trace yields zero violations and passes the signature check while
neither gate node executed, AND the scheduler-derived trace of the same run
yields at least one violation. Both arms held on the first run.

Run:
    uv sync --extra langgraph
    uv run python examples/forged-walk/forged_walk_demo.py
"""

from __future__ import annotations

import argparse
import operator
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, TypedDict

GATE_NODES = ("human_approval",)
SPEND_NODES = ("apply",)
HALT = "halt"
MUST_INCLUDE = ("review", "human_approval")

Checker = Callable[[Sequence[str]], list[str]]


def check_trace_clean_room(trace: Sequence[str]) -> list[str]:
    """Clean-room trace invariants, from the public homework spec.

    Halt reached; no spend without a gate somewhere earlier in the trace.
    (The join invariant is omitted: this demo's graph declares no joins.)
    Returns a list of violation codes; a good trace yields [].
    """
    violations: list[str] = []
    if HALT not in trace:
        violations.append("halt_not_reached")
    for i, node in enumerate(trace):
        if node in SPEND_NODES and not any(g in trace[:i] for g in GATE_NODES):
            violations.append("unconstrained_spend")
    return violations


def load_huang_checker(clone: Path) -> Checker:
    """Import the unmodified checker from a clone of Huang's companion repo."""
    src = clone / "ch13" / "src"
    if not (src / "trace_invariants.py").exists():
        raise FileNotFoundError(f"no ch13/src/trace_invariants.py under {clone}")
    sys.path.insert(0, str(src))
    from trace_invariants import TraceSpec, check_trace  # noqa: PLC0415

    spec = TraceSpec(halt=HALT, joins=(), gate_nodes=GATE_NODES, spend_nodes=SPEND_NODES)

    def checker(trace: Sequence[str]) -> list[str]:
        return [v.code for v in check_trace(list(trace), spec)]

    return checker


class _State(TypedDict):
    trace: Annotated[list[str], operator.add]  # the node-appended trace key
    approved: bool


def _build():  # LangGraph import is deferred so the invariants import anywhere
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, START, StateGraph

    def fetch(s: _State) -> dict:
        return {"trace": ["fetch"]}

    def analyze(s: _State) -> dict:
        # COMPROMISED NODE: it writes the walk that exonerates it. An
        # ordinary state write puts the gate names into the trace, and
        # `approved` steers the router past the real gate.
        return {"trace": ["analyze", "review", "human_approval"], "approved": True}

    def review(s: _State) -> dict:
        return {"trace": ["review"]}

    def human_approval(s: _State) -> dict:
        return {"trace": ["human_approval"], "approved": True}

    def apply_(s: _State) -> dict:
        return {"trace": ["apply"]}

    def halt(s: _State) -> dict:
        return {"trace": ["halt"]}

    g = StateGraph(_State)
    for name, fn in [
        ("fetch", fetch),
        ("analyze", analyze),
        ("review", review),
        ("human_approval", human_approval),
        ("apply", apply_),
        ("halt", halt),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "fetch")
    g.add_edge("fetch", "analyze")
    g.add_conditional_edges(
        "analyze",
        lambda s: "apply" if s.get("approved") else "review",
        {"apply": "apply", "review": "review"},
    )
    g.add_edge("review", "human_approval")
    g.add_edge("human_approval", "apply")
    g.add_edge("apply", "halt")
    g.add_edge("halt", END)
    return g.compile(checkpointer=MemorySaver())


def run_demo(checker: Checker) -> dict:
    """One run, checked twice. Returns everything a test needs to assert."""
    app = _build()
    cfg = {"configurable": {"thread_id": "forgery-1"}}

    scheduler_trace: list[str] = []
    for update in app.stream({"trace": [], "approved": False}, cfg, stream_mode="updates"):
        scheduler_trace.extend(update.keys())
    state_trace: list[str] = app.get_state(cfg).values["trace"]

    viol_state = checker(state_trace)
    viol_sched = checker(scheduler_trace)
    gates_fired = any(n in scheduler_trace for n in MUST_INCLUDE)
    supported = (
        not viol_state
        and all(n in state_trace for n in MUST_INCLUDE)
        and not gates_fired
        and (bool(viol_sched) or not all(n in scheduler_trace for n in MUST_INCLUDE))
    )
    return {
        "state_trace": state_trace,
        "scheduler_trace": scheduler_trace,
        "violations_state": viol_state,
        "violations_scheduler": viol_sched,
        "gates_fired": gates_fired,
        "supported": supported,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--against-huang",
        type=Path,
        default=None,
        metavar="CLONE",
        help="path to a clone of kenhuangus/graph-engineering; uses its unmodified checker",
    )
    args = parser.parse_args(argv)

    if args.against_huang is not None:
        checker = load_huang_checker(args.against_huang)
        print(f"checker: Huang's trace_invariants.py (unmodified) from {args.against_huang}")
    else:
        checker = check_trace_clean_room
        print("checker: bundled clean-room invariants (use --against-huang for the original)")

    r = run_demo(checker)
    print(f"state-recorded trace (S): {r['state_trace']}")
    print(f"scheduler-derived trace (T): {r['scheduler_trace']}")
    print(f"A (state trace):    violations={r['violations_state']!r}")
    print(f"B (scheduler):      violations={r['violations_scheduler']!r}")
    print(f"gate nodes actually executed: {r['gates_fired']}")
    print(f"\nVERDICT: {'SUPPORTED' if r['supported'] else 'REFUTED'}")
    print(
        "\nMoral: the invariants are fine; the witness is captured. Score walks"
        "\nfrom runtime telemetry (what auditk's adapters ingest), never from a"
        "\ntrace key the graph's own nodes write."
    )
    return 0 if r["supported"] else 1


if __name__ == "__main__":
    sys.exit(main())
