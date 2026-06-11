# auditk — Agent Instructions

## Hard rules
- Never delete files without explicit confirmation
- Never run migrations without explicit confirmation
- Run pytest tests/ -x --no-cov -q and mypy --strict src/auditk/ before any commit
- Do not modify demo packs (demos/) — they are signed artifacts
- Do not touch plans/, docs/phases/ — gitignored internal documents

## Project state
- Active scorer: two-stage pipeline — NLI gate (DeBERTa-v3 asymmetric entailment) + LLM judge ensemble
- Judge is pluggable via the `Judge` protocol (src/auditk/analysis/protocols.py);
  default implementation uses Fireworks AI (set FIREWORKS_API_KEY); bring your own by
  implementing the protocol against any API
- 262 tests passing; mypy --strict clean
- See README.md for the full feature set and roadmap

## Model routing
- Architecture/planning: escalate to user
- TDD implementation: proceed autonomously
- Mechanical tasks (lint, git, formatting): proceed without asking

## Known issues
- 4 e2e tests in tests/e2e/test_cli_attest_verify.py fail when [nli] extra is not installed
  (AssertionError: nli@0.2 scorer requires [nli] extra)
  Fix: add pytest.mark.skipif guard checking for torch/transformers availability
