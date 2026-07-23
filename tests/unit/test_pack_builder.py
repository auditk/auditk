"""Tests for auditk.attestation.pack."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auditk.attestation.pack import build
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    EvidencePack,
    FlowType,
    Issuer,
    RiskTier,
    Signature,
    Step,
    Subject,
    Trace,
)

_SPEC_PATH = Path(os.environ.get("AUDITK_SPEC_PATH", "../auditk-spec"))
_PACK_SCHEMA = _SPEC_PATH / "spec" / "v0.1" / "evidence-pack.schema.json"

_TS = datetime(2026, 1, 1, tzinfo=UTC)


class FakeSigner:
    def sign(self, payload: bytes) -> Signature:
        return Signature(
            signer="fake",
            algorithm="ed25519",
            public_key="fakepub",
            signature="fakesig",
            issued_at=datetime.now(UTC),
        )


def _make_trace() -> Trace:
    step = Step(
        step_id="s-1",
        trace_id="t-1",
        timestamp=_TS,
        actor=Actor.AGENT,
        declared_intent="do something",
        action=Action(type=ActionType.UTTERANCE, payload={"text": "hello"}),
    )
    return Trace(
        trace_id="t-1",
        flow_type=FlowType.GENERIC,
        agent_config_ref="cfg-1",
        steps=[step],
        source_adapter="test",
    )


@pytest.fixture
def fake_signer() -> FakeSigner:
    return FakeSigner()


@pytest.fixture
def sample_trace() -> Trace:
    return _make_trace()


def _common_kwargs(signer: FakeSigner) -> dict:
    return dict(
        probe_results=[],
        jurisdiction=["UK"],
        risk_tier=RiskTier.LIMITED,
        issuer=Issuer(name="Test Issuer"),
        subject=Subject(agent_config_ref="cfg-1", agent_version="1.0"),
        signer=signer,
    )


def test_build_returns_evidence_pack(sample_trace: Trace, fake_signer: FakeSigner) -> None:
    pack = build(traces=[sample_trace], **_common_kwargs(fake_signer))
    assert isinstance(pack, EvidencePack)


def test_built_pack_has_one_signature(sample_trace: Trace, fake_signer: FakeSigner) -> None:
    pack = build(traces=[sample_trace], **_common_kwargs(fake_signer))
    assert len(pack.signatures) == 1


@pytest.mark.skipif(
    not _PACK_SCHEMA.exists(),
    reason=f"auditk-spec not found at {_SPEC_PATH}",
)
def test_built_pack_validates_against_evidence_pack_schema(
    sample_trace: Trace, fake_signer: FakeSigner
) -> None:
    import jsonschema

    pack = build(traces=[sample_trace], **_common_kwargs(fake_signer))
    data = pack.model_dump(mode="json")
    schema = json.loads(_PACK_SCHEMA.read_text())
    jsonschema.validate(instance=data, schema=schema)


def test_empty_traces_produces_zero_counts(fake_signer: FakeSigner) -> None:
    pack = build(traces=[], **_common_kwargs(fake_signer))
    assert pack.trace_summary.trace_count == 0
    assert pack.trace_summary.step_count == 0


def test_build_verbose_prints_per_step(
    sample_trace: Trace, fake_signer: FakeSigner, capsys
) -> None:
    """build(verbose=True) prints each step's taxonomy label and reasoning to stdout."""
    pack = build(traces=[sample_trace], verbose=True, **_common_kwargs(fake_signer))
    captured = capsys.readouterr()
    assert pack.drift_metrics is not None
    assert pack.drift_metrics.per_step is not None
    for step_id, step_drift in pack.drift_metrics.per_step.items():
        assert step_id in captured.out
        assert step_drift.label.value in captured.out
        assert step_drift.reasoning in captured.out


def test_judge_severity_and_evidence_flow_into_signed_pack(
    sample_trace: Trace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """severity/evidence produced by a Judge flow through StepDrift -> DriftReport ->
    the signed EvidencePack, survive the same JSON round-trip the CLI does, and the
    pack still verifies with a real Ed25519 signature over the raw JSON on disk —
    exactly the way `auditk verify` recomputes and checks it (not a fresh model_dump).
    """
    from auditk.analysis.scorers.judge import TwoStageJudgeScorer
    from auditk.analysis.taxonomy import RubricResult, TaxonomyLabel
    from auditk.attestation.canonical import canonicalize
    from auditk.attestation.signer import (
        LocalEd25519Signer,
        LocalEd25519Verifier,
        generate_keypair,
    )

    class _AlwaysContradictPredictor:
        def predict(self, premise: str, hypothesis: str) -> tuple[float, float, float]:
            return (0.9, 0.05, 0.05)  # contradiction

    class _FixedJudge:
        model_id = "fake-judge"
        temperature = 0.0

        def adjudicate(
            self, step_id: str, declared_intent: str, action_text: str, gate_label: str
        ) -> RubricResult:
            return RubricResult(
                label=TaxonomyLabel.INSTRUCTION_NONCOMPLIANCE,
                confidence=0.81,
                reasoning="Violated the declared constraint.",
                severity="HIGH",
                evidence="hello",
            )

    scorer = TwoStageJudgeScorer(predictor=_AlwaysContradictPredictor(), judge=_FixedJudge())
    monkeypatch.setattr(
        "auditk.attestation.pack.compute_drift",
        lambda trace, scorer_key=None: scorer.score(trace),
    )

    priv_path, pub_path = generate_keypair(tmp_path / "signing_key")
    real_signer = LocalEd25519Signer(priv_path)

    pack = build(traces=[sample_trace], **_common_kwargs(real_signer))

    # severity/evidence reached the in-memory pack via StepDrift.
    assert pack.drift_metrics is not None
    assert pack.drift_metrics.per_step is not None
    step_drift = pack.drift_metrics.per_step["s-1"]
    assert step_drift.severity == "HIGH"
    assert step_drift.evidence == "hello"

    # Write and reload exactly as the CLI's `attest`/`verify` commands do.
    pack_file = tmp_path / "evidence-pack.json"
    pack_file.write_text(pack.model_dump_json(indent=2))
    raw_pack = json.loads(pack_file.read_text())
    assert raw_pack["drift_metrics"]["per_step"]["s-1"]["severity"] == "HIGH"
    assert raw_pack["drift_metrics"]["per_step"]["s-1"]["evidence"] == "hello"

    manifest = {k: v for k, v in raw_pack.items() if k != "signatures"}
    canonical = canonicalize(manifest)
    verifier = LocalEd25519Verifier(pub_path.read_text())
    for sig in raw_pack["signatures"]:
        verifier.verify(canonical, sig["signature"])  # raises on failure
