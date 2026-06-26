# auditk

> Python reference implementation of [auditk-spec](https://github.com/auditk/auditk-spec) — the open standard for cryptographically attested intent–enactment drift measurement in agentic AI systems.

## What this measures

AI agents operating in consequential contexts — financial advice, clinical triage, investment suitability — must not only produce correct outputs but do what they declared they would do. Under FCA Consumer Duty, MiFID II, GDPR Article 22, and NHS clinical governance, the gap between declared and enacted process is a **compliance failure**, not a quality issue. Current AI explainability approaches reconstruct a narrative post-hoc; auditk produces a **contemporaneous, tamper-evident record** at the time of execution.

The core problem is **intent–enactment drift**: the gap between an agent's declared plan and its actual behaviour at each step. Observability platforms tell you what an agent *did*. auditk tells you whether the agent *did what it declared it would do* — and produces a signed evidence pack you can hand to a regulator, attach to a compliance record, or verify offline.

auditk is validated in the coding agent domain (Claude Code with `TodoWrite` standing plans as the intent declaration mechanism) as a proof of concept for the general framework. Regulated industry deployment — voice pipelines, financial services agents, clinical AI — is the target.

auditk is Apache-2.0, protocol-first, and integrates with whatever you are already using: LangSmith, Langfuse, Phoenix, raw OpenTelemetry, or nothing at all.

## Research

**Paper:** *auditk: an open standard for cryptographically attested intent–enactment drift measurement in agentic AI systems* — arXiv preprint forthcoming; NeurIPS/ICML/ACL safety workshop target.

Key empirical findings:
- Drift range 0.016–0.210 across Claude Code sessions; all five taxonomy categories firing in production
- Cross-model benchmark (4 model families — Claude, Kimi K2, MiniMax M2, DeepSeek) on identical sessions with reversed and distractor seeds: Kimi K2 is the only model to drift on the distractor
- Cross-taxonomy comparison with TRAIL (31 SWE-bench traces): NLI gate: recall 0.62, precision 0.38, F1 0.47. Full pipeline: recall 0.042, precision 0.292, F1 0.074. The low full-pipeline recall against TRAIL is the central finding — instruments measure orthogonal constructs, not a calibration failure. Manual inspection confirms 171 judge-stage steps TRAIL does not flag as errors are correctly resolved as faithful by the judge.
- Attestation integrity: 100 tampered evidence packs → 100% rejection

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
- Cross-taxonomy comparison with TRAIL dataset: NLI gate recall 0.62 / F1 0.47; full pipeline recall 0.042 / F1 0.074 (instruments measure orthogonal constructs)
- Session provenance hooks for Claude Code; Hermes adapter is planned future work
- 262 tests; `mypy --strict` clean

**Worked examples:** `demos/demo-001/` and `demos/demo-005/` — real agent sessions with published evidence packs and drift scores. Note: demo-001/ and demo-005/ were generated with an earlier scalar scoring method (`plan-action-similarity@0.1`). The current pipeline uses the two-stage NLI gate + LLM judge ensemble.

**Coming next:**

- Causal masking in judge: evaluate each step with only the history available to the agent at that point
- Contingent responsiveness metric: detect drift when the agent encounters unexpected resistance
- Voice pipeline validation
- Calibration against human-labelled gold standard (Kaggle)
- Regulated industry pilot (financial services / clinical AI)
- Additional adapters: browser agents

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

## Scope and limitations

auditk detects **specification drift** — the gap between declared and revealed intent — not deception. A sufficiently sophisticated agent pursuing a hidden goal will produce plausible intent declarations that pass both the NLI gate and the judge ensemble. This is a known limitation, not a defect in the instrument: if an agent's beliefs are correct and its declarations are honest, the measurement is reliable. The caveat is that auditk cannot distinguish a well-aligned agent from a deceptive one whose declarations are designed to pass. Interpretability integration — grounding intent declarations in internal model states rather than agent-produced text — is the path toward closing this gap.

## Judge independence

The two-stage scoring pipeline uses a judge ensemble selected to be independent of the agent being audited. In the current cross-model benchmark, the primary judge (GPT OSS 120B, served via Fireworks AI) is from a different model family than all four benchmarked agents (Claude, Kimi K2, MiniMax M2, DeepSeek). This family exclusion is currently enforced by manual judge selection, not by a programmatic check in the pipeline. A future version should enforce it automatically; see the TODO in `src/auditk/benchmark/runner.py`.

## What this is not

- Not a hosted SaaS. auditk is a local tool and open standard — it ingests from LangSmith, Langfuse, Phoenix, and raw OTel rather than replacing them.
- Not a base-model interpretability tool. The unit of analysis is the agent's externally observable declared intent and action trace, not transformer internals.
- Not a framework replacement. Adapter pattern only.

## Architecture

See [auditk-spec](https://github.com/auditk/auditk-spec) for the protocol. Key principles:

- **Pure-functional core.** Analysis takes traces + config + probe spec, returns findings. No global state, no I/O.
- **Adapter pattern at every boundary.** Trace ingestion, config loading, signing, evidence storage — all pluggable Python protocols.
- **Actor-level provenance labelling.** Each step in the trace carries an `Actor` label (`agent` / `user` / `tool` / `environment`), preserving the full human-agent interaction record. Segment-level attestation — signing agent-produced segments independently while marking human turns as unattested — is a planned future property.

## Installation

```bash
pip install auditk

# With NLI support (required for two-stage scoring)
pip install "auditk[nli]"

# With LangGraph adapter
pip install "auditk[langgraph]"
```

Requires Python ≥ 3.11.

## Ecosystem

| Repo | Role |
|------|------|
| [`auditk`](https://github.com/auditk/auditk) | Python reference implementation (this repo) |
| [`auditk-spec`](https://github.com/auditk/auditk-spec) | Language-agnostic protocol: trace, evidence-pack, probe, and agent-config schemas |
| [`auditk-testbed`](https://github.com/auditk/auditk-testbed) | Reference agents (aligned + vulnerable) for probe and pipeline validation |
| [`auditk-trail-comparison`](https://github.com/auditk/auditk-trail-comparison) | Reproducibility code and data for the TRAIL cross-taxonomy comparison (paper §4.3) |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

Apache-2.0. See [LICENSE](LICENSE). Probe family definitions are dual-licensed Apache-2.0 / CC-BY-SA.
