"""Unit tests for the adapter registry."""

import pytest

from glasshouse_core.adapters import get_adapter


def test_get_adapter_generic_otel_has_ingest():
    adapter = get_adapter("generic-otel")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_langgraph_has_ingest():
    adapter = get_adapter("langgraph")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_claude_code_has_ingest():
    adapter = get_adapter("claude-code")
    assert callable(getattr(adapter, "ingest", None))


def test_get_adapter_unknown_raises_key_error_with_available_names():
    with pytest.raises(KeyError) as exc_info:
        get_adapter("unknown-name")
    message = str(exc_info.value)
    assert "unknown-name" in message
    assert "generic-otel" in message
    assert "langgraph" in message
