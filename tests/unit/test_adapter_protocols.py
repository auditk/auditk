"""Unit tests for adapter protocol definitions (T2.1).

Protocol classes use structural subtyping (PEP 544) without runtime_checkable,
so structural conformance is validated via typed helper functions that mypy
checks statically.
"""

from __future__ import annotations

from typing import Any

from auditk.adapters import (
    AgentConfigLoader,
    EndpointProber,
    EvidenceStore,
    ProbeResponse,
    Signer,
    Stimulus,
    TraceAdapter,
)
from auditk.schema import (
    FlowType,
    Outcome,
    Trace,
)

# ---------------------------------------------------------------------------
# Importability checks
# ---------------------------------------------------------------------------


def test_protocol_classes_importable() -> None:
    assert TraceAdapter is not None
    assert AgentConfigLoader is not None
    assert EndpointProber is not None
    assert Signer is not None
    assert EvidenceStore is not None


def test_value_objects_importable() -> None:
    assert Stimulus is not None
    assert ProbeResponse is not None


# ---------------------------------------------------------------------------
# Value-object instantiation
# ---------------------------------------------------------------------------


def test_stimulus_construction() -> None:
    s = Stimulus(channel="user_message", payload={"text": "hello"})
    assert s.channel == "user_message"
    assert s.payload == {"text": "hello"}


def test_probe_response_construction() -> None:
    r = ProbeResponse(text="ok", raw={"status": 200}, latency_ms=12.5)
    assert r.latency_ms == 12.5


# ---------------------------------------------------------------------------
# Structural conformance — validated statically by mypy
#
# Each helper accepts the Protocol type; passing a concrete implementation
# that has the right method signatures is the structural check.
# ---------------------------------------------------------------------------


class _ConcreteTraceAdapter:
    """Minimal concrete class that satisfies TraceAdapter structurally."""

    def ingest(self, raw: Any) -> Trace:
        return Trace(
            trace_id="t-1",
            flow_type=FlowType.GENERIC,
            agent_config_ref="cfg-1",
            steps=[],
            source_adapter="test",
            outcome=Outcome(status="success"),
        )


def _accepts_trace_adapter(adapter: TraceAdapter) -> None:
    """mypy checks that the argument satisfies TraceAdapter."""
    pass


def test_concrete_class_satisfies_trace_adapter_protocol() -> None:
    concrete = _ConcreteTraceAdapter()
    # Static check: mypy will flag this call if _ConcreteTraceAdapter is
    # structurally incompatible with TraceAdapter.
    _accepts_trace_adapter(concrete)
    # Runtime check: the method exists and is callable.
    result = concrete.ingest({"span": "data"})
    assert result.trace_id == "t-1"
