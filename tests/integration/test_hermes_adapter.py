# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the Hermes-agent session adapter.

Fixtures under tests/fixtures/hermes/ are 100% synthetic (invented session
ids, invented paths, invented content) -- shaped to mirror the real Hermes
``messages``-table row format (confirmed by reading ``hermes_state.py`` and
``tools/delegate_tool.py``/``tools/todo_tool.py`` in the sibling
``hermes-agent`` checkout, see src/auditk/adapters/hermes.py's module
docstring), never real ``~/.hermes`` content.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from auditk.adapters.hermes import HermesTraceAdapter, ingest_hermes_session
from auditk.schema import ActionType, Actor, Trace

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "hermes"
_SPEC_PATH = Path(os.environ.get("AUDITK_SPEC_PATH", "../auditk-spec"))
_TRACE_SCHEMA_PATH = _SPEC_PATH / "spec" / "v0.1" / "trace.schema.json"


def _load(name: str) -> list[dict[str, Any]]:
    return json.loads((_FIXTURES / name).read_text())  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def trace_schema() -> dict[str, Any]:
    if not _TRACE_SCHEMA_PATH.exists():
        pytest.skip(
            f"auditk-spec not found at {_SPEC_PATH}; "
            "set AUDITK_SPEC_PATH to run schema-validation tests."
        )
    return json.loads(_TRACE_SCHEMA_PATH.read_text())  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Minimal valid session
# ---------------------------------------------------------------------------


class TestMinimalSession:
    def test_returns_trace(self) -> None:
        trace = ingest_hermes_session(_load("session-minimal.json"))
        assert isinstance(trace, Trace)

    def test_source_adapter(self) -> None:
        trace = ingest_hermes_session(_load("session-minimal.json"))
        assert trace.source_adapter == "hermes"

    def test_trace_id_from_session_id(self) -> None:
        trace = ingest_hermes_session(_load("session-minimal.json"))
        assert trace.trace_id == "hs-min-0001"
        assert trace.agent_config_ref == "hermes:hs-min-0001"

    def test_step_shape_user_tool_call_tool_result(self) -> None:
        trace = ingest_hermes_session(_load("session-minimal.json"))
        assert len(trace.steps) == 3
        user_step, call_step, result_step = trace.steps
        assert user_step.actor == Actor.USER
        assert user_step.action.type == ActionType.UTTERANCE
        assert call_step.actor == Actor.AGENT
        assert call_step.action.type == ActionType.TOOL_CALL
        assert call_step.action.payload["name"] == "terminal"
        assert call_step.action.payload["input"] == {"command": "ls sandbox/"}
        assert result_step.actor == Actor.TOOL
        assert result_step.action.type == ActionType.ENV_EFFECT
        assert result_step.action.payload["tool_result"] == "file1\nfile2"

    def test_validates_against_schema(self, trace_schema: dict[str, Any]) -> None:
        trace = ingest_hermes_session(_load("session-minimal.json"))
        jsonschema.validate(instance=trace.model_dump(mode="json"), schema=trace_schema)


# ---------------------------------------------------------------------------
# Malformed session -- refuse-don't-raise
# ---------------------------------------------------------------------------


class TestMalformedSession:
    def test_processes_best_effort_not_raw_exception(self) -> None:
        """A row with no `role` and a row with an unparseable `tool_calls`
        string / non-str `content` must not leak a bare KeyError/TypeError.
        """
        trace = ingest_hermes_session(_load("session-malformed.json"))
        assert isinstance(trace, Trace)
        # Only the second row (role=assistant) is substantive; the first
        # (no role at all) contributes nothing.
        assert len(trace.steps) == 1
        assert trace.steps[0].action.type == ActionType.UTTERANCE


# ---------------------------------------------------------------------------
# Pairing + delegation
# ---------------------------------------------------------------------------


