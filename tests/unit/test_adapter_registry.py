"""Unit tests for the adapter registry."""

import pytest

from auditk.adapters import get_adapter


def test_get_adapter_generic_otel_has_ingest():
    adapter = get_adapter("generic-otel")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_langgraph_has_ingest():
    adapter = get_adapter("langgraph")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_claude_code_has_ingest():
    adapter = get_adapter("claude-code")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_hermes_has_ingest():
    adapter = get_adapter("hermes")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_hermes_strip_payloads_has_ingest():
    adapter = get_adapter("hermes", strip_payloads=True)
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_unknown_raises_key_error_with_available_names():
    with pytest.raises(KeyError) as exc_info:
        get_adapter("unknown-name")
    message = str(exc_info.value)
    assert "unknown-name" in message
    assert "generic-otel" in message
    assert "langgraph" in message


def test_get_adapter_pi_has_ingest():
    adapter = get_adapter("pi")
    trace = adapter.ingest(
        [
            {
                "type": "session",
                "version": 3,
                "id": "reg-pi-1",
                "timestamp": "2026-09-07T12:00:00.000Z",
                "cwd": "/home/user/project",
            },
            {
                "type": "message",
                "id": "aa000001",
                "parentId": None,
                "timestamp": "2026-09-07T12:00:01.000Z",
                "message": {"role": "user", "content": "hello", "timestamp": 0},
            },
        ]
    )
    assert trace.source_adapter == "pi"


def test_get_adapter_pi_strip_payloads_has_ingest():
    adapter = get_adapter("pi", strip_payloads=True)
    assert hasattr(adapter, "ingest")


def test_pi_health_declaration_registered():
    from auditk.adapters.registry import get_health_declaration

    declaration = get_health_declaration("pi")
    assert declaration is not None
    assert declaration.name == "pi"
    # Real call/result ids exist in the format; no plan-anchor tool does
    # (vanilla pi has no todo/plan tool -- docs/pi-format-notes.md).
    assert declaration.pairing_supported is True
    assert declaration.plan_anchor_supported is False
    assert declaration.plan_anchor_skip_reason
