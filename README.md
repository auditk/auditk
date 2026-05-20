# glasshouse-core

> Python reference implementation of [`glasshouse-spec`](https://github.com/haikomatt/glasshouse-spec) — the open standard for agent alignment evidence.

## Status

**Draft alpha.** Tracking `glasshouse-spec` v0.1.

## What this is

A protocol-first, Apache-2.0 reference implementation of:

- **Analysis engine** — intent–enactment drift detection, belief-state extraction, deterministic counterfactual replay.
- **Probe runner** — load and execute adversarial / alignment probes against an agent endpoint or recorded trace.
- **Evidence-pack builder** — produce signed, regulator-portable evidence packs from traces + probe results.
- **Trace adapters** — ingest from OpenTelemetry / OpenInference, LangSmith API, Langfuse, Phoenix, OpenAI Assistants, Anthropic, generic JSON.

## What this is not

- Not a hosted SaaS competing with LangSmith / Langfuse / Phoenix. We ingest from them.
- Not a base-model mechanistic interpretability tool. The unit of analysis is the agent's externally-observable belief and action.
- Not a framework replacement. Adapters only.

## Installation

```bash
pip install glasshouse-core
```

Optional extras:

```bash
pip install "glasshouse-core[langgraph,browser,otel,pdf]"
```

## Quickstart

```python
from glasshouse_core import probe, attest

# Probe a deployed agent endpoint
report = probe.run(
    endpoint="https://staging.example.com/agent",
    probe_suite="glasshouse-probes-jailbreak",
    flow_type="generic",
)

# Build a signed evidence pack
pack = attest.build(
    traces=report.traces,
    probe_results=report.results,
    jurisdiction=["EU", "UK"],
    risk_tier="high",
    signer=attest.LocalEd25519Signer("./signing_key.ed25519"),
)
pack.write("evidence-pack.json")
```

CLI equivalent:

```bash
glasshouse probe --endpoint https://staging.example.com/agent \
    --suite glasshouse-probes-jailbreak --flow generic --out report.json
glasshouse attest --traces report.json --jurisdiction EU,UK --risk-tier high \
    --signer ./signing_key.ed25519 --out evidence-pack.json
```

## Architecture

See [`glasshouse-spec`](https://github.com/haikomatt/glasshouse-spec) for the protocol; this repo is the Python reference implementation of the engine and adapters. Key principles:

- **Pure-functional core.** Analysis takes traces + config + probe spec, returns findings. No global state. No I/O. Tenancy and persistence are the embedding context's responsibility.
- **Tenancy as opaque metadata.** `tenant_id` flows through traces and packs; the core never owns multi-tenant concerns.
- **Adapter pattern at every boundary.** Trace ingestion, config loading, endpoint probing, signing, evidence storage, trigger sources — all pluggable Python protocols with sensible defaults.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) *(pending)*. All code follows an Engineering Constitution that will be incorporated there: functions <50 lines, files <500 lines, type hints on public APIs, early returns / guard clauses, fail loud (no silent `except: pass`), TDD where contracts are testable.
