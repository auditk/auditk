# auditk — Agent Instructions

## Constitution
Before coding, read relevant sections from docs/constitution/.
See docs/constitution/INDEX.md for what to load when.

## Hard rules
- Never delete files without explicit confirmation
- Never run migrations without explicit confirmation  
- Run pytest tests/ -x --no-cov -q and mypy --strict src/auditk/ before any commit
- Do not modify demo packs (demos/) — they are signed artifacts

## Project state
- D1-D3 complete (coverage fix, NLI scorer, two-stage judge)
- D5 in progress (benchmark harness built, runs in progress)
- Active scorer: llm-judge@0.3 (nli-deberta + gpt-oss-120b judge)
- 230 tests passing

## Model routing
- Architecture/planning: escalate to user
- TDD implementation: proceed autonomously
- Mechanical tasks (lint, git, formatting): proceed without asking
