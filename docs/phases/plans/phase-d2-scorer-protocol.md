# Phase D2 — `Scorer` protocol + NLI scorer (`nli@0.2`)

**Type:** TDD sprint (analysis layer). Adapter (`adapters/claude_code.py`) is untouched — D1 is done.
**Files under change:**
- `src/auditk/analysis/protocols.py` (**new**) — `Scorer` / `NLIPredictor` protocols
- `src/auditk/analysis/scorers/` (**new package**) — `jaccard.py`, `nli.py`, `__init__.py` (registry)
- `src/auditk/analysis/drift.py` — `compute_drift` becomes a thin backward-compat shim
- `src/auditk/attestation/pack.py` + `src/auditk/cli.py` — optional `--scorer` selection
- `pyproject.toml` — new optional extra `[nli]`

**Parent:** `docs/phases/roadmap-v0.2.md` → Phase D → D2
**Predecessor:** `plans/phase-d1-coverage-fix.md` (complete; coverage 3.1% → 43.3% real / 66.7% synthetic)
**Status:** planned

---

## Problem

D1 fixed the *denominator* (coverage). The *scorer* is still the v0 defect:

```
drift_score = 1 - mean( jaccard(intent_tokens, payload_tokens) )   # drift.py:49
```

On a real session with the D1 standing-plan in place this now scores **0.926 drift,
40/42 steps flagged** — a near-total false-positive cascade. Two confirmed root causes:

1. **Symmetric lexical similarity, not directional entailment.** Jaccard asks "do these
   token sets overlap?" Drift is the asymmetric, three-valued question "does this action
   *advance / not-contradict* the declared plan?" Faithful execution with different
   vocabulary scores as drift.
2. **Set-vs-element granularity ("step 1 of 5").** `declared_intent` is now a *standing
   plan* (a set of sub-goals over a horizon). One action realises *one* sub-goal, so its
   tokens overlap a fraction of the plan text by construction → phantom drift.

Per `auditk-scoring-research.md` §2 (Rung B/C) the correct primitive is **NLI/entailment
over a decomposed plan**: a sub-goal is the *premise*, the enacted action is the
*hypothesis*; an action *entailed by any active sub-goal* is faithful, an action that
*contradicts* the plan is genuine drift, and an *unrelated* action is a low-severity
neutral. This dissolves both defects.

---

## Goal

1. Extract the monolithic `compute_drift` into a **pluggable `Scorer` protocol**
   (mirrors garak `Detector` / Inspect `Scorer`; closes the roadmap's deferred
   "Pluggable Detector/Scorer protocol").
2. Preserve the current algorithm as `JaccardScorer` (`plan-action-similarity@0.1`,
   **baseline / deprecated**) with **byte-identical behaviour** — every existing
   `test_drift.py` assertion passes unchanged (Test Integrity Rule).
3. Add **`NLIScorer` (`nli@0.2`)**: deterministic, local, CPU-only, three-valued
   (entail / neutral / contradict), populating the **existing** `DriftReport` fields.
4. Pin scorer identity into the signed pack via the **already-existing**
   `DriftReport.method` / `method_version` fields — **no schema change, no spec change,
   no `verify` change** (see §"Schema changes" for why this is deliberate and how the
   demos stay verifiable).

**Success measure (sanity, not calibration):** on the same real session that scores
0.926 / 40-of-42 under jaccard, `nli@0.2` flags only the genuinely off-plan steps
(expected single-digit count). Rigorous scorer-vs-human calibration is **D4**, not D2.

---

## 1. `Scorer` protocol design

### 1.1 The protocol (`src/auditk/analysis/protocols.py`, new)

Mirror the existing PEP-544 structural pattern in `adapters/protocols.py` (no ABC,
no inheritance requirement):

