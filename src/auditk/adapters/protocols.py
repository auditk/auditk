"""Pluggable adapter protocols for auditk.

Each Protocol defines a boundary that can be satisfied by any concrete class
(structural subtyping / PEP 544). Stimulus and ProbeResponse are value objects
shared by EndpointProber implementations.
"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol
from uuid import UUID

from pydantic import BaseModel

from auditk.adapters.provenance import ProvenanceDeclaration
from auditk.schema import (
    AgentConfig,
    EvidencePack,
    Signature,
    Trace,
)


class Stimulus(BaseModel):
    """Input sent to an agent endpoint during a probe run."""

    # channel values: user_message | system_prompt | retrieved_document |
    #                 tool_output | audio_input | screen_content
    channel: str
    payload: dict[str, Any]


class ProbeResponse(BaseModel):
    """Raw response returned by an agent endpoint."""

    text: str
    raw: dict[str, Any]
    latency_ms: float


class TraceAdapter(Protocol):
    """Convert raw adapter-specific data into a normalised Trace.

    ``provenance_declaration`` (trace-provenance declaration, follow-up to
    PR #15's forged-walk demo -- see ``auditk.adapters.provenance``) is a
    required structural member, not just an ``ingest()`` implementation
    detail: every adapter must say, unconditionally, whether the records it
    reads are scheduler-derived, self-reported, or (the honest default)
    unknown, because a trail's trustworthiness depends on how it was
    captured, not just on what it says.
    """

    provenance_declaration: ClassVar[ProvenanceDeclaration]

    def ingest(self, raw: Any) -> Trace: ...


class AgentConfigLoader(Protocol):
    """Resolve an identifier to an AgentConfig."""

    def load(self, identifier: str) -> AgentConfig: ...


class EndpointProber(Protocol):
    """Send a stimulus to an agent endpoint and return the response."""

    def send(self, stimulus: Stimulus) -> ProbeResponse: ...


class Signer(Protocol):
    """Produce a cryptographic Signature over an arbitrary payload."""

    def sign(self, payload: bytes) -> Signature: ...


class EvidenceStore(Protocol):
    """Persist and retrieve EvidencePack objects."""

    def put(self, pack: EvidencePack) -> str: ...

    def get(self, pack_id: UUID) -> EvidencePack: ...
