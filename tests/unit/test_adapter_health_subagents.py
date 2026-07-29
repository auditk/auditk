# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for P4 part 2 of the cc-adapter-integrity work: extending
the P2 adapter-health canary (`auditk.adapters.health`) so a format change
or broken layout in SUBAGENT transcripts also fires, not just parent
transcripts.

Background: P3 (`load_subagent_transcripts` in `adapters/claude_code.py`)
discovers `<session_dir>/subagents/agent-*.jsonl` + sidecar
`agent-*.meta.json` files and, by design, SKIPS anything malformed --
missing sidecar, unparseable meta, no `toolUseId` -- rather than raising
("best-effort forensic tooling reading a third-party harness's own
files"). That is the right behaviour for ingestion, but it means a broken
subagents/ layout is currently silently swallowed: nothing downstream ever
learns that N transcripts were found on disk but only M < N were usable,
or that a used one had drifted-format records inside it. That is exactly
the Finding-A shape the P2 canary already exists to catch for parent
transcripts (docs/proposals/session-postmortem-reporting.md Phase 5) --
this module extends the SAME canary to see subagent-level drift too.

DECIDED CHECK (proposed here, flagged for review, not silently committed):

`SessionHealthInput` grows one new field, `subagents: list[SubagentHealthInput]`
(default `[]`, so every existing P2 test/caller that never mentions
subagents is completely unaffected). `SubagentHealthInput` is a new small
dataclass describing ONE `agent-*.jsonl` candidate found under a session's
`subagents/` directory:

    agent_id: str
    events: list[dict[str, Any]] = []
    has_meta: bool = True           # a sidecar `.meta.json` was found+parsed
    has_tool_use_id: bool = True     # that meta had a usable `toolUseId`

`check_adapter_health` then, per session, for each `SubagentHealthInput`:
- fires a breach if `has_meta` is False (an `agent-*.jsonl` with no
  matching `meta.json` -- a broken subagents/ layout), or if `has_meta` is
  True but `has_tool_use_id` is False (a `meta.json` present but missing
  the join key) -- both cases where `load_subagent_transcripts` today
  silently drops the transcript instead of surfacing anything;
- otherwise runs the SAME unknown-record-type-share check (D1(c), same
  `max_unhandled_type_share`/`handled_record_types` the top-level call
  already takes -- no new threshold parameters) over that subagent's own
  `events`, exactly as already done for the parent transcript's own
  events. Real subagent record types seen (`user`, `attachment`,
  `assistant`) are already in `KNOWN_RECORD_TYPES`, so this does not
  false-fire on a healthy subagent transcript by default -- mirrors the
  false-positive lesson already learned and fixed for parent transcripts.

This check is a per-session, corpus-size-independent check (like the
existing two): it runs on a single session, no `min_corpus_size` gate,
matching D1's existing "cheap structural checks need no magic number"
precedent.

Every fixture here is a synthetic in-memory `SessionHealthInput`/
`SubagentHealthInput` -- no real `~/.claude` data, no file I/O (this
module tests the pure evaluation function directly; the on-disk-to-
dataclass discovery wiring, extending `cli.py`'s `doctor`/`report` paths
to populate `subagents=`, is GREEN-phase production work, not tested
here).

Every test in this module is expected to FAIL right now with an
`ImportError` (`SubagentHealthInput` does not exist yet) or a `TypeError`
(`SessionHealthInput`'s `subagents=` keyword is not recognised yet).
Production code lands in the next (GREEN) phase, after human review.
"""

from __future__ import annotations

from typing import Any

from auditk.adapters.health import (
    AdapterHealth,
    SessionHealthInput,
    SubagentHealthInput,
    check_adapter_health,
)

# --- fixture builders ------------------------------------------------------
# Mirrors tests/unit/test_adapter_health.py's own helpers.


def _assistant_tool_use(*tool_calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    content = [{"type": "tool_use", "name": name, "input": inp} for name, inp in tool_calls]
    return {"type": "assistant", "message": {"content": content}}


def _user_tool_results(n: int) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "tool_result", "content": "ok"} for _ in range(n)]
    return {"type": "user", "message": {"content": content}}


def _user_text(text: str = "please do the thing") -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}}


def _untyped_record(record_type: str) -> dict[str, Any]:
    return {"type": record_type}


def _healthy_parent_session_events() -> list[dict[str, Any]]:
    """A structurally healthy parent session with one anchor call, so no
    corpus-level or per-session parent breach masks the subagent-specific
    assertions under test."""
    return [
        _user_text(),
        _assistant_tool_use(("TaskCreate", {"subject": "do the thing"})),
        _user_tool_results(1),
    ]


def _healthy_subagent_events(agent_id: str = "AAA") -> list[dict[str, Any]]:
    """A structurally healthy subagent transcript: balanced tool-call /
    tool-result counts, only known record types."""
    return [
        {"type": "user", "agentId": agent_id, "isSidechain": True, "message": {"content": "go"}},
        {
            "type": "assistant",
            "agentId": agent_id,
            "isSidechain": True,
            "message": {
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "x"}}]
            },
        },
        {
            "type": "user",
            "agentId": agent_id,
            "isSidechain": True,
            "message": {"content": [{"type": "tool_result", "content": "ok"}]},
        },
    ]


# --- SubagentHealthInput shape ---------------------------------------------


class TestSubagentHealthInputShape:
    def test_defaults(self) -> None:
        sub = SubagentHealthInput(agent_id="AAA")
        assert sub.agent_id == "AAA"
        assert sub.events == []
        assert sub.has_meta is True
        assert sub.has_tool_use_id is True

    def test_all_fields_settable(self) -> None:
        sub = SubagentHealthInput(
            agent_id="ZZZ", events=[{"type": "user"}], has_meta=False, has_tool_use_id=False
        )
        assert sub.events == [{"type": "user"}]
        assert sub.has_meta is False
        assert sub.has_tool_use_id is False


class TestSessionHealthInputSubagentsField:
    def test_defaults_to_empty_list(self) -> None:
        session = SessionHealthInput(events=_healthy_parent_session_events())
        assert session.subagents == []

    def test_accepts_a_list_of_subagent_health_inputs(self) -> None:
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            subagents=[SubagentHealthInput(agent_id="AAA")],
        )
        assert len(session.subagents) == 1
        assert session.subagents[0].agent_id == "AAA"

    def test_healthy_subagent_does_not_breach(self) -> None:
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            subagents=[SubagentHealthInput(agent_id="AAA", events=_healthy_subagent_events())],
        )
        result = check_adapter_health([session])
        assert isinstance(result, AdapterHealth)
        assert result.ok is True
        assert result.breaches == []


# --- broken subagents/ layout: missing meta.json --------------------------


class TestBrokenLayoutMissingMeta:
    def test_breach_when_subagent_has_no_meta_json(self) -> None:
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="s1",
            subagents=[
                SubagentHealthInput(
                    agent_id="ORPHAN", events=_healthy_subagent_events("ORPHAN"), has_meta=False
                )
            ],
        )
        result = check_adapter_health([session])
        assert result.ok is False
        combined = " ".join(result.breaches).lower()
        assert "orphan" in combined
        assert "meta" in combined

    def test_layout_breach_applies_regardless_of_corpus_size(self) -> None:
        # A single session, far below any corpus-level threshold -- this is
        # a cheap per-session check (like tool-call/result pairing and
        # unknown-type share), not gated on corpus size.
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            subagents=[SubagentHealthInput(agent_id="ORPHAN", has_meta=False)],
        )
        result = check_adapter_health([session])
        assert result.ok is False


# --- broken subagents/ layout: meta.json missing toolUseId ----------------


class TestBrokenLayoutMissingToolUseId:
    def test_breach_when_meta_present_but_no_tool_use_id(self) -> None:
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="s1",
            subagents=[
                SubagentHealthInput(
                    agent_id="NOJOIN",
                    events=_healthy_subagent_events("NOJOIN"),
                    has_meta=True,
                    has_tool_use_id=False,
                )
            ],
        )
        result = check_adapter_health([session])
        assert result.ok is False
        combined = " ".join(result.breaches).lower()
        assert "nojoin" in combined
        assert "tooluseid" in combined.replace("_", "").replace(" ", "")

    def test_missing_meta_and_missing_tool_use_id_are_distinguishable_breaches(self) -> None:
        # Two different failure modes named in the design: a caller
        # triaging output should be able to tell them apart, not see one
        # generic "broken subagent" message for both.
        no_meta_session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="no-meta",
            subagents=[SubagentHealthInput(agent_id="A", has_meta=False)],
        )
        no_join_session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="no-join",
            subagents=[SubagentHealthInput(agent_id="A", has_meta=True, has_tool_use_id=False)],
        )
        no_meta_breaches = " ".join(check_adapter_health([no_meta_session]).breaches).lower()
        no_join_breaches = " ".join(check_adapter_health([no_join_session]).breaches).lower()
        assert no_meta_breaches != no_join_breaches


# --- subagent-transcript unknown-record-type share ------------------------


class TestSubagentUnknownRecordTypeShare:
    def test_no_breach_when_subagent_uses_only_known_benign_types_even_at_large_share(
        self,
    ) -> None:
        # Real subagent record types (user/attachment/assistant) are already
        # in KNOWN_RECORD_TYPES (P2's corrected default) -- this must NOT
        # breach even though "attachment"-only records dominate the share,
        # mirroring the false-positive lesson already learned for parents.
        subagent_events = [
            {"type": "user", "agentId": "AAA", "isSidechain": True, "message": {"content": "go"}},
            *[{"type": "attachment", "agentId": "AAA", "isSidechain": True} for _ in range(5)],
        ]
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            subagents=[SubagentHealthInput(agent_id="AAA", events=subagent_events)],
        )
        result = check_adapter_health([session])
        assert result.ok is True
        assert result.breaches == []

    def test_breach_when_subagent_has_genuinely_unknown_type_above_threshold(self) -> None:
        subagent_events = [
            {"type": "user", "agentId": "AAA", "isSidechain": True, "message": {"content": "go"}},
            *[{"type": "totally-new-subagent-record-type-v9", "agentId": "AAA"} for _ in range(9)],
        ]
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="s1",
            subagents=[SubagentHealthInput(agent_id="AAA", events=subagent_events)],
        )
        result = check_adapter_health([session])
        assert result.ok is False
        combined = " ".join(result.breaches).lower()
        assert "aaa" in combined
        assert "unhandled" in combined or "unknown" in combined

    def test_no_breach_when_unknown_share_is_at_or_below_threshold(self) -> None:
        # 19 known + 1 unknown == 20 records, exactly 5% -- must NOT breach
        # (threshold is "exceeds 5%", matching the parent-level check).
        subagent_events = [
            {"type": "user", "agentId": "AAA", "isSidechain": True, "message": {"content": "go"}}
        ] * 19 + [{"type": "totally-new-subagent-record-type-v9", "agentId": "AAA"}]
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            subagents=[SubagentHealthInput(agent_id="AAA", events=subagent_events)],
        )
        result = check_adapter_health([session])
        assert result.ok is True

    def test_check_does_not_run_when_layout_is_already_broken(self) -> None:
        # A subagent with no meta.json has no reliable toolUseId-joined
        # identity to report a per-agent breach against meaningfully in
        # combination -- the layout breach alone is sufficient signal; this
        # just confirms adding drifted-type events to an already-broken
        # transcript doesn't produce a confusing SECOND, redundant
        # unknown-type breach on top of the layout one for the same agent.
        subagent_events = [
            {"type": "totally-new-subagent-record-type-v9", "agentId": "ORPHAN"} for _ in range(10)
        ]
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="s1",
            subagents=[
                SubagentHealthInput(agent_id="ORPHAN", events=subagent_events, has_meta=False)
            ],
        )
        result = check_adapter_health([session])
        assert result.ok is False
        layout_breaches = [b for b in result.breaches if "orphan" in b.lower()]
        assert len(layout_breaches) == 1


# --- multiple subagents combine correctly ----------------------------------


class TestMultipleSubagentsPerSession:
    def test_one_broken_one_healthy_only_the_broken_one_breaches(self) -> None:
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            session_id="s1",
            subagents=[
                SubagentHealthInput(agent_id="GOOD", events=_healthy_subagent_events("GOOD")),
                SubagentHealthInput(agent_id="BAD", has_meta=False),
            ],
        )
        result = check_adapter_health([session])
        assert result.ok is False
        combined = " ".join(result.breaches).lower()
        assert "bad" in combined
        assert "good" not in combined

    def test_all_healthy_across_multiple_subagents_is_ok(self) -> None:
        session = SessionHealthInput(
            events=_healthy_parent_session_events(),
            subagents=[
                SubagentHealthInput(agent_id="AAA", events=_healthy_subagent_events("AAA")),
                SubagentHealthInput(agent_id="BBB", events=_healthy_subagent_events("BBB")),
            ],
        )
        result = check_adapter_health([session])
        assert result.ok is True
        assert result.breaches == []
