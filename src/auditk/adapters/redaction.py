# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Shared, adapter-generic payload-redaction pass (P1b gap 1).

Before this module, only `ClaudeCodeTraceAdapter` could honour
`strip_payloads`: `claude_code.py:_maybe_redact` is applied inline, at
Step-construction time, keyed to Claude Code's own payload shape
(`tool_call.action.payload["input"]`, `env_effect.action.payload
["tool_result"]`). LangGraph and generic-otel had no equivalent hook at
all, so `auditk ingest --strip-payloads` was a silent no-op for them (see
docs/adapters.md's former "Known contract gaps" #2).

Claude Code's own mechanism is left exactly as it is -- it is already
correct and already pinned by `tests/conformance/providers.py`'s
`_cc_redaction_fixture` and the integration-test suite, and rewriting it
to route through this module would touch a lot of already-working,
well-tested code for no behavioural gain. This module is instead the
NEW, shared, adapter-generic mechanism the *other* adapters (and any
future one) plug into rather than reimplementing their own
`_maybe_redact`: one implementation, applied POST-INGEST over the
already-mapped `Trace`, driven by a small per-adapter declaration of
which `Action.payload` keys hold redactable content for which
`ActionType`. Names, kinds, ids, and timing survive; the declared
content keys' values do not -- the same guarantee Claude Code's own
redaction makes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from auditk.schema import ActionType, Trace

REDACTION_KEY = "redacted"

ContentKeysByActionType = Mapping[ActionType, frozenset[str]]


def redact_value(value: Any) -> dict[str, Any]:
    """The shared redacted-value shape: `{"redacted": True, "size": N}`.

    Same shape and same `len(str(value))` sizing convention as Claude
    Code's own `claude_code.py:_maybe_redact`, so a consumer of a `Trace`
    can't tell which adapter produced a given redacted field from its
    shape alone.
    """
    return {REDACTION_KEY: True, "size": len(str(value)) if value is not None else 0}


def redact_trace(trace: Trace, content_keys: ContentKeysByActionType) -> Trace:
    """Return a copy of `trace` with every step's declared content-bearing
    payload keys redacted.

    For each step, `content_keys.get(step.action.type)` names which
    `Action.payload` keys hold redactable content for that action type
    (e.g. Claude Code's own convention: `{"input"}` for `TOOL_CALL`,
    `{"tool_result"}` for `ENV_EFFECT`). Payload keys not listed --
    names, node/span kinds, error flags, ids -- survive untouched. An
    action type with no entry in `content_keys` (or an empty one) is left
    completely alone, so a format with nothing sensitive to redact for a
    given action type (e.g. `STATE_TRANSITION`'s bare node name) needs no
    special-casing here.

    `trace` and its steps are not mutated -- this returns new Pydantic
    model instances built via `model_copy(update=...)`, matching the
    immutable-by-convention style the rest of the schema module uses.
    """
    redacted_steps = []
    for step in trace.steps:
        keys = content_keys.get(step.action.type)
        if not keys:
            redacted_steps.append(step)
            continue
        new_payload = dict(step.action.payload)
        for key in keys:
            if key in new_payload:
                new_payload[key] = redact_value(new_payload[key])
        redacted_steps.append(
            step.model_copy(
                update={"action": step.action.model_copy(update={"payload": new_payload})}
            )
        )
    return trace.model_copy(update={"steps": redacted_steps})
