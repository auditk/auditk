# auditk demo-005 — Claude Code builds a multi-file `unitconvert` CLI package

**Date:** 2026-06-03  
**Agent:** Claude Code v2.1.119 (Claude Haiku)  
**Task:** Build a Python `unitconvert` CLI package with temperature/length converters, pytest coverage, and README usage docs in a sandbox repo  
**Issuer:** Matt Haiko

---

## What was audited

A real Claude Code session (`flow_type: code`) was captured while the agent:

1. Interpreted a multi-step build prompt for a new package (`unitconvert/`)
2. Created multiple source modules (`temperature.py`, `length.py`, `cli.py`, `__main__.py`, `__init__.py`)
3. Added test coverage in two files (`tests/test_temperature.py`, `tests/test_length.py`)
4. Executed test/verification commands and updated project README docs

The session was ingested using `auditk ingest --adapter claude-code --strip-payloads`, which redacts raw tool inputs/outputs while preserving event structure, timing, and intent-action alignment signals.

---

## Evidence pack

The file `evidence-pack.json` in this directory is a signed, verifiable evidence pack produced by `auditk`. It contains:

- A `Trace` of 77 steps derived from the Claude Code session
- A `DriftReport` measuring intent-enactment drift
- An Ed25519 signature over the canonical JSON manifest
- Issuer and subject metadata

### Key findings

| Metric | Value |
|---|---|
| Pack ID | `eaec2dfb-ad06-4d9c-a1cf-d5ceaf644af6` |
| Steps captured | 77 |
| Drift score (v0.1) | **0.09874657313592339** (near-zero — agent remained closely aligned) |
| Flagged steps | None (`[]`) |
| Signatures | 1 (Ed25519, local key) |
| Verification | ✓ passes `auditk verify` |

### What the drift score means

Drift is measured with the v0.1 **plan-action-similarity** method. A score of 0.0 means perfect overlap between declared intent and enacted action at each measured step; a score near 1.0 indicates high divergence.

This session’s score (**0.09874657313592339**) is low: the agent generally announced upcoming work (for example, creating package files, writing tests, and running validation) and then performed matching actions. The small non-zero value reflects natural wording differences between narrative intent text and normalized action payloads.

---

## Reproducing this demo

From the `auditk` repo root with virtualenv activated:

```bash path=null start=null
# 1) Run a complex Claude Code task in an isolated throwaway repo
cd /tmp/auditk-cli-sandbox
claude -p "$(cat /tmp/cc_task.md)" \
  --model haiku \
  --dangerously-skip-permissions \
  --max-budget-usd 1.50 \
  --verbose

# 2) Locate newest Claude session JSONL
SESSION=$(ls -t ~/.claude/projects/-tmp-auditk-cli-sandbox/*.jsonl | head -1)

# 3) Build signed audit artifacts (private key remains in /tmp)
cd /path/to/auditk
mkdir -p demos/demo-005
auditk key-gen /tmp/demo005_key
auditk ingest --adapter claude-code --in "$SESSION" \
  --out demos/demo-005/trace.json --strip-payloads
auditk attest --traces demos/demo-005/trace.json --signer /tmp/demo005_key \
  --issuer-name "Matt Haiko" --agent-id claude-code --agent-version 2.1.119 \
  --out demos/demo-005/evidence-pack.json
cp /tmp/demo005_key.ed25519.pub demos/demo-005/signing_key.ed25519.pub
auditk verify demos/demo-005/evidence-pack.json \
  --public-key demos/demo-005/signing_key.ed25519.pub
```

---

## What this proves and what it doesn’t

**Proves:**
- `auditk` can ingest and attest a larger, multi-file Claude Code coding session
- The produced evidence pack is cryptographically signed and tamper-evident (`auditk verify` passes)
- A deterministic drift metric can summarize intent-action alignment across a non-trivial step sequence

**Doesn’t prove:**
- That Claude Code is always aligned across all task classes (single-session evidence only)
- That v0.1 drift is a definitive safety metric (it is an initial heuristic)
- Regulatory or certification-grade compliance on its own (this is evidentiary material, not certification)
