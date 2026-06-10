# auditk — Agent Instructions

## Constitution
Before coding, read relevant sections from docs/constitution/.
See docs/constitution/INDEX.md for what to load when.

## Hard rules
- Never delete files without explicit confirmation
- Never run migrations without explicit confirmation
- Run pytest tests/ -x --no-cov -q and mypy --strict src/auditk/ before any commit
- Do not modify demo packs (demos/) — they are signed artifacts
- Do not touch plans/, docs/phases/ — gitignored internal documents

## Project state
- D1-D5 complete (coverage fix, NLI scorer, two-stage judge, benchmark harness, 4-model benchmark)
- Active scorer: two-stage pipeline — NLI gate (DeBERTa-v3) + LLM judge ensemble (gpt-oss-120b)
- 262 tests passing; mypy --strict clean

## Model routing
- Architecture/planning: escalate to user
- TDD implementation: proceed autonomously
- Mechanical tasks (lint, git, formatting): proceed without asking

## Known issues
- 4 e2e tests in tests/e2e/test_cli_attest_verify.py fail when [nli] extra is not installed
  (AssertionError: nli@0.2 scorer requires [nli] extra)
  Fix: add pytest.mark.skipif guard checking for torch/transformers availability
