# auditk

> Python reference implementation of [auditk-spec](https://github.com/auditk/auditk-spec) — the open standard for cryptographically attested intent–enactment drift measurement in agentic AI systems.

## What this is

Coding agents have shell access, edit your files, and push to your repos. Observability platforms tell you what an agent *did*. auditk tells you whether the agent *did what it declared it would do* — and produces a signed, tamper-evident record you can hand to a security team, attach to a compliance record, or verify offline.

The core problem auditk addresses is **intent–enactment drift**: the gap between an agent's declared plan and its actual behaviour. This matters for coding agents today. It matters more for agents making consequential decisions in regulated environments — financial advice, clinical triage, insurance assessment — where the gap between declared and enacted process is a compliance failure, not just a quality issue.

auditk is Apache-2.0, protocol-first, and integrates with whatever you are already using: LangSmith, Langfuse, Phoenix, raw OpenTelemetry, or nothing at all.

## Status

**Alpha — pipeline complete, not yet stable for production use.**

The core pipeline runs end-to-end: an agent session becomes a signed, verifiable evidence pack with a calibrated intent–enactment drift score.

**Working today:**

- Trace adapters: Claude Code session JSONL, OpenTelemetry/OpenInference, LangGraph checkpoints
- Two-stage scoring pipeline: NLI gate (DeBERTa-v3 asymmetric entailment) + LLM judge ensemble
- Five-category taxonomy: `faithful` / `benign_elaboration` / `goal_deviation` / `instruction_noncompliance` / `undeclared_goal` — with severity (LOW/MEDIUM/HIGH) and evidence fields
- Drift score: scalar, decomposable, session-length invariant
- Attestation: Ed25519 signing, canonical JSON, portable evidence pack
- CLI: `key-gen`, `ingest`, `attest`, `verify`
- Cross-model benchmark: 4 models, calibrated and directly comparable
- Session provenance hooks for Claude Code and Hermes
- 262 tests; `mypy --strict` clean

**Worked examples:** `demos/demo-001/` and `demos/demo-005/` — real agent sessions with published evidence packs and drift scores.

**Coming next:**

- Probe path: `run_suite`, jailbreak probe family against the testbed
- Additional adapters: OpenClaw, browser agents
- Voice pipeline validation
- Calibration against human-labelled gold standard

## The pipeline

```bash
# Generate a signing key
auditk key-gen signing_key

# Ingest a Claude Code session
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

- Not a hosted SaaS. auditk is a local tool and open standard — it ingests from LangSmith, Langfuse, Phoenix, and raw OTel rather than replacing them.
- Not a base-model interpretability tool. The unit of analysis is the agent's externally observable declared intent and action trace, not transformer internals.
- Not a framework replacement. Adapter pattern only.

## Architecture

See [auditk-spec](https://github.com/auditk/auditk-spec) for the protocol. Key principles:

- **Pure-functional core.** Analysis takes traces + config + probe spec, returns findings. No global state, no I/O.
- **Adapter pattern at every boundary.** Trace ingestion, config loading, signing, evidence storage — all pluggable Python protocols.
- **Segmented attestation.** Human-agent entanglement is preserved with provenance labelling — attested agent segments are signed independently, human turns are marked as unattested.

## Installation

```bash
pip install auditk

# With NLI support (required for two-stage scoring)
pip install "auditk[nli]"

# With LangGraph adapter
pip install "auditk[langgraph]"
```

Requires Python ≥ 3.11.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

Apache-2.0. See [LICENSE](LICENSE). Probe family definitions are dual-licensed Apache-2.0 / CC-BY-SA.
