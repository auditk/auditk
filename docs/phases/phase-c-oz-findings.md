# Phase C — Oz (Warp) Adapter Format Findings (2026-06-03)

## Outcome

**The C.4 plan's central assumption is wrong and the task is re-scoped.** The
Phase C plan (`plans/phase-c-breadth-sprint.md` §C.4) assumed
`agent_conversations.conversation_data` holds an `AIAgentActionType` event
stream (`RunAgents`, `RequestCommandOutput`, `RequestFileEdits`, …) that maps
directly through `_OZ_ACTION_MAP`. It does not. The action stream is **not in
that column**. This is the escalation the plan asked for (§Escalation item 2)
and a textbook case of the constitution's *External Library Integration —
verify the API before implementation* rule: the upstream shape was assumed, not
inspected.

Investigated against the live store at
`/home/matt/.local/state/warp-terminal/warp.sqlite` (49 conversations).

## What `conversation_data` actually contains

`agent_conversations.conversation_data` is a small JSON **object** (largest row
~4.7 KB) of conversation *metadata*, not steps:

- `server_conversation_token` (UUID) — server-side handle
- `run_id` (UUID) — server-side run handle
- `conversation_usage_metadata` — token/credit usage broken down per model
  (e.g. Kimi K2.6, DeepSeek V4 Pro, Claude Sonnet 4.6), `was_summarized`,
  `context_window_usage`, `credits_spent`
- `artifacts_json` — a JSON list of produced artifacts (`PLAN`,
  `PULL_REQUEST` entries with URL + branch)
- `autoexecute_override` — e.g. `RunToCompletion`

The per-step tool calls / narration are held server-side (referenced by the
tokens above) and are **not** persisted locally in this column.

## Where the real per-step data lives

Three tables hold the locally-recoverable activity, all joinable on
`conversation_id`:

- **`ai_queries`** (7,098 rows) — one row per exchange. `input` is a JSON
  **list** of context items: `{"Query": {"text", "context": [ {"Directory": …},
  {block_id, command, output, exit_code, started_ts, finished_ts}, … ] }}`.
  Carries `model_id`, `planning_model_id`, `coding_model_id`, `output_status`,
  `start_ts`, `working_directory`. This is the turn/intent stream and the
  richest single source.
- **`blocks`** (1,408 rows) — terminal command executions. `stylized_command`
  and `stylized_output` are **ANSI-escaped byte blobs** (must be
  de-escaped/stripped). `ai_metadata` is JSON linking the block to a
  `conversation_id`, a `requested_command_action_id` (e.g. `toolu_…`), and a
  **`subagent_task_id`** — the multi-agent / child-agent signal.
- **`agent_tasks`** (764 rows) — the TODO/plan list. `task` is
  **protobuf-encoded bytes**, not JSON (human-readable titles are embedded,
  e.g. `Review Files In Folder`). Decoding requires the protobuf schema or
  best-effort byte parsing.

## Complex multi-agent trace candidate (already on disk)

Conversation `6339162a-a10f-4c41-a99f-642331d05edd` ("agi-research-hub" build):
**1,153 exchanges**, **74 `agent_tasks`**, **7 `PULL_REQUEST` artifacts**, and
subagent activity (`subagent_task_id` populated on its blocks). This is the
"50+ step, multi-agent orchestration" trace the demo wants — `auditk` was built
by Oz, and this session demonstrates Oz orchestrating itself. No need to trigger
a fresh run.

## Decision — corrected adapter shape (for when C.4 is built)

Do **not** parse `conversation_data` for steps. Instead reconstruct the `Trace`
by joining the three tables on `conversation_id`, ordered by timestamp:

- `ai_queries.input[].Query.text` → `declared_intent` for the exchange;
  `model_id` → trace/agent metadata.
- `blocks` (via `ai_metadata.conversation_id`) → `tool_call` steps
  (command) and their `env_effect` (output/exit_code); de-escape ANSI first;
  `requested_command_action_id` is the stable step key.
- `blocks.ai_metadata.subagent_task_id` (non-null) → child-agent steps →
  `state_transition` (the orchestration signal that replaces the assumed
  `RunAgents` event).
- `agent_tasks` → optional plan/intent backbone (decode protobuf, or skip for
  v0 and use `ai_queries` text only).
- `flow_type = code`; `strip_payloads=True` for any real-session demo (same
  privacy pattern as the Claude Code adapter — review residual text before
  publishing, especially `ai_queries.input` narration).

This is a meaningfully larger adapter than C.2/C.3 (three sources, two custom
decoders: ANSI + protobuf, subagent reconstruction). Per the constitution's
file/function size limits (<500 lines/file, <50 lines/function), it should be
decomposed into: a SQLite reader, an `ai_queries` input parser, a `blocks`
de-escaper, an optional `agent_tasks` protobuf decoder, and the trace assembler
— not one monolithic module.

## Impact on the plan

- **C.4 is now correctly scoped** as a multi-source SQLite reconstruction, not a
  single-column JSON map. `_OZ_ACTION_MAP` (keyed on `AIAgentActionType`
  strings) is obsolete for the local-data path and should be removed from the
  plan's mapping table.
- The narrative ("auditk audits Oz") survives intact: the agi-research-hub
  session is a strong complex multi-agent demo once the adapter exists.
- **Alternative if local reconstruction proves too lossy:** export sessions via
  the Warp/Oz CLI/API (cloud-agent conversation export) instead of reading
  SQLite — a cleaner, supported source. Evaluate before committing to the
  protobuf decode work.

## Next step taken instead (this branch)

Per orchestrator direction, C.4 implementation is deferred. We are validating
the pipeline on a **more complex Claude Code session** (existing, working
`claude-code` adapter) to exercise a richer trace at lower cost, and documenting
these Oz findings for whoever picks up C.4.