```python
from __future__ import annotations
from typing import Protocol
from auditk.schema import DriftReport, Trace


class Scorer(Protocol):
    """Compute an intent-enactment DriftReport from a single Trace.

    Every Scorer MUST expose a stable (method, method_version) identity; these are
    pinned into the signed evidence pack via DriftReport and are the scorer's
    cryptographic fingerprint (research §4.1 — "a drift number is meaningless
    without its scorer fingerprint").
    """

    method: str           # e.g. "nli"
    method_version: str   # e.g. "0.2"

    def score(self, trace: Trace) -> DriftReport: ...


class NLIPredictor(Protocol):
    """Three-valued NLI over (premise, hypothesis). Injected into NLIScorer so the
    scorer is testable without loading model weights (fake in unit tests, real model
    behind the [nli] extra in integration)."""

    def predict(self, premise: str, hypothesis: str) -> tuple[float, float, float]:
        """Return (p_entail, p_neutral, p_contradict), summing to ~1.0."""
        ...
```

`score(trace) -> DriftReport` keeps the **exact** signature of today's `compute_drift`,
so the return contract — and everything downstream (`pack.build`, `verify`) — is
unchanged. The protocol only *names* the boundary that already exists.

### 1.2 Registry (`src/auditk/analysis/scorers/__init__.py`, new)

Mirror `adapters/registry.py`. String keys are `"{method}@{method_version}"`:

```python
DEFAULT_SCORER = "plan-action-similarity@0.1"   # jaccard stays default in D2

def get_scorer(key: str = DEFAULT_SCORER) -> Scorer: ...
def available() -> list[str]:                    # ["plan-action-similarity@0.1", "nli@0.2"]
```

- `get_scorer("nli@0.2")` **lazy-imports** `transformers`/`torch` *inside the factory*,
  raising a clear, actionable `ImportError` ("install auditk[nli]") if the extra is
  absent. Importing the registry never imports torch → base install stays light.
- `JaccardScorer` is registered eagerly (zero heavy deps).

### 1.3 How method + method_version reach the pack (no new wiring)

This already works today and we keep it:

```
Scorer.score → DriftReport(method=…, method_version=…)
  → pack.build: drift_metrics=report           (pack.py:42)
  → manifest = model_dump(exclude={"signatures"}) → canonicalize → sign   (pack.py:46-48)
  → verify re-canonicalises the same manifest and checks the signature    (cli.py:203)
```

`method` / `method_version` are inside the signed manifest, so the scorer identity is
**already attested**. D2 adds nothing here.

> **Deferred (D3):** the *full* fingerprint — model id, HF revision SHA, `transformers`
> / `torch` versions — is richer than two strings and needs a new optional field.
> `NLIScorer` will **expose** that fingerprint as a property and via structured logging
> in D2, but pinning it into the schema is D3 (it requires the additive-field +
> `verify`-hardening work described in §3, deliberately kept out of D2).

### 1.4 Plugging in a new scorer (the extension contract)

1. Write a class with `method`, `method_version`, and `score(self, trace) -> DriftReport`.
2. `register("yourmethod@x.y", factory)` in the registry.
3. It is immediately selectable via `pack.build(..., scorer=...)` and `auditk attest --scorer`.

No changes to `DriftReport`, `pack.build`, `verify`, or the spec are needed to add a
scorer — that is the whole point of the protocol.

### 1.5 `compute_drift` backward-compat shim (`drift.py`)

```python
def compute_drift(trace: Trace) -> DriftReport:
    """Deprecated alias — delegates to the default (jaccard) scorer.
    Retained so existing imports/tests keep working unchanged."""
    return get_scorer(DEFAULT_SCORER).score(trace)
```

`JaccardScorer.score` is the current `drift.py` body moved verbatim (`_tokenise`,
`_jaccard`, `_FLAG_THRESHOLD=0.3`, `method="plan-action-similarity"`,
`method_version="0.1"`). **All six `test_drift.py` tests must pass with zero edits** —
this is the regression contract for the refactor.

---

## 2. NLI scorer design (`nli@0.2`)

### 2.1 Model choice — **`cross-encoder/nli-deberta-v3-small`**

