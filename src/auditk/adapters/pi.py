# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Pi coding-agent adapter for auditk -- GATED, not implemented.

Pi (working identification: Mario Zechner's terminal coding-agent harness,
`pi.dev` / `github.com/earendil-works/pi`) is a prospective external
tester's (Radek Gruchalski) harness. See `docs/pi-format-notes.md` for what
is publicly known about its session format, why that is not enough to write
a parser, and the exact list of things a real sample trace needs to show us
before this adapter can be implemented for real.

Per `docs/adapters.md`'s adapter-writing discipline -- every shipped
adapter's format was "discovered by reading the writer source... not
guessed from data alone" (`hermes.py`'s module docstring) -- this module
deliberately does NOT attempt to parse Pi's documented JSONL session format.
Public docs describe an intended shape; they drift from what a specific
install, version, and set of plugins/skills actually writes. No Pi session
trace, sample or real, has been supplied under explicit permission yet.

So: every entry point on `PiTraceAdapter` refuses loudly with
`PiAdapterGatedError` (a `ValueError` subclass, so it flows cleanly through
the same CLI/registry error handling every other adapter's malformed-input
`ValueError` does -- see `cli.py`'s `ingest`/`report` commands) rather than
guessing, silently no-op'ing, or leaking an internal exception type. This is
intentional and MUST NOT be "fixed" by writing best-effort parsing code
against the public docs alone -- see docs/pi-format-notes.md's "What sample
traces must show us" for what has to land first.

`pi` is registered in `auditk.adapters.registry._REGISTRY`/`_FACTORIES` (so
`--adapter pi` is a real, documented, reachable CLI name rather than an
unknown-adapter `KeyError`) but deliberately absent from `_HEALTH_DECLARATIONS`
-- there is no real record shape to declare a health canary against yet.
"""

from __future__ import annotations

from typing import Any

from auditk.schema import Trace

PI_GATED_MESSAGE = (
    "Pi adapter is gated on sample traces supplied under explicit permission; "
    "see docs/pi-format-notes.md."
)


class PiAdapterGatedError(ValueError):
    """Raised by every `PiTraceAdapter` entry point.

    A `ValueError` subclass (not a bare `ValueError` and not an unrelated
    type) so callers can catch it specifically if they want to, while the
    existing `except (KeyError, ValueError)` handling in `cli.py`'s
    `ingest`/`report` commands still catches it as a clean, non-zero-exit
    refusal -- never a stack trace, never a silent no-op.
    """

    def __init__(self, message: str = PI_GATED_MESSAGE) -> None:
        super().__init__(message)


class PiTraceAdapter:
    """Structural `TraceAdapter` stub for the Pi coding-agent harness.

    Every method refuses loudly (`PiAdapterGatedError`) regardless of what
    `raw` looks like -- there is no code path here that inspects, parses, or
    partially processes `raw` at all. `strip_payloads` is accepted only for
    shape-parity with the other adapters' constructors (`registry.py`'s
    `_FACTORIES` calls every factory the same way); it has no effect, since
    there is nothing to ingest to redact.
    """

    def __init__(self, strip_payloads: bool = False) -> None:
        self.strip_payloads = strip_payloads

    def ingest(self, raw: Any) -> Trace:
        raise PiAdapterGatedError()
