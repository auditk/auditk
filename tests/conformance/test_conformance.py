# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""The adapter conformance kit (P1 of the adapter-contract plan).

Runs the same cases against every adapter registered in
`tests/conformance/providers.PROVIDERS`, so a third party adding an adapter
can point this suite at their own `AdapterConformanceFixtures` and know
whether they satisfy the contract documented in docs/adapters.md.

Case list (mirrors docs/adapters.md's "Conformance" section):

- empty-input        -- refuses via ValueError, never silently returns.
- malformed-input     -- refuse-don't-raise: either refuses via ValueError,
                          or processes best-effort and still returns a
                          Trace; never leaks an undocumented exception type.
- minimal-valid ingest -- smoke check underlying every case below: the
                          adapter's opt-in fixture actually round-trips.
- redaction pass-through -- xfail'd where the adapter has no hook (`kit.
                          RedactionFixture`) is None.
- health pairing invariants (id-matched + id-less) -- xfail'd where the
                          adapter has no `kit.HealthFixture` at all (no
                          declaration written yet); SKIPPED, with the
                          declaration's own reason, where a declaration
                          exists but says this format has no id-pairing
                          concept (`HealthDeclaration.pairing_supported`
                          is False -- true for both langgraph and
                          generic-otel as of P1b, see docs/adapters.md).
- health unknown-type-share -- same xfail-vs-skip rule, driven by
                          `HealthDeclaration.unknown_type_share_supported`.
                          All three shipped adapters support this one.

Every `xfail`/`skip` below carries a reason pointing at docs/adapters.md's
"Known contract gaps" section (xfail: P1's report; skip: the adapter's own
`HealthDeclaration`) -- never silent, never a bare `pytest.mark.xfail`/
`pytest.skip()` with no explanation.
"""

from __future__ import annotations

import pytest

from auditk.adapters.health import SessionHealthInput, check_adapter_health
from auditk.schema import Trace
from tests.conformance.kit import AdapterConformanceFixtures, RefusingAdapterFixtures, xfail_reason
from tests.conformance.providers import PROVIDERS, REFUSING_PROVIDERS


def _id(fixtures: AdapterConformanceFixtures) -> str:
    return fixtures.name


def _refusing_id(fixtures: RefusingAdapterFixtures) -> str:
    return fixtures.name


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestEmptyInput:
    def test_refuses_via_value_error(self, fixtures: AdapterConformanceFixtures) -> None:
        with pytest.raises(ValueError):
            fixtures.adapter.ingest(fixtures.empty_native)


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestMalformedInput:
    def test_refuses_cleanly_or_processes_best_effort(
        self, fixtures: AdapterConformanceFixtures
    ) -> None:
        """MUST NOT: malformed input crashes with anything other than
        ValueError. The adapter MAY either refuse (raise ValueError) or
        process best-effort (return a Trace) -- both are contract-compliant,
        see docs/adapters.md's "malformed-input" section."""
        try:
            result = fixtures.adapter.ingest(fixtures.malformed_native)
        except ValueError:
            return
        assert isinstance(result, Trace)


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestMinimalValidIngest:
    def test_ingests_cleanly(self, fixtures: AdapterConformanceFixtures) -> None:
        trace = fixtures.adapter.ingest(fixtures.minimal_valid_native)
        assert isinstance(trace, Trace)
        assert len(trace.steps) >= 1
        assert trace.source_adapter


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestRedactionPassThrough:
    def test_redaction_strips_payloads(self, fixtures: AdapterConformanceFixtures) -> None:
        if fixtures.redaction is None:
            pytest.xfail(xfail_reason(fixtures, "redaction"))
        trace = fixtures.redaction.redacting_adapter.ingest(fixtures.redaction.native)
        fixtures.redaction.assert_redacted(trace)


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestHealthPairingInvariants:
    def test_id_matched_fully_paired_is_healthy(self, fixtures: AdapterConformanceFixtures) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary pairing"))
        if not fixtures.health.declaration.pairing_supported:
            pytest.skip(fixtures.health.declaration.pairing_skip_reason or "pairing not supported")
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.id_matched_paired_events)],
            declaration=fixtures.health.declaration,
        )
        assert result.ok, result.breaches

    def test_id_less_trailing_in_flight_is_excused(
        self, fixtures: AdapterConformanceFixtures
    ) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary pairing"))
        if not fixtures.health.declaration.pairing_supported:
            pytest.skip(fixtures.health.declaration.pairing_skip_reason or "pairing not supported")
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.id_less_trailing_events)],
            declaration=fixtures.health.declaration,
        )
        assert result.ok, result.breaches

    def test_id_matched_genuine_orphan_breaches(self, fixtures: AdapterConformanceFixtures) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary pairing"))
        if not fixtures.health.declaration.pairing_supported:
            pytest.skip(fixtures.health.declaration.pairing_skip_reason or "pairing not supported")
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.id_matched_orphan_events)],
            declaration=fixtures.health.declaration,
        )
        assert not result.ok


@pytest.mark.parametrize("fixtures", REFUSING_PROVIDERS, ids=_refusing_id)
class TestGatedStubAlwaysRefuses:
    """A gated stub adapter (`pi`, see docs/pi-format-notes.md) is
    deliberately NOT in `PROVIDERS` -- see `kit.RefusingAdapterFixtures`'s
    docstring for why `TestMinimalValidIngest` above cannot cover it (that
    case asserts `ingest()` *succeeds*).

    This class asserts the opposite invariant on purpose: every entry point
    refuses, with the documented message, on every input shape -- empty,
    malformed, AND an otherwise minimal-valid-*looking* input alike. The
    third case is the one that actually distinguishes a loud stub from a
    real adapter: a real adapter's `minimal_valid_native` ingests cleanly;
    a gated stub's does not, because nothing about the input shape matters
    to it at all. When this stub becomes a real adapter, this whole class
    (and the `REFUSING_PROVIDERS` entry it parametrises over) must be
    deleted and replaced with a normal `PROVIDERS` entry -- that's a
    deliberate one-way door, not an oversight.
    """

    def test_empty_native_refuses_with_expected_message(
        self, fixtures: RefusingAdapterFixtures
    ) -> None:
        with pytest.raises(ValueError, match=fixtures.expected_message_fragment):
            fixtures.adapter.ingest(fixtures.empty_native)

    def test_malformed_native_refuses_with_expected_message(
        self, fixtures: RefusingAdapterFixtures
    ) -> None:
        with pytest.raises(ValueError, match=fixtures.expected_message_fragment):
            fixtures.adapter.ingest(fixtures.malformed_native)

    def test_minimal_valid_looking_native_still_refuses(
        self, fixtures: RefusingAdapterFixtures
    ) -> None:
        with pytest.raises(ValueError, match=fixtures.expected_message_fragment):
            fixtures.adapter.ingest(fixtures.minimal_valid_native)


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestHealthUnknownTypeShare:
    def test_unknown_type_share_over_threshold_breaches(
        self, fixtures: AdapterConformanceFixtures
    ) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary record-type allow-list"))
        if not fixtures.health.declaration.unknown_type_share_supported:
            pytest.skip(
                fixtures.health.declaration.unknown_type_share_skip_reason
                or "unknown-type-share not supported"
            )
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.unknown_type_share_events)],
            declaration=fixtures.health.declaration,
        )
        assert not result.ok
