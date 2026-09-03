# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the Pi adapter stub (P3): gated on sample traces, every
entry point refuses loudly -- see `auditk.adapters.pi` / docs/pi-format-notes.md.

This module pins the adapter-level contract; `tests/conformance/
test_conformance.py::TestGatedStubAlwaysRefuses` pins the same behaviour
through the conformance kit's own `REFUSING_PROVIDERS`, and
`tests/unit/test_cli_pi_stub.py` pins it at the CLI boundary.
"""

from __future__ import annotations

import pytest

from auditk.adapters import get_adapter
from auditk.adapters.pi import PI_GATED_MESSAGE, PiAdapterGatedError, PiTraceAdapter


class TestPiAdapterGatedError:
    def test_is_a_value_error(self) -> None:
        """A ValueError subclass -- flows through the same `except
        (KeyError, ValueError)` CLI handling every other adapter's
        malformed-input refusal does (see cli.py)."""
        assert issubclass(PiAdapterGatedError, ValueError)

    def test_default_message_points_at_the_format_notes(self) -> None:
        error = PiAdapterGatedError()
        assert "docs/pi-format-notes.md" in str(error)
        assert "gated on sample traces" in str(error)


class TestPiTraceAdapterRefusesLoudly:
    """Every entry point refuses regardless of `raw`'s shape -- there is no
    code path that inspects `raw` at all."""

    @pytest.mark.parametrize(
        "raw",
        [
            [],
            None,
            {"not": "a list"},
            [{"type": "session", "version": 3, "id": "sess-1"}],
            "not even a list or dict",
        ],
    )
    def test_ingest_always_raises_pi_adapter_gated_error(self, raw: object) -> None:
        adapter = PiTraceAdapter()
        with pytest.raises(PiAdapterGatedError) as exc_info:
            adapter.ingest(raw)
        assert str(exc_info.value) == PI_GATED_MESSAGE

    def test_strip_payloads_flag_has_no_effect_on_the_refusal(self) -> None:
        """`strip_payloads=True` is accepted for shape-parity with the other
        adapters' constructors, but there is nothing to redact -- ingest()
        still refuses identically."""
        adapter = PiTraceAdapter(strip_payloads=True)
        with pytest.raises(PiAdapterGatedError):
            adapter.ingest([{"type": "session"}])


class TestPiAdapterRegistration:
    def test_get_adapter_pi_has_ingest(self) -> None:
        adapter = get_adapter("pi")
        assert callable(getattr(adapter, "ingest", None))

    def test_get_adapter_pi_ingest_refuses(self) -> None:
        adapter = get_adapter("pi")
        with pytest.raises(PiAdapterGatedError):
            adapter.ingest([])

    def test_get_adapter_pi_strip_payloads_still_refuses(self) -> None:
        """`pi` is registered in `_FACTORIES` too (unlike a genuinely
        can't-redact adapter) so `--strip-payloads` surfaces the SAME gated
        message rather than a generic "no redaction support" one."""
        adapter = get_adapter("pi", strip_payloads=True)
        with pytest.raises(PiAdapterGatedError):
            adapter.ingest([])

    def test_pi_has_no_health_declaration(self) -> None:
        """No real record shape exists to declare a canary against yet --
        see docs/pi-format-notes.md."""
        from auditk.adapters.registry import get_health_declaration

        assert get_health_declaration("pi") is None
