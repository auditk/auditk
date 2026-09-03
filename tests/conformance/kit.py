# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""The adapter conformance kit's opt-in shape (P1 of the adapter-contract plan).

`tests/conformance/test_conformance.py` is a single parametrised suite that
runs the same cases against every registered `TraceAdapter`. An adapter opts
in by building one `AdapterConformanceFixtures` (see
`tests/conformance/providers.py` for the three shipped adapters) and adding
it to `providers.PROVIDERS`.

Two of the four field groups (`redaction`, `health`) are `Optional` by
design: an adapter with genuinely no hook for one of them yet still opts in
with an `AdapterConformanceFixtures` whose `redaction`/`health` is `None` --
the suite marks those specific cases `xfail` (never skip, never a silent
pass) rather than refusing to run at all. As of P1b, all three shipped
adapters (`claude-code`, `langgraph`, `generic-otel`) have both hooks, so no
`xfail` is reachable today for them; `redaction`/`health` staying `Optional`
is what lets a fourth, out-of-tree adapter opt in early without writing a
redaction hook or a health `HealthDeclaration` on day one. Within `health`,
a hook may still exist but say a given sub-check's *concept* doesn't apply
to this format (`HealthDeclaration.pairing_supported=False`, for example) --
that is a documented SKIP, not an `xfail`; see docs/adapters.md's "Known
contract gaps" section for both distinctions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from auditk.adapters.health import HealthDeclaration
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
    """One adapter's own-native-format event lists, plus its
    `HealthDeclaration`, covering the pairing/unknown-type-share cases
    `TestHealthPairingInvariants` and `TestHealthUnknownTypeShare` assert
    against (P1b gap 2: the canary is no longer Claude-Code-shape-specific
    -- `declaration` is what tells `check_adapter_health` how to read
    THIS adapter's own record shape, so every field below is built from
    that adapter's own native records, not Claude Code's raw JSONL).

    `declaration.pairing_supported`/`declaration.unknown_type_share_supported`
    tell the suite whether a given case is even applicable to this format
    at all -- when False, the suite treats it as a documented, reasoned
    SKIP (using `declaration.pairing_skip_reason`/
    `unknown_type_share_skip_reason`), never a fake pass and never an
    `xfail` (an `xfail` would say "this SHOULD pass but doesn't yet"; a
    format that genuinely has no such concept should say so plainly
    instead -- see docs/adapters.md's "Known contract gaps").
    """

    declaration: HealthDeclaration
    id_matched_paired_events: list[dict[str, Any]]
    id_less_trailing_events: list[dict[str, Any]]
    id_matched_orphan_events: list[dict[str, Any]]
    unknown_type_share_events: list[dict[str, Any]]


@dataclass(frozen=True)
class RefusingAdapterFixtures:
    """A stub adapter that refuses on every entry point, regardless of input
    shape -- e.g. the Pi adapter, gated on sample traces (see
    docs/pi-format-notes.md).

    Deliberately NOT an `AdapterConformanceFixtures`: that shape's
    `TestMinimalValidIngest` case asserts `ingest()` succeeds on
    `minimal_valid_native`, which a loud-refusing stub by definition never
    does. `providers.REFUSING_PROVIDERS` is this dataclass's own
    parametrisation list, checked by a separate test class in
    `test_conformance.py` that asserts the OPPOSITE invariant from the
    normal suite: every case refuses, with the expected message, every
    time -- including a `minimal_valid_native`-shaped input, which is
    exactly the point (proves the refusal isn't conditional on input shape
    at all). Flipping a stub to a real adapter later means consciously
    deleting its entry here and adding a normal `AdapterConformanceFixtures`
    to `PROVIDERS` instead, which forces rewriting these assertions rather
    than letting a real adapter silently inherit "always refuses".
    """

    name: str
    adapter: TraceAdapter
    empty_native: Any
    malformed_native: Any
    minimal_valid_native: Any
    expected_message_fragment: str


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
