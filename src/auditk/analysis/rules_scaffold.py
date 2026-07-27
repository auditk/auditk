# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Starter-ruleset scaffolding from a discovered CLAUDE.md policy context.

``build_starter_ruleset`` powers ``auditk rules init``: it never invents new
rule content — the YAML body it emits is always exactly the shipped default
ruleset (``rules.default.yaml``, the same file ``load_ruleset`` layers on
top of) — it only *annotates* that body with a review comment listing which
CLAUDE.md-family files were scanned and which generic dev-safety phrases in
them are already covered by a shipped default (or, for concepts with no
generic pattern, a suggestion to add a custom one).

``_KEYWORD_RULES`` is intentionally a small, universal set of dev-safety
concepts (destructive deletes, commit hygiene, secrets, migrations,
container/orchestration teardown) — never wording tied to any one person's
or project's actual CLAUDE.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from auditk.analysis.policy_context import PolicyDoc

_DEFAULT_RULESET_PATH = Path(__file__).parent / "rules.default.yaml"

_RULE_WIDTH = 70


@dataclass(frozen=True)
class _KeywordRule:
    """One generic phrase-pattern -> suggested-rule mapping."""

    label: str
    pattern: re.Pattern[str]
    note: str


_KEYWORD_RULES: tuple[_KeywordRule, ...] = (
    _KeywordRule(
        label="delete / rm -rf",
        pattern=re.compile(
            r"\bdelete\b|\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\b",
            re.IGNORECASE,
        ),
        note="covered by the shipped 'destructive-rm' tripwire",
    ),
    _KeywordRule(
        label="run tests / lint before commit",
        pattern=re.compile(
            r"before\s+(every|each)\s+commit|run\s+tests|linter\s+before",
            re.IGNORECASE,
        ),
        note=(
            "already enforced by the built-in commit-without-tests rule (no ruleset change needed)"
        ),
    ),
    _KeywordRule(
        label=".env / secret / credential",
        pattern=re.compile(r"\.env\b|\bsecrets?\b|\bcredentials?\b", re.IGNORECASE),
        note="covered by the shipped 'env-write' tripwire",
    ),
    _KeywordRule(
        label="database migration",
        pattern=re.compile(r"\bmigrations?\b|\bmigrate\b", re.IGNORECASE),
        note="covered by the shipped 'db-migration' tripwire",
    ),
    _KeywordRule(
        label="docker compose",
        pattern=re.compile(r"\bdocker[\s-]compose\b", re.IGNORECASE),
        note="covered by the shipped 'docker-compose-down' tripwire",
    ),
    _KeywordRule(
        label="infrastructure / deploy config",
        pattern=re.compile(r"\binfrastructure\b|\binfra/|\bdeploy(?:ment)?\b", re.IGNORECASE),
        note=(
            "no generic tripwire ships for this — add a custom tripwire or "
            "roots rule for your infra paths"
        ),
    ),
    _KeywordRule(
        label="kubectl / kubernetes",
        pattern=re.compile(r"\bkubectl\b|\bkubernetes\b", re.IGNORECASE),
        note="covered by the shipped 'kubectl-delete' tripwire",
    ),
)


def scan_keyword_matches(text: str) -> list[_KeywordRule]:
    """Every `_KEYWORD_RULES` entry whose pattern matches somewhere in `text`."""
    return [rule for rule in _KEYWORD_RULES if rule.pattern.search(text)]


def _read_text_or_empty(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def _comment_block(policy_docs: list[PolicyDoc], matches: list[_KeywordRule]) -> str:
    rule = "# " + "-" * _RULE_WIDTH
    lines = [
        rule,
        "# auditk rules init scaffold -- REVIEW AND EDIT before use.",
        "#",
        "# This is a starting point to review and edit, NOT a live rule source: the",
        "# YAML body below is exactly the shipped default ruleset, unmodified. Edit",
        "# roots / scratch_prefixes / thresholds / tripwire_patterns to match your",
        "# actual policies, then save the result as one of:",
        "#   ~/.claude/auditk.rules.yaml   (per-user, applies to all your projects)",
        "#   .auditk/rules.yaml            (per-project, this repo only)",
        "# Both paths are meant to be gitignored -- see auditk.analysis.ruleset.",
        "#",
    ]
    if policy_docs:
        lines.append("# Files scanned:")
        lines.extend(f"#   - [{doc.scope}] {doc.path}" for doc in policy_docs)
    else:
        lines.append(
            "# No CLAUDE.md policy files were found for this location "
            "-- this is a defaults-only scaffold."
        )
    lines.append("#")
    if matches:
        lines.append("# Matched CLAUDE.md phrases -> suggested rule:")
        lines.extend(f"#   - {m.label}: {m.note}" for m in matches)
    else:
        lines.append(
            "# No known policy phrases matched. Review your CLAUDE.md manually and "
            "extend the defaults below as needed."
        )
    lines.append(rule)
    return "\n".join(lines)


def build_starter_ruleset(policy_docs: list[PolicyDoc]) -> str:
    """A starter ruleset: the shipped default YAML, prefixed with a review comment.

    Reads the text of every file in `policy_docs`, scans the concatenation
    for the generic dev-safety phrases in `_KEYWORD_RULES`, and prefixes the
    shipped default ruleset with a comment block naming the files scanned
    and any matches found. The YAML body is always byte-identical to
    ``rules.default.yaml`` -- this function only annotates, never invents
    rule content -- so the result is always valid input to
    ``auditk.analysis.ruleset.load_ruleset``.
    """
    combined_text = "\n".join(_read_text_or_empty(Path(doc.path)) for doc in policy_docs)
    matches = scan_keyword_matches(combined_text)
    header = _comment_block(policy_docs, matches)
    body = _DEFAULT_RULESET_PATH.read_text()
    return f"{header}\n{body}"
