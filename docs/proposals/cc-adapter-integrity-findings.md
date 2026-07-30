# CC Adapter Integrity (P1-P4): Findings (2026-07-29)

## Outcome

The claude-code adapter's blind spots identified in
`docs/proposals/session-postmortem-reporting.md` (Phase 5, "Finding A", and
Phase 6, "Subagent traces") are closed. The adapter now fails loudly
instead of silently when its parsing assumptions break, and it correctly
attributes work a session's subagents actually did instead of treating it
as invisible.

This work ran in four phases, each with its own RED/GREEN/refactor commits:

1. **P1, provenance.** `scripts/corpus_stats.py`, a read-only script that
   reproduces the corpus-wide claims a prior review made, on demand,
   against whatever corpus is on disk.
2. **P2, the canary.** `adapters/health.py`'s `check_adapter_health` and the
   `auditk doctor` CLI command. A corpus-level invariant (at least one
   known plan anchor across 20+ sessions) plus two per-session structural
   checks (tool-call/tool-result pairing, unknown-record-type share).
   `auditk report` now refuses to emit a report over a session that fails
   this check, instead of printing a confidently wrong number.
3. **P3, ingestion.** Subagent (delegate) transcripts are discovered and
   flattened into the parent trace, correctly attributed and correctly
   intentioned, instead of being marked `delegation_unobserved` and
   dropped.
4. **P4, separation.** Delegate steps are visible to structural
   rule-checking but excluded from the parent trace's intent-enactment
   drift score, with a separate per-agent figure computable instead.

## The blind spot, reproduced

Running `python scripts/corpus_stats.py` against the local corpus today
(90 sessions) shows:

```
plan-anchor tool calls (parent transcripts):
  TodoWrite    0
  TaskCreate   111
  TaskUpdate   185

subagent (delegate) transcripts:
  subagent transcript count: 136
  sessions containing subagent transcripts: 36
  delegate tool-call counts:
    Bash    3386
    Read    1452
    Edit    695
    Write   249
    ... (36 more tool names, long tail)
```

Two things worth naming plainly:

- `TodoWrite` sits at zero. The harness renamed its plan-tracking tool at
  some point between when the adapter was written and today, and the
  adapter kept running without complaint. It just quietly stopped
  recognizing the modern anchor. Nobody would have known without running
  this script.
- 695 Edits and 249 Writes were made by subagents, none of them visible to
  a parent-trace-only reading of these sessions. Before P3, an audit tool
  that reads only the parent transcript missed roughly a third of the file
  mutations a session actually performed.

These are live, reproducible numbers, not a fixed historical snapshot; the
corpus keeps growing, including from the sessions that did this work, so a
later run of the same command will show different (larger) totals. That is
the point of building it as a script instead of writing the numbers down
once.

## What shipped

- **`adapters/health.py`**: `AdapterHealth`, `SessionHealthInput`,
  `SubagentHealthInput`, `check_adapter_health`, `KNOWN_RECORD_TYPES`,
  `PLAN_ANCHOR_TOOL_NAMES`. Pure, never raises, corpus-size-independent
  per-session checks plus a corpus-level dead-anchor invariant.
- **`analysis/corpus_walk.py`**: the corpus-walking primitives
  (`discover_sessions`, `iter_jsonl`, `has_plan_store`, `count_tool_calls`,
  ...) shared between `scripts/corpus_stats.py` and `auditk doctor`, so the
  two never drift out of sync on how the on-disk layout is discovered.
