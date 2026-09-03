# Pi adapter — format notes (PROVISIONAL / UNVERIFIED)

**Status: gated.** There is no `auditk.adapters.pi` implementation. `pi` is
registered (`src/auditk/adapters/pi.py`) so `--adapter pi` is a reachable,
documented CLI name, but every entry point on it refuses loudly instead of
parsing anything — see that module's docstring for the exact message. This
page is the reason why, and the list of what has to change before that stub
can become a real adapter.

Everything below is built from **public documentation only**. No Pi session
trace — sample or real — has been read by anyone working on this adapter.
Per `docs/adapters.md`'s adapter-writing discipline (the same discipline
`hermes.py` followed: "format discovered by reading the writer source... not
guessed from data alone"), reading public docs is not a substitute for that.
Docs describe an intended shape; they drift from what a specific install, a
specific version, and a specific set of installed plugins/skills actually
write to disk. Nothing in this file should be typed up as parsing code
without a real sample confirming it first.

## Which "Pi" this is

Working identification: **Mario Zechner's `pi` coding-agent harness**
(`pi.dev`; source at `github.com/earendil-works/pi`, formerly/also mirrored
at `github.com/badlogic/pi-mono`; published on npm as
`@mariozechner/pi-coding-agent` / `@earendil-works/pi-coding-agent`). This is
a terminal coding-agent CLI, TypeScript, actively developed, explicitly
designed to be reshaped via extensions/skills/plugins, with a **publicly
documented, versioned JSONL session format** (`pi.dev/docs/latest/session-format`)
— see "What the docs say" below.

Confidence: **moderate, not confirmed**. The chain of evidence:

- Radek Gruchalski is our prospective external tester for a "Pi" adapter.
- A plugin, `baryonlabs/pi-agent-harness` (also on npm as
  `@baryonlabs/pi-agent-harness`), exists that is explicitly built for "the
  pi coding agent" and adds a multi-agent/subagent delegation tool on top of
  it (README: "a pi coding-agent plugin/package... a bundled subagent
  (single/parallel/chain) delegation tool", "ported from Claude Code").
  `baryonlabs` is a plausible match for Radek's own org/handle, but **this
  was not confirmed** — a web search did not turn up a direct, citable link
  between the `radekg` GitHub account and the `baryonlabs` org (no visible
  shared membership, no first-party statement found). Treat the
  "Radek uses baryonlabs/pi-agent-harness on top of Mario Zechner's pi" chain
  as a reasonable guess, not an established fact.
- No alternative "Pi" candidate turned up in searching that plausibly matches
  "a coding-agent harness a named individual external tester would run
  against auditk" better than this one.

**If this turns out to be the wrong Pi**, everything below is still a
reasonable template for what to ask for and check, but the concrete shape
(field names, storage location) will be wrong and must not be reused as-is.
Before writing a real parser, confirm directly with Radek which tool,
which version, and which plugins/skills are installed.

## What the docs say (unverified against real data)

Source: `pi.dev/docs/latest/session-format` ("JSONL session file format,
entry types, and SessionManager API"), plus `mariozechner.at/posts/2025-11-30-pi-coding-agent/`
(the author's own design-rationale post) and the `pi.dev` front page.

**Framing.** One JSONL file per session,
`~/.pi/agent/sessions/--<path>--/<timestamp>_<uuid>.jsonl`. Unlike Hermes
(flat SQLite rows) or Claude Code (an append-only JSONL list that is
*effectively* linear), Pi's own file is documented as a **tree**: every
non-header line has `"id"` (an 8-character hex string) and `"parentId"`
(another entry's id, or `null` for the first entry), and the docs describe
real branching ("Branching creates new children from earlier entries",
`SessionManager.branch(entryId)` / `createBranchedSession(leafId)`, a
`/tree` command to navigate and branch from any earlier point). This is a
structurally different shape from every adapter shipped today — none of
`claude_code.py`/`langgraph.py`/`generic_otel.py`/`hermes.py` ingest a file
that can legitimately contain more than one path from root to "the current
state." A Pi adapter has to decide, and confirm against real files, whether
"ingest a session" means walking the single path a `buildContextEntries()`-
style call would produce (root to one leaf) or something else — auditk's
`Trace` is a flat `list[Step]`, not a tree, so this decision is load-bearing,
not cosmetic.

**Header.** First line only: `{"type":"session","version":3,"id":"uuid",
"timestamp":"...","cwd":"/path"}` (docs state version 3 is current; versions
1–2 "auto-migrate on load" — i.e. the on-disk shape has already changed at
least twice). A `"parentSession"` field marks a forked session. This is the
version-marker analogue of Hermes' `schema_version` PRAGMA or Claude Code's
event-shape churn (`TodoWrite` → `TaskCreate`/`TaskUpdate`) — expect it to
keep moving.

**Entry types (documented).** `message` (role one of `user` / `assistant` /
`toolResult` / `bashExecution` / `custom` / `branchSummary` /
`compactionSummary`), `model_change`, `thinking_level_change`, `compaction`,
`branch_summary`, `custom` (state-only, non-message), `custom_message`
(participates in LLM context), `label`, `session_info`.

**Ids / pairing.** An `assistant` message's `content` array can contain a
`ToolCall` (`{"type":"toolCall","id":"...","name":"...","arguments":{...}}`);
a later `toolResult` message references it by `"toolCallId"` (+
`"toolName"`, `"content"`, `"isError"` boolean). If this is accurate and
consistently populated, it is a real id-pairing concept — the same category
as Claude Code's `tool_use.id`/`tool_result.tool_use_id` and Hermes'
`tool_calls[].id`/`tool_call_id`, not the "no such concept" case LangGraph
and generic-otel are in. **Unverified**: whether `toolCallId` is ever
missing/null in practice, and whether ids are unique within one session file
or only within one branch.

**Declared intent.** No documented precedence stack (narration → thinking →
standing plan) the way Claude Code's/Hermes' three-tier one is. An
`assistant` message's `content` array can hold `TextContent` and
`ThinkingContent` blocks alongside tool calls — so *inline narration* and a
*thinking-block-style weaker proxy* both plausibly exist as adapter inputs,
mirroring the first two tiers of Claude Code/Hermes. The third tier
(standing plan from a todo/plan-tracking tool) is the one genuine
**documented absence**: Mario Zechner's own post states Pi deliberately has
"No built-in to-do tracking (users write to files instead)" and "No plan
mode (persistent planning uses external `PLAN.md` files)". Vanilla Pi has no
analogue of `TodoWrite`/Hermes' `todo` tool at all — there is nothing to
anchor a standing plan on unless a specific tester's installed
skill/extension adds one (see "Delegation" below for why this matters for
Radek specifically).

**Delegation.** Also a documented absence in vanilla Pi: "No sub-agents
(spawns separate sessions via bash for transparency)" — i.e. core Pi has no
built-in multi-transcript delegation concept comparable to Claude Code's
`Task`/`Agent` tool or Hermes' `delegate_task`, and by the author's own
description a "delegated" call is just an ordinary bash invocation of
another `pi` process, with nothing in the parent session file linking it to
the child session it spawned — if anything, an even weaker link than
Hermes' (which at least gets a `parent_session_id` column on the child row).
**But** — this is the one place the identification uncertainty above matters
most — if Radek is in fact running `baryonlabs/pi-agent-harness` on top of
core Pi, that plugin adds its own "bundled subagent (single/parallel/chain)
delegation tool," which is exactly the kind of extension entry
(`custom`/`custom_message`, or an ordinary `toolCall` to a plugin-defined
tool) the documented format allows for but does not specify the shape of.
Whether *that* delegation tool's calls carry any id linking a call to a
spawned child session is completely unknown from public docs and must come
from a real sample.

**What an adapter must NOT invent (recap from docs/adapters.md, applied
here specifically).** No synthesised standing-plan step if core Pi truly has
no plan-tracking tool in Radek's actual setup. No fabricated
call/child-session pairing for delegation, exactly Hermes' "never clearable
from a single ingest() call" discipline, likely stronger here since even the
parent-session-id-style weak link Hermes has may not exist. No assumption
that `content`/`toolCall`/`toolResult` fields are always present in the
documented shape — a real file may show optional fields, extension-added
fields, or an older un-migrated version.

## What sample traces must show us (mirrors what Hermes discovery needed)

A usable sample set is **not** "one short pi session." Hermes' adapter was
built by reading three separate source files
(`hermes_state.py`, `tools/delegate_tool.py`, `tools/todo_tool.py`) plus a
live corpus; Pi needs the equivalent — real files, not just docs, covering:

1. **Record framing.** At least one real `.jsonl` file, read raw (not
   through any `pi` CLI post-processing), confirming: the header line shape
   exactly as documented or not; whether `version` is really `3` on Radek's
   install or an older/newer number; whether the file is genuinely a tree
   (multiple children off one `parentId`) in ordinary single-threaded usage,
   or effectively linear until `/tree` is used on purpose — this decides
   whether "ingest a session" can stay a simple root-to-tail walk for the
   common case.
2. **Ids / pairing.** Confirmed presence and uniqueness of `toolCall.id` and
   `toolResult.toolCallId` across a session with at least one multi-tool-call
   assistant turn (the Claude-Code/Hermes precedent: id chaining across
   several tool calls in one message needs a real multi-call example to
   pin, not a single-call one). Confirmation of whether `toolCallId` is ever
   absent/null on a real `toolResult` row.
3. **Intent.** A real assistant message that has both `TextContent` and a
   `ThinkingContent` block, to confirm which one this adapter should prefer
   (mirrors Claude Code's/Hermes' "narration wins over thinking" rule) — and
   confirmation of whether Radek's actual toolset includes any plan/todo
   tracking tool at all (a skill, not core Pi) that could be a legitimate
   third precedence tier, or whether `declared_intent`'s only sources here
   are narration and thinking, full stop.
4. **Delegation.** Direct evidence of whether Radek's setup uses
   `pi-agent-harness` (or any other delegation-adding plugin) at all. If it
   does: a real transcript showing what a delegation tool call's `toolCall`/
   `toolResult` (or `custom`/`custom_message`) entries actually look like,
   and whether anything at all — a session id, a file path, a hint — links
   a specific delegation call to the child session(s) it spawned. If the
   answer is "nothing links them," this adapter's delegation handling is a
   one-line decision (`delegation_unobserved = True` unconditionally, same
   as Hermes) — but that has to be confirmed, not assumed, exactly per
   `docs/adapters.md`'s "No fabricated pairings" rule.
5. **Version markers / drift risk.** Real header lines across more than one
   session file/date, to see whether `version` actually changes across
   Radek's own session history (the docs already admit two past migrations)
   — and whether an adapter reading only `version: 3`'s documented shape
   would silently mis-read an older, auto-migrated-on-load file if it is
   ever handed the pre-migration bytes rather than what the `pi` CLI itself
   would produce after opening it.
6. **Record-type vocabulary for the health canary.** A `role`/`type` census
   across real files (the `role(record) -> known_record_types` allow-list
   `adapters/health.py` needs, the same census `hermes.py`'s
   `KNOWN_RECORD_TYPES` comment describes doing against a live corpus) —
   the documented type list above is what the docs say exists, not a
   confirmed exhaustive list of what a real install actually emits
   (extension/plugin entries in particular are the likely source of
   undocumented types).
7. **Redaction surface.** Which fields on a real `toolCall`/`toolResult`
   pair actually carry sensitive content (`arguments`, `content`) so a
   `REDACTION_CONTENT_KEYS` declaration (see `docs/adapters.md`'s
   "Redaction pass-through") can be written correctly — and whether
   `bashExecution` entries (`command`/`output`, a message role of their own,
   not folded into `toolCall`/`toolResult`) need their own redaction keys,
   since nothing shipped today has an analogous role.

None of the above should be inferred, guessed, or stubbed with synthetic
data standing in for a real trace. When real samples exist (supplied by
Radek under explicit permission — never pulled from `~/.claude/projects` or
`~/.hermes`, which are read-only and unrelated to Pi anyway), this file
should be rewritten from them the way `hermes.py`'s module docstring was:
concrete field names confirmed by reading the actual bytes, not the docs
page.
