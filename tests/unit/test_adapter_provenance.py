# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the trace-provenance declaration (follow-up to PR #15's
forged-walk demo -- see `auditk.adapters.provenance` module docstring).

Covers: the `TraceProvenance` enum and `ProvenanceDeclaration` dataclass
themselves, every shipped adapter's own declared classification + reason,
and the registry surfacing (`get_provenance_declaration`). The conformance
kit's cross-adapter "every adapter has one, unconditionally" assertion
lives in `tests/conformance/test_conformance.py::TestProvenanceDeclaration`
instead -- this module only pins each adapter's *specific* classification.
"""

from __future__ import annotations

import pytest

from auditk.adapters.claude_code import (
    CLAUDE_CODE_PROVENANCE_DECLARATION,
    ClaudeCodeTraceAdapter,
)
from auditk.adapters.generic_otel import (
    GENERIC_OTEL_PROVENANCE_DECLARATION,
    OtelTraceAdapter,
)
from auditk.adapters.hermes import HERMES_PROVENANCE_DECLARATION, HermesTraceAdapter
from auditk.adapters.langgraph import (
    LANGGRAPH_PROVENANCE_DECLARATION,
    LangGraphTraceAdapter,
)
from auditk.adapters.pi import PI_PROVENANCE_DECLARATION, PiTraceAdapter
from auditk.adapters.provenance import ProvenanceDeclaration, TraceProvenance
from auditk.adapters.registry import get_provenance_declaration


def test_trace_provenance_has_three_values() -> None:
    assert {p.value for p in TraceProvenance} == {
        "scheduler-derived",
        "self-reported",
        "unknown",
    }


def test_provenance_declaration_requires_non_empty_reason() -> None:
    with pytest.raises(ValueError, match="non-empty reason"):
        ProvenanceDeclaration(name="x", provenance=TraceProvenance.UNKNOWN, reason="   ")


def test_provenance_declaration_accepts_a_real_reason() -> None:
    declaration = ProvenanceDeclaration(
        name="x", provenance=TraceProvenance.UNKNOWN, reason="because reasons"
    )
    assert declaration.provenance is TraceProvenance.UNKNOWN


# --- claude-code: scheduler-derived ---------------------------------------


def test_claude_code_is_scheduler_derived() -> None:
    assert CLAUDE_CODE_PROVENANCE_DECLARATION.provenance is TraceProvenance.SCHEDULER_DERIVED
    assert CLAUDE_CODE_PROVENANCE_DECLARATION.reason.strip()


def test_claude_code_adapter_exposes_its_declaration() -> None:
    adapter = ClaudeCodeTraceAdapter()
    assert adapter.provenance_declaration is CLAUDE_CODE_PROVENANCE_DECLARATION


# --- langgraph: unknown ----------------------------------------------------


def test_langgraph_is_unknown() -> None:
    assert LANGGRAPH_PROVENANCE_DECLARATION.provenance is TraceProvenance.UNKNOWN
    assert LANGGRAPH_PROVENANCE_DECLARATION.reason.strip()


def test_langgraph_adapter_exposes_its_declaration() -> None:
    adapter = LangGraphTraceAdapter()
    assert adapter.provenance_declaration is LANGGRAPH_PROVENANCE_DECLARATION


# --- generic-otel: unknown ---------------------------------------------


def test_generic_otel_is_unknown() -> None:
    assert GENERIC_OTEL_PROVENANCE_DECLARATION.provenance is TraceProvenance.UNKNOWN
    assert GENERIC_OTEL_PROVENANCE_DECLARATION.reason.strip()


def test_generic_otel_adapter_exposes_its_declaration() -> None:
    adapter = OtelTraceAdapter()
    assert adapter.provenance_declaration is GENERIC_OTEL_PROVENANCE_DECLARATION


# --- hermes: unknown ------------------------------------------------------


def test_hermes_is_unknown() -> None:
    assert HERMES_PROVENANCE_DECLARATION.provenance is TraceProvenance.UNKNOWN
    assert HERMES_PROVENANCE_DECLARATION.reason.strip()


def test_hermes_adapter_exposes_its_declaration() -> None:
    adapter = HermesTraceAdapter()
    assert adapter.provenance_declaration is HERMES_PROVENANCE_DECLARATION


# --- pi: unknown -----------------------------------------------------------


def test_pi_is_unknown() -> None:
    assert PI_PROVENANCE_DECLARATION.provenance is TraceProvenance.UNKNOWN
    assert PI_PROVENANCE_DECLARATION.reason.strip()


def test_pi_adapter_exposes_its_declaration() -> None:
    adapter = PiTraceAdapter()
    assert adapter.provenance_declaration is PI_PROVENANCE_DECLARATION


# --- registry surfacing ----------------------------------------------------


def test_get_provenance_declaration_known_adapters() -> None:
    for name, expected in [
        ("claude-code", CLAUDE_CODE_PROVENANCE_DECLARATION),
        ("langgraph", LANGGRAPH_PROVENANCE_DECLARATION),
        ("generic-otel", GENERIC_OTEL_PROVENANCE_DECLARATION),
        ("hermes", HERMES_PROVENANCE_DECLARATION),
        ("pi", PI_PROVENANCE_DECLARATION),
    ]:
        assert get_provenance_declaration(name) is expected


def test_get_provenance_declaration_unknown_adapter_name_is_none() -> None:
    assert get_provenance_declaration("not-a-real-adapter") is None
