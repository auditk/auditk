# glasshouse v0.2 Roadmap

Folds the high-value findings from the competitive-landscape assessment
(OpenInference, OTel GenAI, Langfuse, Inspect AI, garak, PyRIT, promptfoo,
DeepEval; LangSmith baseline) into the build plan. POC (attest → verify) is
already working on `main`; nothing here blocks the T4.9 demo.

## Spec v0.2 questions (decide before the probe library ossifies)

1. **Multi-turn / campaign probes (from PyRIT).** `Probe` carries a single
   `Stimulus`; real jailbreaks (Crescendo, TAP, Skeleton Key) are multi-turn.
   Decision needed: extend `Probe` with an ordered `stimuli[]` / campaign shape,
   or add a separate `Campaign` entity. Highest-stakes schema question.
2. **Standard taxonomy refs on `Probe.kind` (from promptfoo / DeepEval).** Add
   optional `owasp_llm`, `mitre_atlas`, `nist_ai_rmf` reference fields so probes
   speak the security community's lingua franca and strengthen `ComplianceClaim`.
   Small change, large credibility payoff.

## Phase 4b build-vs-borrow decision

3. **Garak probe-corpus ingestion (from NVIDIA garak).** Before authoring a
   large bespoke probe library, evaluate a `garak` adapter that ingests its 37+
   probe modules. Could replace most hand-authoring. Decide during Phase 4b.

## Deferred (Phase C/D — noted, not scheduled)

- **Pluggable Detector/Scorer protocol + Stimulus converters** (garak Buff /
  PyRIT Converter): generalise scoring beyond keyword/regex; `llm_judge` already
  planned. Add a converter concept for encoding/obfuscation stimuli.
- **Inspect AI log adapter** (UK AISI): ingest Inspect logs + reuse parts of its
  200+ `inspect_evals`; resolves the Scanner-vs-drift overlap. Credibility win.
- **Richer anonymiser**: replace binary `strip_payloads` with attribute-level /
  PII-aware masking (cf. OpenInference masking, LangSmith anonymizer).
- **Full OpenInference span-kind coverage** in the OTEL adapter
  (GUARDRAIL/EVALUATOR/EMBEDDING/RERANKER/PROMPT).
- **Optional UsageMetadata** (token/cost) on `Step` (cf. Langfuse/LangSmith).

## Canonical-convention call

OTEL adapter targets **OpenInference** span semantics as canonical (already does
via `openinference.span.kind`); track OTel GenAI semconv as secondary. Revisit
when either convention stabilises.

## Apache-2.0 readiness checklist (repo tidy for OSS release)

- [ ] `LICENSE` present in every repo (core ✅ — confirm spec, testbed)
- [ ] `NOTICE` file with attribution
- [ ] `CONTRIBUTING.md` (currently referenced but pending in core + spec)
- [ ] SPDX header on source files (`# SPDX-License-Identifier: Apache-2.0`)
- [ ] `README` reflects POC-first reality (core ✅, spec ✅)
- [ ] `CODE_OF_CONDUCT.md`
- [ ] Wire CI (lint + mypy + pytest) — surfaces the pre-existing strict errors
- [ ] Fix pre-existing `mypy --strict` errors (`loader.py`, `langgraph.py`)
- [ ] Harden `verify` to reject empty-signature packs
- [ ] `.gitignore` excludes keys/packs (confirm `*.ed25519`, `evidence-pack*.json`)
- [ ] Pin / declare Python version support; ensure `pip install -e .` clean
- [ ] Decide public-org transfer (`auditk`) vs stay private

## Status

POC working: Claude Code session → signed evidence pack → verify, with a v0
drift score. The next concrete milestone is **T4.9** (publish a real-session
evidence pack under `demos/`), not more engine breadth.
