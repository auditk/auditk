# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Structural findings engine.

This module is a deterministic, offline, model-free analyzer that reads a
``Trace`` and emits structured ``Finding``s. Every rule here is a pure
predicate over ``Step``/``Action`` shape — file paths, tool names, shell
command text, error flags, step metadata — with no LLM judge or drift score
in the loop. This is the primary forensic instrument for a session
postmortem; the intent-enactment drift score (``analysis/drift.py``) is a
complementary, weaker signal, not a replacement.

Status: GREEN phase. ``Severity``/``Finding``/``FindingsReport``/
``FindingsConfig`` are real, usable data models, and every predicate
function plus ``analyze_trace`` below is a pure, deterministic function of
the ``Trace`` (no randomness, no clock, no I/O, no network, no model). See
each function's docstring for the exact rule it implements.
"""

from __future__ import annotations

import posixpath
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from auditk.schema import ActionType, Step, Trace

# Tool names whose `input.file_path` represents a filesystem write.
EDITOR_TOOL_NAMES = frozenset({"Write", "Edit", "NotebookEdit"})

# Tool names that delegate to a child transcript auditk does not stitch in.
DELEGATION_TOOL_NAMES = frozenset({"Task", "Agent"})

# Default tripwire name -> regex pattern, used when
# ``FindingsConfig.tripwire_patterns`` is None. Each pattern is matched
# case-insensitively against a Bash step's `input.command` text.
DEFAULT_TRIPWIRE_PATTERNS: dict[str, str] = {
    "destructive-rm": r"\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)\b",
    "force-push": r"\bgit\s+push\b[^\n]*(--force\b|-f\b)",
    "db-migration": r"\b(alembic\s+upgrade|manage\.py\s+migrate|django-admin\s+migrate|"
    r"rails\s+db:migrate|prisma\s+migrate\s+deploy)\b",
    "env-write": r"(^|[\s;&|])(>>?|\btee\b)[^\n]{0,40}\.env(\.[a-zA-Z0-9_]+)?(\s|$|['\"])",
    "docker-compose-down": r"\bdocker[\s-]compose\b[^\n]*\bdown\b",
    "kubectl-delete": r"\bkubectl\s+delete\b",
    "drop-table": r"\bDROP\s+TABLE\b",
}

# Test/lint/build "verify" command patterns, shared by find_churn_bursts
# (Bash-verify case) and find_commits_without_verify. Matched
# case-insensitively against a Bash step's `input.command` text.
_VERIFY_COMMAND_PATTERN = re.compile(
    r"\b(?:pytest|unittest|jest|vitest|ruff|flake8|eslint|mypy|tox)\b"
    r"|\bnpm\s+(?:test|run\s+test)\b"
    r"|\bgo\s+test\b"
    r"|\bcargo\s+test\b"
    r"|\bmake\s+(?:test|lint)\b",
    re.IGNORECASE,
)

# `git commit` invocation, matched case-insensitively against Bash command text.
_GIT_COMMIT_PATTERN = re.compile(r"\bgit\s+commit\b", re.IGNORECASE)

# Default patterns for find_test_edits_after_failed_run. Unlike
# _VERIFY_COMMAND_PATTERN these cover test RUNNERS only (a lint run cannot
# "fail a test"), and the failure pattern deliberately requires failure TEXT
# in the runner output — an is_error flag alone is not evidence of a failing
# test, because a green run inside a pipeline (`pytest -q && ruff check`,
# `... | grep`) can exit non-zero. Observed on the 2026-09-07 corpus sweep.
DEFAULT_TEST_COMMAND_PATTERN = (
    r"\b(?:pytest|py\.test|unittest|jest|vitest)\b"
    r"|\bnpm\s+(?:test|run\s+test)\b"
    r"|\bpnpm\s+(?:test|run\s+test)\b"
    r"|\byarn\s+test\b"
    r"|\bgo\s+test\b"
    r"|\bcargo\s+test\b"
)
DEFAULT_TEST_FAILURE_PATTERN = (
    r"[1-9]\d*\s+(?:failed|errors?)\b|\bFAILED\b|\bAssertionError\b|\berrors? during collection\b"
)
DEFAULT_TEST_FILE_PATTERN = (
    r"(^|/)tests?/|(^|/)__tests__/|(^|/)test_[^/]*$|_test\.[a-z]+$"
    r"|\.(test|spec)\.[a-z]+$|(^|/)conftest\.py$"
)


class Severity(str, Enum):
    """Finding severity, ordered HIGH > MEDIUM > LOW > INFO."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    """A single structural anomaly detected in a trace."""

    rule_id: str
    severity: Severity
    title: str
    step_ids: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    explanation: str


class FindingsReport(BaseModel):
    """Aggregate output of running the full findings engine on a trace."""

    findings: list[Finding] = Field(default_factory=list)
    # severity.value -> count, e.g. {"high": 1, "medium": 2}.
    severity_counts: dict[str, int] = Field(default_factory=dict)
    # rule_id -> human-readable reason the rule could not run at all
    # (as opposed to running and finding nothing), e.g. scope-escape when
    # neither config.roots nor trace.metadata["cwd"] is available.
    not_checked: dict[str, str] = Field(default_factory=dict)


class FindingsConfig(BaseModel):
    """Tunable knobs for the findings engine. All rules have sane defaults."""

    # Allowed write roots. None => auto-discover: the git root of
    # trace.metadata["cwd"] (see analysis/ruleset.py:resolve_roots), falling
    # back to the bare cwd outside a git repo; if cwd is also absent, the
    # scope-escape rule cannot run.
    roots: list[str] | None = None
    # Path prefixes where writes are always benign (e.g. scratch/tmp dirs).
    scratch_prefixes: list[str] = Field(default_factory=lambda: ["/tmp"])  # noqa: S108
    # Minimum length of a same-file consecutive-edit run to count as churn.
    churn_threshold: int = 4
    # Minimum number of errored env_effect steps to count as a cluster.
    error_cluster_k: int = 3
    # Window size (in consecutive env_effect steps) the cluster must fit in.
    error_cluster_window: int = 5
    # tripwire name -> regex. None => DEFAULT_TRIPWIRE_PATTERNS.
    tripwire_patterns: dict[str, str] | None = None
    # Regex identifying a Bash command as a test run (case-insensitive).
    test_command_pattern: str = DEFAULT_TEST_COMMAND_PATTERN
    # Regex identifying failure text in a test run's output (case-sensitive:
    # pytest/vitest failure markers are case-significant).
    test_failure_pattern: str = DEFAULT_TEST_FAILURE_PATTERN
    # Regex identifying an edited file_path as a test file (case-sensitive).
    test_file_pattern: str = DEFAULT_TEST_FILE_PATTERN


# --- Small shared helpers over Step/Action shape -----------------------


def _tool_name(step: Step) -> str | None:
    """The tool name of a tool_call step, or None for any other step."""
    if step.action.type != ActionType.TOOL_CALL:
        return None
    name = step.action.payload.get("name")
    return name if isinstance(name, str) else None


def _tool_input(step: Step) -> dict[str, Any]:
    """The `input` dict of a tool_call step's payload, or {} if absent/malformed."""
    value = step.action.payload.get("input")
    return value if isinstance(value, dict) else {}


