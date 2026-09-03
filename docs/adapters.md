# Writing a trace adapter

An adapter turns one agent harness's native trace format into auditk's
normalised `Trace` (`src/auditk/schema.py`). This page is the contract: what
an adapter must map, what it must never invent, how redaction pass-through
works, how to register an adapter, and how to check your work with the
conformance kit.

It is written against the three adapters shipped today —
`src/auditk/adapters/claude_code.py`, `src/auditk/adapters/langgraph.py`,
`src/auditk/adapters/generic_otel.py` — and describes their actual,
current behaviour, not an aspirational one. Two real gaps are called out
explicitly in "Known contract gaps" below rather than glossed over.

## The shape you're producing

```python
class TraceAdapter(Protocol):
    def ingest(self, raw: Any) -> Trace: ...
```

(`src/auditk/adapters/protocols.py`) That's the whole structural protocol —
PEP 544, no base class to inherit, no `runtime_checkable` marker. Anything
with an `ingest(self, raw: Any) -> Trace` method satisfies it; `mypy` checks
conformance statically (see `tests/unit/test_adapter_protocols.py`).

A `Trace` (`src/auditk/schema.py`) is a flat `list[Step]` plus header fields
(`trace_id`, `flow_type`, `agent_config_ref`, `source_adapter`, `metadata`).
Each `Step` is:

| field | required | meaning |
|---|---|---|
| `step_id` | yes | unique within the trace |
| `parent_step_id` | no | the native format's own parent-linking concept (see below) |
| `trace_id` | yes | must match `Trace.trace_id` |
| `timestamp` | yes | must be timezone-aware |
| `actor` | yes | `Actor.USER \| AGENT \| TOOL \| ENVIRONMENT` |
| `declared_intent` | no | see "Declared intent" below |
| `action` | yes | `Action(type, payload)` — see "Actions" below |
| `context_used` | no | `list[ContextRef]`, retrieval-style context |
| `metadata` | no | free-form `dict[str, Any]`, per-step |

## What an adapter MUST map

### Steps / turns

One native record generally becomes one `Step`, but a single native event
that bundles several distinct actions (e.g. a Claude Code `assistant`
message with two `tool_use` blocks) expands into one `Step` per action —
see `claude_code.py:_assistant_steps`, which turns N tool_use blocks in one
message into N steps chained by `parent_step_id` within that message
(`session-intent-action.jsonl` in the integration tests is the concrete
case: 2 tool_use blocks -> 2 chained `Step`s).

### Actions

Map the native record to one of the four `ActionType`s
(`UTTERANCE`, `TOOL_CALL`, `STATE_TRANSITION`, `ENV_EFFECT`). There is no
fifth bucket to fall back on — `generic_otel.py:_infer_action` is the
clearest example of an exhaustive dispatch (`LLM` -> utterance, `TOOL`/
`RETRIEVER` -> tool_call, `AGENT`/`CHAIN` -> state_transition, else ->
empty utterance) and `langgraph.py:_classify_action` does the same from
checkpoint `metadata.writes`.

### Tool calls + results, with id-linkage where the native format has it

