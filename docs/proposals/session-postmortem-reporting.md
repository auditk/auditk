# Proposal — Session Post-Mortem Reporting

**Status:** Phases 0 to 4 shipped; Phases 5 to 6 complete as of 2026-07-29
(see `docs/proposals/cc-adapter-integrity-findings.md` for the results write-up).
**Author:** orchestrator (Opus), 2026-07-26
**Executor:** CC session (see Phasing; fast mode unless noted)
**Repo:** `auditk`

---

## 1. Why

auditk's flagship domain is the coding agent. The README cites Claude Code sessions
as its proof of concept. Nobody has run it against a current session since
2026-06-07.

Running it today, against a real session, surfaces the following.

### Finding A — the intent anchor no longer exists

`adapters/claude_code.py` derives a "standing plan" from `TodoWrite` tool calls
(`PlanState`, `_extract_todos`, `_active_goal_text`, `_update_standing_plan`,
lines 31-111).

Across the entire local corpus (180 sessions, 2026-06-07 → 2026-07-26):

| Tool | Occurrences |
|---|---|
| `TodoWrite` | **0** |
| `TaskCreate` | 67 |
| `TaskUpdate` | 108 |

The harness renamed the tool. The standing-plan mechanism has been dead code
against live sessions for an unknown number of weeks, and nothing failed loudly.

### Finding B — the default scorer is a false-positive machine on this domain

Session `193be0c2` (2026-07-26, ~1h, 679 steps) scores **drift 0.813 with 346/426
scored steps flagged**. The June 2026 real-session baseline was 0.016 – 0.210.

That session was, structurally, well behaved: TDD followed rigorously (RED proven
via `git stash --keep-index` before each GREEN), 12 clean commits, tests green,
docs written, PR opened.

The cause is visible in a single example:

```
INTENT : I'll open it in the browser and check the console, network activity, ...
ACTION : {"name": "mcp__Claude_Browser__read_console_messages", ...}   → FLAGGED
```

This is near-perfect intent-enactment fidelity. `plan-action-similarity@0.1` is
token Jaccard between prose narration and a JSON tool payload; `read_console_messages`
is one token, so overlap is ~0. The scorer punishes correct behaviour.

63% of steps do carry a `declared_intent` (narration fallback). The problem is not
emptiness, it is that the comparison is structurally mismatched.

### Finding C — what actually diagnosed the session was deterministic, not semantic

The session was diagnosed by hand in minutes using only file paths, tool names,
timestamps and exit codes:

| Signal | Derived from | Result |
|---|---|---|
| Build timeline | tool sequence + timestamps | TDD genuinely followed |
| Scope escape | write paths vs working dir | `.claude/launch.json` edited outside the target repo |
| Churn burst | consecutive edits, no verify between | 6× `script.js`, 20:25:53–20:26:40 |
| Instruction compliance | user's words vs timeline | "use our TDD workflow" — honoured |
| Delegation blindness | `Task` calls vs sidechain records | corpus-wide: 104 delegations, **0** traces |

None of these need a model, an API, or any egress. This is the same
"bound by consequence, not input" principle already adopted for probe selection.

**Conclusion: the primary instrument for coding-session forensics should be
deterministic and structural. Semantic drift is a secondary, optional layer.**

---

## 2. What we are building

An `auditk report` command that produces a **single-session post-mortem**: what
happened, whether it complied with the instructions actually given, and where it
went wrong.

Explicitly *not* in scope: cross-session trend aggregation (deferred; see §7),
paper/benchmark work, any change to bossyk.

### Design constraints

1. **Fully offline by default.** No component in this proposal makes a network
   call. The only existing egress path (`llm-judge@0.3` → Fireworks) stays opt-in
   and out of scope here.
2. **Rule engine and rule content are separate.** The predicate vocabulary and
   evaluator live in auditk (shareable). The ruleset is a local file, never
   committed. Matt's constitution files stay in the vault, unshared. See §5.
3. **No coverage theatre.** The report must state what it did *not* check. Most
   constitution rules ("prefer extending existing code") are not mechanically
   checkable; the ruleset is an explicitly curated checkable subset, and the
   report says so.
4. **Extend, do not duplicate.** `adapters/claude_code.py`, `schema.Trace/Step`
   and the CLI are extended. `Step.metadata` is a free dict and absorbs new
   fields without a schema change.

---

## 3. Phasing

TDD per phase (Red → Green → Refactor → Gate), one commit per logical unit.
Run `pytest` after each meaningful change.