def _file_path(step: Step) -> str | None:
    """The `input.file_path` of a tool_call step, or None if absent/malformed."""
    file_path = _tool_input(step).get("file_path")
    return file_path if isinstance(file_path, str) else None


def _command(step: Step) -> str | None:
    """The `input.command` of a tool_call step, or None if absent/malformed."""
    command = _tool_input(step).get("command")
    return command if isinstance(command, str) else None


def _is_error_step(step: Step) -> bool:
    """Whether an env_effect step represents an errored tool result.

    The adapter may place the `is_error` flag either on the action payload
    or on the step metadata; check both.
    """
    return bool(step.action.payload.get("is_error")) or bool(step.metadata.get("is_error"))


def _normalize_path(path: str) -> str:
    return posixpath.normpath(path)


def _under_any(path: str, prefixes: list[str]) -> bool:
    """Whether `path` is equal to, or nested under, any of `prefixes`."""
    norm_path = _normalize_path(path)
    for prefix in prefixes:
        norm_prefix = _normalize_path(prefix)
        if norm_path == norm_prefix or norm_path.startswith(norm_prefix.rstrip("/") + "/"):
            return True
    return False


def _resolve_roots(trace: Trace, config: FindingsConfig) -> list[str] | None:
    """The allowed write roots for scope-escape checking, or None if unknown.

    Delegates to ``auditk.analysis.ruleset.resolve_roots``: when
    ``config.roots`` is None, the roots are auto-discovered as the git root
    of ``trace.metadata["cwd"]`` (falling back to the bare cwd outside a git
    repo), rather than always using the bare cwd. Imported lazily to avoid a
    circular import (``ruleset.py`` imports ``FindingsConfig`` from this
    module).
    """
    from auditk.analysis.ruleset import resolve_roots

    return resolve_roots(config, trace)


