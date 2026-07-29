# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for P3 (subagent ingestion) of the cc-adapter-integrity
work: docs/proposals/session-postmortem-reporting.md Phase 6, "Subagent
traces (the data already exists)".

P1 quantified the blind spot: 648 Edits / 226 Writes performed by
delegates are invisible to the parent trace because the adapter only
marks `Task`/`Agent` calls as `delegation_unobserved` rather than
resolving them. This module specifies the resolution, per the RESOLVED
design (implemented to, not re-opened):

- D4 — delegate steps FLATTEN into the PARENT trace (no separate Trace).
  Each delegate step's `parent_step_id` is the parent's `Task` step id, and
  `metadata["agent_id"]` is the delegate id.
- D5 — a delegate step's `declared_intent` comes from the delegate's OWN
  brief (`meta.json.description` + the parent `Task` tool_use
  `input.prompt`), never the parent's standing plan.
- D6 — out of scope here: blending delegate drift into the parent's
  trace-level score is a P4 findings concern. This module only covers
  ingest + attribution.
- D7 — on-disk layout: the parent transcript `<uuid>.jsonl` is a SIBLING of
  the `<uuid>/` directory; subagent transcripts live at
  `<uuid>/subagents/agent-<agentId>.jsonl` with a sidecar
  `agent-<agentId>.meta.json`. The join key is `meta.json.toolUseId` ==
  the parent transcript's `Task` tool_use block's own `id`.

PROPOSED ENTRY-POINT SURFACE (flagged for review, not silently decided --
see the "key design question" in the P3 kickoff):

    def load_subagent_transcripts(session_dir: Path) -> list[SubagentTranscript]
    def ingest_claude_code_session(
        events, ..., subagents: list[SubagentTranscript] | None = None
    ) -> Trace

`load_subagent_transcripts` is a new, pure-I/O discovery function living
alongside the existing `load_plan_tasks(session_dir: Path) -> list[dict]`
in `adapters/claude_code.py` -- same shape, same rationale (best-effort
forensic tooling reading a third-party harness's own files, skip rather
than raise on anything malformed). `ingest_claude_code_session` grows ONE
new optional keyword-only parameter, `subagents`, mirroring the existing
`plan_tasks` parameter: the caller loads subagent data (via
`load_subagent_transcripts`) and passes it in already-parsed, so
`ingest_claude_code_session` itself stays pure (no I/O, no filesystem
access) exactly as it is today -- every existing test in
tests/integration/test_cc_adapter_modern.py and this module's own
ingestion tests below builds `events` purely in-memory.

REJECTED ALTERNATIVE: a new `ingest_claude_code_session_dir(path)` entry
point that does its own file I/O (reading the parent transcript from disk
AND discovering the sibling `subagents/` dir) was considered and rejected:
it would (a) duplicate the file-reading logic `cli.py` already owns for
the main transcript, (b) force every unit test exercising subagent
ingestion to write the *parent* transcript to disk too, instead of just
building it in-memory as every existing adapter test already does, and
(c) break the one existing precedent in this exact module for exactly
this kind of problem (`load_plan_tasks` + `plan_tasks=`), for no benefit:
`subagents_dir` is always a plain sibling of the parent transcript's own
path (`transcript_path.parent / transcript_path.stem`), so `cli.py` can
compute it in one line and pass the loaded result in, exactly as it
already does for `--plan-tasks`.

This choice is proposed, not committed -- GREEN-phase review may override
it. Every fixture below is built synthetically under `tmp_path`; nothing
here reads the real `~/.claude` corpus.

Every test in this module is expected to FAIL right now with an
`ImportError` (`SubagentTranscript`/`load_subagent_transcripts` do not
exist yet) or a `TypeError` (`subagents=` is not a recognised keyword
argument of `ingest_claude_code_session` yet). This is the RED phase;
production code lands in the next (GREEN) phase, after human review.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from auditk.adapters.claude_code import (
    SubagentTranscript,
    ingest_claude_code_session,
    load_subagent_transcripts,
)
from auditk.analysis.findings import FindingsConfig, find_writes_outside_roots
from auditk.schema import ActionType, Trace

