# Writing a trace adapter

An adapter turns one agent harness's native trace format into auditk's
normalised `Trace` (`src/auditk/schema.py`). This page is the contract: what
an adapter must map, what it must never invent, how redaction pass-through
works, how to register an adapter, and how to check your work with the
conformance kit.

It is written against the three adapters shipped today —
`src/auditk/adapters/claude_code.py`, `src/auditk/adapters/langgraph.py`,
`src/auditk/adapters/generic_otel.py` — and describes their actual,
current behaviour, not an aspirational one. An earlier version of this
page called out two real contract gaps (redaction pass-through and the
health canary both being Claude-Code-only) rather than glossing over
them; P1b closed both — see "Redaction pass-through" and "The health
canary" below for the design that replaced them, and the Conformance
table for where a genuine per-format limitation (no call/result-id
pairing concept in LangGraph or generic-otel) is now a documented,
reasoned skip rather than a gap.

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
either *is* a tool call/result or it isn't) — see "The health canary"
below for what this means for the health canary's pairing check
(`HealthDeclaration.pairing_supported=False` for both, with a reason).

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

See "The health canary" below — a new adapter gets health checking by
writing a `HealthDeclaration`, not automatically; it's worth reading
before you assume your adapter gets it "for free."

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

`strip_payloads` is contractual as of P1b: every shipped adapter's
constructor takes `strip_payloads: bool = False`
(`ClaudeCodeTraceAdapter`, `LangGraphTraceAdapter`, `OtelTraceAdapter`),
and `auditk ingest --strip-payloads` honours it for all three. Two
mechanisms exist, deliberately, not one — see "Two redaction mechanisms,
by design" below for why.

Names, kinds, ids, and timing always survive; only the sensitive content
keys are replaced with `{"redacted": True, "size": N}` (`N` = `len(str(
original_value))`, or `0` for `None`):

| Adapter | Redacted `Action.payload` keys | Scope |
|---|---|---|
| claude-code | `TOOL_CALL`'s `input`, `ENV_EFFECT`'s `tool_result` | `claude_code.py:_maybe_redact`, applied inline at Step-construction time |
| langgraph | `TOOL_CALL`'s `writes` | `redaction.py:redact_trace`, applied as a post-ingest pass |
| generic-otel | `TOOL_CALL`'s `input`/`output` | `redaction.py:redact_trace`, applied as a post-ingest pass |