- **`adapters/claude_code.py`**: `SubagentTranscript`,
  `load_subagent_transcripts`, and a `subagents` parameter on
  `ingest_claude_code_session` that joins each subagent transcript to its
  owning `Task` step by `toolUseId`, flattens the subagent's own steps in
  with `parent_step_id` pointing at that `Task` step and
  `metadata["agent_id"]` set, and seeds the subagent's `declared_intent`
  from its own brief (its persisted `meta.json` description plus the
  parent's `Task` prompt), never from the parent's own standing plan.
- **`analysis/drift.py`**: `compute_drift` now scores only a trace's own
  steps, excluding anything carrying `metadata["agent_id"]`, so a trace's
  drift score is identical whether or not it contains delegate work. A new
  `compute_delegate_drift` returns a separate drift figure per delegate,
  keyed by agent id, computed only over that delegate's own steps.
- **`cli.py`**: `auditk doctor` (new subcommand); `auditk report` gained
  `--force` to override a health-check refusal; both `ingest` and `report`
  now auto-discover and load a session's sibling `subagents/` directory
  with no new flag required.

## Key decisions

- **The canary is corpus-level for the dead-anchor case, per-session for
  the cheap structural checks.** Whether a plan-tracking tool has vanished
  needs enough sessions to distinguish "genuinely gone" from "this session
  just didn't happen to use it." Tool-call/tool-result pairing and
  unknown-record-type share need no such threshold and run on a single
  session, which is what lets `auditk report` use the same function for
  its one ingested session.
- **A known-benign record-type allowlist, not a bare user/assistant
  filter.** A real parent transcript contains sixteen record types, not
  two; treating "not user or assistant" as "unhandled" would have flagged
  nearly every healthy session as broken. The allowlist is the actual
  contract: a type outside it crossing the threshold is a format-change
  signal worth a human's attention, not routine harness chatter.
- **Delegate steps flatten flat, not as a tree.** Every step a subagent
  produced points directly at the `Task` step that spawned it, rather than
  chaining through the subagent's own internal call order. Simpler to
  reason about and matches what a report reader actually wants to know:
  which delegation produced this action.
- **Drift exclusion lives in one place.** `compute_drift` builds a
  delegate-free view of the trace before handing it to whichever scorer is
  configured, rather than teaching every scorer implementation about
  `agent_id`. `attestation/pack.py`'s evidence-pack drift score inherited
  the fix with no changes of its own, since it only ever calls
  `compute_drift`.
- **Structural findings were never touched.** `analysis/findings.py` sees
  every delegate step exactly as it sees a parent step; only the
  intent-enactment drift number treats them differently. A delegate `Edit`
  outside the allowed roots is still flagged by the same scope-escape rule
  that flags a parent-level one.

## Known limitations (honest)

- **Broken subagent-layout detection is not yet wired end to end.** The
  health check can detect a `subagents/` directory entry with no matching
  `meta.json`, or a `meta.json` missing its join key, but
  `load_subagent_transcripts` currently drops both cases silently rather
  than surfacing them, so `cli.py`'s wiring only exercises the "loaded
  successfully, check its own record types" path against a real corpus.
  Surfacing on-disk load failures themselves, not just their absence, is a
  further increment.
- **Per-agent drift is a bare function, not yet surfaced anywhere.**
  `compute_delegate_drift` exists and is tested but nothing in `cli.py` or
  `attestation/pack.py` calls it yet. `auditk report` still has no drift
  concept at all, by design; whether a per-agent figure eventually belongs
  there is an open question, not answered here.
- **`TaskCreate`/`TaskUpdate` task-index reconstruction is still
  best-effort.** Nothing in this phase touched
  `_resolve_task_index`'s known limitation (assumes 1-based creation
  order); it predates this work and is out of scope for it.

## Next steps

1. Decide whether `auditk report` should ever surface per-agent drift, and
   if so, in what section.
2. Extend `load_subagent_transcripts` (or a sibling discovery function) to
   report on-disk load failures explicitly, not just by omission, so the
   health check's layout-break detection can run against a real corpus
   rather than only against synthetic fixtures.
3. Re-run `auditk doctor` against the live corpus periodically (or wire it
   into CI) so the next harness format change is caught in days, matching
   the original motivation for building this canary at all.
