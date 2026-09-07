# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the Pi coding-agent session adapter.

Unlike every other adapter's integration fixtures (synthetic by
convention), the ``tests/fixtures/pi/*.jsonl`` files are REAL session files
written by pi 0.85.1's own ``SessionManager`` during live ``pi --print``
runs (path-anonymised only) -- see ``tests/fixtures/pi/README.md`` for
provenance and ``docs/pi-format-notes.md`` for the confirmed grammar these
tests pin. Synthetic entry dicts appear below only for shapes the live
corpus cannot produce non-interactively (``bashExecution``, intent
precedence variants, the unreleased v4 header) and are marked as such.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from auditk.adapters.pi import PiTraceAdapter, ingest_pi_session
from auditk.schema import ActionType, Actor, Trace

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "pi"
_SPEC_PATH = Path(os.environ.get("AUDITK_SPEC_PATH", "../auditk-spec"))
_TRACE_SCHEMA_PATH = _SPEC_PATH / "spec" / "v0.1" / "trace.schema.json"


def _load(name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in (_FIXTURES / name).read_text().splitlines() if line.strip()
    ]


@pytest.fixture(scope="module")
def trace_schema() -> dict[str, Any]:
    if not _TRACE_SCHEMA_PATH.exists():
        pytest.skip(
            f"auditk-spec not found at {_SPEC_PATH}; "
            "set AUDITK_SPEC_PATH to run schema-validation tests."
        )
    return json.loads(_TRACE_SCHEMA_PATH.read_text())  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# The rich real session: header, config preamble, multi-tool-call turn
# ---------------------------------------------------------------------------


class TestRichSession:
    def test_returns_trace(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        assert isinstance(trace, Trace)

    def test_source_adapter(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        assert trace.source_adapter == "pi"

    def test_trace_id_is_header_session_id(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        assert trace.trace_id == "01a07bca-7505-72d4-81eb-73bffd067779"

    def test_header_cwd_in_trace_metadata(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        assert trace.metadata["cwd"] == "/home/user/project"

    def test_step_count_and_action_type_sequence(self) -> None:
        """9 entries after the header expand to 12 steps: the two config
        entries, the user turn, assistant #1 (utterance + 2 chained tool
        calls), two tool results, assistant #2 (utterance + 1 tool call),
        one tool result, and the final assistant utterance."""
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        assert [s.action.type for s in trace.steps] == [
            ActionType.STATE_TRANSITION,  # model_change
            ActionType.STATE_TRANSITION,  # thinking_level_change
            ActionType.UTTERANCE,  # user
            ActionType.UTTERANCE,  # assistant narration
            ActionType.TOOL_CALL,  # bash ls
            ActionType.TOOL_CALL,  # read notes.txt
            ActionType.ENV_EFFECT,  # bash result
            ActionType.ENV_EFFECT,  # read result
            ActionType.UTTERANCE,  # assistant narration
            ActionType.TOOL_CALL,  # write summary.txt
            ActionType.ENV_EFFECT,  # write result
            ActionType.UTTERANCE,  # final assistant text
        ]

    def test_step_ids_are_native_entry_and_tool_call_ids(self) -> None:
        """Steps reuse pi's own ids: the 8-hex entry id for whole-entry
        steps, the toolCall's own id for expanded tool-call steps."""
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        assert [s.step_id for s in trace.steps] == [
            "8d822f61",
            "3f919ee8",
            "2be1988e",
            "42ad787d",
            "call_mock_ls",
            "call_mock_read",
            "7269abbc",
            "3e67f728",
            "22088513",
            "call_mock_write",
            "718b424f",
            "412e0e71",
        ]

    def test_native_parent_ids_preserved_on_whole_entry_steps(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        by_id = {s.step_id: s for s in trace.steps}
        assert by_id["8d822f61"].parent_step_id is None
        assert by_id["3f919ee8"].parent_step_id == "8d822f61"
        assert by_id["2be1988e"].parent_step_id == "3f919ee8"
        assert by_id["42ad787d"].parent_step_id == "2be1988e"
        # A toolResult entry's parent is its native parentId, untouched by
        # the sibling expansion of the assistant entry before it.
        assert by_id["7269abbc"].parent_step_id == "42ad787d"

    def test_intra_entry_expansion_chains_by_parent_step_id(self) -> None:
        """One assistant entry with narration + two toolCalls expands to
        three steps chained utterance -> call 1 -> call 2 (the Claude Code
        `_assistant_steps` convention)."""
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        by_id = {s.step_id: s for s in trace.steps}
        assert by_id["call_mock_ls"].parent_step_id == "42ad787d"
        assert by_id["call_mock_read"].parent_step_id == "call_mock_ls"

    def test_actors(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        by_id = {s.step_id: s for s in trace.steps}
        assert by_id["2be1988e"].actor == Actor.USER
        assert by_id["42ad787d"].actor == Actor.AGENT
        assert by_id["call_mock_ls"].actor == Actor.AGENT
        assert by_id["7269abbc"].actor == Actor.ENVIRONMENT

    def test_tool_call_payload(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        call = next(s for s in trace.steps if s.step_id == "call_mock_ls")
        assert call.action.payload["name"] == "bash"
        assert call.action.payload["input"] == {"command": "ls -1"}

    def test_tool_result_payload_pairs_by_verbatim_id(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        result = next(s for s in trace.steps if s.step_id == "7269abbc")
        assert result.action.payload["tool_call_id"] == "call_mock_ls"
        assert result.action.payload["tool_name"] == "bash"
        assert result.action.payload["is_error"] is False
        assert "notes.txt" in str(result.action.payload["tool_result"])

    def test_declared_intent_narration_wins_over_thinking(self) -> None:
        """Assistant entry 42ad787d carries BOTH a thinking block and a
        text block; narration is tier 1 and must win on every step the
        entry expands to."""
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        by_id = {s.step_id: s for s in trace.steps}
        narration = "I'll list the workspace and read the notes file first."
        assert by_id["42ad787d"].declared_intent == narration
        assert by_id["call_mock_ls"].declared_intent == narration
        assert by_id["call_mock_read"].declared_intent == narration

    def test_user_step_has_no_declared_intent(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        user = next(s for s in trace.steps if s.step_id == "2be1988e")
        assert user.declared_intent is None

    def test_state_transition_payloads(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        by_id = {s.step_id: s for s in trace.steps}
        assert by_id["8d822f61"].action.payload["kind"] == "model_change"
        assert by_id["8d822f61"].action.payload["provider"] == "mock"
        assert by_id["8d822f61"].action.payload["model_id"] == "mock-model"
        assert by_id["3f919ee8"].action.payload["kind"] == "thinking_level_change"
        assert by_id["3f919ee8"].action.payload["thinking_level"] == "medium"

    def test_timestamps_are_timezone_aware(self) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        for step in trace.steps:
            assert step.timestamp.tzinfo is not None

    def test_validates_against_schema(self, trace_schema: dict[str, Any]) -> None:
        trace = ingest_pi_session(_load("session-rich.jsonl"))
        jsonschema.validate(json.loads(trace.model_dump_json()), trace_schema)


# ---------------------------------------------------------------------------
# The error session: a real failing tool
# ---------------------------------------------------------------------------


class TestErrorSession:
    def test_is_error_true_preserved(self) -> None:
        trace = ingest_pi_session(_load("session-error.jsonl"))
        errors = [
            s
            for s in trace.steps
            if s.action.type == ActionType.ENV_EFFECT and s.action.payload["is_error"] is True
        ]
        assert len(errors) == 1
        assert errors[0].action.payload["tool_call_id"] == "call_mock_fail"
        assert "ENOENT" in str(errors[0].action.payload["tool_result"])


# ---------------------------------------------------------------------------
# The forked session: parentSession header, copied entries
# ---------------------------------------------------------------------------


class TestForkedSession:
    def test_parent_session_path_in_trace_metadata(self) -> None:
        trace = ingest_pi_session(_load("session-forked.jsonl"))
        assert trace.metadata["parent_session"] == (
            "/home/user/.pi/agent/sessions/--home-user-project--/"
            "2026-09-07T12-14-20-933Z_01a07bca-7505-72d4-81eb-73bffd067779.jsonl"
        )

    def test_trace_id_is_the_fork_not_the_parent(self) -> None:
        trace = ingest_pi_session(_load("session-forked.jsonl"))
        assert trace.trace_id == "01a07bcc-0828-76db-937e-b81ece9ca9d6"

    def test_copied_parent_entries_ingest_with_original_ids(self) -> None:
        trace = ingest_pi_session(_load("session-forked.jsonl"))
        step_ids = [s.step_id for s in trace.steps]
        # Entries copied verbatim from the parent keep their original ids...
        assert "42ad787d" in step_ids
        # ...and the appended new turns follow them.
        assert step_ids.index("42ad787d") < step_ids.index("7925e198")


# ---------------------------------------------------------------------------
# Refusals: empty, headerless, v4
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_empty_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            ingest_pi_session([])

    def test_missing_header_raises_value_error(self) -> None:
        entries = _load("session-rich.jsonl")[1:]
        with pytest.raises(ValueError, match="header"):
            ingest_pi_session(entries)

    def test_v4_header_refused_loudly_with_version_message(self) -> None:
        """The unreleased git-main storage rewrite (docs/pi-format-notes.md
        § version drift): a v4 header must be a clean, explicit refusal,
        never a half-parse. Synthetic by necessity -- no v4 release exists."""
        v4 = [
            {
                "v": 4,
                "kind": "header",
                "id": "sess-v4",
                "storageVersion": 1,
                "createdAt": 0,
                "cwd": "/home/user/project",
            }
        ]
        with pytest.raises(ValueError, match="(?i)version"):
            ingest_pi_session(v4)


# ---------------------------------------------------------------------------
# Shapes the live corpus cannot produce non-interactively (synthetic,
# shaped from the installed writer's own declarations -- see module docstring)
# ---------------------------------------------------------------------------


def _header() -> dict[str, Any]:
    return {
        "type": "session",
        "version": 3,
        "id": "synthetic-session",
        "timestamp": "2026-09-07T12:00:00.000Z",
        "cwd": "/home/user/project",
    }


def _entry(entry_id: str, parent: str | None, **fields: Any) -> dict[str, Any]:
    return {
        "id": entry_id,
        "parentId": parent,
        "timestamp": "2026-09-07T12:00:01.000Z",
        **fields,
    }


class TestSyntheticShapes:
    def test_thinking_only_intent_falls_back_to_thinking(self) -> None:
        entries = [
            _header(),
            _entry(
                "aa000001",
                None,
                type="message",
                message={
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "quietly planning"},
                        {
                            "type": "toolCall",
                            "id": "call_t1",
                            "name": "bash",
                            "arguments": {"command": "true"},
                        },
                    ],
                    "timestamp": 0,
                },
            ),
        ]
        trace = ingest_pi_session(entries)
        call = next(s for s in trace.steps if s.step_id == "call_t1")
        assert call.declared_intent == "quietly planning"

    def test_bash_execution_role_maps_to_tool_call_with_output(self) -> None:
        """The TUI `!` command's own message role (writer source:
        `messages.d.ts:BashExecutionMessage`)."""
        entries = [
            _header(),
            _entry(
                "aa000002",
                None,
                type="message",
                message={
                    "role": "bashExecution",
                    "command": "echo hi",
                    "output": "hi",
                    "exitCode": 0,
                    "cancelled": False,
                    "truncated": False,
                    "timestamp": 0,
                },
            ),
        ]
        trace = ingest_pi_session(entries)
        assert len(trace.steps) == 1
        step = trace.steps[0]
        assert step.action.type == ActionType.TOOL_CALL
        assert step.actor == Actor.USER
        assert step.action.payload["name"] == "bash_execution"
        assert step.action.payload["input"] == {"command": "echo hi"}
        assert step.action.payload["output"] == "hi"
        assert step.action.payload["exit_code"] == 0

    def test_user_string_content_supported(self) -> None:
        """`UserMessage.content` is typed `string | blocks` in the writer;
        the corpus only shows the block form."""
        entries = [
            _header(),
            _entry(
                "aa000003",
                None,
                type="message",
                message={"role": "user", "content": "plain string", "timestamp": 0},
            ),
        ]
        trace = ingest_pi_session(entries)
        assert trace.steps[0].action.payload["text"] == "plain string"

    def test_unknown_entry_type_skipped_not_fatal(self) -> None:
        """Adapter-level behaviour only: unknown types produce no step (the
        health canary owns surfacing their share -- see PI_HEALTH_DECLARATION
        tests in the conformance suite)."""
        entries = [
            _header(),
            _entry("aa000004", None, type="totally-new-entry-type", data={"x": 1}),
            _entry(
                "aa000005",
                "aa000004",
                type="message",
                message={"role": "user", "content": "still here", "timestamp": 0},
            ),
        ]
        trace = ingest_pi_session(entries)
        assert [s.step_id for s in trace.steps] == ["aa000005"]

    def test_custom_entry_maps_to_state_transition_with_custom_type(self) -> None:
        entries = [
            _header(),
            _entry(
                "aa000006",
                None,
                type="custom",
                customType="some-extension",
                data={"k": "v"},
            ),
            _entry(
                "aa000007",
                "aa000006",
                type="message",
                message={"role": "user", "content": "hi", "timestamp": 0},
            ),
        ]
        trace = ingest_pi_session(entries)
        custom = trace.steps[0]
        assert custom.action.type == ActionType.STATE_TRANSITION
        assert custom.action.payload["kind"] == "custom"
        assert custom.action.payload["custom_type"] == "some-extension"


# ---------------------------------------------------------------------------
# Redaction pass-through
# ---------------------------------------------------------------------------


class TestRedaction:
    def test_strip_payloads_redacts_call_input_and_result(self) -> None:
        adapter = PiTraceAdapter(strip_payloads=True)
        trace = adapter.ingest(_load("session-rich.jsonl"))
        call = next(s for s in trace.steps if s.step_id == "call_mock_ls")
        assert call.action.payload["input"] == {
            "redacted": True,
            "size": len(str({"command": "ls -1"})),
        }
        # Structural fields survive.
        assert call.action.payload["name"] == "bash"
        result = next(s for s in trace.steps if s.step_id == "7269abbc")
        assert result.action.payload["tool_result"]["redacted"] is True
        assert result.action.payload["tool_name"] == "bash"

    def test_no_strip_leaves_payloads_intact(self) -> None:
        adapter = PiTraceAdapter()
        trace = adapter.ingest(_load("session-rich.jsonl"))
        call = next(s for s in trace.steps if s.step_id == "call_mock_ls")
        assert call.action.payload["input"] == {"command": "ls -1"}
