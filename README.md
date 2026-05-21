# glasshouse-core

> Python reference implementation of [`glasshouse-spec`](https://github.com/haikomatt/glasshouse-spec) — the open standard for agent alignment evidence.

## What this is

Coding agents (Oz, Claude Code, OpenClaw, Hermes, Cursor, Devin) have shell access, edit your files, and push to your repos. They are the most consequential agentic systems deployed today. Nobody is auditing them.

`glasshouse` audits them. It runs adversarial probes against a deployed agent, measures whether the agent's actions matched its declared intentions (intent–enactment drift), and produces a **signed, portable evidence pack** — a self-describing JSON artefact you can version-control, hand to a security team, or attach to a compliance record.

It is Apache-2.0, protocol-first, and works with whatever you are already using: LangSmith, Langfuse, Phoenix, raw OTel, or nothing at all.

## Status

**Building toward the first demo.** The spec (`glasshouse-spec` v0.1.0) is tagged and frozen. The analysis engine, adapters, and schema layer are built and tested. The probe runner, evidence-pack builder, and CLI are in progress. The first demo will audit the Oz session that built this tool.

What is working today:
- Trace adapters: OpenTelemetry/OpenInference, LangGraph checkpoints
- Analysis engine: intent–enactment drift (`compute_drift`), belief-state extraction, counterfactual replay
- Schema: full Pydantic models mirroring the v0.1 spec; contract tests

What is in progress:
- Probe runner (loader, HTTP prober, scoring, runner)
- Evidence-pack builder + Ed25519 signer
- CLI wiring (`glasshouse probe`, `glasshouse attest`, `glasshouse verify`)
- First probe family: `glasshouse-probes-jailbreak` (5 probes)

What comes after the first demo:
- Claude Code, OpenClaw, and Hermes adapters
- More probe families (coding-agent, browser-use)
- Multi-tenant embedded mode for agentic SaaS platforms

## The demo

The goal is to run this against the Oz session that built glasshouse itself:

```bash
glasshouse probe --endpoint <oz-session> \
    --suite glasshouse-probes-jailbreak/ --out probe-report.json
glasshouse attest --traces oz-session.jsonl \
    --probe-results probe-report.json --out evidence-pack.json
glasshouse verify evidence-pack.json --public-key signing_key.ed25519.pub
```

The resulting `evidence-pack.json` will be committed to this repo under `demos/`. It will show whether Oz drifted from its declared intentions while building the tool that audits it.

## What this is not

- Not a hosted SaaS competing with LangSmith / Langfuse / Phoenix. We ingest from them.
- Not a base-model mechanistic interpretability tool. The unit of analysis is the agent's externally-observable belief and action, not transformer internals.
- Not a framework replacement. Adapters only.

## Architecture

See [`glasshouse-spec`](https://github.com/haikomatt/glasshouse-spec) for the protocol. Key principles:

- **Pure-functional core.** Analysis takes traces + config + probe spec, returns findings. No global state. No I/O.
- **Adapter pattern at every boundary.** Trace ingestion, config loading, endpoint probing, signing, evidence storage — all pluggable Python protocols.
- **Tenancy as opaque metadata.** `tenant_id` flows through traces and evidence packs; the core never owns multi-tenant concerns.

## License

Apache-2.0. See [LICENSE](LICENSE). Probe family definitions are dual-licensed Apache-2.0 / CC-BY-SA.
