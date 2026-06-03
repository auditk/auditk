"""Unit tests for the HTTP prober (httpx + tenacity + respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from auditk.adapters.protocols import Stimulus
from auditk.probes.http_prober import HttpProber


@respx.mock
def test_successful_post_returns_probe_response() -> None:
    respx.post("http://test/agent").mock(
        return_value=httpx.Response(200, json={"text": "Hello"})
    )
    prober = HttpProber("http://test/agent")
    response = prober.send(Stimulus(channel="user_message", payload={"msg": "hi"}))
    assert response.text == "Hello"
    assert response.latency_ms >= 0


@respx.mock
def test_response_without_text_key_uses_raw_str() -> None:
    respx.post("http://test/agent").mock(
        return_value=httpx.Response(200, json={"message": "Hi"})
    )
    prober = HttpProber("http://test/agent")
    response = prober.send(Stimulus(channel="user_message", payload={"msg": "hi"}))
    assert response.text == "Hi"


@respx.mock
def test_http_error_raises() -> None:
    # Tenacity retries 3 times total (initial + 2 retries)
    route = respx.post("http://test/agent").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(500),
            httpx.Response(500),
        ]
    )
    prober = HttpProber("http://test/agent")
    with pytest.raises(httpx.HTTPStatusError):
        prober.send(Stimulus(channel="user_message", payload={"msg": "hi"}))
    assert route.call_count == 3


def test_timeout_is_passed_to_httpx() -> None:
    prober = HttpProber("http://x", timeout=5.0)
    assert prober.timeout == 5.0
