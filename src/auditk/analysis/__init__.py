# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""auditk.analysis — trace analysis utilities."""

from __future__ import annotations

__all__ = ["extract_belief_state", "compute_drift", "replay"]


def __getattr__(name: str) -> object:
    if name == "extract_belief_state":
        from auditk.analysis.belief_state import extract_belief_state

        return extract_belief_state
    if name == "compute_drift":
        from auditk.analysis.drift import compute_drift

        return compute_drift
    if name == "replay":
        from auditk.analysis.replay import replay

        return replay
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
