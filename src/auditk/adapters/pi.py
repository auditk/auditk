# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Pi coding-agent session adapter for auditk.

Converts one pi session -- the parsed lines of a v3 session JSONL file
(``~/.pi/agent/sessions/--<encoded-cwd>--/<timestamp>_<uuid>.jsonl``, header
first, then entries) -- into a normalised ``Trace``.

Format discovered per docs/adapters.md's adapter-writing discipline: a
first-party live corpus written by pi 0.85.1's own ``SessionManager``
(``tests/fixtures/pi/``, real bytes) plus the installed release's own
compiled writer declarations (``dist/core/session-manager.d.ts``,
``dist/core/messages.d.ts``) -- never the public docs alone. The confirmed
grammar, the entry-type census, and the v4 drift risk live in
``docs/pi-format-notes.md``; this docstring only summarises what the code
below relies on.

Format notes
------------
Framing: line 1 is the header (``{"type":"session","version":3,"id":...,
"timestamp":...,"cwd":...}``; ``version`` is *optional* in the writer type
and versions 1-2 auto-migrate on load, so any of ``None``/1/2/3 is
accepted). Every other line is one entry: ``type``, an 8-hex ``id``,
``parentId`` (null only on the first entry), an ISO-8601 ``timestamp``, and
type-specific fields. Entries form an append-only tree (interactive
branching can give one parent several children), but file order is append
order and therefore chronological -- this adapter ingests ALL entries in
file order (an audit wants abandoned branches visible) and preserves
``parentId`` as ``parent_step_id`` rather than walking one root-to-leaf
context path.

The unreleased git-main **v4 storage rewrite** (header
``{"v":4,"kind":"header",...}``, transaction lines) is detected and refused
loudly with a version message -- never half-parsed. When a v4 release
ships, that refusal is the signal to extend this adapter.

Ids / pairing: **real**. An assistant message's ``toolCall`` content block
carries its own ``id``, echoed verbatim as the resolving ``toolResult``
message's ``toolCallId`` (with ``toolName`` and an ``isError`` boolean).
Multi-tool-call turns are real (fixture-confirmed); expanded steps reuse
pi's own ids -- the entry id for whole-entry steps, the toolCall id for
tool-call steps -- and chain within one entry the same way
``claude_code.py:_assistant_steps`` chains multiple ``tool_use`` blocks.

Declared intent: two tiers, no third. Narration (``text`` blocks) wins,
then ``thinking`` blocks (the same weaker-unverified-proxy caveat as Claude
Code's ``thinking``/Hermes' ``reasoning_content``). Vanilla pi has **no
plan/todo tool at all** (the author's own design decision), so unlike
Claude Code/Hermes there is no standing-plan tier and
``PI_HEALTH_DECLARATION.plan_anchor_supported`` is ``False`` with a reason
rather than an empty pretend-vocabulary.

Delegation / forks: vanilla pi has no subagent concept (a "delegated" run
is an ordinary ``bash`` invocation of another ``pi`` process; nothing links
parent to child). The one real cross-session link is the fork:
``parentSession`` on the header is an absolute *file path* to the parent
session, and a forked file copies the parent's entries verbatim (original
ids preserved) before appending -- so every session file is self-contained
and no transcript stitching is needed or permitted. ``parentSession`` is
surfaced as ``Trace.metadata["parent_session"]``.

Redaction pass-through
----------------------
Set ``strip_payloads=True`` to redact a ``TOOL_CALL`` step's ``input`` and
``output`` (the latter exists only on ``bashExecution`` steps, a role no
other shipped adapter has) and an ``ENV_EFFECT`` step's ``tool_result`` via
the shared post-ingest pass (``auditk.adapters.redaction.redact_trace``).
``UTTERANCE`` payloads are deliberately left untouched, the same convention
every other shipped adapter follows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from auditk.adapters.health import HealthDeclaration
from auditk.adapters.redaction import ContentKeysByActionType, redact_trace
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    FlowType,
    Step,
    Trace,
)

# The installed writer's full `SessionEntry` union plus the header's own
# "session" type (`dist/core/session-manager.d.ts`, transcribed in
# docs/pi-format-notes.md) -- the health canary's allow-list. A type
# outside this set is exactly the format-drift signal the
# unknown-record-type-share check exists to catch.
KNOWN_RECORD_TYPES: frozenset[str] = frozenset(
    {
        "session",
        "message",
        "model_change",
        "thinking_level_change",
        "compaction",
        "branch_summary",
        "custom",
        "custom_message",
        "label",
        "session_info",
    }
)

