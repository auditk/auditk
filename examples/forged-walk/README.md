# Forged-walk demo

**Claim (tested, pre-registered):** if an agent graph records its execution
trace as a shared-state key the nodes themselves append to, a single
misbehaving node can forge a compliant walk. Trace invariants (halt reached,
no spend without a prior gate) pass the forged trace clean while the gate
never fired. The trace derived from the runtime's own execution stream, for
the same run, exposes the bypass.

This is why auditk ingests **runtime telemetry** through per-runtime adapters
(`src/auditk/adapters/`) with a conformance kit, rather than reading whatever
trace the graph's state offers. A walk recorded in shared state is a
self-report, not an audit. The invariants you run over it inherit the honesty
of the least honest node.

## Run it

```bash
uv sync --extra langgraph
uv run python examples/forged-walk/forged_walk_demo.py
```

Expected output: the state-recorded trace
`['fetch', 'analyze', 'review', 'human_approval', 'apply', 'halt']` yields
zero violations; the scheduler-derived trace
`['fetch', 'analyze', 'apply', 'halt']` yields `unconstrained_spend`; verdict
`SUPPORTED`. Deterministic, no model calls, no API keys.

## Reproducing against the original checker

The bundled invariants are a clean-room implementation written from the
public homework spec of Ken Huang's *Graph Engineering for Agentic AI
Systems* companion repo (which ships no licence file, so its code is not
vendored here). To run the identical experiment against his unmodified
`check_trace`:

```bash
git clone https://github.com/kenhuangus/graph-engineering /tmp/graph-engineering
uv run python examples/forged-walk/forged_walk_demo.py --against-huang /tmp/graph-engineering
```

Both checkers produce the same verdict: the forgery is invisible from state,
visible from the runtime.

## Scope

An existence proof, not a prevalence study: one compromised node, one graph,
one framework (LangGraph). Scheduler-side recording is better, not
bulletproof — the stream is only as trustworthy as the process emitting it;
adversarial-grade trust needs attestation of the recording layer itself,
which is outside this demo's scope.
