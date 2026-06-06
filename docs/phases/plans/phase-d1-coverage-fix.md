# Phase D1 — Coverage fix: TodoWrite → standing plan state

**Type:** TDD sprint (adapter-only). Scorer (`analysis/drift.py`) is untouched.
**File under change:** `src/auditk/adapters/claude_code.py`
**Parent:** `docs/phases/roadmap-v0.2.md` → Phase D
**Status:** planned

---

## Problem

Only **3.1%** of steps carry `declared_intent`. Root cause is structural, in the
adapter — not the scorer:

- `_assistant_steps` (`claude_code.py:75`) attaches narration as `declared_intent`
  **only to the first `tool_use` block** of a narrated assistant message
  (`claude_code.py:98`: `intent = narration if i == 0 and narration else
  (pending_intent if i == 0 else None)`).
- `pending_intent` is **reset to `None`** after any assistant message that
  contains tool uses (`claude_code.py:100` returns `steps, None`), so intent does
  not survive across the trace.
- `TodoWrite` tool calls — Claude Code's **explicit, structured declared
  sub-goals** — are currently ingested as an opaque `TOOL_CALL` payload
  (`{"name": "TodoWrite", "input": {...}}`) and their rich content is discarded
  for intent purposes.

The agent's single best intent signal is being thrown away.

## Goal

Introduce a **standing plan**: the most recent set of active declared sub-goals,
derived primarily from `TodoWrite`, carried forward across the *whole* trace and
used as the premise for every subsequent **agent action**'s `declared_intent`
when no more-specific same-message narration applies.

**Target:** `declared_intent` coverage **3.1% → >50%** of steps on a
representative session, with **no regression** in existing adapter/scorer tests.

---

## Design decisions (decide in Red, lock before Green)

1. **Representation.** Keep the existing field shape: `declared_intent: str | None`.
   The standing plan is serialised to a single string (the active goals joined),
   so the schema and `compute_drift` are unchanged. No new schema field in D1.
2. **What counts as the standing plan.** The **active** todos = those with status
   `pending` or `in_progress`. Completed todos are excluded. If a `TodoWrite`
   leaves zero active todos, the standing plan is **cleared** (→ `None`) — no
   stale intent.
3. **Precedence (per agent action step).** Resolve `declared_intent` in order:
   1. same-message narration (existing behaviour, first `tool_use` only) — most
      specific, wins when present;
   2. standing plan (active todos) — the new fallback workhorse;
   3. `None`.
   This preserves the existing high-quality narration signal and *adds* coverage.
4. **Scope of attachment.** Standing plan attaches to **agent action steps only**
   (`Actor.AGENT` — assistant `TOOL_CALL` / `UTTERANCE`). `Actor.USER` and
   `Actor.TOOL` (tool-result `ENV_EFFECT`) steps keep `declared_intent = None`:
   drift measures *agent enactment vs. declared intent*; a user turn or an
   environment effect is not the agent enacting its own intent.
5. **Carry-forward.** The standing plan persists across event boundaries until a
   later `TodoWrite` supersedes or clears it. (Generalises today's `pending_intent`,
   which is reset per assistant message.)
6. **`TodoWrite` scanning is position-independent.** A `TodoWrite` appearing as a
   non-first block (`i >= 1`) must still update the standing plan, even though
   that block's own `declared_intent` follows the precedence rule above.
7. **Redaction.** Under `strip_payloads=True`, the `TodoWrite` *action payload*
   stays redacted (as today). But the standing-plan-derived `declared_intent`
   **text is preserved** — consistent with current handling, where narration in
   `declared_intent` is already kept under strip. (Todos are task descriptions,
   the same class of data as narration.)
8. **Malformed input.** `TodoWrite` with missing/`non-list` `todos`, or todo
   items lacking `content`, are ignored; the previous standing plan is retained.

---

## TDD phases

> Follow `docs/constitution/tdd-workflow.md`. **STOP for review at each gate.**

### Red — write failing tests first

Add to `tests/adapters/test_claude_code.py` (or the existing adapter test
module). All must fail before any production change.

1. `test_todowrite_populates_standing_plan` — a session with one `TodoWrite`
   (two `pending` todos): the active-goal text appears as `declared_intent` on
   the next agent action step.
2. `test_standing_plan_attached_to_subsequent_action_intent` — an agent
   `tool_use` step that has neither narration nor `pending_intent`, occurring
   after a `TodoWrite`, has non-null `declared_intent` equal to the standing plan.
3. `test_standing_plan_carries_across_events` — standing plan set in event *N* is
   still applied to an agent action in event *N+2* (no intervening `TodoWrite`).
4. `test_narration_precedence_over_standing_plan` — when a same-message narration
   exists for the first `tool_use`, that narration (not the standing plan) is the
   `declared_intent` for that step. (Regression guard for existing behaviour.)
5. `test_completed_todos_clear_standing_plan` — a `TodoWrite` whose todos are all
   `completed` clears the standing plan; the following agent action has
   `declared_intent is None`.
