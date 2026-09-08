"""Adapter registry — maps adapter names to TraceAdapter instances."""

from collections.abc import Callable

from auditk.adapters.claude_code import (
    CLAUDE_CODE_PROVENANCE_DECLARATION,
    ClaudeCodeTraceAdapter,
)
from auditk.adapters.generic_otel import (
    GENERIC_OTEL_HEALTH_DECLARATION,
    GENERIC_OTEL_PROVENANCE_DECLARATION,
    OtelTraceAdapter,
)
from auditk.adapters.health import CLAUDE_CODE_HEALTH_DECLARATION, HealthDeclaration
from auditk.adapters.hermes import (
    HERMES_HEALTH_DECLARATION,
    HERMES_PROVENANCE_DECLARATION,
    HermesTraceAdapter,
)
from auditk.adapters.langgraph import (
    LANGGRAPH_HEALTH_DECLARATION,
    LANGGRAPH_PROVENANCE_DECLARATION,
    LangGraphTraceAdapter,
)
from auditk.adapters.pi import PI_HEALTH_DECLARATION, PI_PROVENANCE_DECLARATION, PiTraceAdapter
from auditk.adapters.protocols import TraceAdapter
from auditk.adapters.provenance import ProvenanceDeclaration

_REGISTRY: dict[str, TraceAdapter] = {
    "generic-otel": OtelTraceAdapter(),
    "langgraph": LangGraphTraceAdapter(),
    "claude-code": ClaudeCodeTraceAdapter(),
    "hermes": HermesTraceAdapter(),
    "pi": PiTraceAdapter(),
}


def _make_generic_otel(strip_payloads: bool) -> TraceAdapter:
    return OtelTraceAdapter(strip_payloads=strip_payloads)


def _make_langgraph(strip_payloads: bool) -> TraceAdapter:
    return LangGraphTraceAdapter(strip_payloads=strip_payloads)


def _make_claude_code(strip_payloads: bool) -> TraceAdapter:
    return ClaudeCodeTraceAdapter(strip_payloads=strip_payloads)


def _make_hermes(strip_payloads: bool) -> TraceAdapter:
    return HermesTraceAdapter(strip_payloads=strip_payloads)


def _make_pi(strip_payloads: bool) -> TraceAdapter:
    return PiTraceAdapter(strip_payloads=strip_payloads)


# Every registered adapter today honours `strip_payloads` (P1b gap 1). A
# future adapter added to `_REGISTRY` without a matching entry here is one
# that genuinely cannot redact -- `get_adapter(name, strip_payloads=True)`
# then refuses loudly (ValueError) rather than the CLI silently ignoring
# `--strip-payloads`, per docs/adapters.md's redaction contract.
_FACTORIES: dict[str, Callable[[bool], TraceAdapter]] = {
    "generic-otel": _make_generic_otel,
    "langgraph": _make_langgraph,
    "claude-code": _make_claude_code,
    "hermes": _make_hermes,
    "pi": _make_pi,
}

# Per-adapter health-canary declaration (P1b gap 2) -- see
# `auditk.adapters.health.HealthDeclaration`. An adapter absent from this
# map has no health declaration at all; `cli.py` surfaces that visibly
# rather than silently skipping the canary.
_HEALTH_DECLARATIONS: dict[str, HealthDeclaration] = {
    "generic-otel": GENERIC_OTEL_HEALTH_DECLARATION,
    "langgraph": LANGGRAPH_HEALTH_DECLARATION,
    "claude-code": CLAUDE_CODE_HEALTH_DECLARATION,
    "hermes": HERMES_HEALTH_DECLARATION,
    "pi": PI_HEALTH_DECLARATION,
}

# Per-adapter trace-provenance declaration (follow-up to PR #15's
# forged-walk demo) -- see `auditk.adapters.provenance.ProvenanceDeclaration`.
# Unlike `_HEALTH_DECLARATIONS`, every registered adapter has an entry here:
# `UNKNOWN` is always a legitimate, honest classification, so there is no
# "hasn't been written yet" case the way there is for health/redaction.
_PROVENANCE_DECLARATIONS: dict[str, ProvenanceDeclaration] = {
    "generic-otel": GENERIC_OTEL_PROVENANCE_DECLARATION,
    "langgraph": LANGGRAPH_PROVENANCE_DECLARATION,
    "claude-code": CLAUDE_CODE_PROVENANCE_DECLARATION,
    "hermes": HERMES_PROVENANCE_DECLARATION,
    "pi": PI_PROVENANCE_DECLARATION,
}


def get_adapter(name: str, *, strip_payloads: bool = False) -> TraceAdapter:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown adapter {name!r}. Available: {sorted(_REGISTRY)}")
    if not strip_payloads:
        return _REGISTRY[name]
    factory = _FACTORIES.get(name)
    if factory is None:
        raise ValueError(
            f"Adapter {name!r} has no redaction (--strip-payloads) support -- "
            "refusing rather than silently ignoring the flag."
        )
    return factory(True)


def get_health_declaration(name: str) -> HealthDeclaration | None:
    """The adapter's `HealthDeclaration`, or `None` if it has none.

    `None` means exactly that -- no declaration was registered for this
    adapter -- not "the concept doesn't apply." A declaration that exists
    but says a given sub-check doesn't apply to this format sets that
    sub-check's own `*_supported=False` (see `HealthDeclaration`); `None`
    here is the coarser "no declaration was written for this adapter at
    all" case `cli.py` reports as "no health declaration".
    """
    return _HEALTH_DECLARATIONS.get(name)


def get_provenance_declaration(name: str) -> ProvenanceDeclaration | None:
    """The adapter's `ProvenanceDeclaration`, or `None` if `name` itself is
    unregistered (see `get_adapter`).

    Unlike `get_health_declaration`, `None` here never means "this adapter
    hasn't decided" -- `UNKNOWN` is always a legitimate, declared value, so
    every name in `_REGISTRY` also has an entry in `_PROVENANCE_DECLARATIONS`.
    `None` means only "no such adapter name".
    """
    return _PROVENANCE_DECLARATIONS.get(name)