# Header versions the released writer can hand us: `version?: number` with
# 1-2 auto-migrating and 3 current (`CURRENT_SESSION_VERSION = 3`).
_SUPPORTED_HEADER_VERSIONS = (None, 1, 2, 3)

REDACTION_CONTENT_KEYS: ContentKeysByActionType = {
    ActionType.TOOL_CALL: frozenset({"input", "output"}),
    ActionType.ENV_EFFECT: frozenset({"tool_result"}),
}


def _parse_timestamp(value: Any) -> datetime:
    """An entry's ISO-8601 ``timestamp``, timezone-aware; epoch-0 UTC when
    absent or unparseable (best-effort, never raises)."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.fromtimestamp(0, UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return datetime.fromtimestamp(0, UTC)


def _blocks_text(content: Any, block_type: str, text_key: str) -> str | None:
    """Join the text of every ``block_type`` block in a content array, or
    the raw string itself when ``content`` is a plain string (the writer
    types user/custom_message content as ``string | blocks``)."""
    if isinstance(content, str):
        return content if block_type == "text" and content else None
    if not isinstance(content, list):
        return None
    texts = [
        str(block[text_key])
        for block in content
        if isinstance(block, dict) and block.get("type") == block_type and block.get(text_key)
    ]
    if not texts:
        return None
    return "\n".join(texts)


def _tool_call_blocks(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "toolCall"]


def _check_header(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The v3 session header, or a loud refusal.

    Refuses on: a v4 header (the unreleased storage rewrite -- see module
    docstring), an unsupported v3-lineage version number, or a first line
    that is not a session header at all.
    """
    first = entries[0]
    if isinstance(first, dict) and first.get("kind") == "header":
        raise ValueError(
            "unsupported pi session format version "
            f"{first.get('v')!r}: this adapter reads the released v3 JSONL "
            "session format; the v4 storage rewrite is not yet supported "
            "(docs/pi-format-notes.md)"
        )
    if not isinstance(first, dict) or first.get("type") != "session":
        raise ValueError("pi session is missing its session header (expected on line 1)")
    version = first.get("version")
    if version not in _SUPPORTED_HEADER_VERSIONS:
        raise ValueError(
            f"unsupported pi session header version {version!r} "
            "(supported: 3, plus auto-migrated 1-2; docs/pi-format-notes.md)"
        )
    return first


def _state_transition_step(
    entry: dict[str, Any], entry_type: str, session_id: str, timestamp: datetime
) -> Step:
    payload: dict[str, Any] = {"kind": entry_type}
    if entry_type == "model_change":
        payload["provider"] = entry.get("provider")
        payload["model_id"] = entry.get("modelId")
    elif entry_type == "thinking_level_change":
        payload["thinking_level"] = entry.get("thinkingLevel")
    elif entry_type == "compaction":
        payload["summary"] = entry.get("summary")
        payload["first_kept_entry_id"] = entry.get("firstKeptEntryId")
    elif entry_type == "branch_summary":
        payload["summary"] = entry.get("summary")
        payload["from_id"] = entry.get("fromId")
    elif entry_type == "custom":
        payload["custom_type"] = entry.get("customType")
        payload["data"] = entry.get("data")
    elif entry_type == "label":
        payload["target_id"] = entry.get("targetId")
        payload["label"] = entry.get("label")
    elif entry_type == "session_info":
        payload["name"] = entry.get("name")
    return Step(
        step_id=str(entry.get("id")),
        parent_step_id=entry.get("parentId"),
        trace_id=session_id,
        timestamp=timestamp,
        actor=Actor.ENVIRONMENT,
        action=Action(type=ActionType.STATE_TRANSITION, payload=payload),
    )


