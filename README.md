# auditk

> Python reference implementation of [`auditk-spec`](https://github.com/haikomatt/auditk-spec) — the open standard for agent alignment evidence.

## What this is

Coding agents (Oz, Claude Code, OpenClaw, Hermes, Cursor, Devin) have shell access, edit your files, and push to your repos — among the most consequential agentic systems deployed today. The tooling to audit them is still young: observability platforms capture what an agent did, and the few products that produce signed audit receipts are proprietary and hosted.

`auditk` is the open, vendor-neutral alternative. It takes a recorded agent session, measures whether the agent's actions matched its declared intentions (intent–enactment drift), and produces a **signed, portable evidence pack** — a self-describing JSON artefact you can version-control, hand to a security team, attach to a compliance record, and verify offline without installing `auditk`.

It is Apache-2.0, protocol-first, and works with whatever you are already using: LangSmith, Langfuse, Phoenix, raw OTel, or nothing at all.

## Status

**POC complete.** The core pipeline runs end-to-end: a Claude Code session becomes a signed, verifiable evidence pack with an intent–enactment drift score. A worked example is published under `demos/demo-001/` (a real Claude Code session; drift score 0.097).

What is working today:
- Trace adapters: Claude Code session JSONL, OpenTelemetry/OpenInference, LangGraph checkpoints
- Analysis engine: `compute_drift` (intent–enactment drift), belief-state extraction, counterfactual replay
- Attestation: Ed25519 signing, canonical JSON, evidence-pack builder
- CLI: `key-gen`, `ingest`, `attest`, `verify` (probe/replay/diff are Phase 4b stubs)
- 99 tests; `mypy --strict` clean

What comes next:
- Probe path (Phase 4b): `run_suite`, jailbreak probe family against the testbed
- More adapters (Phase C): OpenClaw, Hermes
- More testing against agent traces

## The pipeline

```bash
# Generate a signing key
auditk key-gen signing_key

# Ingest a Claude Code session (strip-payloads redacts tool inputs for safety)
auditk ingest --adapter claude-code \
    --in ~/.claude/projects/<path>/<session>.jsonl \
    --out trace.json --strip-payloads

# Build a signed evidence pack with drift score
auditk attest --traces trace.json \
    --signer signing_key \
    --issuer-name "Your Name" --agent-id claude-code --agent-version 1.0 \
    --out evidence-pack.json

# Verify the signature
auditk verify evidence-pack.json --public-key signing_key.ed25519.pub
```

## What this is not

- Not a hosted SaaS competing with LangSmith / Langfuse / Phoenix. We ingest from them.
- Not a base-model mechanistic interpretability tool. The unit of analysis is the agent's externally-observable belief and action, not transformer internals.
- Not a framework replacement. Adapters only.

## Architecture

See [`auditk-spec`](https://github.com/haikomatt/auditk-spec) for the protocol. Key principles:

- **Pure-functional core.** Analysis takes traces + config + probe spec, returns findings. No global state. No I/O.
- **Adapter pattern at every boundary.** Trace ingestion, config loading, endpoint probing, signing, evidence storage — all pluggable Python protocols.
- **Tenancy as opaque metadata.** `tenant_id` flows through traces and evidence packs; the core never owns multi-tenant concerns.

## License

Apache-2.0. See [LICENSE](LICENSE). Probe family definitions are dual-licensed Apache-2.0 / CC-BY-SA.
