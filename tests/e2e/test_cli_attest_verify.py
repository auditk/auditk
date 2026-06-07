"""E2E tests for the auditk CLI attest/verify pipeline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from auditk.cli import app

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "claude_code" / "session-intent-action.jsonl"

runner = CliRunner()


def test_key_gen_ingest_attest_verify_pipeline(tmp_path: Path) -> None:
    session_file = tmp_path / "session.jsonl"
    shutil.copy(_FIXTURE, session_file)

    key_base = str(tmp_path / "signing_key")
    priv_key = tmp_path / "signing_key.ed25519"
    pub_key = tmp_path / "signing_key.ed25519.pub"

    # 1. key-gen
    result = runner.invoke(app, ["key-gen", key_base])
    assert result.exit_code == 0, result.output
    assert priv_key.exists()
    assert pub_key.exists()

    # 2. ingest
    trace_file = tmp_path / "trace.json"
    result = runner.invoke(
        app,
        ["ingest", "--adapter", "claude-code", "--in", str(session_file), "--out", str(trace_file)],
    )
    assert result.exit_code == 0, result.output
    assert trace_file.exists()
    trace_data = json.loads(trace_file.read_text())
    assert "steps" in trace_data

    # 3. attest
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
            "Test Issuer",
            "--agent-id",
            "test-agent",
            "--agent-version",
            "1.0",
            "--out",
            str(pack_file),
        ],
    )
    assert result.exit_code == 0, result.output
    assert pack_file.exists()
    assert "pack_id" in json.loads(pack_file.read_text())

    # 4. verify
    result = runner.invoke(
        app,
        ["verify", str(pack_file), "--public-key", str(pub_key)],
    )
    assert result.exit_code == 0, result.output
    assert "verified" in result.output


def test_verify_fails_on_tampered_pack(tmp_path: Path) -> None:
    session_file = tmp_path / "session.jsonl"
    shutil.copy(_FIXTURE, session_file)

    key_base = str(tmp_path / "signing_key")
    pub_key = tmp_path / "signing_key.ed25519.pub"

    runner.invoke(app, ["key-gen", key_base])

    trace_file = tmp_path / "trace.json"
    runner.invoke(
        app,
        ["ingest", "--adapter", "claude-code", "--in", str(session_file), "--out", str(trace_file)],
    )

    pack_file = tmp_path / "evidence-pack.json"
    runner.invoke(
        app,
        [
            "attest",
            "--traces",
            str(trace_file),
            "--signer",
            key_base,
            "--issuer-name",
            "Test Issuer",
            "--agent-id",
            "test-agent",
            "--agent-version",
            "1.0",
            "--out",
            str(pack_file),
        ],
    )

    # Tamper: change trace_summary.step_count
    pack_data = json.loads(pack_file.read_text())
    original_count = pack_data["trace_summary"]["step_count"]
    pack_data["trace_summary"]["step_count"] = original_count + 999
    pack_file.write_text(json.dumps(pack_data))

    result = runner.invoke(
        app,
        ["verify", str(pack_file), "--public-key", str(pub_key)],
    )
    assert result.exit_code == 1
    assert "failed" in result.output.lower() or "Verification" in result.output


def test_verify_rejects_pack_with_no_signatures(tmp_path: Path) -> None:
    # Build a pack with signatures removed to confirm verify rejects it.
    session_file = tmp_path / "session.jsonl"
    shutil.copy(_FIXTURE, session_file)
    key_base = str(tmp_path / "signing_key")
    pub_key = tmp_path / "signing_key.ed25519.pub"
    runner.invoke(app, ["key-gen", key_base])
    trace_file = tmp_path / "trace.json"
    runner.invoke(
        app,
        ["ingest", "--adapter", "claude-code", "--in", str(session_file), "--out", str(trace_file)],
    )
    pack_file = tmp_path / "evidence-pack.json"
    runner.invoke(
        app,
        [
            "attest",
            "--traces",
            str(trace_file),
            "--signer",
            key_base,
            "--issuer-name",
            "T",
            "--agent-id",
            "a",
            "--agent-version",
            "1",
            "--out",
            str(pack_file),
        ],
    )
    pack_data = json.loads(pack_file.read_text())
    pack_data["signatures"] = []
    pack_file.write_text(json.dumps(pack_data))
    result = runner.invoke(app, ["verify", str(pack_file), "--public-key", str(pub_key)])
    assert result.exit_code == 1
    assert "no signatures" in result.output
