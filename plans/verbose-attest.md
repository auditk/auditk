# Plan: Add `--verbose` flag to `auditk attest`

## Goal
Add a `--verbose` flag to the `auditk attest` CLI command that prints each step's taxonomy label and reasoning as it is scored.

## Context
- The `attest` command builds and signs an evidence pack from traces.
- During attestation, `compute_drift()` scores each step against its declared intent.
- Three scorers exist: `JaccardScorer`, `NLIScorer`, and `TwoStageJudgeScorer`.
- Only `TwoStageJudgeScorer` currently emits `per_step` `StepDrift` objects with taxonomy labels and reasoning.
- `JaccardScorer` and `NLIScorer` only emit aggregate `flagged_steps` lists.
- To support `--verbose` consistently across all scorers, we must first enrich the two simpler scorers to emit `per_step` data, then wire the print hook into the CLI.

## TDD Approach
1. **Red**: Write tests that assert:
   - `JaccardScorer.score()` returns a `DriftReport` with `per_step` populated, containing `StepDrift` with `label` and `reasoning` for each scored step.
   - `NLIScorer.score()` returns a `DriftReport` with `per_step` populated similarly.
   - `build(verbose=True)` prints step labels and reasoning to stdout during attestation.
   - The `attest` CLI accepts `--verbose` and prints step labels and reasoning.
2. **Green**: Implement the minimal changes:
   - Add `per_step` population to `JaccardScorer` and `NLIScorer`.
   - Add `verbose: bool = False` to `build()` and print from `drift_metrics.per_step` when True.
   - Add `--verbose` flag to `attest` CLI and pass it to `build()`.
3. **Refactor**: Ensure protocol consistency, clean imports, and no regressions in existing tests.

## Files to Touch
| File | Change |
|------|--------|
| `src/auditk/analysis/scorers/jaccard.py` | Populate `per_step` dict with `StepDrift` for each scored step |
| `src/auditk/analysis/scorers/nli.py` | Populate `per_step` dict with `StepDrift` for each scored step |
| `src/auditk/attestation/pack.py` | Add `verbose` param to `build()`; print step info when True |
| `src/auditk/cli.py` | Add `--verbose` option to `attest` command; pass to `build()` |
| `tests/unit/test_drift.py` | Add tests for `JaccardScorer` `per_step` output |
| `tests/unit/test_nli_scorer.py` | Add tests for `NLIScorer` `per_step` output |
| `tests/unit/test_pack_builder.py` | Add test for `build(verbose=True)` stdout |
| `tests/e2e/test_cli_attest_verify.py` | Add test for `attest --verbose` CLI output |

## Acceptance Criteria
- `pytest tests/unit/test_drift.py tests/unit/test_nli_scorer.py tests/unit/test_pack_builder.py tests/e2e/test_cli_attest_verify.py -v` passes.
- Existing full test suite (`pytest`) passes without regressions.
- `attest --verbose` prints lines like: `s-1: faithful — Jaccard similarity 0.85 >= threshold 0.30`
- `attest` without `--verbose` produces no extra output (silent by default).
