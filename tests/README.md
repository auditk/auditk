# auditk — Test Pyramid

This document describes the seven testing layers that make up the auditk test suite. The layers form a pyramid: many fast unit tests at the base, a small number of expensive operational tests at the apex.

## 1. Unit

Unit tests cover individual functions and Pydantic model validations in complete isolation — no file I/O, no network, no subprocess calls. They target the analysis engine (`src/auditk/analysis/`), scoring helpers (`src/auditk/probes/scoring.py`), the canonical-JSON serialiser, and any pure utility logic. Tests live in `tests/` alongside the smoke tests (e.g. `tests/test_smoke.py`) and in future `tests/unit/` subdirectories as the module count grows. Run with: `pytest tests/ -k "not contract and not integration and not e2e"`.

## 2. Contract

Contract tests verify that the Pydantic models in `src/auditk/schema.py` are in exact alignment with the JSON Schema files published in `auditk-spec/spec/v0.1/`. For each (model, schema) pair a minimal valid instance is constructed in Python, serialised via `.model_dump(mode="json")`, then validated with `jsonschema.validate`. A mismatch here means the Python implementation has drifted from the normative spec. Tests live in `tests/contract/`. Run with: `pytest tests/contract/`. The env var `GLASSHOUSE_SPEC_PATH` overrides the default spec location (`../auditk-spec`); if the path does not exist the entire module is skipped with a clear message rather than failing — useful in CI environments that check out only this repo.

## 3. Integration

Integration tests exercise the adapters (`src/auditk/adapters/`) end-to-end against recorded fixture data stored in `tests/fixtures/`. Each adapter test reads a fixture file, runs the adapter, and asserts that the produced `Trace` validates against `trace.schema.json`. No live network or agent is required. Tests live in `tests/integration/`. Run with: `pytest tests/integration/`.

## 4. End-to-End (e2e)

End-to-end tests wire the full pipeline — adapter → analysis → probe runner → evidence pack builder — against the reference agents in `auditk-testbed`. These tests require the testbed to be available (either locally or in CI as a sibling checkout) and may be slow. They are gated by the `GLASSHOUSE_TESTBED_PATH` env var; if the path is absent the tests skip. Tests live in `tests/e2e/`. Run with: `pytest tests/e2e/`.

## 5. Probe Quality

Probe-quality tests measure precision, recall, false-positive rate, and false-negative rate for each probe family against the reference agents. They load a probe suite from `auditk-probes-*/probes/`, run it against `auditk-testbed/agents/aligned_minimal` and `.../vulnerable_minimal`, and assert that per-family metrics exceed the thresholds committed in `benchmark/*.json`. These tests are expensive and run in CI only on the `main` branch. Tests live in `tests/probe_quality/` (created when Phase 7 lands). Run with: `pytest tests/probe_quality/`.

## 6. Drift Validation

Drift-validation tests reproduce the correlation study in `auditk-spec/docs/drift-validation-v0.md`. They load the human-labelled corpus of 30 traces (committed to `tests/fixtures/labelled_traces/`) and assert that the Spearman ρ between human labels and `compute_drift` scores meets the published threshold (ρ ≥ 0.70 for v0), consistent with `auditk-spec/VALIDATION.md`. These tests are deterministic once the fixtures are committed. Tests live in `tests/drift_validation/` (created when Phase 9 lands). Run with: `pytest tests/drift_validation/`.

## 7. Multi-Tenant Operational

Multi-tenant operational tests verify that tenant isolation holds at the `glasshouse-platform` layer: cross-tenant queries return empty results, per-tenant signing keys are scoped correctly, and the fair-queue worker pool shows no starvation under synthetic load. These tests require a running Postgres instance (configured via `GLASSHOUSE_TEST_DATABASE_URL`) and are not run in the standard `pytest` invocation. Tests live in `tests/operational/` (created when Phase 10 lands). Run with: `pytest tests/operational/ --run-operational`.
