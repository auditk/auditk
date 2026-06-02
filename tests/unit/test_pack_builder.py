"""Tests for glasshouse_core.attestation.pack."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from glasshouse_core.attestation.pack import build
from glasshouse_core.schema import (
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

_SPEC_PATH = Path(os.environ.get("GLASSHOUSE_SPEC_PATH", "../glasshouse-spec"))
_PACK_SCHEMA = _SPEC_PATH / "spec" / "v0.1" / "evidence-pack.schema.json"

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeSigner:
    def sign(self, payload: bytes) -> Signature:
        return Signature(
            signer="fake",
            algorithm="ed25519",
            public_key="fakepub",
            signature="fakesig",
            issued_at=datetime.now(timezone.utc),
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
    reason=f"glasshouse-spec not found at {_SPEC_PATH}",
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
