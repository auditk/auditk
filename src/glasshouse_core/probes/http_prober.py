"""HTTP endpoint prober — sends probe stimuli via POST and returns responses."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from tenacity import retry, stop_after_attempt, wait_exponential

from glasshouse_core.adapters.protocols import ProbeResponse, Stimulus

if TYPE_CHECKING:
    pass


class HttpProber:
    """Implements EndpointProber for HTTP/JSON agents.

    Uses httpx for requests (lazy import to avoid requiring the extra at import time).
    Retries up to 3 times with exponential backoff via tenacity.
    """

    def __init__(self, endpoint: str, timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def send(self, stimulus: Stimulus) -> ProbeResponse:
        return self._send_with_retry(stimulus)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _send_with_retry(self, stimulus: Stimulus) -> ProbeResponse:
        import httpx  # lazy import — only needed at call time

        start = time.monotonic()
        response = httpx.post(
            self.endpoint,
            json=stimulus.payload,
            timeout=self.timeout,
        )
        latency_ms = (time.monotonic() - start) * 1000
        response.raise_for_status()
        raw: dict[str, Any] = response.json()
        text: str = raw.get("text") or raw.get("message") or str(raw)
        return ProbeResponse(text=text, raw=raw, latency_ms=latency_ms)