PARENT_SESSION_ID = "parent-session-0001"
# Deliberately different from PARENT_SESSION_ID: a subagent transcript
# carries its OWN `sessionId` in every record, distinct from the parent's.
# Delegate steps must end up with `trace_id == <parent's trace_id>` (the
# flattened Trace they belong to), never this value -- a naive
# implementation that reuses the subagent record's own `sessionId` for
# `trace_id` would leak it through, which these fixtures are built to catch.
SUBAGENT_INTERNAL_SESSION_ID = "subagent-internal-session-9999"


# --- fixture builders ----------------------------------------------------
# Plain dicts mirroring parsed Claude Code JSONL records (same approach as
# tests/integration/test_cc_adapter_modern.py and tests/unit/
# test_corpus_stats.py) -- no real transcript content anywhere here.


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + ("\n" if records else ""))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _user_event(uuid_: str, text: str, *, parent: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "user",
        "uuid": uuid_,
        "sessionId": PARENT_SESSION_ID,
        "message": {"role": "user", "content": text},
    }
    if parent is not None:
        event["parentUuid"] = parent
    return event


def _task_call_event(
    uuid_: str,
    *,
    parent: str,
    task_id: str,
    prompt: str,
    task_input_description: str,
) -> dict[str, Any]:
    """A parent assistant turn making one `Task` tool_use call.

    Real Claude Code `Task` tool input carries both `description` (a short
    3-5 word label) and `prompt` (the actual brief). Per D5, a delegate's
    declared_intent must use THIS `prompt`, but must NOT use this
    `description` -- that field is a parent-side label, distinct from
    `meta.json`'s own (delegate-side) `description`. Fixtures below give
    these two "description" fields deliberately different text so a test
    can tell which one leaked through.
    """
    return {
        "type": "assistant",
        "uuid": uuid_,
        "parentUuid": parent,
        "sessionId": PARENT_SESSION_ID,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": task_id,
                    "name": "Task",
                    "input": {"prompt": prompt, "description": task_input_description},
                }
            ],
        },
    }


def _task_result_event(
    uuid_: str, *, parent: str, task_id: str, content: str = "Subagent finished."
) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid_,
        "parentUuid": parent,
        "sessionId": PARENT_SESSION_ID,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": task_id, "content": content}],
        },
    }


def _todowrite_event(uuid_: str, *, parent: str, standing_plan_text: str) -> dict[str, Any]:
    """A parent TodoWrite call seeding the PARENT's own standing plan --
    used to prove a delegate's declared_intent is never contaminated by it."""
    return {
        "type": "assistant",
        "uuid": uuid_,
        "parentUuid": parent,
        "sessionId": PARENT_SESSION_ID,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": f"{uuid_}-tu",
                    "name": "TodoWrite",
                    "input": {"todos": [{"content": standing_plan_text, "status": "in_progress"}]},
                }
            ],
        },
    }


def _subagent_tool_call_event(
    uuid_: str,
    *,
    agent_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
    parent: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "assistant",
        "uuid": uuid_,
        "agentId": agent_id,
        "isSidechain": True,
        "sessionId": SUBAGENT_INTERNAL_SESSION_ID,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": f"{uuid_}-tu", "name": tool_name, "input": tool_input}
            ],
        },
    }
    if parent is not None:
        event["parentUuid"] = parent
    return event


def _subagent_tool_result_event(
    uuid_: str,
    *,
    agent_id: str,
    tool_use_id: str,
    content: str = "ok",
    parent: str | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "type": "user",
        "uuid": uuid_,
        "agentId": agent_id,
        "isSidechain": True,
        "sessionId": SUBAGENT_INTERNAL_SESSION_ID,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
        },
    }
    if parent is not None:
        event["parentUuid"] = parent
    return event


def _subagent_user_prompt_event(uuid_: str, *, agent_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid_,
        "agentId": agent_id,
        "isSidechain": True,
        "sessionId": SUBAGENT_INTERNAL_SESSION_ID,
        "message": {"role": "user", "content": text},
    }


def _write_subagent_transcript(
    session_dir: Path,
    agent_id: str,
    *,
    task_id: str,
    description: str,
    events: list[dict[str, Any]],
    agent_type: str = "general-purpose",
    spawn_depth: int = 1,
) -> None:
    """Write `<session_dir>/subagents/agent-<agent_id>.jsonl` + its
    `.meta.json` sidecar, per D7's verified on-disk layout."""
    subagents_dir = session_dir / "subagents"
    _write_jsonl(subagents_dir / f"agent-{agent_id}.jsonl", events)
    _write_json(
        subagents_dir / f"agent-{agent_id}.meta.json",
        {
            "agentType": agent_type,
            "description": description,
            "toolUseId": task_id,
            "spawnDepth": spawn_depth,
        },
    )