6. `test_latest_todowrite_supersedes_previous` — two `TodoWrite` calls; steps
   after the second reflect the second's active goals, not the first's.
7. `test_malformed_todowrite_ignored_keeps_previous` — a `TodoWrite` with
   `input={}` (no `todos`) does not crash and does not clear a previously set
   standing plan.
8. `test_todowrite_in_non_first_block_updates_plan` — a `TodoWrite` as block
   `i == 1` of a multi-tool assistant message still updates the standing plan for
   subsequent steps.
9. `test_strip_payloads_redacts_todowrite_payload_but_keeps_intent` — with
   `strip_payloads=True`, the `TodoWrite` step's `action.payload` is redacted, but
   downstream `declared_intent` text derived from the standing plan is present.
10. `test_user_and_tool_result_steps_have_no_intent` — `Actor.USER` and
    `Actor.TOOL` steps keep `declared_intent is None` even while a standing plan
    is active.
11. `test_coverage_exceeds_threshold_on_fixture_session` — load a representative
    fixture (see below) and assert
    `sum(1 for s in trace.steps if s.declared_intent) / len(trace.steps) > 0.50`.

**Fixture:** add `tests/fixtures/claude_code/session_with_todos.jsonl` — a
representative captured (or hand-built) session with ≥1 `TodoWrite` and ≥12 mixed
steps (assistant narration, multi-tool messages, tool results, user turns). If a
real session is used, run it through the existing anonymiser / `strip_payloads`
before committing.

### Green — minimum production code to pass

Smallest change set in `claude_code.py`:

- Thread a **standing plan** alongside `pending_intent` through
  `ingest_claude_code_session` (loop at `claude_code.py:49`) and
  `_event_to_steps` (`claude_code.py:64`). (In Green, a tuple/extra param is
  acceptable; tidy in Refactor.)
- In `_assistant_steps`: before building steps, scan **all** `tool_use` blocks for
  `name == "TodoWrite"`; update the standing plan from the latest one found.
- Compute each agent step's `declared_intent` via the precedence rule
  (narration → standing plan → `None`); **return the standing plan forward**
  instead of resetting to `None`.
- Leave `_user_steps` / `ENV_EFFECT` paths producing `declared_intent = None`.

Run `pytest tests/ -q` after each meaningful change. Hardcoding to pass a single
test is acceptable here; generalise only as later tests force it.

### Refactor — clean up (tests stay green)

- Introduce a small `PlanState` dataclass (`pending_intent: str | None`,
  `standing_plan: str | None`) to replace tuple threading.
- Extract helpers: `_extract_todos(block) -> list[dict] | None` and
  `_active_goal_text(todos) -> str | None` (status filter + join).
- Keep every function ≤ ~30 lines (`docs/constitution/code-structure.md`).
- Re-run the gate: `pytest tests/ -q`, then `ruff format --check src/ tests/`,
  `ruff check src/ tests/`, `mypy --strict src/auditk` (per repo CI: lint +
  mypy + pytest).

---

## Acceptance criteria

- [ ] **AC1** `TodoWrite` active todos are extracted into standing plan state.
- [ ] **AC2** Every agent action after a `TodoWrite` (until superseded/cleared)
      has non-null `declared_intent`, unless same-message narration applies.
- [ ] **AC3** Standing plan persists across event boundaries (carry-forward).
- [ ] **AC4** Same-message narration still takes precedence (no regression).
- [ ] **AC5** Completed-only / empty `TodoWrite` clears the standing plan.
- [ ] **AC6** Latest `TodoWrite` supersedes the previous standing plan.
- [ ] **AC7** Malformed `TodoWrite` is ignored without clearing or crashing.
- [ ] **AC8** `Actor.USER` / `Actor.TOOL` steps keep `declared_intent is None`.
- [ ] **AC9** `strip_payloads=True` redacts the `TodoWrite` payload but preserves
      standing-plan `declared_intent` text.
- [ ] **AC10** Coverage on the fixture session is **> 50%** of steps.
- [ ] **AC11** Existing adapter + scorer tests pass unchanged; `compute_drift`
      still produces a `DriftReport` with no schema change.

## Gate (Done When)

All AC checked; `pytest tests/ -q` green; `ruff format --check`, `ruff check`,
and `mypy --strict src/auditk` clean; reviewed. Then proceed to **D2**
(`Scorer` protocol + NLI scorer) — not before.

## Out of scope (do NOT do in D1)

- Any change to `analysis/drift.py` / the scoring algorithm (that is D2).
- Schema changes to `Step` / `declared_intent` (string premise is sufficient).
- Plan-decomposition, NLI, or LLM-judge logic (D2/D3).
- Coverage measurement as a shipped feature (D1 measures it in a test only).

## Issues & Fixes

_(populate during execution — Test Integrity Rule: never silently change a test;
document any post-Green test change with a reason.)_