In every case `UTTERANCE` (narration / the LLM's own response) is
deliberately left untouched — a model's stated intent is not the
sensitive-payload surface this exists to protect, only the concrete tool
call/result content is.

### Two redaction mechanisms, by design

Claude Code's own `_maybe_redact` (`claude_code.py`) is applied inline,
threaded through step construction, and was already correct and
already pinned by tests before P1b — rewriting it to route through a
shared pass would have touched a lot of already-working, well-tested code
for no behavioural gain. `src/auditk/adapters/redaction.py` is instead the
NEW, shared, adapter-generic mechanism the other two adapters (and any
future one) plug into: one implementation (`redact_trace`), applied
POST-INGEST over the already-mapped `Trace`, driven by a small
`ActionType -> frozenset[str]` "which payload keys are content for this
action type" declaration each adapter owns (`langgraph.py
:REDACTION_CONTENT_KEYS`, `generic_otel.py:REDACTION_CONTENT_KEYS`). If
you're writing a new adapter, reach for `redact_trace` — it's less code
than reimplementing `_maybe_redact`, and it's already exercised by the
conformance kit below.

**The CLI never silently ignores `--strip-payloads`.**
`adapters/registry.py:get_adapter(name, strip_payloads=True)` looks the
adapter up in `_FACTORIES` (every adapter with redaction support is
there) and constructs a `strip_payloads=True` instance; an adapter absent
from `_FACTORIES` makes `get_adapter` raise `ValueError` instead, which
`cli.py`'s `ingest` command turns into a clean, non-zero-exit refusal —
never a no-op. If you add a new adapter that genuinely cannot redact its
format, simply don't add it to `_FACTORIES`: the CLI will refuse loudly
on `--strip-payloads` for it rather than silently proceeding as if the
flag had no effect.

## Registering an adapter

`src/auditk/adapters/registry.py` has three flat name -> * maps: `_REGISTRY`
(name -> a no-strip `TraceAdapter` instance), `_FACTORIES` (name ->
`Callable[[bool], TraceAdapter]`, for `--strip-payloads`; see above), and
`_HEALTH_DECLARATIONS` (name -> `HealthDeclaration`; see "The health
canary" below):

```python
_REGISTRY: dict[str, TraceAdapter] = {
    "generic-otel": OtelTraceAdapter(),
    "langgraph": LangGraphTraceAdapter(),
    "claude-code": ClaudeCodeTraceAdapter(),
}
```

`get_adapter(name)` raises `KeyError` listing the available names on a
miss. Add your adapter to all three maps (plus a CLI-facing name) to make
it fully reachable from `auditk ingest --adapter <name>` /
`auditk report --adapter <name>` (`src/auditk/cli.py`). Note that `cli.py`
still special-cases `adapter == "claude-code"` in both commands for
`--plan-tasks` and subagent discovery — that part of the contract is
unchanged by P1b.

## The health canary

`adapters/health.py:check_adapter_health` reads a `HealthDeclaration`
(passed via its `declaration` keyword, defaulting to
`CLAUDE_CODE_HEALTH_DECLARATION`) rather than assuming Claude Code's raw
JSONL shape — this is what closed P1b's "health canary is
Claude-Code-shape-specific" gap. A declaration is a small set of pure
extractor callables plus supported/reason flags, one per sub-check:

- **unknown-record-type-share**: `record_type(record) -> str | None` plus
  `known_record_types` — Claude Code's is `event["type"]` against
  `KNOWN_RECORD_TYPES`; LangGraph's is `metadata.source` (LangGraph's own
  `Literal["input", "loop", "update", "fork"]`, see
  `langgraph.py:LANGGRAPH_HEALTH_DECLARATION`); generic-otel's is
  `attributes["openinference.span.kind"]` against the OpenInference spec's
  own span-kind vocabulary (`generic_otel.py:GENERIC_OTEL_HEALTH_DECLARATION`).
  All three support this check.
- **call/result id-pairing**: `call_ids`/`result_ref_ids` per record, plus
  `pairing_boundary` (the id-less fallback's trailing-call boundary). Only
  Claude Code supports this: a `tool_use`/`tool_result` id pair is a real
  concept in its native format. Neither LangGraph nor generic-otel has an
  equivalent — a checkpoint records a node's writes *after* the node
  already ran, and an exported TOOL/RETRIEVER span already carries both
  its input and output as one record — so both declare
  `pairing_supported=False` with a reason, and the conformance suite
  SKIPs (not xfails, not fake-passes) that case for them.
- **plan-anchor** (corpus-level, `doctor` only): `call_names` per record
  plus `anchor_tool_names`. Only Claude Code supports this too — neither
  format has a built-in plan-tracking-tool concept the way Claude Code's
  harness does (`TodoWrite`/`TaskCreate`/`TaskUpdate`).

`check_adapter_health`'s default parameter value IS
`CLAUDE_CODE_HEALTH_DECLARATION`, so every pre-P1b call site that doesn't
pass a `declaration` is byte-for-byte unaffected — verified by running
`auditk doctor` against the same live corpus before and after this
change and diffing the output.

`cli.py`'s `report` command wires this generically: any adapter with an
entry in `registry._HEALTH_DECLARATIONS` gets the canary gate (breach
without `--force` refuses to emit a report, exactly claude-code's
existing behaviour); an adapter with none is told so explicitly ("no
health declaration") rather than the check being silently skipped.
`doctor` still only ever walks a Claude Code on-disk corpus (its
session/plan-store/subagents discovery is inherently Claude-Code-shaped)
and passes `CLAUDE_CODE_HEALTH_DECLARATION` explicitly for clarity.

### Worked example: a hypothetical fourth adapter's declaration

Say you're writing an adapter for a hypothetical `acme-agent` JSON-lines
format where every record has a `"kind"` field from a small fixed set,
and there's no separate call/result-id concept (like LangGraph/
generic-otel) but there IS a `"todo"` tool the harness calls to track its
plan (like Claude Code):

```python
from auditk.adapters.health import HealthDeclaration


def _acme_record_type(record: dict[str, Any]) -> str | None:
    return record.get("kind")


ACME_HEALTH_DECLARATION = HealthDeclaration(
    name="acme-agent",
    record_type=_acme_record_type,
    known_record_types=frozenset({"turn", "tool_call", "note"}),
    pairing_supported=False,
    pairing_skip_reason="acme-agent records have no call/result id pair.",
    call_names=lambda record: [record["tool"]] if record.get("kind") == "tool_call" else [],
    anchor_tool_names=frozenset({"todo"}),
)
```

That's the whole hook: `known_record_types` catches a future `acme-agent`
release adding a record kind this adapter has never seen, and
`anchor_tool_names`/`call_names` let the corpus-level dead-anchor
invariant recognise `"todo"` calls the same way it recognises Claude
Code's `TaskCreate`. `pairing_supported=False` is a documented, reasoned
decision, not an oversight — exactly the langgraph/generic-otel pattern
above.

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
`redaction`/`health` fixtures are `None` when the adapter has no hook at
all for that case, which the suite reflects as `xfail`, never a skip. As
of P1b all three shipped adapters have both hooks, so no `xfail` is
reachable for them today — `Optional` stays the shape so a fourth,
out-of-tree adapter can still opt in before writing one. Within `health`,
a hook may exist but say a sub-check's *concept* doesn't apply to this
format (`HealthDeclaration.pairing_supported=False`, for example); the
suite reflects that as a `pytest.skip()` carrying the declaration's own
reason — a documented, reasoned skip, not an `xfail` and not a fake pass.
`tests/conformance/providers.py` is the reference implementation of this
for `claude-code`, `langgraph`, and `generic-otel`.

| Case | claude-code | langgraph | generic-otel |
|---|---|---|---|
| empty-input refuses via `ValueError` | pass | pass | pass |
| malformed-input refuses cleanly or processes best-effort (never an undocumented exception) | pass | pass | pass |
| minimal-valid input ingests cleanly | pass | pass | pass |
| redaction pass-through | pass | pass | pass |
| health pairing invariants (id-matched + id-less) | pass | skip (no id-pairing concept) | skip (no id-pairing concept) |
| health unknown-record-type-share | pass | pass | pass |

If you're adding a fourth adapter: get every non-`xfail`/non-`skip` case
above passing before calling it done. Reach for `pytest.xfail` only when
you haven't written a hook yet at all; reach for the declaration-driven
skip (`*_supported=False` + a reason) only when the concept genuinely
doesn't exist in your format — never as a place to bury a bug.
