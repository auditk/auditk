# Proposal: `auditk share-case` — the opt-in feedback primitive

Status: design / scope (for implementation by a follow-up session). Not yet built.

## Why

auditk is privacy-preserving by design: local, no telemetry, no data egress, with
payload redaction already available (`ingest --strip-payloads`, `_maybe_redact`).
That is the right posture for its regulated audience, but it means we cannot learn
how the instrument performs in the wild by watching it. The only way to get quality
signal from a no-egress audience is to make **contributing a case a deliberate,
low-friction, user-controlled action**.

`share-case` turns a disagreement (a step where a human reviewer overrode a pipeline
label) into a redacted, anonymised, shareable **correction record** that the user
reviews and chooses to submit (attach to a disagreement issue, or PR into the gold
set). This is the concrete mechanism behind the §3.9 human-review layer, the §3.8
distillation loop, and the F10 public gold-standard dataset: each correction both
improves the pipeline and grows the shared benchmark.

The signed, redactable evidence pack is already the contribution primitive; this
command just makes producing a case from it a single step.

## Command

Fits `cli.py` as a sibling of `attest` / `verify` (typer, `@app.command()`):

```
auditk share-case \
  --pack evidence-pack.json \        # or --trace trace.json
  --step <step_id> \                 # repeatable; the disagreed step(s)
  --should-be <label> \              # the human's corrected label (optional)
  --note "<short free-text>" \       # optional, off by default in structure mode
  --redact structure|full \          # default: structure
  --out case.json
```

- `--redact structure` (default): keep tool *names*, labels, severities, and the
  judge's evidence quote; drop tool *arguments*, tool *results*, and the raw
  declared-intent/action text (replace with length + a hash).
- `--redact full`: additionally drop the evidence quote and the note; emit only
  structural fields (labels, tool names, gate/judge decisions, confidences).
- Never emits: signatures, keys, raw payloads, timestamps at finer than day
  granularity, or anything not in the schema below.

The command prints the record to stdout and writes `--out`, then prints a one-line
reminder: *"Review case.json before sharing; it is what you will make public."*

## Correction-record schema

A new `spec/v0.1/correction.schema.json` (additive; does not touch the frozen
four). One record per disagreed step:

```json
{
  "schema": "auditk/correction@0.1",
  "redaction_level": "structure",
  "auditk_version": "0.x.y",
  "adapter": "claude-code",
  "domain": "coding",
  "step": {
    "declared_intent": {"len": 142, "sha256_8": "a1b2c3d4"},
    "action": {"tool_name": "WriteFile", "arg_len": 88, "sha256_8": "e5f6a7b8"},
    "pipeline": {
      "gate_label": "neutral",
      "judge_label": "faithful",
      "confidence": 0.0,
      "severity": "LOW",
      "evidence": "n/a",
      "judge_model": "gpt-oss-120b",
      "nli_model": "cross-encoder/nli-deberta-v3-small"
    },
    "human": {"corrected_label": "instruction_noncompliance", "note": ""}
  }
}
```

`full` mode drops `evidence`, `note`, and the `declared_intent`/`action` text
metadata beyond `tool_name`.

## Implementation notes

- Reuse, do not reinvent: `_maybe_redact` (adapters) for payload stripping, the
  evidence-pack reader used by `verify`/`diff`, and the `StepDrift` fields
  (`label`, `severity`, `evidence`, `reasoning`, `overturned_gate`) that already
  carry the judge output.
- Pull `judge_model` from the pack's pinned model version (added in `feat(schema)`),
  and `nli_model` from the gate config.
- The hash is a truncated SHA-256 of the redacted-out text, so two users who hit the
  same underlying step produce the same hash (dedup) without revealing content.
- `--from-annotations <db>`: a convenience path that reads the annotation tool's
  export (`/api/export`) and emits one correction record per human-vs-pipeline
  disagreement, so the existing review UI feeds the loop directly.

## Skeleton (illustrative; wire to the real modules)

```python
@app.command(name="share-case")
def share_case(
    pack: str = typer.Option(None, help="Evidence pack to extract the case from."),
    trace: str = typer.Option(None, help="Trace file (alternative to --pack)."),
    step: list[str] = typer.Option(..., help="Step id(s); repeatable."),
    should_be: str = typer.Option(None, "--should-be", help="Human's corrected label."),
    note: str = typer.Option("", help="Optional short note."),
    redact: str = typer.Option("structure", help="structure | full"),
    out: str = typer.Option("case.json", help="Output correction record."),
) -> None:
    source = load_pack(pack) if pack else load_trace(trace)
    records = []
    for sid in step:
        sd = source.step_drift(sid)          # StepDrift: label/severity/evidence/...
        st = source.trace_step(sid)          # declared_intent, action, tool_name
        records.append(build_correction(
            st, sd,
            corrected_label=should_be,
            note=note if redact != "full" else "",
            redact=redact,
            judge_model=source.judge_model,
        ))
    write_json(out, records if len(records) > 1 else records[0])
    typer.echo(f"Wrote {out}. Review it before sharing; it is what you will make public.")
```

`build_correction` applies `_maybe_redact`, computes the truncated hashes, and drops
the `full`-mode fields. Add a validator against `correction.schema.json`.

## Done when

- `auditk share-case --pack ... --step ... --should-be ... --out case.json` emits a
  schema-valid record with zero raw payloads at `structure` level and zero free text
  at `full` level (test: assert no tool arguments / results / raw intent text appear
  in the output for a fixture pack).
- `--from-annotations` emits one record per disagreement from an annotation export.
- `correction.schema.json` added to `auditk-spec` (additive) with a round-trip test.
- README "Feedback" section and the disagreement issue template both point here.
