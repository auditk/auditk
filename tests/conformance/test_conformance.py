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
                          adapter has no hook (`kit.HealthFixture` is None).
- health unknown-type-share -- same xfail rule as pairing invariants.

Every `xfail` below carries a reason pointing at docs/adapters.md's "Known
contract gaps" section and the P1 report -- never a silent skip, never a
bare `pytest.mark.xfail` with no explanation.
"""

from __future__ import annotations

import pytest

from auditk.adapters.health import SessionHealthInput, check_adapter_health
from auditk.schema import Trace
from tests.conformance.kit import AdapterConformanceFixtures, xfail_reason
from tests.conformance.providers import PROVIDERS


def _id(fixtures: AdapterConformanceFixtures) -> str:
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
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.id_matched_paired_events)]
        )
        assert result.ok, result.breaches

    def test_id_less_trailing_in_flight_is_excused(
        self, fixtures: AdapterConformanceFixtures
    ) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary pairing"))
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.id_less_trailing_events)]
        )
        assert result.ok, result.breaches

    def test_id_matched_genuine_orphan_breaches(self, fixtures: AdapterConformanceFixtures) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary pairing"))
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.id_matched_orphan_events)]
        )
        assert not result.ok


@pytest.mark.parametrize("fixtures", PROVIDERS, ids=_id)
class TestHealthUnknownTypeShare:
    def test_unknown_type_share_over_threshold_breaches(
        self, fixtures: AdapterConformanceFixtures
    ) -> None:
        if fixtures.health is None:
            pytest.xfail(xfail_reason(fixtures, "health-canary record-type allow-list"))
        result = check_adapter_health(
            [SessionHealthInput(events=fixtures.health.unknown_type_share_events)]
        )
        assert not result.ok
