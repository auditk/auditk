"""D3.0 — verify is additive-immune: old packs verify after schema gains fields."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import create_model
from typer.testing import CliRunner

from auditk.cli import app
from auditk.schema import DriftReport, EvidencePack

runner = CliRunner()

_DEMOS = Path(__file__).parent.parent.parent / "demos"


def _verify_demo(pack_dir: Path) -> None:
    pack = pack_dir / "evidence-pack.json"
    pub = pack_dir / "signing_key.ed25519.pub"
    assert pack.exists(), f"Demo pack not found: {pack}"
    assert pub.exists(), f"Demo public key not found: {pub}"
    result = runner.invoke(app, ["verify", str(pack), "--public-key", str(pub)])
    assert result.exit_code == 0, result.output
    assert "verified" in result.output


def test_demo_001_verifies() -> None:
    """Demo-001 (built with spec v0.1) still verifies."""
    _verify_demo(_DEMOS / "demo-001")


def test_demo_005_verifies() -> None:
    """Demo-005 (built with spec v0.1) still verifies."""
    _verify_demo(_DEMOS / "demo-005")


def test_old_pack_verifies_after_schema_evolution(monkeypatch) -> None:
    """A pack built before an optional field was added still verifies because
    canonicalize uses raw JSON, not model_dump."""

    class DriftReportEvolved(DriftReport):
        new_field: int | None = None

    evidence_pack_evolved = create_model(
        "EvidencePackEvolved",
        __base__=EvidencePack,
        drift_metrics=(DriftReportEvolved | None, None),
    )

    monkeypatch.setattr("auditk.schema.EvidencePack", evidence_pack_evolved)
    _verify_demo(_DEMOS / "demo-001")


def test_freshly_built_jaccard_pack_verifies(tmp_path: Path) -> None:
    """Parity: build → verify round-trip with a jaccard scorer."""
    trace = {
        "trace_id": "t-1",
        "flow_type": "generic",
        "agent_config_ref": "cfg-1",
        "steps": [
            {
                "step_id": "s-1",
                "trace_id": "t-1",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "actor": "agent",
                "declared_intent": "Say hello",
                "action": {"type": "utterance", "payload": {"text": "hello"}},
            }
        ],
        "source_adapter": "test",
        "outcome": {"status": "success"},
    }
    trace_file = tmp_path / "trace.json"
    trace_file.write_text(json.dumps(trace))

    key_base = str(tmp_path / "key")
    runner.invoke(app, ["key-gen", key_base])

    pack_file = tmp_path / "evidence-pack.json"
    result = runner.invoke(
        app,
        [
            "attest",
            "--traces",
            str(trace_file),
            "--signer",
            key_base,
            "--issuer-name",
            "Test",
            "--agent-id",
            "a",
            "--agent-version",
            "1",
            "--out",
            str(pack_file),
            "--scorer",
            "jaccard",
        ],
    )
    assert result.exit_code == 0, result.output
    assert pack_file.exists()

    pub_key = tmp_path / "key.ed25519.pub"
    result = runner.invoke(app, ["verify", str(pack_file), "--public-key", str(pub_key)])
    assert result.exit_code == 0, result.output
    assert "verified" in result.output
