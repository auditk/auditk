# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Conformance fixture providers for the three shipped adapters.

Every native-format fixture below is synthetic and invented for this suite
(no real corpus data, no real session/checkpoint/span content) -- same
convention as tests/unit/test_adapter_health.py and the other adapter test
modules. `PROVIDERS` is what `tests/conformance/test_conformance.py`
parametrises over; a new adapter opts in by adding one more
`AdapterConformanceFixtures` here (or, for an out-of-tree adapter, building
one the same way against its own package).
"""

from __future__ import annotations

import json
from typing import Any

from auditk.adapters.claude_code import ClaudeCodeTraceAdapter
from auditk.adapters.generic_otel import GENERIC_OTEL_HEALTH_DECLARATION, OtelTraceAdapter
from auditk.adapters.health import CLAUDE_CODE_HEALTH_DECLARATION
from auditk.adapters.hermes import HERMES_HEALTH_DECLARATION, HermesTraceAdapter
from auditk.adapters.langgraph import LANGGRAPH_HEALTH_DECLARATION, LangGraphTraceAdapter
from auditk.schema import ActionType, Trace
from tests.conformance.kit import AdapterConformanceFixtures, HealthFixture, RedactionFixture

# --- claude-code --------------------------------------------------------
# Native format: a list of parsed Claude Code JSONL event dicts. Helpers
# mirror tests/unit/test_adapter_health.py's fixture builders (same
# synthetic-dict convention, not real transcript data).


def _cc_user_text(text: str = "please do the thing") -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}}


def _cc_assistant_tool_use(
    name: str, tool_input: dict[str, Any], tool_id: str | None = None
) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_use", "name": name, "input": tool_input}
    if tool_id is not None:
        block["id"] = tool_id
    return {"type": "assistant", "message": {"content": [block]}}


def _cc_user_tool_result(tool_use_id: str | None = None, content: str = "ok") -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "content": content}
    if tool_use_id is not None:
        block["tool_use_id"] = tool_use_id
    return {"type": "user", "message": {"content": [block]}}


def _cc_redaction_fixture() -> RedactionFixture:
    native = [
        _cc_user_text("please list the sandbox directory"),
        _cc_assistant_tool_use("Bash", {"command": "ls sandbox/"}, tool_id="call-ls"),
        _cc_user_tool_result(tool_use_id="call-ls", content="file1\nfile2"),
    ]

    def _assert_redacted(trace: Trace) -> None:
        tool_call = next(s for s in trace.steps if s.action.type.value == "tool_call")
        assert tool_call.action.payload["input"] == {
            "redacted": True,
            "size": len(str({"command": "ls sandbox/"})),
        }
        tool_result = next(s for s in trace.steps if s.action.type.value == "env_effect")
        assert tool_result.action.payload["tool_result"] == {
            "redacted": True,
            "size": len("file1\nfile2"),
        }

    return RedactionFixture(
        redacting_adapter=ClaudeCodeTraceAdapter(strip_payloads=True),
        native=native,
        assert_redacted=_assert_redacted,
    )


def _cc_health_fixture() -> HealthFixture:
    id_matched_paired = [
        _cc_user_text(),
        _cc_assistant_tool_use("Read", {"file_path": "/a"}, tool_id="call-read"),
        _cc_user_tool_result(tool_use_id="call-read"),
    ]
    # No id, no result at all -- the capture just ended mid-call. The
    # id-less fallback (_trailing_in_flight_call_count) must excuse this,
    # not breach it.
    id_less_trailing = [_cc_assistant_tool_use("Read", {"file_path": "/a"})]
    # call-a's result never arrives, but call-b (issued after it) gets a
    # full round trip -- a genuine mid-transcript orphan, not truncation.
    id_matched_orphan = [
        _cc_assistant_tool_use("Read", {"file_path": "/a"}, tool_id="call-a"),
        _cc_assistant_tool_use("Bash", {"command": "echo hi"}, tool_id="call-b"),
        _cc_user_tool_result(tool_use_id="call-b"),
    ]
    unknown_type_share = [{"type": "totally-new-conformance-type"} for _ in range(4)] + [
        _cc_user_text()
    ]
    return HealthFixture(
        declaration=CLAUDE_CODE_HEALTH_DECLARATION,
        id_matched_paired_events=id_matched_paired,
        id_less_trailing_events=id_less_trailing,
        id_matched_orphan_events=id_matched_orphan,
        unknown_type_share_events=unknown_type_share,
    )


_CLAUDE_CODE = AdapterConformanceFixtures(
    name="claude-code",
    adapter=ClaudeCodeTraceAdapter(),
    empty_native=[],
    # Structurally a list of dicts, but neither record carries the fields
    # the happy path assumes (no substantive text/tool_use shape). The CC
    # adapter is defensive throughout (isinstance checks on every nested
    # field), so this is processed best-effort rather than refused --
    # see docs/adapters.md's "malformed-input" section.
    malformed_native=[{"type": "user"}, {"type": "assistant", "message": {"content": "oops"}}],
    minimal_valid_native=[
        _cc_user_text("list the sandbox directory"),
        _cc_assistant_tool_use("Bash", {"command": "ls sandbox/"}, tool_id="call-1"),
        _cc_user_tool_result(tool_use_id="call-1"),
    ],
    redaction=_cc_redaction_fixture(),
    health=_cc_health_fixture(),
)


# --- langgraph -----------------------------------------------------------
# Native format: a list of serialised LangGraph CheckpointTuple dicts.


def _lg_checkpoint(
    checkpoint_id: str,
    *,
    thread_id: str = "thread-1",
    step: int = 0,
    writes: dict[str, Any] | None = None,
    parent_checkpoint_id: str | None = None,
    source: str = "loop",
) -> dict[str, Any]:
    checkpoint: dict[str, Any] = {
        "config": {"configurable": {"thread_id": thread_id}},
        "checkpoint": {"id": checkpoint_id},
        # `source` is LangGraph's own CheckpointMetadata field -- one of
        # "input"/"loop"/"update"/"fork" in real usage (see
        # `LANGGRAPH_HEALTH_DECLARATION`). Defaulted to "loop" (a normal
        # superstep) so existing callers that don't care about it still
        # build a well-formed, KNOWN-type checkpoint.
        "metadata": {"step": step, "writes": writes or {}, "source": source},
    }
    if parent_checkpoint_id is not None:
        checkpoint["parent_config"] = {"configurable": {"checkpoint_id": parent_checkpoint_id}}
    return checkpoint


def _lg_redaction_fixture() -> RedactionFixture:
    # A TOOL_CALL-shaped checkpoint (no "messages" key, not the "respond"
    # node) -- see `_classify_action`. `node_writes` is the sensitive
    # content; `node_name` ("run_sandbox_ls") is structural and must
    # survive redaction untouched.
    node_writes = {"result": "file1\nfile2", "cwd": "sandbox/"}
    native = [_lg_checkpoint("ck-1", writes={"run_sandbox_ls": node_writes})]

    def _assert_redacted(trace: Trace) -> None:
        tool_call = next(s for s in trace.steps if s.action.type == ActionType.TOOL_CALL)
        assert tool_call.action.payload["node"] == "run_sandbox_ls"
        assert tool_call.action.payload["writes"] == {
            "redacted": True,
            "size": len(str(node_writes)),
        }

    return RedactionFixture(
        redacting_adapter=LangGraphTraceAdapter(strip_payloads=True),
        native=native,
        assert_redacted=_assert_redacted,
    )


def _lg_health_fixture() -> HealthFixture:
    # LangGraph has no call/result id-pairing concept
    # (`LANGGRAPH_HEALTH_DECLARATION.pairing_supported` is False, with a
    # reason) -- these three lists exist for structural completeness
    # only; `TestHealthPairingInvariants` SKIPs rather than asserting
    # against them, per the declaration.
    id_matched_paired = [_lg_checkpoint("ck-1")]
    id_less_trailing = [_lg_checkpoint("ck-1")]
    id_matched_orphan = [_lg_checkpoint("ck-1")]
    # 4 checkpoints whose `metadata.source` is a value LangGraph has never
    # emitted (not in {"input", "loop", "update", "fork"}), 1 with a known
    # source -- 80% unknown, well over the 5% floor.
    unknown_type_share = [
        _lg_checkpoint(f"ck-unknown-{i}", source="totally-new-conformance-source") for i in range(4)
    ] + [_lg_checkpoint("ck-known", source="loop")]
    return HealthFixture(
        declaration=LANGGRAPH_HEALTH_DECLARATION,
        id_matched_paired_events=id_matched_paired,
        id_less_trailing_events=id_less_trailing,
        id_matched_orphan_events=id_matched_orphan,
        unknown_type_share_events=unknown_type_share,
    )


_LANGGRAPH = AdapterConformanceFixtures(
    name="langgraph",
    adapter=LangGraphTraceAdapter(),
    empty_native=[],
    # Missing `config` entirely -- ingest_checkpoints refuses via ValueError
    # (see the P1 fix(adapters) commit; previously a bare KeyError).
    malformed_native=[{"checkpoint": {"id": "c1"}, "metadata": {}}],
    minimal_valid_native=[
        _lg_checkpoint(
            "ck-1", writes={"respond": {"messages": [{"type": "ai", "content": "hello"}]}}
        )
    ],
    redaction=_lg_redaction_fixture(),
    health=_lg_health_fixture(),
)


# --- generic-otel ----------------------------------------------------------
# Native format: a list of OTel/OpenInference span dicts.


def _otel_span(
    span_id: str,
    *,
    trace_id: str = "trace-1",
    parent_span_id: str | None = None,
    name: str = "span",
    kind: str = "AGENT",
    start_time: str = "2026-01-01T00:00:00Z",
    attributes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs = {"openinference.span.kind": kind, **(attributes or {})}
    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "start_time": start_time,
        "attributes": attrs,
    }


def _otel_redaction_fixture() -> RedactionFixture:
    native = [
        _otel_span(
            "span-1",
            kind="TOOL",
            name="Bash",
            attributes={"input.value": "ls sandbox/", "output.value": "file1\nfile2"},
        )
    ]

    def _assert_redacted(trace: Trace) -> None:
        tool_call = next(s for s in trace.steps if s.action.type == ActionType.TOOL_CALL)
        assert tool_call.action.payload["name"] == "Bash"
        assert tool_call.action.payload["input"] == {
            "redacted": True,
            "size": len("ls sandbox/"),
        }
        assert tool_call.action.payload["output"] == {
            "redacted": True,
            "size": len("file1\nfile2"),
        }

    return RedactionFixture(
        redacting_adapter=OtelTraceAdapter(strip_payloads=True),
        native=native,
        assert_redacted=_assert_redacted,
    )


def _otel_health_fixture() -> HealthFixture:
    # generic-otel has no call/result id-pairing concept
    # (`GENERIC_OTEL_HEALTH_DECLARATION.pairing_supported` is False, with
    # a reason) -- these three lists exist for structural completeness
    # only; `TestHealthPairingInvariants` SKIPs rather than asserting
    # against them, per the declaration.
    id_matched_paired = [_otel_span("span-1", kind="TOOL")]
    id_less_trailing = [_otel_span("span-1", kind="TOOL")]
    id_matched_orphan = [_otel_span("span-1", kind="TOOL")]
    # 4 spans whose `openinference.span.kind` is a value never seen in the
    # OpenInference spec's own span-kind vocabulary, 1 with a known kind --
    # 80% unknown, well over the 5% floor.
    unknown_type_share = [
        _otel_span(f"span-unknown-{i}", kind="TOTALLY-NEW-SPAN-KIND-V9") for i in range(4)
    ] + [_otel_span("span-known", kind="LLM")]
    return HealthFixture(
        declaration=GENERIC_OTEL_HEALTH_DECLARATION,
        id_matched_paired_events=id_matched_paired,
        id_less_trailing_events=id_less_trailing,
        id_matched_orphan_events=id_matched_orphan,
        unknown_type_share_events=unknown_type_share,
    )


_GENERIC_OTEL = AdapterConformanceFixtures(
    name="generic-otel",
    adapter=OtelTraceAdapter(),
    empty_native=[],
    # Missing `span_id` on the only span -- ingest_otel_spans refuses via
    # ValueError (see the P1 fix(adapters) commit; previously a bare
    # KeyError from _span_to_step).
    malformed_native=[{"trace_id": "trace-1", "start_time": "2026-01-01T00:00:00Z", "name": "x"}],
    minimal_valid_native=[_otel_span("span-1")],
    redaction=_otel_redaction_fixture(),
    health=_otel_health_fixture(),
)


# --- hermes --------------------------------------------------------------
# Native format: a list of Hermes `messages`-table row dicts, ordered by
# timestamp (see src/auditk/adapters/hermes.py's module docstring for the
# real column shapes -- role/content/tool_call_id/tool_calls/tool_name --
# this mirrors, confirmed against a live corpus and the hermes-agent writer
# source, not guessed).


def _hermes_row(role: str, **kwargs: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"session_id": "hermes-conformance-1", "role": role, "timestamp": 0.0}
    row.update(kwargs)
    return row


def _hermes_tool_calls_json(call_id: str, name: str, arguments: dict[str, Any]) -> str:
    """The real on-disk shape of an `assistant` row's `tool_calls` column:
    a JSON-encoded string wrapping a list of OpenAI-style function-call
    dicts, whose own `function.arguments` is itself a JSON-encoded string."""
    return json.dumps(
        [
            {
                "id": call_id,
                "call_id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    )


def _hermes_redaction_fixture() -> RedactionFixture:
    native = [
        _hermes_row("user", id=1, content="please list the sandbox directory"),
        _hermes_row(
            "assistant",
            id=2,
            tool_calls=_hermes_tool_calls_json("call-ls", "terminal", {"command": "ls sandbox/"}),
        ),
        _hermes_row(
            "tool", id=3, tool_call_id="call-ls", tool_name="terminal", content="file1\nfile2"
        ),
    ]

    def _assert_redacted(trace: Trace) -> None:
        tool_call = next(s for s in trace.steps if s.action.type == ActionType.TOOL_CALL)
        assert tool_call.action.payload["input"] == {
            "redacted": True,
            "size": len(str({"command": "ls sandbox/"})),
        }
        tool_result = next(s for s in trace.steps if s.action.type == ActionType.ENV_EFFECT)
        assert tool_result.action.payload["tool_result"] == {
            "redacted": True,
            "size": len("file1\nfile2"),
        }

    return RedactionFixture(
        redacting_adapter=HermesTraceAdapter(strip_payloads=True),
        native=native,
        assert_redacted=_assert_redacted,
    )


def _hermes_health_fixture() -> HealthFixture:
    id_matched_paired = [
        _hermes_row("user", id=1),
        _hermes_row(
            "assistant",
            id=2,
            tool_calls=_hermes_tool_calls_json("call-read", "read_file", {"path": "/a"}),
        ),
        _hermes_row("tool", id=3, tool_call_id="call-read", tool_name="read_file", content="ok"),
    ]
    # Hermes' tool_calls entries always carry a real id when they exist at
    # all -- there is no genuinely id-less native shape to model here the
    # way Claude Code's fixture does. This instead models the real
    # "capture ends mid-call" case directly: a call issued, no result row
    # ever arrives, and nothing follows it either -- the id-matched
    # trailing-in-flight excuse must still apply.
    id_less_trailing = [
        _hermes_row(
            "assistant",
            id=1,
            tool_calls=_hermes_tool_calls_json("call-a", "read_file", {"path": "/a"}),
        )
    ]
    # call-a's result never arrives, but call-b (issued after it) gets a
    # full round trip -- a genuine mid-transcript orphan, not truncation.
    id_matched_orphan = [
        _hermes_row(
            "assistant",
            id=1,
            tool_calls=_hermes_tool_calls_json("call-a", "read_file", {"path": "/a"}),
        ),
        _hermes_row(
            "assistant",
            id=2,
            tool_calls=_hermes_tool_calls_json("call-b", "terminal", {"command": "echo hi"}),
        ),
        _hermes_row("tool", id=3, tool_call_id="call-b", tool_name="terminal", content="hi"),
    ]
    # 4 rows whose `role` Hermes has never emitted, 1 with a known role --
    # 80% unknown, well over the 5% floor.
    unknown_type_share = [_hermes_row("totally-new-conformance-role", id=i) for i in range(4)] + [
        _hermes_row("user", id=5)
    ]
    return HealthFixture(
        declaration=HERMES_HEALTH_DECLARATION,
        id_matched_paired_events=id_matched_paired,
        id_less_trailing_events=id_less_trailing,
        id_matched_orphan_events=id_matched_orphan,
        unknown_type_share_events=unknown_type_share,
    )


_HERMES = AdapterConformanceFixtures(
    name="hermes",
    adapter=HermesTraceAdapter(),
    empty_native=[],
    # Missing `role` entirely on the first row, unparseable `tool_calls`
    # string and a non-str `content` on the second -- the Hermes adapter is
    # defensive throughout (mirrors Claude Code's isinstance-checked
    # style), so this is processed best-effort rather than refused -- see
    # docs/adapters.md's "malformed-input" section.
    malformed_native=[
        {"session_id": "hermes-conformance-malformed"},
        {
            "session_id": "hermes-conformance-malformed",
            "role": "assistant",
            "tool_calls": "not-json-at-all",
            "content": 123,
        },
    ],
    minimal_valid_native=[
        _hermes_row("user", id=1, content="list the sandbox directory"),
        _hermes_row(
            "assistant",
            id=2,
            tool_calls=_hermes_tool_calls_json("call-1", "terminal", {"command": "ls sandbox/"}),
        ),
        _hermes_row("tool", id=3, tool_call_id="call-1", tool_name="terminal", content="ok"),
    ],
    redaction=_hermes_redaction_fixture(),
    health=_hermes_health_fixture(),
)


PROVIDERS: list[AdapterConformanceFixtures] = [_CLAUDE_CODE, _LANGGRAPH, _GENERIC_OTEL, _HERMES]