Requirements: local, deterministic, **no runtime network**, small, CPU-only (no GPU),
three-valued, permissive licence.

| Model | Params (approx) | Disk fp32 | CPU/pair | 3-class | Licence | Verdict |
|---|---|---|---|---|---|---|
| **`cross-encoder/nli-deberta-v3-small`** | ~44M backbone (~142M w/ embeddings) | ~280 MB | ~40–120 ms | ✅ contra/entail/neutral | **Apache-2.0** (DeBERTa-v3 base = MIT) | **CHOSEN** — best accuracy/size on CPU; native cross-encoder takes (premise, hypothesis) pairs directly |
| `cross-encoder/nli-MiniLM2-L6-H768` | ~33M | ~130 MB | ~15–50 ms | ✅ | Apache-2.0 | **Fallback** — ~2× faster, lower accuracy; for CPU-constrained CI / large traces |
| `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | ~86M backbone | ~370 MB | ~80–200 ms | ✅ | MIT | **Accuracy-upgrade option** (MNLI+FEVER+ANLI → more robust); heavier — revisit at D4 if calibration demands it |
| `facebook/bart-large-mnli` | ~407M | ~1.6 GB | ~300–700 ms | ✅ | MIT | **Rejected** — violates "small, CPU"; ~10× the footprint for marginal gain |

**Why a cross-encoder (not a bi-encoder / zero-shot pipeline):** it scores a
(premise, hypothesis) *pair* in one forward pass and emits logits over
`[contradiction, entailment, neutral]` — exactly our three-valued primitive, no prompt
engineering.

**Critical correctness note:** **do not hardcode the label order.** Read
`model.config.id2label` and map by *name*. The cross-encoder family's order is
`["contradiction", "entailment", "neutral"]`, which differs from many `*-mnli`
checkpoints — a silent swap here would invert the scorer.

**Integration risk:** DeBERTa-v3 uses a SentencePiece tokenizer. Require
`sentencepiece` and `transformers>=4.40` (older versions had a `DebertaV2TokenizerFast`
bug). Lock both in the `[nli]` extra.

### 2.2 Determinism & "no runtime network"

- `model.eval()`, `torch.no_grad()`, fp32, batched single-thread CPU forward → a
  deterministic function of inputs on a fixed `(model revision, torch, transformers)`.
- **Pin the HF revision to a commit SHA**, not a tag/branch.
- Load with **`local_files_only=True`** → the scorer *never* hits the network at
  runtime; it raises a clear error if weights aren't cached. Provisioning is a separate,
  documented one-time step (`huggingface-cli download cross-encoder/nli-deberta-v3-small
  --revision <SHA>`). (A convenience `auditk` fetch command is **out of scope** — D3+.)
- **Cross-platform float noise:** transformer CPU output can differ in the last ULPs
  across BLAS/torch builds. Because D2's headline score is a *count-based fraction*
  (§2.5) driven by an **argmax**, sub-ULP noise cannot change it except at exact ties
  (measure-zero). Record the `(model, revision, torch, transformers)` fingerprint so the
  number is reproducible on a pinned stack. (Continuous probabilities are *not* written
  into the signed pack in D2 — another reason the count-based score is the right call.)

### 2.3 Plan decomposition (sub-goal extraction)

The premise is a **sub-goal**, not the whole plan — this is the set-vs-element fix.
`declared_intent` is D1's standing-plan string (active todos joined). Decompose it back
into sub-goals:

```python
def decompose(plan_text: str) -> list[str]:
    # split on newlines / leading bullets ("- ", "* ", "1. ") / "; "; strip; drop empties;
    # cap at K=12 sub-goals (bounds NLI calls); if empty, return [plan_text] (whole-plan fallback)