def _message_steps(entry: dict[str, Any], session_id: str, timestamp: datetime) -> list[Step]:
    """One ``message`` entry's steps, dispatched by its message role.

    Defensive throughout (mirrors ``claude_code.py``'s isinstance-checked
    style): a message that is not a dict, or an unknown role, produces no
    steps rather than raising -- best-effort per docs/adapters.md.
    """
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    role = message.get("role")
    entry_id = str(entry.get("id"))
    parent_id = entry.get("parentId")

    if role == "user":
        text = _blocks_text(message.get("content"), "text", "text")
        return [
            Step(
                step_id=entry_id,
                parent_step_id=parent_id,
                trace_id=session_id,
                timestamp=timestamp,
                actor=Actor.USER,
                action=Action(type=ActionType.UTTERANCE, payload={"text": text}),
            )
        ]

    if role == "assistant":
        content = message.get("content")
        narration = _blocks_text(content, "text", "text")
        thinking = _blocks_text(content, "thinking", "thinking")
        # Two-tier precedence: narration wins, thinking is the weaker
        # unverified proxy, and there is no standing-plan tier at all in
        # vanilla pi (module docstring).
        intent = narration or thinking
        steps: list[Step] = []
        previous_id = parent_id
        if narration:
            steps.append(
                Step(
                    step_id=entry_id,
                    parent_step_id=previous_id,
                    trace_id=session_id,
                    timestamp=timestamp,
                    actor=Actor.AGENT,
                    declared_intent=intent,
                    action=Action(type=ActionType.UTTERANCE, payload={"text": narration}),
                )
            )
            previous_id = entry_id
        for index, call in enumerate(_tool_call_blocks(content)):
            call_id = call.get("id")
            step_id = str(call_id) if call_id else f"{entry_id}:call:{index}"
            steps.append(
                Step(
                    step_id=step_id,
                    parent_step_id=previous_id,
                    trace_id=session_id,
                    timestamp=timestamp,
                    actor=Actor.AGENT,
                    declared_intent=intent,
                    action=Action(
                        type=ActionType.TOOL_CALL,
                        payload={
                            "name": call.get("name"),
                            "input": call.get("arguments"),
                            "tool_call_id": call_id,
                        },
                    ),
                )
            )
            previous_id = step_id
        return steps

    if role == "toolResult":
        return [
            Step(
                step_id=entry_id,
                parent_step_id=parent_id,
                trace_id=session_id,
                timestamp=timestamp,
                actor=Actor.ENVIRONMENT,
                action=Action(
                    type=ActionType.ENV_EFFECT,
                    payload={
                        "tool_call_id": message.get("toolCallId"),
                        "tool_name": message.get("toolName"),
                        "tool_result": message.get("content"),
                        "is_error": bool(message.get("isError")),
                    },
                ),
            )
        ]

    if role == "bashExecution":
        # The TUI `!` command: a user-initiated shell execution recorded as
        # call and result in one message -- a role no other shipped format
        # has (writer source: `messages.d.ts:BashExecutionMessage`).
        return [
            Step(
                step_id=entry_id,
                parent_step_id=parent_id,
                trace_id=session_id,
                timestamp=timestamp,
                actor=Actor.USER,
                action=Action(
                    type=ActionType.TOOL_CALL,
                    payload={
                        "name": "bash_execution",
                        "input": {"command": message.get("command")},
                        "output": message.get("output"),
                        "exit_code": message.get("exitCode"),
                        "cancelled": bool(message.get("cancelled")),
                        "truncated": bool(message.get("truncated")),
                    },
                ),
            )
        ]

    return []


def ingest_pi_session(entries: list[dict[str, Any]]) -> Trace:
    """Convert the parsed lines of one pi v3 session JSONL file into a Trace.

    Args:
        entries: The session file's lines as parsed dicts, in file order --
            the header first, then every entry.

    Raises:
        ValueError: If ``entries`` is empty, the header is missing, the
            header is the unreleased v4 shape or an unsupported version, or
            the session data is malformed beyond best-effort processing.
    """
    if not entries:
        raise ValueError("pi session must not be empty")

    header = _check_header(entries)

    try:
        session_id = str(header.get("id") or "unknown")
        metadata: dict[str, Any] = {"cwd": header.get("cwd")}
        if header.get("parentSession"):
            metadata["parent_session"] = header["parentSession"]

        steps: list[Step] = []
        for entry in entries[1:]:
            if not isinstance(entry, dict):
                continue
            entry_type = entry.get("type")
            timestamp = _parse_timestamp(entry.get("timestamp"))
            if entry_type == "message":
                steps.extend(_message_steps(entry, session_id, timestamp))
            elif entry_type == "custom_message":
                text = _blocks_text(entry.get("content"), "text", "text")
                steps.append(
                    Step(
                        step_id=str(entry.get("id")),
                        parent_step_id=entry.get("parentId"),
                        trace_id=session_id,
                        timestamp=timestamp,
                        actor=Actor.ENVIRONMENT,
                        action=Action(
                            type=ActionType.UTTERANCE,
                            payload={"text": text, "custom_type": entry.get("customType")},
                        ),
                    )
                )
            elif entry_type in KNOWN_RECORD_TYPES and entry_type != "session":
                steps.append(_state_transition_step(entry, entry_type, session_id, timestamp))
            # Unknown entry types produce no step; the health canary owns
            # surfacing their share (docs/adapters.md: "surfaced, not
            # silently dropped" is a corpus-level census concern).
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"malformed pi session data: {exc}") from exc

    return Trace(
        trace_id=session_id,
        flow_type=FlowType.GENERIC,
        agent_config_ref=f"pi:{session_id}",
        steps=steps,
        source_adapter="pi",
        metadata=metadata,
    )


