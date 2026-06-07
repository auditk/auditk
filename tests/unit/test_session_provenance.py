"""TDD tests for session provenance hooks and correlate_sessions.py."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Paths to the hook files we expect to exist
CLAUDE_HOOK = Path.home() / ".claude" / "hooks" / "post-session.sh"
HERMES_HOOK = Path.home() / ".hermes" / "hooks" / "post-session.sh"
CORRELATE_SCRIPT = Path(__file__).parents[2] / "scripts" / "correlate_sessions.py"

PROVENANCE_FILE = Path.home() / ".agent_provenance.jsonl"


class TestClaudeHook:
    """Tests for ~/.claude/hooks/post-session.sh."""

    def test_hook_file_exists(self) -> None:
        assert CLAUDE_HOOK.exists(), f"Claude hook missing: {CLAUDE_HOOK}"

    def test_hook_is_executable(self) -> None:
        assert CLAUDE_HOOK.exists()
        assert os.access(CLAUDE_HOOK, os.X_OK), "Claude hook not executable"

    def test_hook_appends_jsonl_with_session_id(self) -> None:
        assert CLAUDE_HOOK.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            provenance = Path(tmpdir) / "provenance.jsonl"
            env = {
                **os.environ,
                "CLAUDE_SESSION_ID": "sess-cc-12345",
                "CLAUDE_MODEL": "claude-sonnet-4",
                "AGENT_PROVENANCE_PATH": str(provenance),
            }
            # Mock stdin with SessionEnd JSON input
            stdin_input = json.dumps({
                "event": "SessionEnd",
                "session_id": "sess-cc-12345",
                "messages": [{"role": "user", "content": "Hello Claude"}],
            })
            result = subprocess.run(
                ["bash", str(CLAUDE_HOOK)],
                input=stdin_input,
                env=env,
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            assert result.returncode == 0, result.stderr

            lines = provenance.read_text().strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["session_id"] == "sess-cc-12345"
            assert entry["agent"] == "claude-code"
            assert entry["model"] == "claude-sonnet-4"
            assert entry["project"] == Path(tmpdir).name
            assert "timestamp" in entry
            assert entry["first_message"] == "Hello Claude"

    def test_hook_fallback_timestamp_when_no_session_id_env(self) -> None:
        assert CLAUDE_HOOK.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            provenance = Path(tmpdir) / "provenance.jsonl"
            env = {
                **os.environ,
                "AGENT_PROVENANCE_PATH": str(provenance),
            }
            # Ensure CLAUDE_SESSION_ID is not set
            env.pop("CLAUDE_SESSION_ID", None)
            stdin_input = json.dumps({"event": "SessionEnd"})
            result = subprocess.run(
                ["bash", str(CLAUDE_HOOK)],
                input=stdin_input,
                env=env,
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            assert result.returncode == 0, result.stderr
            lines = provenance.read_text().strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["session_id"].startswith("cc-")
            assert "timestamp" in entry


class TestHermesHook:
    """Tests for ~/.hermes/hooks/post-session.sh."""

    def test_hook_file_exists(self) -> None:
        assert HERMES_HOOK.exists(), f"Hermes hook missing: {HERMES_HOOK}"

    def test_hook_is_executable(self) -> None:
        assert HERMES_HOOK.exists()
        assert os.access(HERMES_HOOK, os.X_OK), "Hermes hook not executable"

    def test_hook_appends_jsonl_with_session_id(self) -> None:
        assert HERMES_HOOK.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            provenance = Path(tmpdir) / "provenance.jsonl"
            env = {
                **os.environ,
                "HERMES_SESSION_ID": "sess-hh-67890",
                "HERMES_MODEL": "accounts/fireworks/models/kimi-k2p6",
                "AGENT_PROVENANCE_PATH": str(provenance),
            }
            result = subprocess.run(
                ["bash", str(HERMES_HOOK)],
                env=env,
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            assert result.returncode == 0, result.stderr

            lines = provenance.read_text().strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["session_id"] == "sess-hh-67890"
            assert entry["agent"] == "hermes"
            assert entry["model"] == "accounts/fireworks/models/kimi-k2p6"
            assert "timestamp" in entry

    def test_hook_fallback_timestamp_when_no_session_id_env(self) -> None:
        assert HERMES_HOOK.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            provenance = Path(tmpdir) / "provenance.jsonl"
            env = {
                **os.environ,
                "AGENT_PROVENANCE_PATH": str(provenance),
            }
            env.pop("HERMES_SESSION_ID", None)
            result = subprocess.run(
                ["bash", str(HERMES_HOOK)],
                env=env,
                capture_output=True,
                text=True,
                cwd=tmpdir,
            )
            assert result.returncode == 0, result.stderr
            lines = provenance.read_text().strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["session_id"].startswith("hh-")
            assert "timestamp" in entry


class TestCorrelateScript:
    """Tests for scripts/correlate_sessions.py."""

    def test_script_exists(self) -> None:
        assert CORRELATE_SCRIPT.exists(), f"Script missing: {CORRELATE_SCRIPT}"

    def test_script_is_executable_python(self) -> None:
        assert CORRELATE_SCRIPT.exists()
        assert CORRELATE_SCRIPT.suffix == ".py"

    def test_correlate_reads_provenance_and_packs(self) -> None:
        assert CORRELATE_SCRIPT.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            provenance = Path(tmpdir) / "provenance.jsonl"
            packs_dir = Path(tmpdir) / "benchmark_results" / "real_sessions"
            packs_dir.mkdir(parents=True)

            # Write provenance
            provenance.write_text(
                json.dumps({
                    "session_id": "sess-001",
                    "agent": "claude-code",
                    "model": "claude-sonnet-4",
                    "project": "auditk",
                    "branch": "main",
                    "recent_commits": ["abc123 fix bug"],
                    "timestamp": "2026-06-07T12:00:00Z",
                    "first_message": "Hello",
                }) + "\n"
            )

            # Write a pack file with matching session_id
            pack = packs_dir / "sess-001_pack.json"
            pack.write_text(json.dumps({
                "session_id": "sess-001",
                "drift_score": 0.15,
                "flagged": False,
            }))

            env = {
                **os.environ,
                "AGENT_PROVENANCE_PATH": str(provenance),
                "PACK_DIR": str(packs_dir),
            }
            result = subprocess.run(
                [sys.executable, str(CORRELATE_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            output = result.stdout
            assert "sess-001" in output
            assert "claude-code" in output
            assert "0.15" in output
            assert "False" in output

    def test_correlate_graceful_missing_provenance(self) -> None:
        assert CORRELATE_SCRIPT.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            packs_dir = Path(tmpdir) / "benchmark_results" / "real_sessions"
            packs_dir.mkdir(parents=True)
            pack = packs_dir / "sess-002_pack.json"
            pack.write_text(json.dumps({
                "session_id": "sess-002",
                "drift_score": 0.25,
                "flagged": True,
            }))

            env = {
                **os.environ,
                "AGENT_PROVENANCE_PATH": str(Path(tmpdir) / "missing.jsonl"),
                "PACK_DIR": str(packs_dir),
            }
            result = subprocess.run(
                [sys.executable, str(CORRELATE_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert "sess-002" in result.stdout
            assert "N/A" in result.stdout or "missing" in result.stdout.lower()

    def test_correlate_join_on_session_id(self) -> None:
        assert CORRELATE_SCRIPT.exists()
        with tempfile.TemporaryDirectory() as tmpdir:
            provenance = Path(tmpdir) / "provenance.jsonl"
            packs_dir = Path(tmpdir) / "benchmark_results" / "real_sessions"
            packs_dir.mkdir(parents=True)

            provenance.write_text(
                json.dumps({
                    "session_id": "sess-003",
                    "agent": "hermes",
                    "model": "kimi-k2p6",
                    "project": "glasshouse",
                    "branch": "dev",
                    "recent_commits": ["def456 add feat"],
                    "timestamp": "2026-06-07T13:00:00Z",
                }) + "\n"
            )

            pack = packs_dir / "sess-003_pack.json"
            pack.write_text(json.dumps({
                "session_id": "sess-003",
                "drift_score": 0.05,
                "flagged": False,
            }))

            env = {
                **os.environ,
                "AGENT_PROVENANCE_PATH": str(provenance),
                "PACK_DIR": str(packs_dir),
            }
            result = subprocess.run(
                [sys.executable, str(CORRELATE_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            output = result.stdout
            assert "sess-003" in output
            assert "hermes" in output
            assert "glasshouse" in output
            assert "dev" in output
            assert "0.05" in output