```

> **Red-phase lock:** add a test asserting `decompose` splits on **the exact delimiter
> D1's adapter uses to join active todos** (check `adapters/claude_code.py`). The D1↔D2
> join/split contract must be explicit, or sub-goals silently fuse.

### 2.4 Scoring algorithm (per scored step)

Scored steps = the same filter as jaccard: `step.declared_intent is not None` (D1
already restricts intent to `Actor.AGENT` action steps).

```
hyp        = render_action(step)              # deterministic NL rendering of the action
sub_goals  = decompose(step.declared_intent)
for g in sub_goals:
    (e, n, c) = predictor.predict(premise=g, hypothesis=hyp)
    argmax_g  = argmax(e, n, c)

label(step):                                  # priority: ENTAIL > CONTRADICT > NEUTRAL
    FAITHFUL       if any sub_goal argmax == entail        # ← "entailed by ANY sub-goal" = the step-1-of-5 fix
    CONTRADICTION  elif any sub_goal argmax == contradict
    NEUTRAL        else
```

The **"entailed by any active sub-goal ⇒ faithful"** rule is the core claim: it is
precisely what stops "executing step 1 of a 5-step plan" registering as drift.

`render_action(step)` (deterministic): for `TOOL_CALL` →
`"call tool {name} with {args}"`; for `UTTERANCE` → the text; default → `str(payload)`.
Render is the **hypothesis** because, per research §2 ("don't trust the agent's own
narration as ground truth"), we score against *enacted actions*, treating
`declared_intent` as the plan-side claim.

### 2.5 Aggregation → `DriftReport` (param-free in D2)

```
n_scored        = count(scored steps)
n_contradiction = count(steps labelled CONTRADICTION)
drift_score     = 0.0 if n_scored == 0 else n_contradiction / n_scored   # contradiction fraction
drift_per_trace = {trace.trace_id: drift_score}
flagged_steps   = [step_id for CONTRADICTION steps]                       # genuine drift only
method, method_version = "nli", "0.2"
```

- **Param-free by design.** Labels come from a 3-way **argmax** (no thresholds), and the
  score is a pure contradiction fraction. No uncalibrated magic constants enter the
  signed pack — consistent with research §4.2 ("calibrate before publishing numbers").
  Probability thresholds (`τ_entail`, `τ_contra`) and any neutral weighting `λ` are
  **D4 calibration knobs**, explicitly *not* introduced here.
- **NEUTRAL steps are not in `flagged_steps`** (drift = contradiction mass). They are
  the low-severity "benign elaboration / undeclared sub-goal" bucket; surfacing them
  per-step is D3 (needs the schema field — §3).

### 2.6 Flag taxonomy in D2 (coarse, NLI-grounded) vs D3 (fine)

D2 emits the **three NLI-grounded buckets only**, mapped to the standard taxonomy's
coarse cells:

| NLI label | D2 bucket | Maps to taxonomy cell(s) |
|---|---|---|
| entailment | FAITHFUL | `faithful` |
| neutral | NEUTRAL | `benign-elaboration` **or** `undeclared-goal` (undistinguished) |
| contradiction | CONTRADICTION | `goal-deviation` **or** `instruction-noncompliance` (undistinguished) |

The **fine 5-way split** (`benign-elaboration` vs `undeclared-goal`;
`goal-deviation` vs `instruction-noncompliance`) is **D3**, because — per TRAIL
(research §1, Cluster B) — those pairs have "ambiguous boundaries" and need the
constraint set + LLM-judge adjudication to separate. Forcing the split deterministically
in D2 would be guessing. D2 stays honest: three buckets it can actually ground.

### 2.7 Testability (dependency injection — fake over mock)

`NLIScorer(predictor: NLIPredictor | None = None, *, model_id=..., revision=...)`:

- **Unit tests** inject a `FakeNLIPredictor` returning scripted `(e,n,c)` per
  (premise, hypothesis) → fast, deterministic, **no weights downloaded**, full algorithm
  coverage. (Per `testing-constitution`: fake store over mock.)
- **One** integration test (`@pytest.mark.skipif` on `RUN_NLI_MODEL=1` +
  `importorskip("torch")`) loads the real `cross-encoder/nli-deberta-v3-small` and
  asserts a known entailment/contradiction pair classifies correctly — the only test
  that touches real weights.

---

## 3. Schema changes — **none in D2** (deliberate), and why

**Decision: D2 changes no schema, no spec, and no `verify`.** `NLIScorer` populates the
**existing** `DriftReport` fields (`drift_score`, `drift_per_trace`, `flagged_steps`,
`method`, `method_version`). Rationale, grounded in the actual code:

- **The backward-compat trap is real and concrete.** `verify` re-serialises the loaded
  model — `pack_obj.model_dump(mode="json", exclude={"signatures"})` (`cli.py:203`,
  same in `pack.py:46`) — and there are **two committed signed packs**
  (`demos/demo-001/evidence-pack.json`, `demos/demo-005/evidence-pack.json`). Adding
  *any* field to `DriftReport` (even `Optional`, default `None`) makes the re-dump emit
  `"...":null`, changing the canonical bytes → **those demos fail `auditk verify`.**
  That is a regression for zero D2 benefit.
- **Spec/contract parity is *not* the blocker** (verified): `DriftReport` in
  `evidence-pack.schema.json` does **not** set `additionalProperties: false`, so the
  contract test would tolerate additive fields. The verification trap, not the spec, is
  what forbids field additions until `verify` is hardened.
- **Step / EvidencePack:** no change needed. `declared_intent` (D1) is sufficient input;
  `EvidencePack.drift_metrics: DriftReport | None` already carries the report.

### What this defers to D3 (with the exact mechanism, pre-designed here)

When D3 needs to pin the per-step taxonomy and the full scorer fingerprint, do it in
this order:

1. **Harden `verify` to be additive-change-immune** (small, independently *correct*):
   canonicalize the **raw loaded JSON minus `signatures`**, not a model re-dump. You
   should verify *the bytes that were signed*, not a round-trip through an evolving
   model. Regression test: `demos/demo-001` & `demos/demo-005` still verify — before
   *and* after adding a `DriftReport` field.
2. **Then** add additive-optional fields, e.g.
   `DriftReport.per_step: list[StepDrift] | None = None` (step_id, label, e/n/c) and
   `DriftReport.scorer_fingerprint: dict | None = None` (model id, revision SHA, lib
   versions).
3. Mirror the optional props into `auditk-spec` `evidence-pack.schema.json` (hygiene;
   `additionalProperties` is already permissive so the contract stays green either way).

> **Optional D2 stretch (pull forward only if you want per-step taxonomy now):** do
> step 1 above as a tiny prepended sub-phase **D2.0**, then steps 2–3. It is clean and
> unblocks all of D3–D5. The plan recommends **leaving it in D3** to keep D2 surgical —
> but it is fully specified here if you choose otherwise.

---

## 4. TDD sprint structure

> Follow `docs/constitution/tdd-workflow.md`. **STOP for review at each gate.**
> One PR per sub-phase. Run `pytest tests/ -x --no-cov -q` then
> `ruff format --check src/ tests/ && ruff check src/ tests/ && mypy --strict src/auditk`
> before every commit.

### Sub-phase A — `Scorer` protocol + registry + Jaccard refactor (no behaviour change)

**Red** (`tests/unit/test_scorer_protocol.py`, new):
1. `test_jaccard_scorer_satisfies_protocol` — `JaccardScorer` is usable as `Scorer`;
   exposes `method == "plan-action-similarity"`, `method_version == "0.1"`.
2. `test_registry_resolves_jaccard_key` — `get_scorer("plan-action-similarity@0.1")`
   returns a working scorer; `available()` lists it.
3. `test_registry_unknown_key_raises` — clear error on bad key.
4. `test_compute_drift_delegates_to_default_scorer` — `compute_drift(t)` equals
   `get_scorer(DEFAULT_SCORER).score(t)`.

**Green:** create `analysis/protocols.py`, `analysis/scorers/{__init__,jaccard}.py`;
move the `drift.py` body verbatim into `JaccardScorer.score`; make `compute_drift` the
shim. **`tests/unit/test_drift.py` must pass unchanged** (the regression contract).

**Refactor:** dedupe; ensure registry import pulls **no** heavy deps; docstrings mark
jaccard deprecated.

**Gate:** all tests green incl. unchanged `test_drift.py`; lint + mypy clean.

### Sub-phase B — `NLIScorer` (`nli@0.2`)

**Red** (`tests/unit/test_nli_scorer.py`, new; uses `FakeNLIPredictor`):
1. `test_nli_scorer_satisfies_protocol` — `method=="nli"`, `method_version=="0.2"`.
2. `test_entailed_action_not_flagged` — sub-goal entails action → FAITHFUL, not flagged,
   contributes 0 to drift.
3. `test_contradicting_action_flagged` — contradiction → flagged; in `flagged_steps`.
4. `test_neutral_action_not_in_flagged_steps` — neutral → not flagged (D2 rule).
5. `test_entailed_by_any_subgoal_is_faithful` — **the step-1-of-5 fix**: a multi-sub-goal
   plan where only sub-goal 3 entails the action → FAITHFUL (regression vs the 0.926 FP).
6. `test_drift_score_is_contradiction_fraction` — 1 contradiction of 4 scored → 0.25.
7. `test_no_scored_steps_returns_zero` — no `declared_intent` anywhere → 0.0, `[]`.
8. `test_decompose_matches_d1_join_delimiter` — split mirrors the D1 join char.
9. `test_decompose_caps_subgoals_at_k` and `test_decompose_empty_falls_back_to_whole`.
10. `test_label_order_read_from_id2label` — predictor adapter maps by label *name*, not
    index (guards the contra/entail order swap).
11. `test_nli_scorer_deterministic` — two `score()` calls, identical `DriftReport`.
12. `test_missing_nli_extra_raises_actionable_error` — `get_scorer("nli@0.2")` without
    the extra → `ImportError` mentioning `auditk[nli]` (monkeypatch the import).
13. `test_real_model_classifies_known_pair` — **integration**, `@skipif RUN_NLI_MODEL`,
    `importorskip("torch")`: real model, one entail + one contradict pair.

**Green:** `analysis/scorers/nli.py` — `NLIScorer` + `TransformersNLIPredictor`
(lazy import, `local_files_only=True`, `id2label` mapping, batched `no_grad` forward);
register `"nli@0.2"` with a lazy factory; add `[nli]` extra to `pyproject.toml`
(`transformers>=4.40`, `torch>=2.2`, `sentencepiece>=0.2`).

**Refactor:** extract `decompose`, `render_action`, `_label_from_dist`; functions ≤ ~30
lines (`code-structure`); type everything for `mypy --strict`.

**Gate:** unit tests green torch-free; integration green locally with `RUN_NLI_MODEL=1`;
lint + mypy clean.

### Sub-phase C — selection wiring (`pack.build` + CLI)

**Red:**
- `tests/unit/test_pack_builder.py`: `test_build_accepts_scorer` — `build(..., scorer=…)`
  uses it (assert `drift_metrics.method`); default (no arg) still jaccard → existing
  pack tests unchanged.
- `tests/e2e/test_cli_attest_verify.py`: `test_attest_with_scorer_flag` — `attest
  --scorer nli@0.2` (FakeNLIPredictor or `RUN_NLI_MODEL`-gated) writes a pack whose
  `drift_metrics.method == "nli"`, and `verify` **passes**.

**Green:** add `scorer: Scorer | None = None` to `pack.build` (default →
`get_scorer(DEFAULT_SCORER)`, preserving current behaviour); add `--scorer` to the
`attest` CLI command, resolved via the registry with a clear error on unknown/uninstalled.

**Refactor:** tidy; ensure base (no-`[nli]`) install still runs the full default path.

**Gate:** full suite green; **`auditk verify demos/demo-001/evidence-pack.json` and
`demos/demo-005` still pass** (proves zero attestation regression); lint + mypy clean.

### E2E (per project `pm-workflow.md`)

Extend `tests/e2e/test_cli_attest_verify.py`: ingest a fixture session → `attest
--scorer nli@0.2` → `verify` round-trips green. Gate the real-model leg behind
`RUN_NLI_MODEL=1`; the default-scorer round-trip runs unconditionally. Committed as part
of the ticket, not run ad-hoc.

---

## 5. Acceptance criteria

- [ ] **AC1** `Scorer` protocol exists; `JaccardScorer` & `NLIScorer` both satisfy it.
- [ ] **AC2** Registry resolves `"plan-action-similarity@0.1"` and `"nli@0.2"`; unknown
      keys and a missing `[nli]` extra raise actionable errors.
- [ ] **AC3** `compute_drift` is a shim over the default scorer; **all existing
      `test_drift.py` tests pass unchanged** (Test Integrity).
- [ ] **AC4** `NLIScorer` is three-valued (entail/neutral/contradict) via `id2label`
      name-mapping (no hardcoded order).
- [ ] **AC5** Action entailed by **any** active sub-goal ⇒ FAITHFUL (step-1-of-5 fix).
- [ ] **AC6** `drift_score` = contradiction fraction; `flagged_steps` = contradiction
      steps only; param-free.
- [ ] **AC7** `NLIScorer` is deterministic, runs CPU-only, and never hits the network at
      runtime (`local_files_only=True`).
- [ ] **AC8** Base install (no `[nli]`) imports the registry and runs the jaccard default
      with no torch dependency.
- [ ] **AC9** `pack.build(..., scorer=…)` and `attest --scorer` select the scorer; method
      identity lands in the signed `drift_metrics`.
- [ ] **AC10** **No schema / spec / `verify` change**; `demos/demo-001` and
      `demos/demo-005` still `verify` green.
- [ ] **AC11** Sanity: on the C.0 real session, `nli@0.2` flags ≪ jaccard's 40/42
      (single digits), confirming FP collapse.

## Gate (Done When)

All ACs checked; `pytest tests/ -x --no-cov -q` green; `ruff format --check`,
`ruff check`, `mypy --strict src/auditk` clean; E2E committed; **demos still verify**;
reviewed. Then proceed to **D3** (plan-decomposition + two-stage judge) — not before.

---

## 6. Out of scope — what D2 does **NOT** include

- **LLM judge** / two-stage adjudication / boolean rubric → **D3**.
- **Fine 5-way taxonomy** (benign-elaboration vs undeclared-goal; goal-deviation vs
  instruction-noncompliance) → **D3** (needs constraints + judge; ambiguous boundaries).
- **Any schema / spec / `verify` change**, `per_step` field, `scorer_fingerprint` field,
  spec-v0.2 bump → **D3** (mechanism pre-designed in §3; *optional* D2.0 stretch only).
- **Calibration**: human-label set, Pearson r / Cohen's κ, thresholds (`τ_entail`,
  `τ_contra`), neutral weight `λ` → **D4**.
- **Cross-model benchmark** (Claude/Kimi/MiniMax/DeepSeek/GPT, seeds, CIs) → **D5**.
- **Changing the default scorer** — jaccard stays default; NLI is opt-in until D4
  calibrates it. (Flipping the default also avoids forcing torch on every install.)
- **Embedding-cosine rung** (research Rung A) — skip straight to NLI.
- **Auto-download / bundling of model weights**; an `auditk` fetch command — manual
  `huggingface-cli download` only; documented, not coded.
- **GPU, batching/perf tuning beyond a basic batched forward**, ONNX export.
- **Adapter changes** (D1 done), multi-turn/campaign probes, garak/Inspect-log adapters,
  richer anonymiser, full OpenInference span coverage — all roadmap "breadth deferral",
  gated behind D2.

---

## 7. Issues & Fixes

_(populate during execution — Test Integrity Rule: never silently change a test;
document any post-Green test change with a reason.)_

- _none yet_