### Phase 0 — Fixtures and the characterisation test

- Build a **synthetic** fixture reproducing the structure of a modern session:
  `TaskCreate`/`TaskUpdate`, `thinking` blocks, tool errors, a scope-escaping
  write, a churn burst. This is the CI fixture — it contains no real data.
- Add a **local-only** integration test against a real session, gated behind
  `RUN_REAL_SESSION_TEST=1` plus an explicit path env var. Never committed data.
- RED: assert current behaviour is wrong — a well-behaved synthetic session
  currently scores as heavily drifted.

**Gate:** the failing test encodes Findings A and B.

### Phase 1 — Adapter fidelity

`adapters/claude_code.py`:

- **Intent anchor:** prefer the **persisted plan store** at
  `~/.claude/tasks/<session-uuid>/N.json`. Each file is a structured task
  (`id`, `subject`, `description`, `activeForm`, `status`, `blocks`,
  `blockedBy`) — the standing plan as a first-class object with completion
  status and dependency edges, rather than something reconstructed by scraping
  tool calls. Fall back to `TaskCreate`/`TaskUpdate` calls in the transcript
  when the store is absent, and keep `TodoWrite` handling for back-compat with
  the June-era traces and demos. Factor the anchor into a tool-name → extractor
  mapping so the next rename is a data change, not a code change.
- **`thinking` blocks → `declared_intent`.** Currently dropped entirely (2,752
  in the 25 most recent sessions). This is the highest-quality intent signal
  available and it directly fixes June's Finding 2 (pre-plan steps scoring as
  spurious deviation).
- **Preserve `is_error`** on tool-result steps. Currently lost — error-density
  and failed-command rules cannot be written without it.
- **Trace-level metadata** (currently `{}`): `cwd` (note: sessions span more
  than one), `gitBranch`, `sessionId`, `version`, model, time span.
- **Step-level metadata:** `permissionMode`, token usage where present.
- **Delegation markers:** record `Task`/`Agent` calls explicitly as
  `delegation_unobserved` so the blind spot is visible in the trace rather than
  silently absent.
- Handle or explicitly ignore the other dropped record types (`attachment`,
  `system`, `image`, `file-history-*`), with a documented decision per type.

**Gate:** Phase 0's synthetic fixture ingests with intent coverage and error
flags present; the drift false-positive rate on it collapses.

### Phase 2 — Structural findings engine

New module, e.g. `analysis/findings.py`. Deterministic, offline, no model.

Emits `Finding` records — **not** a scalar score:

```
Finding: rule_id, severity, title, step_ids[], evidence, explanation
```

Predicate vocabulary (each a pure function over `Trace`):

| Predicate | Catches |
|---|---|
| `write_outside_roots(roots)` | scope escape (the `launch.json` case) |
| `edits_without_verify(n)` | churn bursts, unverified rewrites |
| `commit_without(check)` | commit with no preceding test/lint run |
| `bash_matches(pattern)` | tripwires: `rm`, migrations, `.env`, infra paths |
| `tool_error_cluster(k, window)` | thrashing / repeated failure |
| `delegation_unobserved()` | subagent blind spot |
| `artifact_abandoned()` | files created then never referenced again |

Ordering/sequence predicates need step ordering: use `timestamp`, and treat
`parent_step_id` as a tree not a sequence (it is not linear).

**Gate:** on the synthetic fixture, the engine finds exactly the planted
anomalies and no others. False positives are the failure condition.

### Phase 3 — `auditk report`

New CLI command. Consumes a `Trace`, emits markdown (human) and JSON (machine).

Sections:

1. **Header** — project, branch, duration, step/tool counts, cost if available.
2. **Timeline** — the condensed build timeline. This is the single most
   informative artifact; it is what diagnosed `193be0c2` by eye. Collapse noise,
   surface test/commit/write/error events.
3. **Findings** — ranked by severity, each with step references and evidence.
4. **Instruction compliance** — the user's own turns extracted, each paired with
   what followed. ("use our TDD workflow" → the timeline that honoured it.)
5. **Not checked** — explicit list of rules in the ruleset that could not be
   evaluated, and categories out of scope. Anti-coverage-theatre.

**Gate:** `auditk report` on the real `193be0c2` session produces a post-mortem
that names the `launch.json` scope escape and the `script.js` churn burst, and
does *not* flag the TDD work as a problem.

### Phase 4 — Ruleset loading, environment discovery, and the CLAUDE.md bridge

