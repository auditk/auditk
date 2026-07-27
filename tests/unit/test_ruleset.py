# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for the ruleset cascade loader (auditk/analysis/ruleset.py).

Covers ``load_ruleset``'s layered precedence (shipped default -> home ->
nearest .auditk/rules.yaml -> $AUDITK_RULES -> explicit --rules path),
``find_git_root``'s pure-filesystem git root discovery, and
``resolve_roots``'s auto-discovery of write roots from a trace's cwd. All
filesystem cases use ``tmp_path`` — no real user files are read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from auditk.analysis.findings import DEFAULT_TRIPWIRE_PATTERNS, FindingsConfig
from auditk.analysis.ruleset import RulesetError, find_git_root, load_ruleset, resolve_roots
from auditk.schema import Action, ActionType, Actor, FlowType, Step, Trace

_RULES_YAML_PATH = (
    Path(__file__).parent.parent.parent / "src" / "auditk" / "analysis" / ("rules.default.yaml")
)


def _write_yaml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _trace_with_cwd(cwd: str | None) -> Trace:
    step = Step(
        step_id="s1",
        trace_id="t1",
        timestamp=datetime(2026, 7, 1, tzinfo=UTC),
        actor=Actor.AGENT,
        action=Action(
            type=ActionType.TOOL_CALL,
            payload={"name": "Edit", "input": {"file_path": "/anywhere/file.py"}},
        ),
    )
    metadata = {"cwd": cwd} if cwd is not None else {}
    return Trace(
        trace_id="t1",
        flow_type=FlowType.CODE,
        agent_config_ref="test:t1",
        steps=[step],
        source_adapter="test",
        metadata=metadata,
    )


# --- default-only ------------------------------------------------------


def test_load_ruleset_defaults_match_findings_config_defaults(tmp_path: Path) -> None:
    empty_start = tmp_path / "start"
    empty_home = tmp_path / "home"
    empty_start.mkdir()
    empty_home.mkdir()

    config = load_ruleset(start_dir=empty_start, home=empty_home, env={})

    defaults = FindingsConfig()
    assert config.roots is None
    assert config.scratch_prefixes == defaults.scratch_prefixes
    assert config.churn_threshold == defaults.churn_threshold
    assert config.error_cluster_k == defaults.error_cluster_k
    assert config.error_cluster_window == defaults.error_cluster_window
    assert config.tripwire_patterns == DEFAULT_TRIPWIRE_PATTERNS


# --- precedence ----------------------------------------------------------


