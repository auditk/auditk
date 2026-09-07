# Pi adapter — format notes (CONFIRMED against real bytes, 2026-09-07)

**Status: evidence gate cleared.** This page was rewritten from a first-party
live corpus (real session files written by an npm-installed pi, committed
under `tests/fixtures/pi/`) plus a read of the installed release's own writer
source — the same discipline `hermes.py` followed. The previous revision of
this page was built from public docs only and said so loudly; its "what a
sample must show us" checklist is resolved item-by-item at the bottom.

## Which "Pi" this is — now confirmed

**Mario Zechner's `pi` coding agent.** Installed and run locally:
`@earendil-works/pi-coding-agent` **0.85.1** from npm (`pi --version` →
`0.85.1`; the older `@mariozechner/pi-coding-agent` mirror is at 0.73.1 and
stale). Source read at `github.com/earendil-works/pi` plus — decisive for
format questions — the **installed package's own compiled declarations**
(`dist/core/session-manager.d.ts`, `dist/core/messages.d.ts`), which are the
writer that produced our fixture bytes.

Radek Gruchalski confirmed on the 2026-09-07 call that pi is his harness.
Which extensions/skills he runs is still unconfirmed (see "What still needs
Radek" below).

## The on-disk format (v3, confirmed)

One JSONL file per session at
`~/.pi/agent/sessions/--<encoded-cwd>--/<ISO-timestamp>_<uuidv7>.jsonl`.

**Header (line 1):**

```json
{"type":"session","version":3,"id":"<uuid>","timestamp":"<ISO8601>","cwd":"/abs/path"}
```

`parentSession` (optional) appears on forked sessions and is an **absolute
file path to the parent session file**, not an id — a real cross-session
link, stronger than anything Hermes has. `CURRENT_SESSION_VERSION = 3`;
versions 1–2 auto-migrate on load (`migrateSessionEntries`). `version` is
typed *optional* in the writer (`version?: number`) — do not require it.

**Every subsequent line** is one entry:

```json
{"type":"<entry type>","id":"<8-hex>","parentId":"<8-hex>|null","timestamp":"<ISO8601>", ...}
```

Entries form a **tree** (append-only; `branch()` moves a leaf pointer, so one
parent can have several children), but in ordinary non-interactive usage the
chain is strictly linear — confirmed on all three fixtures: every entry's
`parentId` is the previous entry's `id`. **File order is append order and
therefore chronological**, even when branches exist.

**Entry type vocabulary** (the installed writer's full `SessionEntry` union —
this is the health-canary allow-list):

| type | fields beyond the base | seen in fixtures |
|---|---|---|
| `message` | `message` (an AgentMessage, below) | yes |
| `model_change` | `provider`, `modelId` | yes |
| `thinking_level_change` | `thinkingLevel` | yes |
| `compaction` | `summary`, `firstKeptEntryId`, `tokensBefore`, `details?`, `usage?`, `fromHook?` | no (from writer source) |
| `branch_summary` | `fromId`, `summary`, `details?`, `usage?`, `fromHook?` | no (from writer source) |
| `custom` | `customType`, `data?` — extension state, NOT in LLM context | no (from writer source) |
| `custom_message` | `customType`, `content` (string or blocks), `display`, `details?` — IS in LLM context | no (from writer source) |
| `label` | `targetId`, `label` | no (from writer source) |
| `session_info` | `name?` | no (from writer source) |

**Message roles** (`message.message.role`): `user`, `assistant`, `toolResult`
(all three in fixtures), plus from the writer source: `bashExecution`
(`command`, `output`, `exitCode`, `cancelled`, `truncated`,
`excludeFromContext?` — the TUI `!` command), `custom`, `branchSummary`,
`compactionSummary`.

**Assistant message shape** (fixture-confirmed): `content` is an array of
`{"type":"thinking","thinking":...,"thinkingSignature":...}`,
`{"type":"text","text":...}` and
`{"type":"toolCall","id":...,"name":...,"arguments":{...}}` blocks, plus
envelope fields `api`, `provider`, `model`, `usage`, `stopReason`
(`"toolUse"`/`"stop"`/...), `rawStopReason`, `responseId`, `timestamp`
(epoch-ms — note the *entry* timestamp is ISO-8601; both exist).

**Id pairing is real.** A `toolCall.id` is echoed verbatim as the resolving
`toolResult` message's `toolCallId` (with `toolName` and `isError` boolean
alongside). Multi-tool-call turns are real: `session-rich.jsonl` has one
assistant message carrying two `toolCall` blocks, answered by two separate
`toolResult` message entries in order. Same category as Claude Code and
Hermes: pairing by id, never by proximity.

**Declared intent: two tiers, no third.** Narration (`text` blocks) and
`thinking` blocks coexist in one assistant message (fixture-confirmed; over
an OpenAI-compatible backend the `reasoning_content` stream lands as a
`thinking` block with `thinkingSignature: "reasoning_content"`). Vanilla pi
has **no plan/todo tool** — the author's own design decision ("no built-in
to-do tracking", "no plan mode") — so unlike Claude Code/Hermes there is no
standing-plan third tier. Precedence for the adapter: narration wins, then
thinking, then `None`. A plan-tracking *extension* would surface as `custom`/
`custom_message` entries or a `toolCall` to an extension tool; nothing may be
synthesised from those without a real sample of the specific extension.