# Health-canary declaration (docs/adapters.md's "The health canary"). Pi
# supports the unknown-record-type-share check (real `type` vocabulary,
# transcribed from the installed writer) and the call/result pairing check
# (real verbatim ids); the plan-anchor check's concept does not exist in
# vanilla pi at all, so it is declared unsupported with a reason -- never
# an empty pretend-vocabulary that would fake a pass.


def _pi_record_type(record: dict[str, Any]) -> str | None:
    if not isinstance(record, dict):
        return None
    entry_type = record.get("type")
    return str(entry_type) if entry_type is not None else None


def _pi_message(record: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(record, dict) or record.get("type") != "message":
        return None
    message = record.get("message")
    return message if isinstance(message, dict) else None


def _pi_call_ids(record: dict[str, Any]) -> list[str | None]:
    """The id of every toolCall block on one assistant message entry."""
    message = _pi_message(record)
    if message is None or message.get("role") != "assistant":
        return []
    calls = _tool_call_blocks(message.get("content"))
    return [str(c["id"]) if c.get("id") else None for c in calls]


def _pi_result_ref_ids(record: dict[str, Any]) -> list[str | None]:
    """The ``toolCallId`` one toolResult message claims to resolve --
    always a length-1 list for a toolResult, ``[None]`` if the id itself
    is missing (count the record, note the missing id)."""
    message = _pi_message(record)
    if message is None or message.get("role") != "toolResult":
        return []
    value = message.get("toolCallId")
    return [str(value) if value else None]


def _pi_pairing_boundary(record: dict[str, Any]) -> bool:
    """A user message entry is the id-less fallback's hard boundary: a new
    user turn is the clearest "the previous call round is over" signal the
    format carries (the Claude Code/Hermes choice)."""
    message = _pi_message(record)
    return message is not None and message.get("role") == "user"


def _pi_call_names(record: dict[str, Any]) -> list[str]:
    message = _pi_message(record)
    if message is None or message.get("role") != "assistant":
        return []
    names: list[str] = []
    for call in _tool_call_blocks(message.get("content")):
        name = call.get("name")
        if name:
            names.append(str(name))
    return names


PI_HEALTH_DECLARATION = HealthDeclaration(
    name="pi",
    record_type=_pi_record_type,
    known_record_types=KNOWN_RECORD_TYPES,
    call_ids=_pi_call_ids,
    result_ref_ids=_pi_result_ref_ids,
    pairing_boundary=_pi_pairing_boundary,
    call_names=_pi_call_names,
    plan_anchor_supported=False,
    plan_anchor_skip_reason=(
        "vanilla pi has no plan/todo tracking tool at all (the author's own "
        "design decision -- docs/pi-format-notes.md): there is no anchor "
        "vocabulary to check a standing plan against"
    ),
)


class PiTraceAdapter:
    """Structural TraceAdapter for pi v3 session-file line lists.

    Set ``strip_payloads=True`` to redact TOOL_CALL/ENV_EFFECT steps'
    content-bearing payload keys via the shared post-ingest redaction pass
    (``auditk.adapters.redaction.redact_trace`` -- see module docstring).
    """

    def __init__(self, strip_payloads: bool = False) -> None:
        self.strip_payloads = strip_payloads

    def ingest(self, raw: Any) -> Trace:
        trace = ingest_pi_session(raw)
        if self.strip_payloads:
            trace = redact_trace(trace, REDACTION_CONTENT_KEYS)
        return trace
