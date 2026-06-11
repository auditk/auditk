# Contributing to auditk

Thank you for considering a contribution. This is an Apache-2.0 open-source project.

## Before you start

- Read `README.md` and the open GitHub issues to understand where the project is heading.
- Check open issues/PRs before opening a duplicate.
- For non-trivial changes, open an issue first to discuss the approach.

## Development setup

```bash
git clone https://github.com/auditk/auditk
cd auditk
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                          # 98+ tests, should be green
python -m mypy --strict src/    # should be clean
```

## Engineering conventions

All code must follow the Engineering Constitution:

- Functions < 50 lines; files < 500 lines
- Type hints on all public APIs
- Early returns / guard clauses over nesting
- Fail loud: no silent `except: pass` — log and re-raise or skip with a warning
- Comments explain *why*, not *what*; method names self-document
- TDD where contracts are testable: write the test first

## Commit messages

```
<scope>: <imperative summary, ≤72 chars>

Body (optional): what changed and why. Wrap at 72 chars.

Co-Authored-By: Your Name <you@example.com>
```

## Pull requests

- One logical change per PR.
- All tests must pass; `mypy --strict src/` must be clean.
- Add or update tests for every changed behaviour.
- Do not add Python dependencies not already in `pyproject.toml` without
  a discussion in the PR/issue first.

## Probe families (`auditk-probes-*`)

Probe definition YAML files are dual-licensed **Apache-2.0 / CC-BY-SA-4.0**.
Add the following SPDX comment to the top of each YAML file:

```yaml
# SPDX-License-Identifier: Apache-2.0 AND CC-BY-SA-4.0
```

## Spec changes (`auditk-spec`)

The v0.1 schemas under `spec/v0.1/` are frozen. Changes require an RFC issue
against the `auditk-spec` repo and a new versioned directory (`v0.2/`).
Do not modify frozen schemas.

## License

By contributing, you agree that your contributions will be licensed under the
Apache License, Version 2.0, consistent with the project's `LICENSE` file.