def _build_single_delegation_trace(tmp_path: Path) -> tuple[Trace, str]:
    """One parent Task call (id `tu-task-A`), delegated to agent `AAA`,
    whose subagent transcript makes a Read then an Edit call (each with its
    own tool_result). Returns `(trace, task_id)`."""
    task_id = "tu-task-A"
    events = [
        _user_event("u1", "Please fix the flaky auth test."),
        _task_call_event(
            "a1",
            parent="u1",
            task_id=task_id,
            prompt="Investigate and fix tests/test_auth.py::test_login_flow, which is flaky.",
            task_input_description="fix flaky test",
        ),
        _task_result_event("u2", parent="a1", task_id=task_id),
    ]
    session_dir = tmp_path / "session-dir"
    subagent_events = [
        _subagent_user_prompt_event("su1", agent_id="AAA", text="Investigate and fix the test."),
        _subagent_tool_call_event(
            "sa1",
            agent_id="AAA",
            tool_name="Read",
            tool_input={"file_path": "/work/tests/test_auth.py"},
            parent="su1",
        ),
        _subagent_tool_result_event("su2", agent_id="AAA", tool_use_id="sa1-tu", parent="sa1"),
        _subagent_tool_call_event(
            "sa2",
            agent_id="AAA",
            tool_name="Edit",
            tool_input={
                "file_path": "/work/tests/test_auth.py",
                "old_string": "assert token.is_valid()",
                "new_string": "assert token.is_valid(clock=frozen_clock)",
            },
            parent="su2",
        ),
        _subagent_tool_result_event("su3", agent_id="AAA", tool_use_id="sa2-tu", parent="sa2"),
    ]
    _write_subagent_transcript(
        session_dir,
        "AAA",
        task_id=task_id,
        description="Fix flaky auth test.",
        events=subagent_events,
    )
    subagents = load_subagent_transcripts(session_dir)
    trace = ingest_claude_code_session(events, subagents=subagents)
    return trace, task_id


# --- SubagentTranscript shape ---------------------------------------------


class TestSubagentTranscriptShape:
    def test_has_agent_id_meta_and_events_fields(self) -> None:
        transcript = SubagentTranscript(
            agent_id="AAA", meta={"toolUseId": "tu1"}, events=[{"type": "user"}]
        )
        assert transcript.agent_id == "AAA"
        assert transcript.meta == {"toolUseId": "tu1"}
        assert transcript.events == [{"type": "user"}]


# --- load_subagent_transcripts (discovery) --------------------------------


