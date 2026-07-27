# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for `auditk rules init` (starter ruleset scaffolding).

Covers the pure scaffold builder (``auditk.analysis.rules_scaffold``) and the
``auditk rules init`` CLI command. All filesystem cases use ``tmp_path`` —
this module never reads the real user's ``~/.claude/CLAUDE.md``. Every CLI
invocation below passes an explicit ``--from`` directory *and* monkeypatches
``$HOME`` to an isolated ``tmp_path``, since ``discover_policy_context``
(called with no ``home=`` override inside the CLI) would otherwise fall back
to the real ``Path.home()`` for the GLOBAL scope.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from auditk.analysis.findings import FindingsConfig
from auditk.analysis.policy_context import discover_policy_context
from auditk.analysis.rules_scaffold import (
    _KEYWORD_RULES,
    build_starter_ruleset,
    scan_keyword_matches,
)
from auditk.analysis.ruleset import load_ruleset
from auditk.cli import app

runner = CliRunner()


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


# --- pure scaffold builder -------------------------------------------------


def test_build_starter_ruleset_with_no_docs_notes_none_found() -> None:
    scaffold = build_starter_ruleset([])

    assert "No CLAUDE.md policy files were found" in scaffold
    loaded = yaml.safe_load(scaffold)
    assert isinstance(loaded, dict)
    assert "tripwire_patterns" in loaded


def test_build_starter_ruleset_body_matches_shipped_default() -> None:
    default_path = (
        Path(__file__).parent.parent.parent / "src" / "auditk" / "analysis" / "rules.default.yaml"
    )
    scaffold = build_starter_ruleset([])
    loaded_scaffold = yaml.safe_load(scaffold)
    loaded_default = yaml.safe_load(default_path.read_text())

    assert loaded_scaffold == loaded_default


