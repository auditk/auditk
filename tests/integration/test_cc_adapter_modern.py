# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the modern Claude Code harness (TaskCreate/TaskUpdate,
thinking blocks, tool_result is_error, and Trace.metadata).

These tests encode the DESIRED post-fix behaviour of
``src/auditk/adapters/claude_code.py`` (see docs/proposals/session-postmortem-reporting.md
for context). The adapter currently only understands ``TodoWrite`` for the
standing-plan/declared_intent anchor, drops ``thinking`` blocks, ignores
tool_result ``is_error``, and never populates ``Trace.metadata``. Every test
in this file is therefore expected to FAIL against the current adapter; they
will start passing once Phase 1 (production code) lands.

Fixtures are synthetic (no real session data) — see
tests/fixtures/claude_code/session_modern_taskcreate.jsonl and
tests/fixtures/claude_code/plan_store_modern.json.
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from auditk.adapters.claude_code import ingest_claude_code_session, load_plan_tasks
from auditk.schema import Actor

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "claude_code"

TASK1_SUBJECT = "Add CSV export endpoint"
TASK2_SUBJECT = "Fix flaky auth test"
BOTH_TASKS_ACTIVE = f"{TASK1_SUBJECT}\n{TASK2_SUBJECT}"
ONLY_TASK1_ACTIVE = TASK1_SUBJECT
THINKING_TEXT = (
    "The user wants two things: a CSV export endpoint and a fix for the "
    "flaky auth test. I will track both as tasks before touching any code."
)


def _load(name: str) -> list[dict]:
    return [
        json.loads(line) for line in (_FIXTURES / name).read_text().splitlines() if line.strip()
    ]


def _load_plan_store(name: str) -> list[dict]:
    return json.loads((_FIXTURES / name).read_text())


def test_taskcreate_populates_intent_anchor_for_subsequent_tool_use() -> None:
    """A tool_use step after the TaskCreates should carry the active task
    subject(s) as declared_intent, not None.

    Fails today because ``_extract_todos``/``_update_standing_plan`` only
    recognise the ``TodoWrite`` tool name; ``TaskCreate`` is ignored entirely,
    so the standing plan never leaves its initial ``None`` state.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    # step index 5: assistant a2's Bash call, after both TaskCreates in a1.
    bash_step = trace.steps[5]
    assert bash_step.action.type.value == "tool_call"
    assert bash_step.action.payload["name"] == "Bash"
    assert bash_step.declared_intent == BOTH_TASKS_ACTIVE
    assert bash_step.declared_intent is not None


def test_taskupdate_narrows_intent_anchor_to_remaining_active_tasks() -> None:
    """After a TaskUpdate completes one task, later steps should reflect only
    the tasks still pending/in_progress.

    Fails today for the same reason as above: TaskUpdate is not recognised,
    so there is no standing plan to narrow in the first place.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    # step index 10: assistant a5's final Bash call, after the TaskUpdate
    # in a4 marks task 2 ("Fix flaky auth test") completed.
    final_bash = trace.steps[10]
    assert final_bash.action.type.value == "tool_call"
    assert final_bash.declared_intent == ONLY_TASK1_ACTIVE


def test_thinking_block_used_as_declared_intent() -> None:
    """An assistant `thinking` block should feed declared_intent for that
    message's step(s), the same way inline narration text does today.

    Fails today because ``_join_text`` only collects blocks of type=="text";
    thinking blocks are never read anywhere in the adapter, so neither step
    below can contain the thinking text.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    # a1 contains a thinking block followed by two TaskCreate tool_use blocks
    # (steps 1 and 2). Accept either placement since Phase 1 may attach the
    # thinking-derived intent to the first tool_use only, or to both.
    first_taskcreate_step = trace.steps[1]
    second_taskcreate_step = trace.steps[2]
    candidates = [first_taskcreate_step.declared_intent, second_taskcreate_step.declared_intent]
    combined = "\n".join(filter(None, candidates))
    assert THINKING_TEXT in combined


def test_tool_result_is_error_is_preserved() -> None:
    """The env_effect step for an errored tool_result must record the error
    somewhere discoverable on the Step, either in the action payload or in
    step.metadata (Phase 1 chooses the placement).

    Fails today because `_user_steps` builds
    ``Action(payload={"tool_result": ...})`` and never reads the
    ``is_error`` key from the tool_result block, and `_make_step` never
    populates `Step.metadata` at all.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    # step index 6: the tool_result for tu3 (Bash), which has is_error: true.
    error_step = trace.steps[6]
    assert error_step.actor == Actor.TOOL
    assert error_step.action.type.value == "env_effect"
    assert True in (error_step.action.payload.get("is_error"), error_step.metadata.get("is_error"))


