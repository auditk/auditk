"""auditk.adapters — pluggable boundary protocols.

Import the five Protocol classes and the two shared value objects from here.
"""

from auditk.adapters.protocols import (
    AgentConfigLoader,
    EndpointProber,
    EvidenceStore,
    ProbeResponse,
    Signer,
    Stimulus,
    TraceAdapter,
)
from auditk.adapters.registry import _REGISTRY as REGISTRY
from auditk.adapters.registry import get_adapter

__all__ = [
    "AgentConfigLoader",
    "EndpointProber",
    "EvidenceStore",
    "ProbeResponse",
    "Signer",
    "Stimulus",
    "TraceAdapter",
    "get_adapter",
    "REGISTRY",
]