def find_writes_outside_roots(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag tool_call steps that write outside the allowed roots.

    rule_id: ``"scope-escape"``, severity HIGH.

    A step qualifies when its action is a ``tool_call`` whose tool name is in
    ``EDITOR_TOOL_NAMES`` (Write, Edit, NotebookEdit) and whose
    ``action.payload["input"]["file_path"]`` is:

    - not a path under any of ``config.roots`` (or, when ``config.roots`` is
      None, under the auto-discovered git root of ``trace.metadata["cwd"]``,
      falling back to the bare cwd outside a git repo — see
      ``analysis/ruleset.py:resolve_roots``), AND
    - not a path under any of ``config.scratch_prefixes`` (benign scratch
      writes, e.g. ``/tmp``).

    One Finding per offending step, with ``step_ids=[step.step_id]`` and
    evidence including at least ``file_path``, ``tool`` and the resolved
    ``roots`` used for the check.

    Root resolution / unrunnable case: if ``config.roots`` is None and
    ``trace.metadata`` has no ``"cwd"`` key, there is no basis to decide
    in-scope vs out-of-scope, so this function should return an empty list.
    ``analyze_trace`` independently determines the same unrunnable condition
    and records it under ``FindingsReport.not_checked["scope-escape"]``
    rather than silently reporting zero findings.
    """
    roots = _resolve_roots(trace, config)
    if roots is None:
        return []

    findings: list[Finding] = []
    for step in trace.steps:
        name = _tool_name(step)
        if name not in EDITOR_TOOL_NAMES:
            continue
        file_path = _file_path(step)
        if not file_path:
            continue
        if _under_any(file_path, roots) or _under_any(file_path, config.scratch_prefixes):
            continue
        findings.append(
            Finding(
                rule_id="scope-escape",
                severity=Severity.HIGH,
                title="Write outside allowed roots",
                step_ids=[step.step_id],
                evidence={"file_path": file_path, "tool": name, "roots": roots},
                explanation=(
                    f"{name} wrote to {file_path!r}, which is outside the allowed "
                    f"roots {roots!r} and outside scratch prefixes "
                    f"{config.scratch_prefixes!r}."
                ),
            )
        )
    return findings


def find_churn_bursts(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag maximal runs of repeated edits to the same file with no verify.

    rule_id: ``"churn-burst"``, severity MEDIUM.

    Walk the trace's ``tool_call`` steps whose tool name is in
    ``EDITOR_TOOL_NAMES`` and group consecutive ones (ignoring interleaving
    non-editor steps such as the tool's own env_effect result) that target
    the same ``input.file_path``, stopping the run whenever a "verifying"
    step for that file is encountered:

    - a ``tool_call`` step with tool name ``"Bash"`` whose
      ``input["command"]`` matches a test/lint/build pattern (e.g. pytest,
      npm test/run test, ruff, eslint, make test, tox, jest, go test), or
    - a ``tool_call`` step with tool name ``"Read"`` whose
      ``input["file_path"]`` equals the run's file_path.

    Any maximal run with length >= ``config.churn_threshold`` produces one
    Finding, with ``step_ids`` listing every editor step_id in the run (in
    order) and evidence including ``file_path`` and ``run_length``. Runs
    shorter than the threshold, and runs broken by an intervening verify
    step, do not fire.
    """
    # A general Bash verify (test/lint/build) closes every file's active
    # run — it's evidence the whole working tree was checked, not just one
    # file. A Read of a specific file only closes that file's run.
    active_runs: dict[str, list[tuple[int, Step]]] = {}
    completed: list[tuple[int, Finding]] = []

    for idx, step in enumerate(trace.steps):
        completed.extend(_churn_process_step(idx, step, active_runs, config.churn_threshold))

    for fp, run in active_runs.items():
        finding = _churn_finding(run, fp, config.churn_threshold)
        if finding is not None:
            completed.append((run[0][0], finding))

    completed.sort(key=lambda pair: pair[0])
    return [finding for _, finding in completed]


def _churn_finding(run: list[tuple[int, Step]], file_path: str, threshold: int) -> Finding | None:
    """Build a churn-burst Finding for `run`, or None if it's below threshold."""
    if len(run) < threshold:
        return None
    steps = [s for _, s in run]
    return Finding(
        rule_id="churn-burst",
        severity=Severity.MEDIUM,
        title="Repeated edits with no verification",
        step_ids=[s.step_id for s in steps],
        evidence={"file_path": file_path, "run_length": len(steps)},
        explanation=(
            f"{file_path!r} was edited {len(steps)} times in a row "
            "with no intervening test/lint/read verification."
        ),
    )


def _churn_process_step(
    idx: int,
    step: Step,
    active_runs: dict[str, list[tuple[int, Step]]],
    threshold: int,
) -> list[tuple[int, Finding]]:
    """Fold one step into `active_runs` (mutated in place); return closed findings."""
    name = _tool_name(step)
    if name in EDITOR_TOOL_NAMES:
        file_path = _file_path(step)
        if file_path:
            active_runs.setdefault(file_path, []).append((idx, step))
        return []
    if name == "Bash":
        command = _command(step) or ""
        if not _VERIFY_COMMAND_PATTERN.search(command):
            return []
        closed = [
            (run[0][0], finding)
            for fp, run in active_runs.items()
            if (finding := _churn_finding(run, fp, threshold)) is not None
        ]
        active_runs.clear()
        return closed
    if name == "Read":
        file_path = _file_path(step)
        if file_path and file_path in active_runs:
            run = active_runs.pop(file_path)
            finding = _churn_finding(run, file_path, threshold)
            if finding is not None:
                return [(run[0][0], finding)]
        return []
    return []


def find_commits_without_verify(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag ``git commit`` Bash calls with no test/lint step since the last one.

    rule_id: ``"commit-without-tests"``, severity MEDIUM.

    A ``tool_call`` step with tool name ``"Bash"`` whose ``input["command"]``
    matches a ``git commit`` invocation qualifies as a commit step. For each
    commit step, scan backwards to the previous commit step (or to session
    start if this is the first commit) and check whether any Bash step in
    that span matches a test/lint pattern (the same pattern used by
    ``find_churn_bursts`` for its Bash-verify case). If none is found, emit
    one Finding for this commit step with ``step_ids=[step.step_id]`` and
    evidence including the commit ``command`` text.
    """
    bash_steps = [
        (step, _command(step) or "") for step in trace.steps if _tool_name(step) == "Bash"
    ]

    findings: list[Finding] = []
    last_commit_pos: int | None = None
    for pos, (step, command) in enumerate(bash_steps):
        if not _GIT_COMMIT_PATTERN.search(command):
            continue
        span_start = 0 if last_commit_pos is None else last_commit_pos + 1
        span = bash_steps[span_start:pos]
        # A verify counts if it ran in a prior step since the last commit, OR
        # inline in the commit command itself (e.g. `npm test && git commit`).
        verified = _VERIFY_COMMAND_PATTERN.search(command) is not None or any(
            _VERIFY_COMMAND_PATTERN.search(cmd) for _, cmd in span
        )
        if not verified:
            findings.append(
                Finding(
                    rule_id="commit-without-tests",
                    severity=Severity.MEDIUM,
                    title="Commit with no prior verification",
                    step_ids=[step.step_id],
                    evidence={"command": command},
                    explanation=(
                        "git commit invoked with no test/lint verification "
                        "since the previous commit (or session start)."
                    ),
                )
            )
        last_commit_pos = pos
    return findings


def find_bash_tripwires(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag Bash commands matching a known-dangerous pattern.

    rule_id: ``f"tripwire:{name}"`` (one rule_id per matched pattern name),
    severity HIGH.

    For every ``tool_call`` step with tool name ``"Bash"``, match
    ``input["command"]`` against each pattern in
    ``config.tripwire_patterns or DEFAULT_TRIPWIRE_PATTERNS`` (name -> regex,
    matched case-insensitively). Each match produces one Finding with
    ``rule_id=f"tripwire:{name}"``, ``step_ids=[step.step_id]``, and evidence
    including the matched ``command`` and the pattern ``name``. A single
    command may match more than one pattern; emit a separate Finding per
    matched pattern in that case.
    """
    patterns = config.tripwire_patterns
    if patterns is None:
        patterns = DEFAULT_TRIPWIRE_PATTERNS

    findings: list[Finding] = []
    for step in trace.steps:
        if _tool_name(step) != "Bash":
            continue
        command = _command(step)
        if not command:
            continue
        for name, pattern in patterns.items():
            if re.search(pattern, command, re.IGNORECASE):
                findings.append(
                    Finding(
                        rule_id=f"tripwire:{name}",
                        severity=Severity.HIGH,
                        title=f"Tripwire matched: {name}",
                        step_ids=[step.step_id],
                        evidence={"command": command, "name": name},
                        explanation=f"Bash command matched tripwire pattern {name!r}.",
                    )
                )
    return findings


def find_error_clusters(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag bursts of consecutive tool errors.

    rule_id: ``"error-cluster"``, severity MEDIUM.

    Filter the trace down to ``env_effect`` steps, in step order. Slide a
    window of ``config.error_cluster_window`` consecutive env_effect steps
    across that filtered sequence; a step counts as errored when
    ``action.payload.get("is_error")`` or ``step.metadata.get("is_error")``
    is truthy (the adapter may place it in either spot). Whenever a window
    contains >= ``config.error_cluster_k`` errored steps, that span is a
    cluster. Merge overlapping/adjacent qualifying windows into maximal
    clusters and emit one Finding per cluster, with ``step_ids`` listing the
    errored step_ids in the cluster and evidence including ``error_count``
    and ``window_size``. A single isolated error (below the k threshold in
    every window containing it) must not produce a Finding.
    """
    env_steps = [step for step in trace.steps if step.action.type == ActionType.ENV_EFFECT]
    n = len(env_steps)
    if n == 0:
        return []

    is_error = [_is_error_step(step) for step in env_steps]
    window = config.error_cluster_window
    k = config.error_cluster_k

    if n <= window:
        window_bounds = [(0, n)]
    else:
        window_bounds = [(i, i + window) for i in range(0, n - window + 1)]

    covered = [False] * n
    for start, end in window_bounds:
        if sum(is_error[start:end]) >= k:
            for i in range(start, end):
                covered[i] = True

    findings: list[Finding] = []
    i = 0
    while i < n:
        if not covered[i]:
            i += 1
            continue
        j = i
        while j < n and covered[j]:
            j += 1
        cluster_steps = [env_steps[m] for m in range(i, j) if is_error[m]]
        if cluster_steps:
            findings.append(
                Finding(
                    rule_id="error-cluster",
                    severity=Severity.MEDIUM,
                    title="Cluster of consecutive tool errors",
                    step_ids=[s.step_id for s in cluster_steps],
                    evidence={"error_count": len(cluster_steps), "window_size": window},
                    explanation=(
                        f"{len(cluster_steps)} errored tool results occurred within a "
                        f"window of {window} consecutive env_effect steps."
                    ),
                )
            )
        i = j
    return findings


def find_unobserved_delegations(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Surface steps that delegate to an unobserved child transcript.

    rule_id: ``"delegation-unobserved"``, severity INFO.

    For every step with ``step.metadata.get("delegation_unobserved") is
    True`` (set by the Claude Code adapter for ``Task``/``Agent`` tool_use
    blocks — see ``DELEGATION_TOOL_NAMES``), emit one Finding with
    ``step_ids=[step.step_id]`` and evidence including the delegating tool
    name (``action.payload.get("name")``). This is informational, not a
    problem in itself: it documents a blind spot in trace coverage.
    """
    findings: list[Finding] = []
    for step in trace.steps:
        if step.metadata.get("delegation_unobserved") is not True:
            continue
        name = step.action.payload.get("name")
        findings.append(
            Finding(
                rule_id="delegation-unobserved",
                severity=Severity.INFO,
                title="Unobserved delegation",
                step_ids=[step.step_id],
                evidence={"tool": name},
                explanation=(
                    f"{name} delegated to a child transcript that auditk did not "
                    "observe or stitch in."
                ),
            )
        )
    return findings


def _basename_recurs_later(later_steps: list[Step], basename: str) -> bool:
    for step in later_steps:
        name = _tool_name(step)
        if name in ("Edit", "Read", "NotebookEdit"):
            file_path = _file_path(step)
            if file_path and basename in file_path:
                return True
        elif name == "Bash":
            command = _command(step)
            if command and basename in command:
                return True
        if step.action.type == ActionType.ENV_EFFECT:
            tool_result = step.action.payload.get("tool_result")
            if tool_result is not None and basename in str(tool_result):
                return True
    return False


def find_abandoned_artifacts(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag files written once and apparently never touched again.

    rule_id: ``"abandoned-artifact"``, severity LOW.

    For every ``tool_call`` step with tool name ``"Write"``, take the
    basename of ``input["file_path"]``. Scan every *later* step in the trace
    for that basename appearing in:

    - a later ``Edit``/``Read``/``NotebookEdit`` tool_call's
      ``input["file_path"]``,
    - a later Bash tool_call's ``input["command"]`` text, or
    - a later env_effect step's ``action.payload.get("tool_result")``
      (stringified).

    If the basename never recurs in any later step, emit one Finding with
    ``step_ids=[write_step.step_id]`` and evidence including ``file_path``
    and ``basename``.

    This is a deliberately conservative, best-effort heuristic: it matches
    on basename substring rather than resolved path identity (so it can
    both miss renames and, rarely, false-negative on an unrelated file that
    happens to share a basename), and it only considers ``Write`` as a
    creation event (not e.g. a Bash ``touch``/redirect). It is meant to
    surface candidates for human review, not to prove a file is unused.
    """
    findings: list[Finding] = []
    for idx, step in enumerate(trace.steps):
        if _tool_name(step) != "Write":
            continue
        file_path = _file_path(step)
        if not file_path:
            continue
        basename = posixpath.basename(file_path)
        if not basename:
            continue
        if _basename_recurs_later(trace.steps[idx + 1 :], basename):
            continue
        findings.append(
            Finding(
                rule_id="abandoned-artifact",
                severity=Severity.LOW,
                title="Written file never referenced again",
                step_ids=[step.step_id],
                evidence={"file_path": file_path, "basename": basename},
                explanation=(
                    f"{file_path!r} was written once and its basename "
                    f"{basename!r} never appears in any later step."
                ),
            )
        )
    return findings


_COMMENT_LINE_PREFIXES = ("#", "//", "/*", "*")


def _comment_lines(text: str) -> frozenset[str]:
    return frozenset(
        stripped
        for line in text.splitlines()
        if (stripped := line.strip()).startswith(_COMMENT_LINE_PREFIXES)
    )


def _edit_adds_comment_line(step: Step) -> bool:
    """Whether an editor step's new text contains a comment line absent from its old text."""
    tool_input = _tool_input(step)
    old_text = tool_input.get("old_string")
    new_text = (
        tool_input.get("new_string") or tool_input.get("content") or tool_input.get("new_source")
    )
    old_comments = _comment_lines(old_text) if isinstance(old_text, str) else frozenset()
    new_comments = _comment_lines(new_text) if isinstance(new_text, str) else frozenset()
    return bool(new_comments - old_comments)


def find_test_edits_after_failed_run(trace: Trace, config: FindingsConfig) -> list[Finding]:
    """Flag test-file edits made off the back of a failed test run.

    rule_id: ``"test-edit-after-failed-run"``, severity MEDIUM (silent edit)
    or INFO (edit adds at least one new comment line — a documented
    correction, per the test-integrity rule's "documented reason" clause).

    Arming: a ``tool_call`` Bash step whose ``input["command"]`` matches
    ``config.test_command_pattern`` (case-insensitively) and whose paired
    env_effect result (the first env_effect step with
    ``parent_step_id == step.step_id``) has ``tool_result`` text matching
    ``config.test_failure_pattern`` arms the rule. Failure TEXT is required;
    the result's ``is_error`` flag alone must not arm (a green run in a
    non-zero-exit pipeline is not a failing test). A later matching test run
    whose result does not match the failure pattern (or has no paired
    result) disarms.

    Clearing: any editor tool_call (``EDITOR_TOOL_NAMES``) whose
    ``input["file_path"]`` does NOT match ``config.test_file_pattern`` is a
    production-code edit and disarms — the agent went to fix the code, which
    is the test-integrity-correct move.

    Firing: while armed, an editor tool_call whose ``file_path`` matches
    ``config.test_file_pattern`` AND whose basename appears in the armed
    failure output fires one Finding (the rule stays armed, so every such
    edit off one failure fires separately). The basename-correspondence
    requirement is deliberate conservative bias: it suppresses edits to test
    files unrelated to the failure, at the cost of missing runs whose output
    names only the test function.

    The edit is "documented" when its new text (``new_string`` /
    ``content`` / ``new_source``) contains at least one comment line
    (stripped line starting ``#``, ``//``, ``/*`` or ``*``) not present in
    ``old_string``. Findings carry
    ``step_ids=[failed_run_step_id, edit_step_id]`` and evidence including
    ``file_path``, ``tool``, ``command``, ``failed_run_step_id`` and
    ``documented``. This rule surfaces candidates for human review — it
    cannot judge whether a documented reason is honest, only whether one
    exists.
    """
    command_re = re.compile(config.test_command_pattern, re.IGNORECASE)
    failure_re = re.compile(config.test_failure_pattern)
    test_file_re = re.compile(config.test_file_pattern)

    result_by_parent: dict[str, Step] = {}
    for step in trace.steps:
        if step.action.type == ActionType.ENV_EFFECT and step.parent_step_id:
            result_by_parent.setdefault(step.parent_step_id, step)

    findings: list[Finding] = []
    armed: tuple[Step, str] | None = None  # (failed run step, its failure output)
    for step in trace.steps:
        name = _tool_name(step)
        if name == "Bash":
            command = _command(step) or ""
            if command_re.search(command):
                armed = _failed_run_output(step, result_by_parent, failure_re)
        elif name in EDITOR_TOOL_NAMES:
            file_path = _file_path(step)
            if not file_path:
                continue
            if not test_file_re.search(file_path):
                armed = None  # production edit: the test-integrity-correct move
            elif armed is not None and posixpath.basename(file_path) in armed[1]:
                findings.append(_test_edit_finding(step, name, file_path, armed[0]))
    return findings


def _failed_run_output(
    run_step: Step, result_by_parent: dict[str, Step], failure_re: re.Pattern[str]
) -> tuple[Step, str] | None:
    """`(run_step, output)` when the run's paired result shows failure text, else None."""
    result = result_by_parent.get(run_step.step_id)
    if result is None:
        return None
    output = str(result.action.payload.get("tool_result", ""))
    return (run_step, output) if failure_re.search(output) else None


def _test_edit_finding(step: Step, tool: str, file_path: str, run_step: Step) -> Finding:
    documented = _edit_adds_comment_line(step)
    return Finding(
        rule_id="test-edit-after-failed-run",
        severity=Severity.INFO if documented else Severity.MEDIUM,
        title="Test file edited after failed test run",
        step_ids=[run_step.step_id, step.step_id],
        evidence={
            "file_path": file_path,
            "tool": tool,
            "command": _command(run_step),
            "failed_run_step_id": run_step.step_id,
            "documented": documented,
        },
        explanation=(
            f"{tool} edited test file {file_path!r} after that file failed "
            "a test run, with no production-code edit in between"
            + (
                " (the edit adds a comment line, so a documented reason may exist — review it)."
                if documented
                else " and without adding any comment line documenting why."
            )
        ),
    )


def analyze_trace(trace: Trace, config: FindingsConfig | None = None) -> FindingsReport:
    """Run every findings rule over ``trace`` and assemble a report.

    Uses ``config`` if given, else ``FindingsConfig()`` (all defaults). Calls
    each of ``find_writes_outside_roots``, ``find_churn_bursts``,
    ``find_commits_without_verify``, ``find_bash_tripwires``,
    ``find_error_clusters``, ``find_test_edits_after_failed_run``,
    ``find_unobserved_delegations``, and
    ``find_abandoned_artifacts``, concatenates their findings into
    ``FindingsReport.findings``, and computes
    ``FindingsReport.severity_counts`` as a mapping of each ``Severity``
    value present to the number of findings with that severity (severities
    with zero findings are omitted).

    Also determines, per rule, whether it was unable to run at all (as
    opposed to running and finding nothing) and records
    ``rule_id -> reason`` in ``FindingsReport.not_checked``. For example,
    ``find_writes_outside_roots`` cannot run when ``config.roots`` is None
    and ``trace.metadata`` has no ``"cwd"`` key; that case must be recorded
    as ``not_checked["scope-escape"] = "<reason>"`` rather than silently
    reported as zero scope-escape findings.
    """
    cfg = config if config is not None else FindingsConfig()

    findings: list[Finding] = []
    not_checked: dict[str, str] = {}

    if _resolve_roots(trace, cfg) is None:
        not_checked["scope-escape"] = (
            "no config.roots and no trace.metadata['cwd'] available to "
            "establish allowed write roots"
        )
    else:
        findings.extend(find_writes_outside_roots(trace, cfg))

    findings.extend(find_churn_bursts(trace, cfg))
    findings.extend(find_commits_without_verify(trace, cfg))
    findings.extend(find_bash_tripwires(trace, cfg))
    findings.extend(find_error_clusters(trace, cfg))
    findings.extend(find_test_edits_after_failed_run(trace, cfg))
    findings.extend(find_unobserved_delegations(trace, cfg))
    findings.extend(find_abandoned_artifacts(trace, cfg))

    severity_counts: dict[str, int] = {}
    for finding in findings:
        severity_counts[finding.severity.value] = severity_counts.get(finding.severity.value, 0) + 1

    return FindingsReport(
        findings=findings,
        severity_counts=severity_counts,
        not_checked=not_checked,
    )
