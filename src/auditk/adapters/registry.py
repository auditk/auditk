from auditk.adapters.claude_code import ClaudeCodeTraceAdapter
from auditk.adapters.generic_otel import OtelTraceAdapter
from auditk.adapters.langgraph import LangGraphTraceAdapter
from auditk.adapters.protocols import TraceAdapter

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