auditk is a **public** repo (`github.com/auditk/auditk`). A ruleset that encoded
one user's constitution would leak that user's setup into shared code. So the
governing principle is **three layers, only the middle ships**:

| Layer | Ships? | Contents |
|---|---|---|
| Engine + predicate vocabulary | yes, generic | the seven predicates, `Finding` types |
| `rules.default.yaml` | yes, generic | universally-safe defaults: `rm -rf`/`DROP TABLE`/force-push tripwires, generic thresholds, roots = auto git-root. **What runs on any machine with zero config.** |
| Local ruleset | **never** | a user's own `auditk.rules.yaml`, gitignored, out of the repo entirely |

Three mechanisms keep it generic rather than bespoke:

1. **Discovery cascade** (mirrors how Claude Code resolves CLAUDE.md itself):
   `--rules PATH` › `$AUDITK_RULES` › a repo-local `.auditk/rules.yaml` walking up
   from the session's cwd › `~/.claude/auditk.rules.yaml` › the shipped default.
   Layered merge — a project ruleset extends the global, which extends the
   default. A machine with none of these gets the safe defaults.
2. **Roots auto-discovery** = the git top-level of the session's cwd (deterministic,
   works anywhere). Removes any hardcoded path and simultaneously fixes the Phase 2
   scope-escape roots limitation (default roots were the first-event cwd, too broad
   for sessions that `cd` into a repo).
3. **CLAUDE.md is read from wherever it lives on the running machine**, never a
   baked-in path.

**The CLAUDE.md bridge (decision: surface + opt-in scaffold, deterministic, no LLM):**
- **Surface:** the report discovers the CLAUDE.md cascade in effect for the session
  (global `~/.claude/CLAUDE.md`, plus `CLAUDE.md` and `.claude/CLAUDE.md` walking up
  from the session cwd, plus `.claude/rules/*.md`) and lists them in a **Policy
  context** section, so a reader knows what standard the session ran under — on
  whatever machine, reading that machine's files. Rules themselves still come from
  the ruleset layer.
- **Scaffold:** an opt-in `auditk rules init` keyword-scans the discovered CLAUDE.md
  for known hard-rule patterns (`never delete`, `before every commit`, `.env`,
  `migration`, `infrastructure`) and emits a **starter** `rules.yaml` the user then
  edits. The scaffold is a reviewed convenience, never the live rule source; the
  live path stays fully deterministic.

**Ruleset schema (sketch):** roots (or `auto`), scratch prefixes, per-predicate
enable + thresholds, tripwire name→regex map, and a `verify_patterns` list — i.e.
the existing `FindingsConfig` fields, made loadable/mergeable from YAML plus a
`disclaimer` field. No new predicates; this is config plumbing.

**Documentation** (`docs/rules.md`): the predicate catalogue, the default ruleset,
the discovery cascade, how to author a private ruleset, and the explicit non-goal —
auditk ships generic checks, reads whatever CLAUDE.md is on this machine, and a
user's constitution stays local and unshared.

**Gate:** the repo contains no private rule content (a test asserts the shipped
default is generic); `report` output differs correctly with and without a local
ruleset; on a machine with no config the defaults run; the Policy-context section
lists the real CLAUDE.md cascade for a given session cwd.

### Phase 5 — Adapter self-check (canary)

**Complete (2026-07-29).** Shipped as `adapters/health.py`'s
`check_adapter_health` plus the `auditk doctor` CLI command. Results and
known limitations: `docs/proposals/cc-adapter-integrity-findings.md`.

The lesson of Finding A is not "TodoWrite got renamed". It is that **the adapter
parses a private, explicitly unstable format and failed silently when it
changed.** The Claude Code docs say so directly: *"The entry format is internal
to Claude Code and changes between versions, so scripts that parse these files
directly can break on any release."*

So the tool must fail loudly rather than emit a confident wrong number:

- Record the harness `version` on the trace (available per record).
- Assert invariants at ingest: intent coverage above a floor, at least one known
  plan anchor present, tool-call and tool-result counts balanced, no unhandled
  record type above a threshold share.
- On breach, **refuse to emit a drift score** and report an adapter-health
  failure instead. A 0.813 that means "the anchor is gone" is worse than no
  number.
- Add a nightly or on-demand check against the most recent local session so a
  format change is caught in days, not weeks.

**Gate:** simulate a renamed anchor tool in the synthetic fixture; the pipeline
raises an adapter-health failure rather than scoring it.

