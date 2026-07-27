# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Layered ruleset cascade + git-root auto-discovery for the findings engine.

``load_ruleset`` builds a ``FindingsConfig`` by overlaying up to five YAML
layers, lowest to highest precedence:

1. the shipped generic default (``rules.default.yaml``, next to this module),
2. ``~/.claude/auditk.rules.yaml`` (a user-wide, machine-local ruleset —
   never checked into any repo),
3. the nearest ``.auditk/rules.yaml`` found by walking up from the current
   directory (a project-local ruleset),
4. the file named by ``$AUDITK_RULES``,
5. an explicit path (e.g. ``auditk report --rules <path>``).

Each layer is a YAML mapping with the same shape as ``FindingsConfig``.
Scalars and list-valued fields (``scratch_prefixes``, ``roots``) *replace*
the accumulated value; ``tripwire_patterns`` *merges* key-by-key so a layer
can add a tripwire without dropping the defaults. ``roots: auto`` (the
shipped default) maps to ``roots=None`` on the config — auto-discovery of
the allowed write roots then happens at analysis time via
``resolve_roots``/``find_git_root`` below, not at load time.

A missing optional layer (home ruleset, local ``.auditk/rules.yaml``, unset
``$AUDITK_RULES``, no ``explicit_path``) is silently skipped. A layer that
*is* named/present but cannot be read or parsed raises ``RulesetError`` —
callers should never silently ignore a malformed ``--rules`` file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

from auditk.analysis.findings import FindingsConfig

if TYPE_CHECKING:
    from auditk.schema import Trace

_DEFAULT_RULESET_PATH = Path(__file__).parent / "rules.default.yaml"
_HOME_RULESET_RELATIVE = Path(".claude") / "auditk.rules.yaml"
_LOCAL_RULESET_RELATIVE = Path(".auditk") / "rules.yaml"
_ENV_VAR = "AUDITK_RULES"


class RulesetError(Exception):
    """A ruleset YAML layer is present but could not be read or parsed."""


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read and parse `path` as a YAML mapping, or raise RulesetError."""
    try:
        text = path.read_text()
    except OSError as exc:
        raise RulesetError(f"Could not read ruleset file {path}: {exc}") from exc

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RulesetError(f"Invalid YAML in ruleset file {path}: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise RulesetError(
            f"Ruleset file {path} must contain a mapping at the top level, "
            f"got {type(loaded).__name__}"
        )
    return loaded


def _normalize_roots(layer: dict[str, Any]) -> dict[str, Any]:
    """Map a YAML `roots: auto` sentinel to `roots: None` for FindingsConfig."""
    if layer.get("roots") == "auto":
        layer = dict(layer)
        layer["roots"] = None
    return layer


def _merge_layer(accumulated: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    """Overlay `layer` onto `accumulated`; tripwire_patterns merges, rest replace."""
    merged = dict(accumulated)
    for key, value in layer.items():
        if key == "tripwire_patterns" and isinstance(value, dict):
            existing = merged.get("tripwire_patterns")
            existing_patterns: dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
            existing_patterns.update(value)
            merged["tripwire_patterns"] = existing_patterns
        else:
            merged[key] = value
    return merged


def _find_local_ruleset(start_dir: Path) -> Path | None:
    """Walk up from `start_dir` looking for `.auditk/rules.yaml`."""
    current = start_dir.resolve()
    while True:
        candidate = current / _LOCAL_RULESET_RELATIVE
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_ruleset(
    explicit_path: str | Path | None = None,
    *,
    start_dir: Path | None = None,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> FindingsConfig:
    """Build a ``FindingsConfig`` from the layered ruleset cascade.

    See the module docstring for the five layers and their precedence.
    `start_dir` defaults to ``Path.cwd()``, `home` to ``Path.home()``, `env`
    to ``os.environ``.
    """
    resolved_start_dir = start_dir if start_dir is not None else Path.cwd()
    resolved_home = home if home is not None else Path.home()
    resolved_env: Mapping[str, str] = env if env is not None else os.environ

    merged: dict[str, Any] = {}

    merged = _merge_layer(merged, _normalize_roots(_read_yaml_mapping(_DEFAULT_RULESET_PATH)))

    home_ruleset = resolved_home / _HOME_RULESET_RELATIVE
    if home_ruleset.is_file():
        merged = _merge_layer(merged, _normalize_roots(_read_yaml_mapping(home_ruleset)))

    local_ruleset = _find_local_ruleset(resolved_start_dir)
    if local_ruleset is not None:
        merged = _merge_layer(merged, _normalize_roots(_read_yaml_mapping(local_ruleset)))

    env_ruleset_value = resolved_env.get(_ENV_VAR)
    if env_ruleset_value:
        merged = _merge_layer(merged, _normalize_roots(_read_yaml_mapping(Path(env_ruleset_value))))

    if explicit_path is not None:
        merged = _merge_layer(merged, _normalize_roots(_read_yaml_mapping(Path(explicit_path))))

    return FindingsConfig(**merged)


def find_git_root(start: Path) -> Path | None:
    """Walk up from `start` looking for a `.git` entry; return its containing dir.

    Pure filesystem check — no subprocess, no network. A `.git` entry may be
    a directory (a normal repo) or a file (a linked worktree, pointing at
    the real gitdir elsewhere); either counts. Returns None if no ancestor
    of `start` (including `start` itself) has a `.git` entry.
    """
    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def resolve_roots(config: FindingsConfig, trace: Trace) -> list[str] | None:
    """The allowed write roots for scope-escape checking, or None if unknown.

    If `config.roots` is explicitly set, it wins outright. Otherwise,
    auto-discover from `trace.metadata["cwd"]`: the git root containing that
    cwd if it's inside a git repo, else the bare cwd. Returns None only when
    there is no cwd at all, in which case the scope-escape rule cannot run
    (see FindingsReport.not_checked).
    """
    if config.roots is not None:
        return config.roots

    cwd = trace.metadata.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None

    git_root = find_git_root(Path(cwd))
    return [str(git_root) if git_root is not None else cwd]
