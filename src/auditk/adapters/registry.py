"""Adapter registry — maps adapter names to TraceAdapter instances."""

from collections.abc import Callable

from auditk.adapters.claude_code import ClaudeCodeTraceAdapter
from auditk.adapters.generic_otel import OtelTraceAdapter
from auditk.adapters.langgraph import LangGraphTraceAdapter
from auditk.adapters.protocols import TraceAdapter

_REGISTRY: dict[str, TraceAdapter] = {
    "generic-otel": OtelTraceAdapter(),
    "langgraph": LangGraphTraceAdapter(),
    "claude-code": ClaudeCodeTraceAdapter(),
}


def _make_generic_otel(strip_payloads: bool) -> TraceAdapter:
    return OtelTraceAdapter(strip_payloads=strip_payloads)


def _make_langgraph(strip_payloads: bool) -> TraceAdapter:
    return LangGraphTraceAdapter(strip_payloads=strip_payloads)


def _make_claude_code(strip_payloads: bool) -> TraceAdapter:
    return ClaudeCodeTraceAdapter(strip_payloads=strip_payloads)


# Every registered adapter today honours `strip_payloads` (P1b gap 1). A
# future adapter added to `_REGISTRY` without a matching entry here is one
# that genuinely cannot redact -- `get_adapter(name, strip_payloads=True)`
# then refuses loudly (ValueError) rather than the CLI silently ignoring
# `--strip-payloads`, per docs/adapters.md's redaction contract.
_FACTORIES: dict[str, Callable[[bool], TraceAdapter]] = {
    "generic-otel": _make_generic_otel,
    "langgraph": _make_langgraph,
    "claude-code": _make_claude_code,
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