def test_home_ruleset_overrides_default(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    _write_yaml(home / ".claude" / "auditk.rules.yaml", "churn_threshold: 7\n")

    config = load_ruleset(start_dir=start_dir, home=home, env={})
    assert config.churn_threshold == 7


def test_local_auditk_ruleset_overrides_home(tmp_path: Path) -> None:
    start_dir = tmp_path / "start" / "nested"
    home = tmp_path / "home"
    start_dir.mkdir(parents=True)
    home.mkdir()
    _write_yaml(home / ".claude" / "auditk.rules.yaml", "churn_threshold: 7\n")
    _write_yaml(tmp_path / "start" / ".auditk" / "rules.yaml", "churn_threshold: 9\n")

    config = load_ruleset(start_dir=start_dir, home=home, env={})
    assert config.churn_threshold == 9


def test_env_var_overrides_local(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    _write_yaml(tmp_path / "start" / ".auditk" / "rules.yaml", "churn_threshold: 9\n")
    env_ruleset = tmp_path / "env_rules.yaml"
    _write_yaml(env_ruleset, "churn_threshold: 11\n")

    config = load_ruleset(start_dir=start_dir, home=home, env={"AUDITK_RULES": str(env_ruleset)})
    assert config.churn_threshold == 11


def test_explicit_path_overrides_everything(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    _write_yaml(tmp_path / "start" / ".auditk" / "rules.yaml", "churn_threshold: 9\n")
    env_ruleset = tmp_path / "env_rules.yaml"
    _write_yaml(env_ruleset, "churn_threshold: 11\n")
    explicit_ruleset = tmp_path / "explicit_rules.yaml"
    _write_yaml(explicit_ruleset, "churn_threshold: 13\n")

    config = load_ruleset(
        explicit_path=explicit_ruleset,
        start_dir=start_dir,
        home=home,
        env={"AUDITK_RULES": str(env_ruleset)},
    )
    assert config.churn_threshold == 13


# --- tripwire merge ------------------------------------------------------


def test_tripwire_patterns_merge_rather_than_replace(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    _write_yaml(
        home / ".claude" / "auditk.rules.yaml",
        "tripwire_patterns:\n  custom: 'my-custom-pattern'\n",
    )

    config = load_ruleset(start_dir=start_dir, home=home, env={})
    assert config.tripwire_patterns is not None
    for name, pattern in DEFAULT_TRIPWIRE_PATTERNS.items():
        assert config.tripwire_patterns[name] == pattern
    assert config.tripwire_patterns["custom"] == "my-custom-pattern"


# --- malformed explicit ruleset -------------------------------------------


def test_malformed_explicit_ruleset_raises(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    bad_ruleset = tmp_path / "bad_rules.yaml"
    _write_yaml(bad_ruleset, "this: [is: not, valid: yaml\n")

    with pytest.raises(RulesetError):
        load_ruleset(explicit_path=bad_ruleset, start_dir=start_dir, home=home, env={})


def test_explicit_ruleset_not_a_mapping_raises(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    bad_ruleset = tmp_path / "list_rules.yaml"
    _write_yaml(bad_ruleset, "- 1\n- 2\n")

    with pytest.raises(RulesetError):
        load_ruleset(explicit_path=bad_ruleset, start_dir=start_dir, home=home, env={})


def test_missing_explicit_ruleset_raises(tmp_path: Path) -> None:
    start_dir = tmp_path / "start"
    home = tmp_path / "home"
    start_dir.mkdir()
    home.mkdir()
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(RulesetError):
        load_ruleset(explicit_path=missing, start_dir=start_dir, home=home, env={})


# --- find_git_root ---------------------------------------------------------


def test_find_git_root_walks_up_to_dot_git_dir(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sub = repo / "sub"
    (repo / ".git").mkdir(parents=True)
    sub.mkdir()

    assert find_git_root(sub) == repo


def test_find_git_root_returns_none_outside_a_repo(tmp_path: Path) -> None:
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()

    assert find_git_root(non_repo) is None


def test_find_git_root_accepts_dot_git_file_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sub = repo / "sub"
    sub.mkdir(parents=True)
    (repo / ".git").write_text("gitdir: /elsewhere/.git/worktrees/repo\n")

    assert find_git_root(sub) == repo


# --- resolve_roots ---------------------------------------------------------


def test_resolve_roots_uses_git_root_of_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    sub = repo / "sub"
    (repo / ".git").mkdir(parents=True)
    sub.mkdir()

    trace = _trace_with_cwd(str(sub))
    config = FindingsConfig(roots=None)

    assert resolve_roots(config, trace) == [str(repo)]


def test_resolve_roots_falls_back_to_cwd_outside_a_repo(tmp_path: Path) -> None:
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()

    trace = _trace_with_cwd(str(non_repo))
    config = FindingsConfig(roots=None)

    assert resolve_roots(config, trace) == [str(non_repo)]


def test_resolve_roots_returns_none_with_no_cwd() -> None:
    trace = _trace_with_cwd(None)
    config = FindingsConfig(roots=None)

    assert resolve_roots(config, trace) is None


def test_resolve_roots_respects_explicit_roots() -> None:
    trace = _trace_with_cwd("/somewhere")
    config = FindingsConfig(roots=["/explicit/root"])

    assert resolve_roots(config, trace) == ["/explicit/root"]


# --- generic guard: the shipped default ships nothing machine-specific ----


def test_default_ruleset_file_is_generic() -> None:
    text = _RULES_YAML_PATH.read_text()
    assert "/home/" not in text
    assert ".claude" not in text
    import yaml

    data = yaml.safe_load(text)
    assert data.get("roots") in (None, "auto")


# --- integration: git-root fallback preserves existing findings behaviour -


def test_analyze_trace_scope_escape_unaffected_by_git_root_fallback() -> None:
    """The anomalies fixture's cwd (/work/proj) is not a git repo, so
    find_git_root returns None and root resolution falls back to the bare
    cwd exactly as before Phase 4a — the planted scope-escape finding must
    be unchanged.
    """
    import json

    from auditk.adapters.claude_code import ingest_claude_code_session
    from auditk.analysis.findings import analyze_trace

    fixtures = Path(__file__).parent.parent / "fixtures" / "claude_code"
    events = [
        json.loads(line)
        for line in (fixtures / "session_anomalies.jsonl").read_text().splitlines()
        if line.strip()
    ]
    trace = ingest_claude_code_session(events)
    assert trace.metadata.get("cwd") == "/work/proj"

    report = analyze_trace(trace)
    scope_findings = [f for f in report.findings if f.rule_id == "scope-escape"]
    assert len(scope_findings) == 1
    assert scope_findings[0].evidence.get("file_path") == "/work/OTHER/secrets.yaml"
