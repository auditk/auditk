# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the adapter-health canary (Phase 5 of
docs/proposals/session-postmortem-reporting.md; P2 of the vault's
`coding-tasks/auditk/phase-cc-adapter-integrity.md` scope).

Specifies `auditk.adapters.health.check_adapter_health` and its
`AdapterHealth` / `SessionHealthInput` shapes, per the RESOLVED design
(D1/D2 in the scope doc):

- D1: the corpus-level dead-anchor invariant ("across >= 20 sessions, at
  least one known plan anchor -- TodoWrite/TaskCreate/TaskUpdate/persisted
  plan store -- must appear") only applies once the corpus reaches that
  size. Below it, only the two cheap per-session structural checks run:
  (a) tool-call / tool-result count pairing, and (b) a genuinely
  UNKNOWN-record-type share > 5% of a session's records.
- D2: `check_adapter_health` is a PURE function that never raises. It
  returns `AdapterHealth(ok, breaches)` -- never an exception, never a
  score.

Correction folded in at the RED gate: real corpus data shows parent
transcripts contain 16 record types, and `user`+`assistant` are only 65.5%
of records -- known-benign types like `last-prompt` (6.1%) and
`queue-operation` (5.8%) individually exceed a naive 5% "share of
user/assistant" floor, which would false-positive a dead-parser breach on
essentially every healthy session. The check's real intent is to catch a
genuinely NEW/UNKNOWN record type appearing at significant share -- a
format-change signal -- not merely "not user/assistant". So the default
`handled_record_types` is `KNOWN_RECORD_TYPES`, a single-source-of-truth
frozenset of every currently-observed type (seeded generously so it does
not false-fire today); a share of records outside that set is what fires
the breach. A new type crossing the threshold firing IS the canary working
as designed -- a human triages it and adds it to `KNOWN_RECORD_TYPES`.

Every fixture here is built in-test as plain dicts mirroring parsed Claude
Code JSONL records (same approach as tests/unit/test_corpus_stats.py) --
never real ~/.claude data, never file I/O.

Every test in this module is expected to FAIL right now with an
`ImportError` -- `auditk.adapters.health` does not exist yet. This is the
RED phase: production code (and the exact wording of breach messages) lands
in the next (GREEN) phase, after human review of this file.
"""

from __future__ import annotations

from typing import Any

from auditk.adapters.health import (
    KNOWN_RECORD_TYPES,
    AdapterHealth,
    SessionHealthInput,
    check_adapter_health,
)

# --- fixture builders --------------------------------------------------
# Mirrors tests/unit/test_corpus_stats.py's `_assistant_record` helper: raw
# dicts shaped like parsed Claude Code JSONL records, not real transcripts.


def _assistant_tool_use(*tool_calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    content = [{"type": "tool_use", "name": name, "input": inp} for name, inp in tool_calls]
    return {"type": "assistant", "message": {"content": content}}


def _user_tool_results(n: int) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "tool_result", "content": "ok"} for _ in range(n)]
    return {"type": "user", "message": {"content": content}}


def _assistant_tool_use_with_ids(*tool_calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    """Like `_assistant_tool_use`, but each call also carries its `id` --
    the real-transcript shape (confirmed against a live Claude Code
    session's raw JSONL: `tool_use` blocks carry `id`, `tool_result` blocks
    carry `tool_use_id`) needed to exercise the id-matched pairing path in
    `health.py` (`_tool_use_id`/`_unresolved_call_count_by_id`), rather than
    the id-less fallback that `_assistant_tool_use` above still exercises."""
    content = [
        {"type": "tool_use", "id": call_id, "name": name, "input": inp}
        for call_id, name, inp in tool_calls
    ]
    return {"type": "assistant", "message": {"content": content}}


def _user_tool_result_for(*tool_use_ids: str) -> dict[str, Any]:
    """A `user` event whose `tool_result` blocks each resolve exactly one
    of `tool_use_ids`, by id -- the real-transcript shape `_tool_result_ref_id`
    reads. Pairs with `_assistant_tool_use_with_ids` above."""
    content: list[dict[str, Any]] = [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}
        for tool_use_id in tool_use_ids
    ]
    return {"type": "user", "message": {"content": content}}


def _user_text(text: str = "please do the thing") -> dict[str, Any]:
    return {"type": "user", "message": {"content": text}}


def _untyped_record(record_type: str) -> dict[str, Any]:
    """A record carrying only a `type` field, no `assistant`/`user` message
    shape -- e.g. a known-benign type (`last-prompt`, `system`, ...) or a
    genuinely unknown one, depending on what the test needs. Whether this
    counts as "unhandled" depends entirely on the `handled_record_types` set
    in force, not on this helper -- see `KNOWN_RECORD_TYPES`."""
    return {"type": record_type}


def _healthy_session_events(anchor_tool: str | None = "TaskCreate") -> list[dict[str, Any]]:
    """A small, structurally well-behaved session: balanced tool-call /
    tool-result counts, only `user`/`assistant` record types, and (unless
    `anchor_tool` is None) exactly one plan-anchor tool call."""
    events: list[dict[str, Any]] = [_user_text()]
    if anchor_tool is not None:
        events.append(_assistant_tool_use((anchor_tool, {"subject": "do the thing"})))
        events.append(_user_tool_results(1))
    events.append(_assistant_tool_use(("Bash", {"command": "echo hi"})))
    events.append(_user_tool_results(1))
    return events


def _no_anchor_corpus(size: int, *, anchor_tool: str | None = None) -> list[SessionHealthInput]:
    """`size` structurally-healthy sessions, none of which call a known plan
    anchor tool (or, if `anchor_tool` is given, calling a tool by that name --
    used to simulate a renamed/unrecognised anchor tool)."""
    return [
        SessionHealthInput(
            events=_healthy_session_events(anchor_tool=anchor_tool),
            session_id=f"session-{i}",
        )
        for i in range(size)
    ]


# --- AdapterHealth dataclass shape --------------------------------------


class TestAdapterHealthShape:
    def test_has_ok_and_breaches_fields(self) -> None:
        health = AdapterHealth(ok=True, breaches=[])
        assert health.ok is True
        assert health.breaches == []

    def test_breaches_defaults_to_empty_list_when_omitted(self) -> None:
        health = AdapterHealth(ok=True)
        assert health.breaches == []

    def test_default_breaches_are_not_a_shared_mutable(self) -> None:
        # Dataclass footgun check: each instance must get its own list, not
        # the same default object appended-to across calls.
        first = AdapterHealth(ok=True)
        first.breaches.append("oops")
        second = AdapterHealth(ok=True)
        assert second.breaches == []


# --- healthy corpus: ok=True, no breaches -------------------------------


class TestHealthyCorpus:
    def test_ok_true_on_healthy_corpus_of_20_sessions(self) -> None:
        sessions = [
            SessionHealthInput(
                events=_healthy_session_events(
                    anchor_tool=("TaskCreate" if i % 2 == 0 else "TaskUpdate")
                ),
                session_id=f"session-{i}",
            )
            for i in range(20)
        ]
        result = check_adapter_health(sessions)
        assert isinstance(result, AdapterHealth)
        assert result.ok is True
        assert result.breaches == []

    def test_single_healthy_session_below_corpus_threshold_is_ok(self) -> None:
        # Only one session -- far below the N>=20 corpus-level threshold.
        # Per D1, a single session missing every anchor must NOT be treated
        # as a corpus-level dead-anchor breach; the per-session checks
        # (which this session passes) are the only thing that applies.
        sessions = [SessionHealthInput(events=_healthy_session_events(anchor_tool=None))]
        result = check_adapter_health(sessions)
        assert result.ok is True
        assert result.breaches == []


# --- D1(a): corpus-level dead-anchor invariant --------------------------


class TestCorpusLevelDeadAnchor:
    def test_breach_when_zero_anchors_across_20_sessions(self) -> None:
        sessions = _no_anchor_corpus(20)
        result = check_adapter_health(sessions)
        assert result.ok is False
        assert result.breaches != []

    def test_breach_message_mentions_anchor_and_session_count(self) -> None:
        sessions = _no_anchor_corpus(20)
        result = check_adapter_health(sessions)
        combined = " ".join(result.breaches).lower()
        assert "anchor" in combined
        assert "20" in combined

    def test_below_threshold_corpus_with_zero_anchors_does_not_breach(self) -> None:
        # 19 sessions -- one short of the N>=20 floor -- must not trigger
        # the corpus-level check at all (D1: the invariant applies "across
        # a corpus of N >= 20 sessions").
        sessions = _no_anchor_corpus(19)
        result = check_adapter_health(sessions)
        assert result.ok is True
        assert result.breaches == []

    def test_exactly_20_sessions_is_at_the_threshold(self) -> None:
        sessions = _no_anchor_corpus(20)
        result = check_adapter_health(sessions, min_corpus_size=20)
        assert result.ok is False

    def test_persisted_plan_store_alone_satisfies_the_invariant(self) -> None:
        # No plan-anchor TOOL CALLS anywhere, but one session has a
        # persisted plan store on disk -- per the adapter's own anchor
        # precedence (plan store > TaskCreate/TaskUpdate > TodoWrite), that
        # counts as a real anchor and must NOT breach.
        sessions = _no_anchor_corpus(19)
        sessions.append(
            SessionHealthInput(
                events=_healthy_session_events(anchor_tool=None),
                session_id="session-with-plan-store",
                has_plan_store=True,
            )
        )
        result = check_adapter_health(sessions)
        assert result.ok is True
        assert result.breaches == []

    def test_a_single_todowrite_anywhere_in_the_corpus_satisfies_the_invariant(self) -> None:
        sessions = _no_anchor_corpus(19)
        sessions.append(
            SessionHealthInput(
                events=_healthy_session_events(anchor_tool="TodoWrite"),
                session_id="session-with-todowrite",
            )
        )
        result = check_adapter_health(sessions)
        assert result.ok is True


# --- ACCEPTANCE GATE: renamed anchor tool -> breach, not a score --------


class TestAcceptanceGateRenamedAnchorTool:
    """Mirrors Finding A / the proposal's Phase 5 gate: 'simulate a renamed
    anchor tool in the synthetic fixture ... the pipeline raises an
    adapter-health failure rather than scoring it.'

    Every one of these 20 sessions calls a plan-tracking tool -- just not
    under any name `check_adapter_health` recognises (the harness renamed
    it, exactly as happened to TodoWrite -> TaskCreate/TaskUpdate in real
    usage). This must be indistinguishable, from the canary's point of
    view, from a corpus with zero plan-tracking calls at all: a dead parser
    looks the same whether the tool vanished or was renamed to something
    unrecognised.
    """

    def test_renamed_anchor_tool_across_whole_corpus_triggers_breach(self) -> None:
        sessions = _no_anchor_corpus(20, anchor_tool="TodoWriteV2")
        result = check_adapter_health(sessions)
        assert result.ok is False
        assert result.breaches != []

    def test_renamed_anchor_tool_breach_is_indistinguishable_from_no_anchor_at_all(self) -> None:
        renamed = check_adapter_health(_no_anchor_corpus(20, anchor_tool="TodoWriteV2"))
        absent = check_adapter_health(_no_anchor_corpus(20, anchor_tool=None))
        assert renamed.ok == absent.ok == False  # noqa: E712


# --- D1(b): per-session tool-call / tool-result pairing -----------------


class TestPerSessionToolCallResultPairing:
    def test_breach_when_tool_calls_outnumber_tool_results(self) -> None:
        # Two tool_use blocks in one assistant turn, but only one
        # tool_result comes back -- a dropped/truncated result.
        events = [
            _user_text(),
            _assistant_tool_use(("Bash", {"command": "one"}), ("Bash", {"command": "two"})),
            _user_tool_results(1),
        ]
        sessions = [SessionHealthInput(events=events, session_id="unbalanced")]
        result = check_adapter_health(sessions)
        assert result.ok is False
        combined = " ".join(result.breaches).lower()
        assert "tool-call" in combined or "tool call" in combined
        assert "tool-result" in combined or "tool result" in combined

    def test_breach_message_identifies_the_offending_session(self) -> None:
        events = [
            _assistant_tool_use(("Bash", {"command": "one"}), ("Bash", {"command": "two"})),
            _user_tool_results(1),
        ]
        sessions = [SessionHealthInput(events=events, session_id="session-xyz")]
        result = check_adapter_health(sessions)
        assert any("session-xyz" in b for b in result.breaches)

    def test_no_breach_when_counts_are_balanced(self) -> None:
        events = [
            _assistant_tool_use(("Bash", {"command": "one"}), ("Bash", {"command": "two"})),
            _user_tool_results(2),
        ]
        sessions = [SessionHealthInput(events=events, session_id="balanced")]
        result = check_adapter_health(sessions)
        assert result.ok is True
        assert result.breaches == []

    def test_this_check_applies_regardless_of_corpus_size(self) -> None:
        # A single session, far below the corpus-level threshold, still
        # gets the cheap per-session pairing check (D1: "per-session checks
        # are limited to the cheap structural ones needing no magic
        # number" -- these are not gated on corpus size). Uses a genuinely
        # orphaned call (see test_single_trailing_in_flight_call_does_not_breach
        # just below for why a merely-trailing unresolved call must NOT
        # qualify here).
        #
        # Test Integrity Rule note (re-pinned 2026-09-03, approved by Matt):
        # this fixture used to leave WHICH call the lone tool_result resolved
        # unstated -- id-less blocks, so "Read orphaned, Bash resolved" was
        # merely the intended reading, not something the check could actually
        # tell apart from "Bash orphaned, Read resolved" (both reduce to the
        # same type/count shape). That ambiguity is exactly what let a real
        # live-corpus session (two trailing parallel calls, one resolved by
        # id, capture truncated before the other's result arrived) false-
        # breach: the fix widening `_trailing_in_flight_call_count` for that
        # id-less shape necessarily also excused *this* fixture, since they
        # were indistinguishable. Now that pairing is id-matched when ids are
        # present (`_unresolved_call_count_by_id`), the ambiguity is gone --
        # this fixture is re-pinned to the variant that keeps its original
        # intent (a genuine mid-transcript orphan, Test Integrity Rule
        # justification: "test assumed call/result pairing was unknowable
        # from types alone; id-matching makes the ambiguous fixture decidable
        # -- re-pinned to the still-breaching variant per Test Integrity
        # Rule, approved by Matt 2026-09-03").
        events = [
            _assistant_tool_use_with_ids(("call-read", "Read", {"file_path": "/a"})),
            # The Read's result never arrives -- the single tool_result that
            # follows is pinned, by id, to the LATER Bash call instead. So
            # the session carries on with a full, separately-resolved round
            # trip afterward: this is not "the capture just ended mid-call",
            # something else happened while the Read's result was dropped.
            _assistant_tool_use_with_ids(("call-bash", "Bash", {"command": "echo hi"})),
            _user_tool_result_for("call-bash"),
        ]
        result = check_adapter_health([SessionHealthInput(events=events)])
        assert result.ok is False

    def test_trailing_parallel_calls_excused_when_id_matched_result_resolves_the_other(
        self,
    ) -> None:
        # RED-phase reproduction of the live-corpus false breach: two
        # trailing parallel tool_use calls (WebSearch, WebFetch shape from
        # the real session), capture truncated after only the FIRST call's
        # result comes back. Id-less, this was indistinguishable from the
        # genuine-orphan fixture above and false-breached (8 tool-call(s) vs
        # 7 tool-result(s), 1 unresolved, 0 excused as trailing, because the
        # naive reverse walk in `_trailing_in_flight_call_count` stopped at
        # the first `user` event it saw, whether or not that event's results
        # actually covered the trailing calls). With ids present, the
        # second/later call (WebFetch-equivalent) is the one still open, and
        # nothing issued after it ever resolves -- exactly "trailing
        # in-flight": excused, not a breach.
        events = [
            _assistant_tool_use_with_ids(("call-search", "WebSearch", {"query": "x"})),
            _assistant_tool_use_with_ids(("call-fetch", "WebFetch", {"url": "y"})),
            _user_tool_result_for("call-search"),
        ]
        result = check_adapter_health(
            [SessionHealthInput(events=events, session_id="trailing-parallel")]
        )
        assert result.ok is True
        assert result.breaches == []

    def test_id_matched_orphan_breaches_even_when_a_later_call_resolves(self) -> None:
        # Guard: same two-call/one-result shape as the excused case just
        # above, but the result is pinned to the SECOND (later-issued) call
        # instead of the first. The first call is then a genuine
        # mid-transcript orphan -- something issued after it DID get a
        # result, so it cannot be "the capture ended while everything from
        # here on was still open". Must still breach.
        events = [
            _assistant_tool_use_with_ids(("call-a", "Read", {"file_path": "/a"})),
            _assistant_tool_use_with_ids(("call-b", "Bash", {"command": "echo hi"})),
            _user_tool_result_for("call-b"),
        ]
        result = check_adapter_health([SessionHealthInput(events=events, session_id="orphan-a")])
        assert result.ok is False

    def test_mixed_id_presence_falls_back_to_count_based_pairing_and_still_breaches(
        self,
    ) -> None:
        # Defensive fallback coverage: if even ONE tool_use block in a
        # session is missing its id (malformed/foreign input, not a real
        # Claude Code transcript -- real transcripts carry `id` on every
        # tool_use block), id-matching cannot be trusted for ANY call in
        # that session -- `_unresolved_call_count_by_id` returns None
        # (short-circuits on the first missing id) and the check falls back
        # to the original, coarser `_trailing_in_flight_call_count`
        # heuristic. This must never make the check WEAKER than before this
        # change: same shape as `test_breach_when_tool_calls_outnumber_tool_results`
        # (two calls, one id-less result, no trailing excuse available under
        # the old heuristic because the transcript doesn't end on a run of
        # `assistant` events) -- one call now happens to carry an id, but
        # that must not enable a partial/best-effort id-match; it still
        # breaches exactly as the fully id-less version does.
        events = [
            _assistant_tool_use_with_ids(("call-1", "Bash", {"command": "one"})),
            _assistant_tool_use(("Bash", {"command": "two"})),  # no id
            _user_tool_results(1),  # no tool_use_id either
        ]
        result = check_adapter_health(
            [SessionHealthInput(events=events, session_id="id-less-mixed")]
        )
        assert result.ok is False

    def test_single_trailing_in_flight_call_does_not_breach(self) -> None:
        # GREEN-phase correction (Test Integrity Rule): this test originally
        # asserted that a session consisting of ONE unresolved tool call
        # breaches on its own. That turned out to be a false-positive-prone
        # reading of "pairing": a transcript legitimately ends mid-call all
        # the time (harness killed, session closed, capture truncated) --
        # and a real "clean" fixture used across several other test modules
        # (tests/fixtures/claude_code/session_modern_taskcreate.jsonl, whose
        # own docstring calls it "a clean session with zero HIGH/MEDIUM
        # findings") ends in exactly this shape. Wiring the original,
        # trailing-intolerant check into `auditk report` made that
        # already-passing, already-"clean"-labelled fixture fail health with
        # a false "tool-call/tool-result mismatch" breach. The check (and
        # this test) were corrected together: a merely-trailing unresolved
        # call is not the "adapter dropped a result" signal this exists to
        # catch (see `_trailing_in_flight_call_count`'s docstring) -- only
        # an orphaned one (covered by the test just above) is.
        events = [_assistant_tool_use(("Read", {"file_path": "/a"}))]
        result = check_adapter_health([SessionHealthInput(events=events)])
        assert result.ok is True


# --- D1(c): per-session UNKNOWN-record-type share -------------------------
# Corrected at the RED gate: the default `handled_record_types` is
# `KNOWN_RECORD_TYPES` (every currently-observed type, not just
# user/assistant), so known-benign chatter like `last-prompt` and
# `queue-operation` must never breach on its own, however large its share.
# Only a genuinely unrecognised type crossing the 5% floor should fire.


class TestPerSessionUnknownRecordTypeShare:
    def test_known_benign_types_do_not_breach_even_at_large_share_by_default(self) -> None:
        # last-prompt/queue-operation/system are all in KNOWN_RECORD_TYPES;
        # here they are the overwhelming majority (5/6 ~= 83%) of records,
        # which must still NOT breach with the default handled set -- this
        # is exactly the false-positive the RED-gate correction fixes.
        events = [
            _user_text(),
            _untyped_record("last-prompt"),
            _untyped_record("last-prompt"),
            _untyped_record("queue-operation"),
            _untyped_record("system"),
            _untyped_record("mode"),
        ]
        result = check_adapter_health([SessionHealthInput(events=events, session_id="chatty")])
        assert result.ok is True
        assert result.breaches == []

    def test_breach_when_genuinely_unknown_type_share_exceeds_5_percent(self) -> None:
        # 10 total records; 4 carry a type nobody has ever seen before --
        # 40%, well over the 5% floor, and NOT in KNOWN_RECORD_TYPES.
        events = (
            [_user_text(), _assistant_tool_use(("Bash", {"command": "ok"}))]
            + [_untyped_record("totally-new-type-v9") for _ in range(4)]
            + [_user_tool_results(1)]
            + [_untyped_record("last-prompt")] * 3  # known-benign padding
        )
        assert len(events) == 10
        result = check_adapter_health([SessionHealthInput(events=events, session_id="noisy")])
        assert result.ok is False
        combined = " ".join(result.breaches).lower()
        assert "unhandled" in combined or "unknown" in combined

    def test_no_breach_when_unknown_share_is_at_or_below_5_percent(self) -> None:
        # 19 known + 1 unknown == 20 records, exactly 5% unknown -- must NOT
        # breach; the threshold is "exceeds 5%", not "reaches 5%".
        events = [*([_user_text()] * 19), _untyped_record("totally-new-type-v9")]
        result = check_adapter_health([SessionHealthInput(events=events, session_id="borderline")])
        assert result.ok is True
        assert result.breaches == []

    def test_breach_message_identifies_the_offending_session(self) -> None:
        events = [_untyped_record("totally-new-type-v9") for _ in range(3)] + [_user_text()]
        result = check_adapter_health(
            [SessionHealthInput(events=events, session_id="unknown-type-heavy")]
        )
        assert any("unknown-type-heavy" in b for b in result.breaches)

    def test_known_record_types_constant_covers_the_documented_set(self) -> None:
        expected = {
            "assistant",
            "user",
            "last-prompt",
            "queue-operation",
            "system",
            "mode",
            "attachment",
            "ai-title",
            "custom-title",
            "permission-mode",
            "file-history-snapshot",
            "pr-link",
            "agent-name",
            "file-history-delta",
            "bridge-session",
            "frame-link",
            "summary",
        }
        assert expected <= set(KNOWN_RECORD_TYPES)

    def test_custom_handled_record_types_override_still_works(self) -> None:
        # A caller may still narrow or widen the handled set explicitly --
        # e.g. to declare a brand-new type handled before it's added to
        # KNOWN_RECORD_TYPES.
        events = [_untyped_record("totally-new-type-v9") for _ in range(5)] + [_user_text()]
        result = check_adapter_health(
            [SessionHealthInput(events=events, session_id="s")],
            handled_record_types=frozenset({"user", "assistant", "totally-new-type-v9"}),
        )
        assert result.ok is True

    def test_override_can_also_narrow_and_trigger_a_breach(self) -> None:
        # Passing a narrower handled set than the default must be honoured
        # -- e.g. a caller auditing strictly against user/assistant only.
        events = [_untyped_record("last-prompt") for _ in range(5)] + [_user_text()]
        result = check_adapter_health(
            [SessionHealthInput(events=events, session_id="s")],
            handled_record_types=frozenset({"user", "assistant"}),
        )
        assert result.ok is False


# --- purity: never raises -----------------------------------------------


class TestPurityNeverRaises:
    def test_does_not_raise_on_malformed_events(self) -> None:
        malformed_sessions = [
            SessionHealthInput(events=[{"no_type_field": True}], session_id="a"),
            SessionHealthInput(events=[{"type": "assistant", "message": None}], session_id="b"),
            SessionHealthInput(
                events=[{"type": "assistant", "message": {"content": "not a list"}}],
                session_id="c",
            ),
            SessionHealthInput(
                events=[{"type": "user", "message": {"content": [{"not_a_type_field": 1}]}}],
                session_id="d",
            ),
        ]
        result = check_adapter_health(malformed_sessions)
        assert isinstance(result, AdapterHealth)

    def test_does_not_raise_on_empty_corpus(self) -> None:
        result = check_adapter_health([])
        assert isinstance(result, AdapterHealth)
        assert result.ok is True  # nothing to check, nothing failed


# --- multiple simultaneous breaches --------------------------------------


class TestMultipleBreaches:
    def test_corpus_and_session_level_breaches_both_reported(self) -> None:
        sessions = _no_anchor_corpus(19)
        unbalanced_events = [
            _assistant_tool_use(("Bash", {"command": "one"}), ("Bash", {"command": "two"})),
            _user_tool_results(1),
        ]
        sessions.append(SessionHealthInput(events=unbalanced_events, session_id="also-unbalanced"))
        # 20 sessions total, none with a recognised anchor, plus one with an
        # imbalanced tool-call/tool-result count.
        result = check_adapter_health(sessions)
        assert result.ok is False
        assert len(result.breaches) >= 2
        combined = " ".join(result.breaches).lower()
        assert "anchor" in combined
        assert "tool-call" in combined or "tool call" in combined