def test_build_starter_ruleset_lists_scanned_files(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    _write(claude_md, "# Policy\nSome unrelated content.\n")
    docs = discover_policy_context(tmp_path, home=tmp_path / "no-such-home")

    scaffold = build_starter_ruleset(docs)

    assert str(claude_md) in scaffold


def test_scan_keyword_matches_destructive_delete() -> None:
    matches = scan_keyword_matches("Never delete files without explicit user confirmation")
    assert any("destructive-rm" in m.note for m in matches)


def test_scan_keyword_matches_commit_without_tests() -> None:
    matches = scan_keyword_matches("Run tests + linter before every commit")
    assert any("commit-without-tests" in m.note for m in matches)


def test_scan_keyword_matches_env_secret() -> None:
    matches = scan_keyword_matches(
        "Never modify environment config (.env, docker-compose, secrets)"
    )
    assert any("env-write" in m.note for m in matches)
    assert any("docker-compose-down" in m.note for m in matches)


def test_scan_keyword_matches_migration() -> None:
    matches = scan_keyword_matches(
        "Never run database migrations without explicit user confirmation"
    )
    assert any("db-migration" in m.note for m in matches)


def test_scan_keyword_matches_kubectl() -> None:
    matches = scan_keyword_matches("Do not run kubectl delete against production")
    assert any("kubectl-delete" in m.note for m in matches)


def test_scan_keyword_matches_infra_is_suggestion_only() -> None:
    matches = scan_keyword_matches("Never touch infrastructure/ or deployment config")
    infra_matches = [m for m in matches if "infra" in m.label.lower()]
    assert infra_matches
    assert "custom tripwire" in infra_matches[0].note


def test_scan_keyword_matches_empty_for_unrelated_text() -> None:
    assert scan_keyword_matches("Please write nice docstrings and use type hints.") == []


def test_build_starter_ruleset_includes_matched_rule_names(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    _write(
        claude_md,
        "# Team policy\nRun tests + linter before every commit.\nNever write .env files.\n",
    )
    docs = discover_policy_context(tmp_path, home=tmp_path / "no-such-home")

    scaffold = build_starter_ruleset(docs)

    assert "commit-without-tests" in scaffold
    assert "env-write" in scaffold


def test_scaffold_yaml_body_is_loadable_by_load_ruleset(tmp_path: Path) -> None:
    claude_md = tmp_path / "CLAUDE.md"
    _write(claude_md, "# Policy\nRun tests before every commit.\n")
    docs = discover_policy_context(tmp_path, home=tmp_path / "no-such-home")
    scaffold = build_starter_ruleset(docs)

    scaffold_file = tmp_path / "scaffold.yaml"
    scaffold_file.write_text(scaffold)

    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    config = load_ruleset(
        explicit_path=scaffold_file, start_dir=isolated_home, home=isolated_home, env={}
    )

    assert config.tripwire_patterns is not None
    assert "destructive-rm" in config.tripwire_patterns


# --- CLI: `auditk rules init` ----------------------------------------------


def test_cli_rules_init_stdout_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_home(tmp_path, monkeypatch)
    project = tmp_path / "project"
    _write(
        project / "CLAUDE.md",
        "# Team policy\nRun tests + linter before every commit.\nNever write .env files.\n",
    )

    result = runner.invoke(app, ["rules", "init", "--from", str(project)])

    assert result.exit_code == 0, result.output
    assert "commit-without-tests" in result.output
    assert "env-write" in result.output
    assert str(project / "CLAUDE.md") in result.output

    loaded = yaml.safe_load(result.output)
    assert isinstance(loaded, dict)
    config = FindingsConfig(
        **{**loaded, "roots": None if loaded.get("roots") == "auto" else loaded.get("roots")}
    )
    assert config.tripwire_patterns is not None


def test_cli_rules_init_no_claude_md_still_emits_default_scaffold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    result = runner.invoke(app, ["rules", "init", "--from", str(empty_dir)])

    assert result.exit_code == 0, result.output
    assert "No CLAUDE.md policy files were found" in result.output
    loaded = yaml.safe_load(result.output)
    assert isinstance(loaded, dict)
    assert "tripwire_patterns" in loaded


def test_cli_rules_init_out_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolated_home(tmp_path, monkeypatch)
    project = tmp_path / "project"
    _write(project / "CLAUDE.md", "# Policy\nRun tests before every commit.\n")
    out_file = tmp_path / "out" / "rules.yaml"

    result = runner.invoke(app, ["rules", "init", "--from", str(project), "--out", str(out_file)])

    assert result.exit_code == 0, result.output
    assert out_file.exists()
    loaded = yaml.safe_load(out_file.read_text())
    assert isinstance(loaded, dict)
    assert "tripwire_patterns" in loaded


def test_cli_rules_init_out_refuses_overwrite_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    project = tmp_path / "project"
    _write(project / "CLAUDE.md", "# Policy\n")
    out_file = tmp_path / "rules.yaml"
    out_file.write_text("pre-existing: true\n")

    result = runner.invoke(app, ["rules", "init", "--from", str(project), "--out", str(out_file)])

    assert result.exit_code != 0
    assert out_file.read_text() == "pre-existing: true\n"


def test_cli_rules_init_out_force_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_home(tmp_path, monkeypatch)
    project = tmp_path / "project"
    _write(project / "CLAUDE.md", "# Policy\n")
    out_file = tmp_path / "rules.yaml"
    out_file.write_text("pre-existing: true\n")

    result = runner.invoke(
        app,
        ["rules", "init", "--from", str(project), "--out", str(out_file), "--force"],
    )

    assert result.exit_code == 0, result.output
    loaded = yaml.safe_load(out_file.read_text())
    assert isinstance(loaded, dict)
    assert "tripwire_patterns" in loaded


# --- generic guard ----------------------------------------------------------

# Any string that would tie the shipped keyword map / scaffold template to a
# specific person, machine, or project. This is a defense against ever
# committing something like this session's own user/project context by
# accident.
_FORBIDDEN_SUBSTRINGS = (
    "matt",
    "dawson",
    "bossyk",
    "auditk-paper",
    "narwhal",
    "/home/matt",
    "lesportif",
)


def test_keyword_map_is_generic() -> None:
    haystacks = [rule.label for rule in _KEYWORD_RULES] + [rule.note for rule in _KEYWORD_RULES]
    combined = " ".join(haystacks).lower()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in combined, f"keyword map leaks {forbidden!r}"


def test_empty_scaffold_text_is_generic() -> None:
    scaffold = build_starter_ruleset([]).lower()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        assert forbidden not in scaffold, f"scaffold leaks {forbidden!r}"


def test_no_absolute_home_paths_hardcoded_in_scaffold_module() -> None:
    import auditk.analysis.rules_scaffold as mod

    source = Path(mod.__file__).read_text()
    assert not re.search(r"/home/\w+", source)
