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

> **Deferred until after D2** (see Phase D). This is engine-breadth work; scorer
> correctness (D1–D2) takes priority over probe-corpus expansion.

3. **Garak probe-corpus ingestion (from NVIDIA garak).** Before authoring a
   large bespoke probe library, evaluate a `garak` adapter that ingests its 37+
   probe modules. Could replace most hand-authoring. Decide during Phase 4b.

## Deferred (Phase C/D — noted, not scheduled; gated behind D2 — see Phase D)

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
- [x] Wire CI (lint + mypy + pytest) — surfaces the pre-existing strict errors ✅
- [x] Fix pre-existing `mypy --strict` errors (`loader.py`, `langgraph.py`) ✅
- [x] Harden `verify` to reject empty-signature packs ✅
- [x] `.gitignore` excludes keys/packs (confirm `*.ed25519`, `evidence-pack*.json`) ✅
- [ ] Pin / declare Python version support; ensure `pip install -e .` clean
- [ ] Decide public-org transfer (`auditk`) vs stay private

## Phase C.0 — Claude Code adapter intent-extraction audit

- [x] **Complete.** `pending_intent` carried across separate assistant messages
- **Coverage:** 3.1% (intent extraction still sparse)
- **Drift score:** has real range (0.012 to 0.385)
- **First flagged step detected** in live session analysis
- **False positive pattern identified:** plan-level intent vs step-level action
- **Next:** superseded by **Phase D** — the FP is rooted in adapter coverage
  (D1) and lexical scoring (D2), not per-step scoping alone

## Phase D — Scorer overhaul (intent-enactment drift)

Rationale (research pass, 2026-06-06). The v0 scorer is a generation behind SOTA.
Jaccard measures *symmetric lexical similarity*; drift is an *asymmetric,
semantic* relation ("does this action advance the declared plan?"). Two coupled
defects feed the C.0 false-positive pattern:

- **Granularity** — a plan is a set of sub-goals over a horizon; an action is one
  element. Comparing a 5-step plan's text to step 1's payload guarantees low
  overlap → the "executing step 1 of 5" FP.
- **Coverage (3.1%)** — the adapter only attaches narration to the *first*
  `tool_use` of a narrated assistant message. This is an *adapter* bug, not a
  scorer bug.

Correct primitive = **entailment + plan-decomposition coverage**, not similarity.
**Sequencing rule:** fix the coverage denominator (D1) before touching the scorer
— a better scorer measured on 3.1% of steps yields noisy, misleading signal.

- **D1 — Coverage fix (adapter).** Wire `TodoWrite` (+ plan-mode / `ExitPlanMode`
  plans) into a standing plan state carried across the whole trace; use it as the
  premise for every subsequent action's `declared_intent`. Target coverage
  3.1% → >50%. Near-zero scorer change. Plan: `plans/phase-d1-coverage-fix.md`.
- [x] **D2 — `Scorer` protocol + NLI scorer.** Extract
  `Scorer.score(trace) -> DriftReport` (mirror Inspect Scorer / garak Detector;
  resolves the deferred "Pluggable Detector/Scorer protocol"). Keep `jaccard@0.1`
  as baseline; add `nli@0.2` (deterministic, local, three-valued: entail /
  neutral / contradict). Pin `method + method_version` into the signed pack.
  **Results:** Jaccard post-D1: 0.926 drift, 40/42 flagged (false positive cascade).
  NLI post-D2: 0.262 drift, 11/42 flagged (**72% FP reduction**).
- [x] **D3 — Plan-decomposition + two-stage judge.** Behind the protocol:
  `llm-judge@0.3`, boolean rubric, judge pinned. Deterministic NLI gates; judge
  adjudicates only the candidates NLI flags. Emit drift taxonomy (faithful /
  benign-elaboration / goal-deviation / instruction-noncompliance /
  undeclared-goal), aligned to TRAIL + the spec-v0.2 taxonomy refs.
  **Results:** drift_score: **0.167** (7/42 `goal_deviation`), faithful: 32, neutral: 3.
  Judge overturned 4 NLI false positives (gate contradictions rescued to faithful).
  Full scorer fingerprint (nli model+revision + judge model+temperature) attested
  in the signed pack.
- **D4 — Calibration harness.** 200–500 human-labelled (plan, action, label)
  pairs; compute scorer-vs-human Pearson r (target > 0.7) and Cohen's κ on the
  boolean flag; emit calibration stats into the signed pack. Gate before any
  cross-model numbers are published.
- **D5 — Cross-model benchmark.** Claude / Kimi / MiniMax / DeepSeek / GPT under
  a *fixed* scorer + *independent* judge (avoid self-preference bias), multiple
  seeds, capability-normalised, with CIs. Real-session-native → contamination-free
  by construction.

**Breadth deferral:** engine-breadth work (probe library, garak ingestion,
Inspect-log adapter, richer anonymiser, full OpenInference span coverage) is
deferred until after **D2**. Scorer correctness precedes engine breadth.

## Status

POC working: Claude Code session → signed evidence pack → verify, with NLI drift
scoring live. T4.9 (publish a real-session evidence pack under `demos/`) remains
independent and unblocked. **D3 complete.** The next concrete *engine* milestone
is **D4** (calibration harness: 200–500 human-labelled pairs, scorer-vs-human
Pearson r / Cohen's κ, gate before cross-model numbers are published).