class TestPairingAndDelegationSession:
    def test_tool_call_and_result_share_no_synthesised_id(self) -> None:
        """Sanity: the real ids in the fixture actually match end to end."""
        events = _load("session-pairing-delegation.json")
        assistant_build = events[1]
        tool_build = events[2]
        tool_calls = json.loads(assistant_build["tool_calls"])
        assert tool_calls[0]["id"] == tool_build["tool_call_id"] == "call-build"

    def test_delegate_task_call_marked_unobserved(self) -> None:
        trace = ingest_hermes_session(_load("session-pairing-delegation.json"))
        delegate_step = next(
            s for s in trace.steps if s.action.payload.get("name") == "delegate_task"
        )
        assert delegate_step.metadata.get("delegation_unobserved") is True

    def test_non_delegation_tool_call_not_marked_unobserved(self) -> None:
        trace = ingest_hermes_session(_load("session-pairing-delegation.json"))
        build_step = next(s for s in trace.steps if s.action.payload.get("name") == "terminal")
        assert "delegation_unobserved" not in build_step.metadata

    def test_delegate_result_step_carries_summary_content(self) -> None:
        trace = ingest_hermes_session(_load("session-pairing-delegation.json"))
        result_step = trace.steps[-1]
        assert result_step.action.type == ActionType.ENV_EFFECT
        assert "Surveyed release notes" in result_step.action.payload["tool_result"]

    def test_narration_feeds_declared_intent_of_first_tool_step_only(self) -> None:
        trace = ingest_hermes_session(_load("session-pairing-delegation.json"))
        build_step = next(s for s in trace.steps if s.action.payload.get("name") == "terminal")
        assert build_step.declared_intent == "Checking the build first."


# ---------------------------------------------------------------------------
# Unknown record type (health canary fodder, not a step-building concern)
# ---------------------------------------------------------------------------


class TestUnknownRecordTypeSession:
    def test_unknown_role_rows_contribute_no_steps(self) -> None:
        """`billing_event` rows aren't in the substantive-role set, so they
        never become Steps -- but they're still visible to a health-canary
        walk over the raw records (see test_adapter_health_hermes style
        assertions in the conformance suite)."""
        trace = ingest_hermes_session(_load("session-unknown-record-type.json"))
        assert len(trace.steps) == 1
        assert trace.steps[0].actor == Actor.USER


# ---------------------------------------------------------------------------
# Rewind (active=0) exclusion -- built inline, no fixture file needed
# ---------------------------------------------------------------------------


def _row(role: str, **kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"session_id": "hs-rewind-0001", "role": role, "timestamp": 1793000400.0}
    base.update(kwargs)
    return base


class TestRewindExclusion:
    def test_active_zero_row_excluded_from_steps(self) -> None:
        events = [
            _row("user", content="first attempt", id=401),
            _row("user", content="rewound and retried", id=402, active=0),
            _row("user", content="second attempt", id=403),
        ]
        trace = ingest_hermes_session(events)
        texts = [s.action.payload["text"] for s in trace.steps]
        assert "rewound and retried" not in texts
        assert texts == ["first attempt", "second attempt"]

    def test_missing_active_column_treated_as_active(self) -> None:
        events = [_row("user", content="no active column at all", id=404)]
        trace = ingest_hermes_session(events)
        assert len(trace.steps) == 1


# ---------------------------------------------------------------------------
# Todo-anchor declared-intent precedence -- built inline
# ---------------------------------------------------------------------------