**Delegation.** Vanilla pi has no subagent concept (a "delegated" run is an
ordinary `bash` invocation of another `pi` process; nothing links parent to
child). The one real cross-session link is the **fork**: `parentSession`
(header, absolute path) — and a forked file **copies the parent's entries
verbatim, original ids preserved**, then appends (fixture-confirmed). So a
forked session file is self-contained; no stitching is needed or permitted.
Tool-call steps never get delegation markers in vanilla pi.

**Version drift risk, concrete.** The git repo's unreleased main has a
**v4 storage rewrite** (`packages/agent/src/harness/session/jsonl/`): header
becomes `{"v":4,"kind":"header","id",...,"createdAt":<epoch-ms>}` and every
other line becomes a transaction of committed writes
(`{"kind":"entry"|"usage"|"value"|"list","seq":...}`), with v3 handled as a
legacy import. The released 0.85.1 still writes v3 (its coding agent uses its
own `SessionManager`, not the new storage engine). The adapter must
**detect a v4 header and refuse loudly with a version message**, never
half-parse it; when a v4 release ships, that refusal is the signal to extend.

## How this corpus was generated

pi 0.85.1 was pointed, via `~/.pi/agent/models.json` (custom provider,
`api: "openai-completions"`), at a local scripted OpenAI-compatible SSE
server (`tests/fixtures/pi/generation-server.py`), then run non-interactively
(`pi --print ...`, and `pi --fork <id> --print ...` for the fork case) in a
sandbox workspace. The **writer and the tool executions are real pi**; only
the model's outputs were scripted (which also mirrors the actual target
deployment: pi against a self-hosted OpenAI-compatible endpoint — exactly
Radek's llama.cpp setup). pi's builtin tool surface, from its own request
bodies: `read`, `bash`, `edit`, `write`.

## Assumed grammar (the one-paragraph statement for review)

A pi session is one JSONL file: line 1 a v3 header
(`type:"session"`, optional `version`, `id`, ISO `timestamp`, `cwd`, optional
`parentSession` absolute path); every other line an entry with `type`, 8-hex
`id`, `parentId` (null only on the first entry), ISO `timestamp`, and
type-specific fields per the table above, in append (= chronological) order,
forming a tree that is linear except where interactive branching occurred.
The adapter ingests **all entries in file order**, maps `message` entries to
steps by role (`user` → USER utterance; `assistant` → one step per
text/toolCall action, thinking as intent tier 2; `toolResult` → ENV_EFFECT
paired by verbatim `toolCallId`; `bashExecution` → its own TOOL_CALL+result
shape), maps `model_change`/`thinking_level_change`/`compaction`/
`branch_summary`/`session_info`/`label` to STATE_TRANSITION steps, surfaces
unknown entry types and unknown `customType`s rather than dropping them,
preserves `parentId` as `parent_step_id`, refuses loudly on a v4/unknown
header, and never invents pairings, plans, or delegation links.

## The old checklist, resolved

1. **Framing** — v3 confirmed on real bytes; effectively linear in ordinary
   usage; tree structure preserved via `parentId`. Ingest = file order (an
   audit wants abandoned branches visible, and append order is
   chronological), not a root-to-leaf context walk.
2. **Ids/pairing** — real, verbatim, multi-call turn pinned in fixtures.
   `toolCallId` never absent in the corpus; treat a missing one as an
   unpaired result, never guess.
3. **Intent** — narration + thinking tiers confirmed in one real message;
   third tier confirmed absent in vanilla pi.
4. **Delegation** — none in vanilla; fork link is a header path, forks are
   self-contained copies. Radek's extension list still unknown.
5. **Version markers** — `version: 3` on all three fixture headers; field
   optional in the writer type; v1–2 migrate on load; unreleased v4 shape
   documented above with a mandatory loud refusal.
6. **Type census** — the installed writer's full unions transcribed above;
   health canary allow-list = the 9 entry types + observed roles.
7. **Redaction surface** — `toolCall.arguments` and `toolResult.content` are
   the sensitive keys (TOOL_CALL `input` / ENV_EFFECT `tool_result` after
   mapping); `bashExecution.command`/`.output` need their own keys, a role no
   shipped adapter has.

## What still needs Radek

Only one thing, and it no longer blocks the adapter: **which extensions and
skills his pi runs.** Extension entries (`custom`/`custom_message`, unknown
`customType`s, extension tools in `toolCall.name`) are format-legal and the
adapter surfaces them without parsing; a sample from his actual install would
let us decide whether any of them deserve first-class mapping (a plan tool
would become intent tier 3; a delegation tool would need its own
unobserved-delegation marking). His raw session files, supplied under
explicit permission, remain the gold standard the corpus here approximates —
never pulled from anyone's `~/.pi` without that permission.