### Phase 6 — Subagent traces (the data already exists)

**Complete (2026-07-29).** Shipped as `adapters/claude_code.py`'s
`load_subagent_transcripts` plus the `subagents` parameter on
`ingest_claude_code_session`, and, on the scoring side (folded into this
phase as D6), `analysis/drift.py`'s delegate-aware `compute_drift` and new
`compute_delegate_drift`. Results and known limitations:
`docs/proposals/cc-adapter-integrity-findings.md`.

**Correction to an earlier reading of this corpus.** Subagent activity is not
missing. It is stored one directory deeper than a `projects/*/*.jsonl` glob
reaches:

```
~/.claude/projects/<slug>/<session-uuid>/subagents/agent-<agent-id>.jsonl
```

Present locally today:

| | |
|---|---|
| Subagent transcripts | **105** across 32 sessions |
| Records | 13,042, **all** `isSidechain: true` |
| Delegate tool calls | 2,521 Bash, 1,105 Read, **516 Edit, 192 Write** |

516 edits and 192 writes were performed by delegates and appear nowhere in the
parent transcript. An audit tool that reads only the parent trace is missing
real mutations — which makes this a correctness gap in auditk's core claim, not
a nice-to-have.

Correlation is straightforward: the filename carries the agent id, which is the
same id the parent's `Task`/`Agent` tool call returns.

Work:
- Discover the sibling `subagents/` directory during ingest.
- Ingest each child transcript and attach it beneath the parent's `Task` step,
  preserving attribution (which delegate did what).
- Ensure the structural rules from Phase 2 evaluate over delegate steps too —
  a scope escape by a subagent is still a scope escape.
- Decide and document how a delegate's steps affect trace-level intent scoring
  (the delegate has its own brief; it should be scored against *that*, not the
  parent's plan).

**Retrospective recovery is possible** for the 32 sessions that have them. No
hook is required. `SubagentStart`/`SubagentStop` hooks do exist and provide
`agent_transcript_path` if a durable copy is ever wanted, but they are not
needed to read what is already on disk.

Caveat: this layout, like `isSidechain` itself, is undocumented and the docs warn
the format changes between releases. Phase 5's canary must cover it.

**Gate:** a session containing a delegation produces a trace in which the
subagent's own tool calls are present, attributed, and rule-checked.

---

## 4. Deferred, with reasons

| Item | Why deferred |
|---|---|
| Fixing the semantic scorer | Structural layer delivers most value at zero egress. Once Phase 1 lands, re-evaluate whether `plan-action-similarity` should be repaired or demoted to opt-in. |
| Cross-session trend aggregation | Needs the per-session layer first. Phase 3's JSON output is designed to be the record it would aggregate. |
| Parsing CLAUDE.md / constitution directly | Natural-language rules need interpretation, which drags a model back into the loop and breaks the offline constraint. |
| Subagent trace capture | The 104-delegation blind spot is a harness-level gap, not fixable in the adapter. Phase 1 makes it *visible*; closing it is separate work. |
| Redaction / scrubbing | Not needed while everything stays local. Becomes a hard blocker before any trace leaves the machine — see §6. |

---

## 5. Rule content and privacy

The separation:

- **auditk repo (shareable):** predicate vocabulary, evaluator, report renderer,
  public default ruleset.
- **Local only (never committed):** Matt's ruleset derived from CLAUDE.md and the
  vault constitution files.

The vault constitution is not read by auditk, not copied, and not referenced by
path in committed code. This also matches the open-core split already in use
(auditk open, policy-relative layer proprietary).

---

## 6. Egress note

Everything in this proposal runs locally. Worth recording, because it is easy to
assume otherwise:

- `--strip-payloads` is **not** a cleaning step. On a real session it redacted 304
  tool payloads but kept 224 utterance steps in the clear — ~157KB of prose,
  carrying absolute paths and project names. It removes code and keeps
  conversation.
- There is no PII or secret scrubber anywhere in the repo (confirmed absence).
- Before any trace, evidence pack or report leaves this machine, a real
  redaction pass is required. Out of scope here; flagged as a prerequisite.

---

## 7. Success criterion

Run `auditk report` on a session that felt bad, and get an answer that is true,
specific, and actionable — without sending anything anywhere.

The `193be0c2` session is the acceptance fixture: a genuinely well-behaved
session with two real, subtle anomalies. A report that praises the TDD, names the
scope escape and the churn burst, and admits what it did not check, is the target.