class TestLoadSubagentTranscripts:
    def test_discovers_transcript_with_meta_sidecar(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-dir"
        events = [_subagent_user_prompt_event("su1", agent_id="AAA", text="do the thing")]
        _write_subagent_transcript(
            session_dir,
            "AAA",
            task_id="tu-task-A",
            description="Fix the flaky auth test.",
            events=events,
        )

        transcripts = load_subagent_transcripts(session_dir)

        assert len(transcripts) == 1
        transcript = transcripts[0]
        assert transcript.agent_id == "AAA"
        assert transcript.meta == {
            "agentType": "general-purpose",
            "description": "Fix the flaky auth test.",
            "toolUseId": "tu-task-A",
            "spawnDepth": 1,
        }
        assert transcript.events == events

    def test_returns_empty_list_when_no_subagents_dir(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-dir-empty"
        session_dir.mkdir()
        assert load_subagent_transcripts(session_dir) == []

    def test_returns_empty_list_when_session_dir_itself_missing(self, tmp_path: Path) -> None:
        assert load_subagent_transcripts(tmp_path / "does-not-exist") == []

    def test_skips_transcript_with_no_meta_sidecar(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-dir"
        subagents_dir = session_dir / "subagents"
        _write_jsonl(
            subagents_dir / "agent-ORPHAN.jsonl",
            [_subagent_user_prompt_event("su1", agent_id="ORPHAN", text="x")],
        )
        # Deliberately no agent-ORPHAN.meta.json written.

        assert load_subagent_transcripts(session_dir) == []

    def test_skips_malformed_meta_json(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-dir"
        subagents_dir = session_dir / "subagents"
        _write_jsonl(
            subagents_dir / "agent-BAD.jsonl",
            [_subagent_user_prompt_event("su1", agent_id="BAD", text="x")],
        )
        (subagents_dir / "agent-BAD.meta.json").write_text("not json")

        assert load_subagent_transcripts(session_dir) == []

    def test_discovers_multiple_transcripts(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-dir"
        _write_subagent_transcript(
            session_dir, "AAA", task_id="tu-task-A", description="A", events=[]
        )
        _write_subagent_transcript(
            session_dir, "BBB", task_id="tu-task-B", description="B", events=[]
        )

        transcripts = load_subagent_transcripts(session_dir)

        assert {t.agent_id for t in transcripts} == {"AAA", "BBB"}


# --- RED item 1: delegate steps flatten into the parent trace ------------


class TestSubagentStepsFlattenIntoParentTrace:
    def test_delegate_tool_call_steps_are_present(self, tmp_path: Path) -> None:
        trace, _task_id = _build_single_delegation_trace(tmp_path)

        names = {
            s.action.payload.get("name")
            for s in trace.steps
            if s.action.type == ActionType.TOOL_CALL
        }
        assert {"Read", "Edit"} <= names

    def test_delegate_steps_parent_step_id_points_at_the_task_step(self, tmp_path: Path) -> None:
        trace, _task_id = _build_single_delegation_trace(tmp_path)

        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        delegate_calls = [
            s
            for s in trace.steps
            if s.action.type == ActionType.TOOL_CALL
            and s.action.payload.get("name") in ("Read", "Edit")
        ]
        assert delegate_calls
        assert all(s.parent_step_id == task_step.step_id for s in delegate_calls)

    def test_delegate_steps_carry_agent_id_metadata(self, tmp_path: Path) -> None:
        trace, _task_id = _build_single_delegation_trace(tmp_path)

        delegate_calls = [
            s
            for s in trace.steps
            if s.action.type == ActionType.TOOL_CALL
            and s.action.payload.get("name") in ("Read", "Edit")
        ]
        assert delegate_calls
        assert all(s.metadata.get("agent_id") == "AAA" for s in delegate_calls)

    def test_delegate_steps_trace_id_matches_the_parent_trace(self, tmp_path: Path) -> None:
        trace, _task_id = _build_single_delegation_trace(tmp_path)

        delegate_calls = [
            s
            for s in trace.steps
            if s.action.type == ActionType.TOOL_CALL
            and s.action.payload.get("name") in ("Read", "Edit")
        ]
        assert delegate_calls
        assert all(s.trace_id == trace.trace_id for s in delegate_calls)
        # Guards against a naive implementation leaking the subagent
        # record's OWN internal sessionId through as trace_id instead.
        assert trace.trace_id != SUBAGENT_INTERNAL_SESSION_ID

    def test_delegate_tool_result_steps_are_also_attributed(self, tmp_path: Path) -> None:
        # D4: "each delegate step" -- not just tool_call steps. The
        # subagent's own tool_result (env_effect) steps must carry the same
        # parent_step_id/agent_id attribution.
        trace, _task_id = _build_single_delegation_trace(tmp_path)

        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        delegate_results = [
            s
            for s in trace.steps
            if s.action.type == ActionType.ENV_EFFECT and s.metadata.get("agent_id") == "AAA"
        ]
        assert delegate_results
        assert all(s.parent_step_id == task_step.step_id for s in delegate_results)

    def test_task_step_no_longer_marked_delegation_unobserved_when_matched(
        self, tmp_path: Path
    ) -> None:
        trace, _task_id = _build_single_delegation_trace(tmp_path)

        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        assert "delegation_unobserved" not in task_step.metadata


# --- RED item 2: declared_intent from the delegate's OWN brief -----------


class TestDelegateDeclaredIntentFromOwnBrief:
    def test_declared_intent_contains_meta_description_and_parent_prompt(
        self, tmp_path: Path
    ) -> None:
        task_id = "tu-task-B"
        prompt_text = "Investigate and fix tests/test_auth.py::test_login_flow, which is flaky."
        meta_description = "DELEGATE-BRIEF: fix flaky auth test"
        parent_task_input_description = "PARENT-SIDE-LABEL: should not leak"
        parent_standing_plan_text = "PARENT PLAN: totally unrelated top-level task"

        events = [
            _user_event("u1", "Please do several things."),
            _todowrite_event("a0", parent="u1", standing_plan_text=parent_standing_plan_text),
            _task_call_event(
                "a1",
                parent="a0",
                task_id=task_id,
                prompt=prompt_text,
                task_input_description=parent_task_input_description,
            ),
            _task_result_event("u2", parent="a1", task_id=task_id),
        ]
        session_dir = tmp_path / "session-dir"
        subagent_events = [
            # Tool-only message, no narration/thinking -- per the adapter's
            # existing precedence (narration/thinking win over the standing
            # anchor; see claude_code.py's module docstring), this is what
            # forces the seeded brief itself to surface as declared_intent.
            _subagent_tool_call_event(
                "sa1",
                agent_id="AAA",
                tool_name="Read",
                tool_input={"file_path": "/work/tests/test_auth.py"},
            ),
        ]
        _write_subagent_transcript(
            session_dir,
            "AAA",
            task_id=task_id,
            description=meta_description,
            events=subagent_events,
        )
        subagents = load_subagent_transcripts(session_dir)

        trace = ingest_claude_code_session(events, subagents=subagents)

        delegate_step = next(
            s
            for s in trace.steps
            if s.action.type == ActionType.TOOL_CALL and s.action.payload.get("name") == "Read"
        )
        assert delegate_step.declared_intent is not None
        assert meta_description in delegate_step.declared_intent
        assert prompt_text in delegate_step.declared_intent
        assert parent_task_input_description not in delegate_step.declared_intent
        assert parent_standing_plan_text not in delegate_step.declared_intent


# --- RED item 3: join is by toolUseId, not by order/position -------------


class TestJoinByToolUseId:
    def test_two_delegations_attach_to_the_correct_task(self, tmp_path: Path) -> None:
        task_a_id, task_b_id = "tu-task-A", "tu-task-B"
        events = [
            _user_event("u1", "Do two independent things via subagents."),
            _task_call_event(
                "a1",
                parent="u1",
                task_id=task_a_id,
                prompt="Task A prompt",
                task_input_description="A",
            ),
            _task_result_event("u2", parent="a1", task_id=task_a_id),
            _task_call_event(
                "a2",
                parent="u2",
                task_id=task_b_id,
                prompt="Task B prompt",
                task_input_description="B",
            ),
            _task_result_event("u3", parent="a2", task_id=task_b_id),
        ]
        session_dir = tmp_path / "session-dir"
        _write_subagent_transcript(
            session_dir,
            "AAA",
            task_id=task_a_id,
            description="brief A",
            events=[
                _subagent_tool_call_event(
                    "sa1", agent_id="AAA", tool_name="Read", tool_input={"file_path": "/work/a.py"}
                )
            ],
        )
        _write_subagent_transcript(
            session_dir,
            "BBB",
            task_id=task_b_id,
            description="brief B",
            events=[
                _subagent_tool_call_event(
                    "sb1",
                    agent_id="BBB",
                    tool_name="Write",
                    tool_input={"file_path": "/work/b.py", "content": "x"},
                )
            ],
        )
        subagents = load_subagent_transcripts(session_dir)

        trace = ingest_claude_code_session(events, subagents=subagents)

        task_a_step = next(
            s
            for s in trace.steps
            if s.action.payload.get("name") == "Task"
            and s.action.payload.get("input", {}).get("prompt") == "Task A prompt"
        )
        task_b_step = next(
            s
            for s in trace.steps
            if s.action.payload.get("name") == "Task"
            and s.action.payload.get("input", {}).get("prompt") == "Task B prompt"
        )
        read_step = next(s for s in trace.steps if s.action.payload.get("name") == "Read")
        write_step = next(s for s in trace.steps if s.action.payload.get("name") == "Write")

        assert read_step.parent_step_id == task_a_step.step_id
        assert read_step.parent_step_id != task_b_step.step_id
        assert read_step.metadata.get("agent_id") == "AAA"

        assert write_step.parent_step_id == task_b_step.step_id
        assert write_step.parent_step_id != task_a_step.step_id
        assert write_step.metadata.get("agent_id") == "BBB"


# --- RED item 4: graceful retention when no transcript matches -----------


class TestGracefulRetentionWhenNoMatchingTranscript:
    @staticmethod
    def _lone_task_events(task_id: str = "tu-task-lonely") -> list[dict[str, Any]]:
        return [
            _user_event("u1", "Do one thing via a subagent."),
            _task_call_event(
                "a1",
                parent="u1",
                task_id=task_id,
                prompt="do the thing",
                task_input_description="d",
            ),
            _task_result_event("u2", parent="a1", task_id=task_id),
        ]

    def test_no_subagents_argument_at_all_retains_marker(self) -> None:
        trace = ingest_claude_code_session(self._lone_task_events())
        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        assert task_step.metadata.get("delegation_unobserved") is True

    def test_explicit_none_subagents_retains_marker(self) -> None:
        trace = ingest_claude_code_session(self._lone_task_events(), subagents=None)
        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        assert task_step.metadata.get("delegation_unobserved") is True

    def test_empty_subagents_list_retains_marker(self) -> None:
        trace = ingest_claude_code_session(self._lone_task_events(), subagents=[])
        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        assert task_step.metadata.get("delegation_unobserved") is True

    def test_non_matching_tool_use_id_retains_marker_and_contributes_no_steps(self) -> None:
        task_id = "tu-task-A"
        events = self._lone_task_events(task_id=task_id)
        orphan = SubagentTranscript(
            agent_id="ZZZ",
            meta={
                "agentType": "general-purpose",
                "description": "unrelated",
                "toolUseId": "tu-task-DIFFERENT",
                "spawnDepth": 1,
            },
            events=[
                _subagent_tool_call_event(
                    "sz1", agent_id="ZZZ", tool_name="Bash", tool_input={"command": "echo hi"}
                )
            ],
        )

        trace = ingest_claude_code_session(events, subagents=[orphan])

        task_step = next(s for s in trace.steps if s.action.payload.get("name") == "Task")
        assert task_step.metadata.get("delegation_unobserved") is True
        assert not any(s.metadata.get("agent_id") == "ZZZ" for s in trace.steps)


# --- RED item 5 / ACCEPTANCE GATE: delegate steps visible to rule-checking


class TestAcceptanceGateDelegateVisibleToFindings:
    """The proposal's Phase 6 gate: 'a session containing a delegation
    produces a trace in which the subagent's own tool calls are present,
    attributed, and rule-checked.' Concretely: an existing findings-engine
    predicate (`find_writes_outside_roots`) must see and flag a delegate's
    Edit call, exactly as it already does for a parent-level Edit call --
    proving delegate steps are not just present in `trace.steps` but
    actually reachable by downstream analysis, not merely inert data."""

    def test_delegate_edit_outside_roots_is_flagged_by_findings(self, tmp_path: Path) -> None:
        task_id = "tu-task-A"
        events = [
            _user_event("u1", "Fix something via a subagent."),
            _task_call_event(
                "a1", parent="u1", task_id=task_id, prompt="fix it", task_input_description="d"
            ),
            _task_result_event("u2", parent="a1", task_id=task_id),
        ]
        session_dir = tmp_path / "session-dir"
        out_of_scope_path = "/outside-scope/evil.py"
        _write_subagent_transcript(
            session_dir,
            "AAA",
            task_id=task_id,
            description="fix it",
            events=[
                _subagent_tool_call_event(
                    "sa1",
                    agent_id="AAA",
                    tool_name="Edit",
                    tool_input={
                        "file_path": out_of_scope_path,
                        "old_string": "a",
                        "new_string": "b",
                    },
                )
            ],
        )
        subagents = load_subagent_transcripts(session_dir)

        trace = ingest_claude_code_session(events, subagents=subagents)
        config = FindingsConfig(roots=["/work/allowed-project"])

        findings = find_writes_outside_roots(trace, config)

        matching = next(
            (f for f in findings if f.evidence.get("file_path") == out_of_scope_path), None
        )
        assert matching is not None, f"no scope-escape finding for {out_of_scope_path}: {findings}"

        delegate_edit_step = next(
            s
            for s in trace.steps
            if s.action.type == ActionType.TOOL_CALL and s.action.payload.get("name") == "Edit"
        )
        assert delegate_edit_step.step_id in matching.step_ids