Only the Claude Code native format carries an explicit call/result id today
(`tool_use.id` / `tool_result.tool_use_id`) — a Claude Code `assistant`
`tool_use` block becomes one `TOOL_CALL` step, its `user` `tool_result`
becomes one `ENV_EFFECT` step, and `tool_result.is_error` is preserved on
`Step.action.payload["is_error"]` (`claude_code.py:_user_steps`). Preserve
whatever id linkage your native format actually carries; do not invent one
if it isn't there. LangGraph and generic-otel spans currently do not carry
an explicit call/result id pair in the same sense (a checkpoint or span
either *is* a tool call/result or it isn't) — see "Known contract gaps"
below for what this means for the health canary.

### Declared intent

`declared_intent` is a best-effort summary of what the agent says it's
doing, not a guess you compute. `claude_code.py`'s precedence order (module
docstring) is the fullest worked example: inline narration text wins,
then a `thinking` block (documented as a *weaker, unverified* proxy — "a
model may think through options it doesn't commit to"), then the standing
plan carried forward from the most recent anchor update (a persisted plan
store, a `TaskCreate`/`TaskUpdate` call, or the legacy `TodoWrite` call, in
that authority order). `generic_otel.py` takes `input.value` off an `LLM`
span, truncated to 200 chars (`_span_to_step`); `langgraph.py` reads
`channel_values.intent` off the checkpoint (`_get_declared_intent`) —
both single-source, no precedence stack, because their native formats don't
expose the layered narration/thinking/plan-store distinction Claude Code's
does.

### Delegation / parent linkage

Every adapter populates `parent_step_id` from whatever parent-linking
concept its native format has: `langgraph.py:_get_parent_step_id` reads the
checkpoint's own `parent_config.configurable.checkpoint_id`;
`generic_otel.py` uses `parent_span_id` directly; `claude_code.py` uses the
JSONL event's own `parentUuid` for a step's first record.

Claude Code additionally does real **subagent transcript stitching**: a
`Task`/`Agent` tool_use is joined by `tool_use.id` to a delegate
transcript's own `meta["toolUseId"]` (`claude_code.py:
_index_delegation_task_steps` / `_index_subagents_by_tool_use_id`), and on a
match the delegate's own steps are flattened into the parent trace with
`parent_step_id` overridden to the owning `Task` step and
`metadata["agent_id"]` set (`_ingest_subagent_transcript`, the D4 "flat"
shape). An unmatched delegation call (no subagent transcript supplied, or
none with a matching id) gets `metadata["delegation_unobserved"] = True`
instead — the blind spot is made visible on the step rather than silently
looking like a fully-observed call. Neither LangGraph nor generic-otel has
an equivalent multi-transcript delegation concept today; if your harness
does (a parent run that spawns and persists a child run's own trace
separately), model it the same way: join by whatever id the harness uses to
link them, flatten with an explicit `parent_step_id`/`agent_id` marker, and
mark the unmatched case rather than dropping it.

### The health hooks

See "Known contract gaps" below — this is the one area where the aspiration
and the current code disagree, and it's worth reading before you assume
your adapter gets health checking "for free."

## What an adapter must NOT invent

- **No synthesised events.** Every `Step` traces back to one (or part of
  one) native record. Don't manufacture a step to make the trace "look
  complete."
- **No guessed intent.** If the native format has nothing narration-like,
  `declared_intent` is `None`. `generic_otel.py`'s minimal-span test
  (`tests/integration/test_generic_otel_adapter.py::test_minimal_fixture`)
  pins exactly this: no attributes -> `declared_intent is None`, not a
  fabricated summary.
- **No fabricated pairings.** `claude_code.py`'s delegation join only
  flattens a subagent transcript when its `meta["toolUseId"]` actually
  matches a `Task`/`Agent` tool_use id in the parent — an unmatched call
  stays marked `delegation_unobserved`, it is never guessed at. The same
  discipline applies to the health canary's call/result pairing (below):
  results are matched to calls by id when ids exist; nothing pairs a result
  to a call by proximity or type alone once ids are available.
- **Unknown record types are surfaced, not silently dropped.**
  `adapters/health.py`'s `KNOWN_RECORD_TYPES` allow-list exists because a
  harness rename (`TodoWrite` -> `TaskCreate`/`TaskUpdate`) once broke the
  Claude Code adapter's plan-anchor detection *silently* — a drift score
  computed on top of a dead parser looked like a real number
  (`adapters/health.py` module docstring, "Finding A"). A new adapter for a
  format that similarly evolves should have some way to notice "I stopped
  recognising most of what I'm seeing" rather than quietly scoring less and
  less of the session.
- **Refuse-don't-raise on malformed input.** Concretely: an adapter may
  either (a) refuse cleanly with a `ValueError` describing what's wrong, or
  (b) process best-effort and still return a `Trace` (Claude Code's
  defensive `isinstance()` checks throughout `claude_code.py` mean most
  malformed records fall into this bucket rather than raising at all) — but
  it must never leak an undocumented, internal exception type (a bare
  `KeyError`/`AttributeError`/`TypeError` from three functions deep) as its
  observable contract. `generic_otel.py:ingest_otel_spans` and
  `langgraph.py:ingest_checkpoints` used to do exactly that on a span/
  checkpoint missing a required field; both now catch
  `(KeyError, TypeError, AttributeError)` around the native-record
  extraction and re-raise `ValueError("malformed ... data: ...")` (see the
  `fix(adapters)` commit on this branch). Empty input is the one case where
  every adapter agrees: `ingest([])` always raises `ValueError` — never a
  silent empty `Trace`.

## Redaction pass-through

Only the Claude Code adapter implements this today.
`ClaudeCodeTraceAdapter(strip_payloads=True)` (or
`ingest_claude_code_session(events, strip_payloads=True)`) replaces every
tool-input and tool-result payload with `{"redacted": True, "size": N}` via
`claude_code.py:_maybe_redact` — names, types, and timing survive; content
does not. This is what `auditk ingest --strip-payloads` uses
(`docs/using-in-practice.md`) before a pack leaves a machine that may hold
sensitive code.

**This is not yet part of the `TraceAdapter` protocol.** `strip_payloads`
is a `ClaudeCodeTraceAdapter.__init__` keyword, not something
`protocols.TraceAdapter` declares, and `LangGraphTraceAdapter`/
`OtelTraceAdapter` take no constructor arguments at all — there is
currently no way to ask either of them to redact a checkpoint or a span.
See "Known contract gaps" below.

If you're writing a new adapter and your native format can carry sensitive
payloads (it almost certainly can), follow the same shape: a
`strip_payloads`-equivalent constructor flag, and a redaction helper that
keeps structural metadata (name, size, error flag) while dropping content.

## Registering an adapter

`src/auditk/adapters/registry.py` is a flat name -> instance map:

```python
_REGISTRY: dict[str, TraceAdapter] = {
    "generic-otel": OtelTraceAdapter(),
    "langgraph": LangGraphTraceAdapter(),
    "claude-code": ClaudeCodeTraceAdapter(),
}
```

`get_adapter(name)` raises `KeyError` listing the available names on a
miss. Add your adapter here (plus a CLI-facing name) to make it reachable
from `auditk ingest --adapter <name>` /
`auditk report --adapter <name>` (`src/auditk/cli.py`). Note that `cli.py`
currently special-cases `adapter == "claude-code"` in both commands for
`--plan-tasks` and subagent discovery, and in `report` for the health-check
gate; `--strip-payloads` on `ingest` is silently a no-op for any adapter
other than `claude-code` (the CLI's `else` branch calls
`trace_adapter.ingest(events)` with no way to pass the flag through) — see
"Known contract gaps."

## Known contract gaps

Two things a natural reading of "adapter contract" implies, that the code
does not yet deliver uniformly. Documented here rather than silently
special-cased or bodged into a false pass:

1. **The health canary is Claude-Code-shape-specific, not adapter-generic.**
   `adapters/health.py:check_adapter_health` takes
   `SessionHealthInput(events=...)`, where `events` is read as raw Claude
   Code JSONL event dicts — `event["type"] in {"assistant", "user"}`,
   `message.content[*].type in {"tool_use", "tool_result"}`,
   `tool_use.id`/`tool_result.tool_use_id`. It has no code path that reads a
   LangGraph checkpoint or an OTel span. `src/auditk/cli.py`'s `report` and
   `doctor` commands only ever construct `SessionHealthInput` in the
   `adapter == "claude-code"` branch; `report --adapter langgraph` or
   `--adapter generic-otel` never runs `check_adapter_health` at all, gets
   no plan-anchor check, no unknown-record-type-share check, and no
   pairing-invariant check. A new adapter for a third native format gets
   none of this for free — writing an equivalent canary for your own
   format (a per-record "type" allow-list, a call/result pairing
   invariant, a "did the harness's own progress-tracking mechanism ever
   fire" check) is your own follow-up work, not something this contract
   currently wires in for you.
2. **Redaction pass-through is Claude-Code-only** — see above. `TraceAdapter`
   has no redact parameter, and the other two adapter classes have no
   redaction mechanism to opt into.

Both are exercised, not hidden, by the conformance kit below: the relevant
cases are `pytest.xfail`'d for `langgraph`/`generic-otel` with a reason
string pointing back to this section, rather than skipped or silently
passing.

## Conformance

`tests/conformance/` is a parametrised pytest suite that runs the same
cases against every adapter in `tests/conformance/providers.PROVIDERS`.
Run it on its own with:

```bash
uv run pytest tests/conformance/ -v --no-cov
```

An adapter opts in by building one
`tests/conformance/kit.AdapterConformanceFixtures` — required fields are
`empty_native`, `malformed_native`, `minimal_valid_native`; optional
`redaction`/`health` fixtures are `None` when the adapter has no hook for
that case yet (see "Known contract gaps"), which the suite reflects as
`xfail`, never a skip. `tests/conformance/providers.py` is the reference
implementation of this for `claude-code`, `langgraph`, and `generic-otel`.

| Case | claude-code | langgraph | generic-otel |
|---|---|---|---|
| empty-input refuses via `ValueError` | pass | pass | pass |
| malformed-input refuses cleanly or processes best-effort (never an undocumented exception) | pass | pass | pass |
| minimal-valid input ingests cleanly | pass | pass | pass |
| redaction pass-through | pass | xfail (gap 2) | xfail (gap 2) |
| health pairing invariants (id-matched + id-less) | pass | xfail (gap 1) | xfail (gap 1) |
| health unknown-record-type-share | pass | xfail (gap 1) | xfail (gap 1) |

If you're adding a fourth adapter: get every non-`xfail` case above passing
before calling it done, and only reach for `pytest.xfail` (with a reason
that says *why*, per the two gaps above) when the gap is a real, undecided
piece of design — not a place to bury a bug.
