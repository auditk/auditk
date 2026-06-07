"""Build and sign auditk evidence packs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from auditk.adapters.protocols import Signer
from auditk.analysis.drift import compute_drift
from auditk.attestation.canonical import canonicalize
from auditk.schema import (
    EvidencePack,
    Issuer,
    ProbeResult,
    RiskTier,
    Subject,
    Trace,
    TraceSummary,
)


def build(
    traces: list[Trace],
    probe_results: list[ProbeResult],
    jurisdiction: list[str],
    risk_tier: RiskTier,
    issuer: Issuer,
    subject: Subject,
    signer: Signer,
    scorer_key: str | None = None,
    verbose: bool = False,
) -> EvidencePack:
    """Build and sign an EvidencePack from traces and probe results."""
    now = datetime.now(UTC)
    summary = _build_summary(traces, now)
    drift = compute_drift(traces[0], scorer_key) if traces else None

    if verbose and drift is not None and drift.per_step:
        for step_id, step_drift in drift.per_step.items():
            print(f"{step_id}: {step_drift.label.value} — {step_drift.reasoning}")

    pack = EvidencePack(
        pack_id=uuid4(),
        issued_at=now,
        issuer=issuer,
        subject=subject,
        probe_results=probe_results,
        drift_metrics=drift,
        trace_summary=summary,
    )

    manifest = pack.model_dump(mode="json", exclude={"signatures"})
    canonical_bytes = canonicalize(manifest)
    signature = signer.sign(canonical_bytes)
    pack.signatures.append(signature)
    return pack


def _build_summary(traces: list[Trace], now: datetime) -> TraceSummary:
    if not traces:
        return TraceSummary(
            trace_count=0,
            step_count=0,
            flow_types=[],
            time_range=(now, now),
        )
    step_count = sum(len(t.steps) for t in traces)
    flow_types = list({t.flow_type for t in traces})
    timestamps = [s.timestamp for t in traces for s in t.steps]
    time_range = (min(timestamps), max(timestamps)) if timestamps else (now, now)
    return TraceSummary(
        trace_count=len(traces),
        step_count=step_count,
        flow_types=flow_types,
        time_range=time_range,
    )