class TestTodoAnchorDeclaredIntent:
    def test_todo_call_seeds_standing_plan_for_later_tool_call(self) -> None:
        todo_args = json.dumps(
            {
                "todos": [
                    {"id": "t1", "content": "Add CSV export endpoint", "status": "pending"},
                    {"id": "t2", "content": "Fix flaky auth test", "status": "pending"},
                ],
                "merge": False,
            }
        )
        events = [
            _row("user", content="please do both things", id=501),
            _row(
                "assistant",
                id=502,
                content=None,
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-todo",
                            "call_id": "call-todo",
                            "type": "function",
                            "function": {"name": "todo", "arguments": todo_args},
                        }
                    ]
                ),
            ),
            _row(
                "assistant",
                id=503,
                content=None,
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-2",
                            "call_id": "call-2",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "csv.py"}),
                            },
                        }
                    ]
                ),
            ),
        ]
        trace = ingest_hermes_session(events)
        write_step = next(s for s in trace.steps if s.action.payload.get("name") == "write_file")
        assert write_step.declared_intent == "Add CSV export endpoint\nFix flaky auth test"

    def test_todo_merge_true_updates_by_id_without_dropping_others(self) -> None:
        first_args = json.dumps(
            {
                "todos": [
                    {"id": "t1", "content": "Task one", "status": "pending"},
                    {"id": "t2", "content": "Task two", "status": "pending"},
                ],
                "merge": False,
            }
        )
        second_args = json.dumps({"todos": [{"id": "t1", "status": "completed"}], "merge": True})
        events = [
            _row("user", content="do two things", id=601),
            _row(
                "assistant",
                id=602,
                content=None,
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-todo-1",
                            "call_id": "call-todo-1",
                            "type": "function",
                            "function": {"name": "todo", "arguments": first_args},
                        }
                    ]
                ),
            ),
            _row(
                "assistant",
                id=603,
                content=None,
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-todo-2",
                            "call_id": "call-todo-2",
                            "type": "function",
                            "function": {"name": "todo", "arguments": second_args},
                        }
                    ]
                ),
            ),
            _row(
                "assistant",
                id=604,
                content=None,
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-3",
                            "call_id": "call-3",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": json.dumps({"path": "x"}),
                            },
                        }
                    ]
                ),
            ),
        ]
        trace = ingest_hermes_session(events)
        write_step = next(s for s in trace.steps if s.action.payload.get("name") == "write_file")
        # t1 is now completed (no longer active); t2 is still pending.
        assert write_step.declared_intent == "Task two"


# ---------------------------------------------------------------------------
# Adapter class + redaction
# ---------------------------------------------------------------------------


class TestHermesTraceAdapter:
    def test_adapter_satisfies_protocol(self) -> None:
        adapter = HermesTraceAdapter()
        trace = adapter.ingest(_load("session-minimal.json"))
        assert isinstance(trace, Trace)
        assert trace.source_adapter == "hermes"

    def test_empty_session_raises(self) -> None:
        adapter = HermesTraceAdapter()
        with pytest.raises(ValueError, match="empty"):
            adapter.ingest([])

    def test_all_session_meta_rows_raises(self) -> None:
        adapter = HermesTraceAdapter()
        with pytest.raises(ValueError):
            adapter.ingest([_row("session_meta", id=701)])

    def test_strip_payloads_redacts_tool_call_and_result(self) -> None:
        adapter = HermesTraceAdapter(strip_payloads=True)
        trace = adapter.ingest(_load("session-minimal.json"))
        call_step = next(s for s in trace.steps if s.action.type == ActionType.TOOL_CALL)
        assert call_step.action.payload["input"] == {
            "redacted": True,
            "size": len(str({"command": "ls sandbox/"})),
        }
        # Structural fields survive.
        assert call_step.action.payload["name"] == "terminal"
        result_step = next(s for s in trace.steps if s.action.type == ActionType.ENV_EFFECT)
        assert result_step.action.payload["tool_result"] == {
            "redacted": True,
            "size": len("file1\nfile2"),
        }

    def test_strip_payloads_leaves_narration_untouched(self) -> None:
        adapter = HermesTraceAdapter(strip_payloads=True)
        trace = adapter.ingest(_load("session-pairing-delegation.json"))
        utterance_step = next(s for s in trace.steps if s.action.type == ActionType.UTTERANCE)
        assert utterance_step.action.payload["text"]
