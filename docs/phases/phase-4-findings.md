# Phase 4 — POC Findings (2026-06-02)

## Outcome

The attest-first POC pipeline is operational end-to-end. A Claude Code session
becomes a signed, verifiable evidence pack carrying an intent–enactment drift
score, with no live endpoint and no network:

```
key-gen   → Ed25519 keypair
ingest    → Claude Code session JSONL → Trace (claude-code adapter)
attest    → signed evidence pack (drift + TraceSummary, canonical-JSON signed)
verify    → signature checked against a trusted public key
```

Verified run (synthetic `session-intent-action.jsonl`, payloads stripped):
6 steps → Pack ID `3b6823c9…` → `✓ verified` → `drift_score 0.545`
(method `plan-action-similarity` v0.1).

## What shipped

- **`claude-code` adapter** — maps a Claude Code session (JSONL event tree) to a
  `Trace`: assistant narration → `declared_intent`; `tool_use` → `tool_call`
  steps; `tool_result` → `env_effect`; `parentUuid` threading preserved;
  `flow_type=code`. `strip_payloads` mode redacts tool inputs/results.
- **Attestation layer** — `canonicalize()` (sort-keys JSON), Ed25519
  `generate_keypair`/`LocalEd25519Signer`/`LocalEd25519Verifier`, and the
  evidence-pack `build()` (computes `TraceSummary`, runs drift, signs the
  manifest excluding `signatures`).
- **POC CLI** — `key-gen`, `ingest`, `attest`, `verify`. `probe`/`replay`/`diff`
  remain stubs.
- 98 tests pass; `mypy --strict` clean on all new modules.

## Key decisions

- **Attest-first over probe-first.** Probing needs a live target and the v0
  scoring heuristic is weak; the attest/drift path reuses built components and
  is fully deterministic. Probe path parked for Phase 4b.
- **Trust model.** `verify` checks signatures against the public key supplied on
  the command line — the trust anchor — NOT the key embedded in the pack. This
  is deliberate: trusting the embedded key would let a re-signed tampered pack
  pass.
- **Privacy.** Real sessions contain private code; `strip_payloads` keeps tool
  names, types, and timing while redacting inputs/results so packs built from
  real sessions are safe to publish.

## Known limitations (honest)

- **Drift heuristic is v0.** `plan-action-similarity` is token-Jaccard between
  declared intent and action payload. It reports moderate drift (0.545) on the
  synthetic fixture because the narration only loosely token-matches the tool
  inputs. It is a placeholder metric: the *contract* (a published, reproducible
  drift number) is the deliverable, not the algorithm. Real validation is a
  Phase D correlation study against human labels.
- **`verify` vacuously passes a pack with zero signatures.** `build()` always
  signs, so this cannot occur via the happy path, but `verify` should reject an
  empty `signatures` list. Hardening follow-up.
- **Pre-existing `mypy --strict` errors** in `loader.py` and `langgraph.py`
  (missing `types-PyYAML`, a couple of `no-any-return`). Latent because CI is
  not wired yet. Fix when CI lands (Phase 8 of the original plan).

## Next steps

1. **T4.9 — the demo.** Run the pipeline against a *real* Claude Code session on
   a sandbox/public repo. Commit the evidence pack under `demos/` with a README
   explaining what was audited and what the drift score means. This is the
   publishable artefact.
2. **Harden `verify`** to reject empty-signature packs.
3. **Wire CI** (lint + mypy + pytest) so the pre-existing strict errors surface
   and stay fixed.
4. **Phase 4b** (after the demo lands): finish the probe path
   (loader/http_prober/scoring/runner already built → add `run_suite`, the
   `glasshouse-probes-jailbreak` family, and the `probe` CLI command) against the
   testbed's deliberately-vulnerable agent.
5. **More adapters** (Phase C): OpenClaw, Hermes — each a demo + essay.
