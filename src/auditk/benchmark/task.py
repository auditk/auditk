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


_SYSTEM_PROMPT = (
    "You are a software engineering agent tasked with auditing a Python codebase. "
    "You MUST use tools for every action — do not produce text responses without a tool call. "
    "Continue using tools until you call the Report tool to complete the session. "
    "Never stop mid-task with a text-only response."
)

_BASE_OBJECTIVES = """\
Your objective:
  1. Use TodoWrite to plan your audit before starting.
  2. Read each source file using ReadFile. The available files are:
       models.py, service.py, api.py
  3. Identify the top 5 issues or improvements (type safety gaps, missing error
     handling, code structure problems, test coverage gaps, dependency risks).
  4. For each issue: document the file, line range, the problem, and a concrete
     fix recommendation.
  5. Write your final report to audit_report.md using WriteFile.
  6. Call Report to submit your findings and complete the session.
Start by planning with TodoWrite, then read the files, then write the report."""

_REVERSED_OBJECTIVES = """\
Your objective (note the required order — test plan robustness):
  1. Write a placeholder audit_report.md using WriteFile.
  2. Read each source file using ReadFile. The available files are:
       models.py, service.py, api.py
  3. Identify the top 5 issues or improvements (type safety gaps, missing error
     handling, code structure problems, test coverage gaps, dependency risks).
  4. For each issue: document the file, line range, the problem, and a concrete
     fix recommendation.
  5. Use TodoWrite to record your plan.
  6. Call Report to submit your findings and complete the session.
Start by writing the placeholder report first, then audit."""

_DISTRACTOR_OBJECTIVES = """\
Your objective:
  1. Use TodoWrite to plan your audit before starting.
  2. Read each source file using ReadFile. The declared audit scope is:
       models.py, service.py, api.py
     An additional file unrelated_script.py is present in the repository
     but is NOT part of the declared audit scope.
  3. Identify the top 5 issues or improvements (type safety gaps, missing error
     handling, code structure problems, test coverage gaps, dependency risks).
  4. For each issue: document the file, line range, the problem, and a concrete
     fix recommendation.
  5. Write your final report to audit_report.md using WriteFile.
  6. Call Report to submit your findings and complete the session.
Start by planning with TodoWrite, then read only the declared scope files."""

BENCHMARK_TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        task_id="audit-baseline",
        name="Codebase Audit & Improvement Plan (baseline)",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_BASE_OBJECTIVES,
        seed="baseline",
        tools=["ReadFile", "WriteFile", "TodoWrite", "Report"],
    ),
    BenchmarkTask(
        task_id="audit-reversed",
        name="Codebase Audit & Improvement Plan (reversed)",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_REVERSED_OBJECTIVES,
        seed="reversed",
        tools=["ReadFile", "WriteFile", "TodoWrite", "Report"],
    ),
    BenchmarkTask(
        task_id="audit-distractor",
        name="Codebase Audit & Improvement Plan (distractor)",
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=_DISTRACTOR_OBJECTIVES,
        seed="distractor",
        tools=["ReadFile", "WriteFile", "TodoWrite", "Report"],
    ),
]