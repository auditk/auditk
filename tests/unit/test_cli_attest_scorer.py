"""Red-phase tests for the --scorer flag on auditk attest (Sub-phase C)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from auditk.cli import app

runner = CliRunner()


def _write_minimal_trace(path: Path) -> None:
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
    path.write_text(json.dumps(trace))


def test_attest_with_jaccard_scorer(tmp_path: Path) -> None:
    """Explicit --scorer jaccard produces the default drift report."""
    trace_file = tmp_path / "trace.json"
    _write_minimal_trace(trace_file)
    key_base = str(tmp_path / "key")

    runner.invoke(app, ["key-gen", key_base])

    pack_file = tmp_path / "pack.json"
    result = runner.invoke(
        app,
        [
            "attest",
            "--traces", str(trace_file),
            "--signer", key_base,
            "--issuer-name", "Test",
            "--agent-id", "a",
            "--agent-version", "1",
            "--out", str(pack_file),
            "--scorer", "jaccard",
        ],
    )
    assert result.exit_code == 0, result.output
    pack = json.loads(pack_file.read_text())
    assert pack["drift_metrics"] is not None
    assert pack["drift_metrics"]["method"] == "plan-action-similarity"


def test_attest_with_nli_scorer_missing_extra_fails_gracefully(tmp_path: Path, monkeypatch) -> None:
    """--scorer nli without the [nli] extra fails with a helpful message."""
    trace_file = tmp_path / "trace.json"
    _write_minimal_trace(trace_file)
    key_base = str(tmp_path / "key")

    runner.invoke(app, ["key-gen", key_base])

    # Ensure RUN_NLI_MODEL is not set so the scorer refuses to load
    monkeypatch.delenv("RUN_NLI_MODEL", raising=False)

    pack_file = tmp_path / "pack.json"
    result = runner.invoke(
        app,
        [
            "attest",
            "--traces", str(trace_file),
            "--signer", key_base,
            "--issuer-name", "Test",
            "--agent-id", "a",
            "--agent-version", "1",
            "--out", str(pack_file),
            "--scorer", "nli",
        ],
    )
    assert result.exit_code != 0
    assert "auditk[nli]" in result.output or "nli" in result.output.lower()


def test_attest_with_invalid_scorer_fails(tmp_path: Path) -> None:
    """An unknown --scorer value is rejected."""
    trace_file = tmp_path / "trace.json"
    _write_minimal_trace(trace_file)
    key_base = str(tmp_path / "key")

    runner.invoke(app, ["key-gen", key_base])

    pack_file = tmp_path / "pack.json"
    result = runner.invoke(
        app,
        [
            "attest",
            "--traces", str(trace_file),
            "--signer", key_base,
            "--issuer-name", "Test",
            "--agent-id", "a",
            "--agent-version", "1",
            "--out", str(pack_file),
            "--scorer", "invalid",
        ],
    )
    assert result.exit_code != 0
    assert "scorer" in result.output.lower() or "Unknown" in result.output
