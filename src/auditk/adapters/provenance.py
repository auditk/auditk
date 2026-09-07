# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Trace-provenance declaration (follow-up to PR #15's forged-walk demo).

``examples/forged-walk`` demonstrates that a self-reported trail -- state an
agent, or one of its own nodes, writes about its own actions -- is forgeable
by that same node. A trail's trustworthiness therefore depends on *where in
the pipeline* it was captured, not just on what it says. This module applies
the same "small pure declaration, surfaced through the registry" pattern
``adapters/health.py`` (``HealthDeclaration``) already established to one
more question every adapter must answer about its own native format: was
this trace's data written by the runtime/scheduler that ran the agent, or by
the agent (or one of its own nodes) self-reporting on itself?

Three classifications
----------------------
- ``SCHEDULER_DERIVED`` -- the runtime or checkpointer that actually
  executed the agent wrote this record, independent of anything the agent
  itself chose to say. Claude Code is the one shipped adapter that can
  honestly claim this: its own harness process writes the session JSONL
  transcript this adapter reads, and the agent being audited has no write
  access to that file.
- ``SELF_REPORTED`` -- the agent, or one of its own nodes, wrote the record
  about its own actions -- exactly the shape ``examples/forged-walk`` shows
  is forgeable by a single misbehaving node.
- ``UNKNOWN`` -- the conservative default. Declared, not merely absent: an
  adapter whose native format could carry either kind of record, or whose
  ingestion code cannot itself tell which kind it was fed without more
  evidence than the record stream alone provides, says so explicitly with a
  non-empty ``reason`` -- never a silent omission.

Why every other shipped adapter today declares ``UNKNOWN``
------------------------------------------------------------
- ``langgraph``: a checkpoint's ``metadata.writes`` can hold either a
  state-key value a graph *node* set on itself (self-reported -- the node
  could lie about its own output the same way ``examples/forged-walk``'s
  misbehaving node does) or a value written by LangGraph's own
  stream/checkpointer machinery as it executes a step (scheduler-derived).
  Nothing in the serialised ``CheckpointTuple`` shape this adapter reads
  distinguishes the two -- see ``langgraph.py:_classify_action`` and
  ``ingest_checkpoints``, neither of which inspects *who* produced a given
  write, only *what* it looks like. Declaring one or the other here would
  be exactly the guess ``docs/adapters.md``'s "What an adapter must NOT
  invent" section already forbids for declared intent and pairings.
- ``generic-otel``: an OpenInference span's ``input.value``/``output.value``
  may have been emitted by the agent framework's own instrumentation
  (scheduler-derived) or by a node's own custom span (self-reported) --
  this adapter has no way to know which exporter produced a given span,
  because OpenInference's wire format carries no such marker (confirmed by
  reading ``generic_otel.py:_span_to_step``/``_infer_action`` end to end;
  neither reads or infers an emitter identity).
- ``hermes``: rows in the ``messages`` table are written by the same Hermes
  runtime process for both the harness's own bookkeeping and whatever the
  agent chooses to report about a tool call -- confirmed by reading
  ``hermes_state.py``'s own write paths (see ``hermes.py``'s module
  docstring), which gives no per-row marker distinguishing a write the
  *scheduler* made independently of the agent from one the agent's own
  turn produced.
- ``pi``: same shape as Hermes -- ``SessionManager`` (see ``pi.py``'s module
  docstring) writes every entry from inside the same process the agent
  itself runs in, with no per-entry marker separating harness-driven writes
  from the agent's own reported content.

No adapter may skip this declaration
-------------------------------------
Unlike ``HealthDeclaration``'s per-sub-check opt-out (``*_supported=False``
for a concept that genuinely doesn't exist in a format), ``UNKNOWN`` is
always an available, honest answer here, so there is no legitimate case for
an adapter to have *no* ``provenance_declaration`` at all -- see
``tests/conformance/test_conformance.py::TestProvenanceDeclaration``, which
is a hard assertion for every registered adapter, never an ``xfail`` and
never a ``skip``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TraceProvenance(str, Enum):
    """Where a trace's underlying native records were actually written from."""

    SCHEDULER_DERIVED = "scheduler-derived"
    SELF_REPORTED = "self-reported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProvenanceDeclaration:
    """One adapter's declared answer to "where did this data come from."

    ``reason`` is mandatory and must be non-empty for every classification,
    including (especially) ``UNKNOWN`` -- per this module's docstring, a
    bare "we don't know" with no explanation is indistinguishable from an
    adapter nobody has actually thought about.
    """

    name: str
    provenance: TraceProvenance
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError(
                f"ProvenanceDeclaration for {self.name!r} must give a non-empty reason -- "
                "see the auditk.adapters.provenance module docstring."
            )
