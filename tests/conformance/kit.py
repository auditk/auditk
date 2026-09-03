# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""The adapter conformance kit's opt-in shape (P1 of the adapter-contract plan).

`tests/conformance/test_conformance.py` is a single parametrised suite that
runs the same cases against every registered `TraceAdapter`. An adapter opts
in by building one `AdapterConformanceFixtures` (see
`tests/conformance/providers.py` for the three shipped adapters) and adding
it to `providers.PROVIDERS`.

Two of the four field groups (`redaction`, `health`) are `Optional` by
design: not every adapter has a redaction hook or a health-canary hook today
(see docs/adapters.md's "Known contract gaps" section, and the P1 report).
An adapter that has neither still opts in with an `AdapterConformanceFixtures`
whose `redaction`/`health` are `None` -- the suite marks those specific cases
`xfail` (never skip, never a silent pass) rather than refusing to run at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from auditk.adapters.protocols import TraceAdapter
from auditk.schema import Trace


@dataclass(frozen=True)
class RedactionFixture:
    """What `TestRedactionPassThrough` needs to exercise one adapter's
    redaction pass-through (the CC adapter's `strip_payloads`/`_maybe_redact`
    generalised, per docs/adapters.md's "Redaction" section)."""

    redacting_adapter: TraceAdapter
    native: Any
    assert_redacted: Callable[[Trace], None]


@dataclass(frozen=True)
class HealthFixture:
    """Raw event lists shaped for `auditk.adapters.health.SessionHealthInput`,
    covering the four pairing/unknown-type-share cases `TestHealthPairing`
    and `TestHealthUnknownTypeShare` assert against. Only meaningful for an
    adapter whose native format is the Claude-Code raw-JSONL shape
    `adapters/health.py` actually reads (`event["type"] in {"assistant",
    "user"}`, `message.content[*].type in {"tool_use", "tool_result"}`) --
    see docs/adapters.md for why this is not yet adapter-agnostic.
    """

    id_matched_paired_events: list[dict[str, Any]]
    id_less_trailing_events: list[dict[str, Any]]
    id_matched_orphan_events: list[dict[str, Any]]
    unknown_type_share_events: list[dict[str, Any]]


@dataclass(frozen=True)
class AdapterConformanceFixtures:
    """One adapter's opt-in into the conformance kit.

    `empty_native`/`malformed_native`/`minimal_valid_native` are required --
    every adapter must have an opinion on all three (see docs/adapters.md's
    "empty-input"/"malformed-input" sections for the exact invariant each is
    checked against). `redaction`/`health` are `None` when the adapter has no
    hook for that case yet.
    """

    name: str
    adapter: TraceAdapter
    empty_native: Any
    malformed_native: Any
    minimal_valid_native: Any
    redaction: RedactionFixture | None = None
    health: HealthFixture | None = None


def xfail_reason(fixtures: AdapterConformanceFixtures, case: str) -> str:
    """Shared wording for the contract-gap `pytest.xfail()` calls in
    test_conformance.py -- referenced from the P1 conformance report so a
    reader can find why a given adapter/case pair is xfail'd rather than
    passing."""
    return (
        f"{fixtures.name}: no {case} hook for this adapter today -- documented "
        "contract gap, not a bug (see docs/adapters.md 'Known contract gaps' "
        "and the P1 adapter-conformance-kit report's xfail'd-gaps section)."
    )
