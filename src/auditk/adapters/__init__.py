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
from auditk.adapters.provenance import ProvenanceDeclaration, TraceProvenance
from auditk.adapters.registry import _REGISTRY as REGISTRY
from auditk.adapters.registry import get_adapter, get_provenance_declaration

__all__ = [
    "AgentConfigLoader",
    "EndpointProber",
    "EvidenceStore",
    "ProbeResponse",
    "ProvenanceDeclaration",
    "Signer",
    "Stimulus",
    "TraceAdapter",
    "TraceProvenance",
    "get_adapter",
    "get_provenance_declaration",
    "REGISTRY",
]
