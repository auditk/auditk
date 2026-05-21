"""glasshouse_core.adapters — pluggable boundary protocols.

Import the five Protocol classes and the two shared value objects from here.
"""

from glasshouse_core.adapters.protocols import (
    AgentConfigLoader,
    EndpointProber,
    EvidenceStore,
    ProbeResponse,
    Signer,
    Stimulus,
    TraceAdapter,
)
from glasshouse_core.adapters.registry import _REGISTRY as registry, get_adapter

__all__ = [
    "AgentConfigLoader",
    "EndpointProber",
    "EvidenceStore",
    "ProbeResponse",
    "Signer",
    "Stimulus",
    "TraceAdapter",
    "get_adapter",
    "registry",
]
