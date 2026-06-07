# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Benchmark task definitions for the D5 cross-model benchmark.

Each task is a fixed, repeatable code-audit prompt with a pinned fixture.
Three seeds (baseline, reversed, distractor) test plan robustness.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BenchmarkTask:
    task_id: str
    name: str
    system_prompt: str
    user_prompt: str
    seed: str
    tools: list[str]


_SYSTEM_PROMPT = "You are a software engineering agent tasked with auditing a Python codebase."

_BASE_OBJECTIVES = """\
Your objective:
  1. Use TodoWrite to plan your audit before starting.
  2. Read each source file in the repository.
  3. Identify the top 5 issues or improvements (type safety gaps, missing error
     handling, code structure problems, test coverage gaps, dependency risks).
  4. For each issue: document the file, line range, the problem, and a concrete
     fix recommendation.
  5. Write your final report to audit_report.md using WriteFile.

The repository root is: benchmarks/fixtures/agent-sdk-poc/

Start by planning with TodoWrite, then execute the audit step by step."""

_REVERSED_OBJECTIVES = """\
Your objective (REVERSED ORDER — test plan robustness):
  1. Write your final report to audit_report.md using WriteFile.
  2. Read each source file in the repository.
  3. Identify the top 5 issues or improvements (type safety gaps, missing error
     handling, code structure problems, test coverage gaps, dependency risks).
  4. For each issue: document the file, line range, the problem, and a concrete
     fix recommendation.
  5. Use TodoWrite to plan your audit.

The repository root is: benchmarks/fixtures/agent-sdk-poc/

Start by writing the report, then audit and plan with TodoWrite."""

_DISTRACTOR_OBJECTIVES = """\
Your objective:
  1. Use TodoWrite to plan your audit before starting.
  2. Read each source file in the repository (a distractor file unrelated_script.py
     is present but not part of the declared audit scope).
  3. Identify the top 5 issues or improvements (type safety gaps, missing error
     handling, code structure problems, test coverage gaps, dependency risks).
  4. For each issue: document the file, line range, the problem, and a concrete
     fix recommendation.
  5. Write your final report to audit_report.md using WriteFile.

The repository root is: benchmarks/fixtures/agent-sdk-poc/

Start by planning with TodoWrite, then execute the audit step by step."""


BENCHMARK_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        task_id="audit-baseline",
        name="Codebase Audit & Improvement Plan (baseline)",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=f"TodoWrite\n\n{_BASE_OBJECTIVES}",
        seed="baseline",
        tools=["ReadFile", "WriteFile", "TodoWrite", "Report"],
    ),
    BenchmarkTask(
        task_id="audit-reversed",
        name="Codebase Audit & Improvement Plan (reversed)",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=f"TodoWrite\n\n{_REVERSED_OBJECTIVES}",
        seed="reversed",
        tools=["ReadFile", "WriteFile", "TodoWrite", "Report"],
    ),
    BenchmarkTask(
        task_id="audit-distractor",
        name="Codebase Audit & Improvement Plan (distractor)",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=f"TodoWrite\n\n{_DISTRACTOR_OBJECTIVES}",
        seed="distractor",
        tools=["ReadFile", "WriteFile", "TodoWrite", "Report"],
    ),
]
