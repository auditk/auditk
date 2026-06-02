from glasshouse_core.adapters.claude_code import ClaudeCodeTraceAdapter
from glasshouse_core.adapters.generic_otel import OtelTraceAdapter
from glasshouse_core.adapters.langgraph import LangGraphTraceAdapter
from glasshouse_core.adapters.protocols import TraceAdapter

_REGISTRY: dict[str, TraceAdapter] = {
    "generic-otel": OtelTraceAdapter(),
    "langgraph": LangGraphTraceAdapter(),
    "claude-code": ClaudeCodeTraceAdapter(),
}


def get_adapter(name: str) -> TraceAdapter:
    if name not in _REGISTRY:
        raise KeyError(
            f"Unknown adapter {name!r}. Available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]