def test_trace_metadata_populated_from_session_header_fields() -> None:
    """Trace.metadata should carry the session's cwd/gitBranch/version/sessionId.

    Fails today because `ingest_claude_code_session` builds the `Trace()`
    without ever passing a `metadata=` argument, so it defaults to `{}`.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    assert trace.metadata.get("cwd") == "/work/example-project"
    assert trace.metadata.get("gitBranch") == "main"
    assert trace.metadata.get("version") == "2.1.215"
    assert trace.metadata.get("sessionId") == "test-session-0001"


def test_plan_store_tasks_provide_intent_anchor() -> None:
    """Passing the persisted plan-store tasks should let the adapter derive
    intent from the store directly.

    RED by construction: `ingest_claude_code_session` has no `plan_tasks`
    parameter yet, so this call raises TypeError until Phase 1 adds it.
    Do not wrap this in pytest.raises — the failure itself is the point.
    """
    events = _load("session_modern_taskcreate.jsonl")
    plan_tasks = _load_plan_store("plan_store_modern.json")
    trace = ingest_claude_code_session(events, plan_tasks=plan_tasks)
    covered = sum(1 for s in trace.steps if s.declared_intent)
    assert covered > 0


def test_intent_coverage_exceeds_threshold_on_modern_fixture_session() -> None:
    """Headline characterisation test: on a well-behaved modern (TaskCreate-
    based) session, the majority of steps should carry a non-empty
    declared_intent.

    Fails today because coverage is ~0: none of TaskCreate, TaskUpdate, or
    thinking blocks are recognised as intent sources by the current adapter,
    so every agent step's declared_intent resolves to None.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    covered = sum(1 for s in trace.steps if s.declared_intent)
    total = len(trace.steps)
    assert total >= 8, f"expected >=8 steps, got {total}"
    assert covered / total > 0.50, f"coverage {covered}/{total} = {covered / total:.2%}"


def test_permission_mode_recorded_on_step_metadata() -> None:
    """Every event in the modern fixture carries `permissionMode: "default"`;
    it should land on each produced Step's metadata so evidence packs can
    show what guardrail was active when an action happened.
    """
    trace = ingest_claude_code_session(_load("session_modern_taskcreate.jsonl"))
    assert len(trace.steps) > 0
    assert all(step.metadata.get("permission_mode") == "default" for step in trace.steps)


def test_delegation_tool_use_marks_step_unobserved() -> None:
    """A `Task`/`Agent` tool_use delegates to a child transcript we do not
    stitch in during this phase; the blind spot must at least be visible on
    the step rather than silently looking like a normal, fully-observed
    tool call.
    """
    events: list[dict[str, Any]] = [
        {
            "type": "user",
            "uuid": "u1",
            "parentUuid": None,
            "sessionId": "sess-delegate",
            "timestamp": "2026-07-01T10:00:00.000Z",
            "message": {"role": "user", "content": "Delegate this to a subagent."},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "parentUuid": "u1",
            "sessionId": "sess-delegate",
            "timestamp": "2026-07-01T10:00:01.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "Task",
                        "input": {"description": "sub-agent work", "prompt": "do the thing"},
                    }
                ],
            },
        },
    ]
    trace = ingest_claude_code_session(events)
    delegate_step = trace.steps[1]
    assert delegate_step.action.payload["name"] == "Task"
    assert delegate_step.metadata.get("delegation_unobserved") is True


def test_load_plan_tasks_reads_and_sorts_by_numeric_stem(tmp_path: Path) -> None:
    (tmp_path / "2.json").write_text(json.dumps({"subject": "second", "status": "pending"}))
    (tmp_path / "1.json").write_text(json.dumps({"subject": "first", "status": "pending"}))
    (tmp_path / "not-numeric.json").write_text(json.dumps({"subject": "ignored"}))
    tasks = load_plan_tasks(tmp_path)
    assert [t["subject"] for t in tasks] == ["first", "second"]


def test_load_plan_tasks_missing_dir_returns_empty() -> None:
    assert load_plan_tasks(Path("/nonexistent/plan/store/dir")) == []


@pytest.mark.skipif(
    not os.environ.get("RUN_REAL_SESSION_TEST"), reason="requires a real session path"
)
def test_real_session_intent_coverage() -> None:
    """Local-only sanity check against a real Claude Code session on disk.

    Gated behind RUN_REAL_SESSION_TEST + AUDITK_REAL_SESSION so it is skipped
    by default in CI and never touches committed fixture data.
    """
    path = os.environ.get("AUDITK_REAL_SESSION")
    if not path:
        pytest.skip("set AUDITK_REAL_SESSION to a session .jsonl")
    events = _load_events_at(path)
    trace = ingest_claude_code_session(events)
    covered = sum(1 for s in trace.steps if s.declared_intent)
    total = len(trace.steps)
    assert total > 0
    assert covered / total > 0.4, f"coverage {covered}/{total} = {covered / total:.2%}"


def _load_events_at(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
