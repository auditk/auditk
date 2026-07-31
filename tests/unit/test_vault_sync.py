# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for `auditk.vault.writeback.result_for` and
`sync_bound_notes` (P3 of docs/proposals/phase-grade-binding-writeback.md).

Specifies the write-back bridge documented on `sync_bound_notes` itself: for
each note `discover_bound_notes` (P1) surfaces, run the caller-injected
`run_invariant` to get an `AdapterHealth`, map it to a `"pass"`/`"fail"`
verdict via `result_for` (W4), splice it in with `set_grade_binding_result`
(P2), and -- unless the splice was a no-op or `dry_run=True` -- enqueue the
spliced text into the vault's durable outbox
(`Harness/Skills/smart-notes/bin/vault-outbox.py enqueue`) rather than ever
writing the live vault working tree (W2).

Every test in this module is expected to FAIL right now:
- `result_for` tests fail because the stub raises `NotImplementedError`
  instead of returning `"pass"`/`"fail"`.
- `sync_bound_notes` tests fail the same way -- `NotImplementedError`
  propagates before any outbox interaction happens, which is the correct RED
  failure mode (not an import/typo/mypy error in this test file's own code).

Hermeticity: every test that constructs a vault + calls `sync_bound_notes`
sets `VAULT_OUTBOX` (via `monkeypatch.setenv`) to a `tmp_path` subdirectory
before doing anything else, so this module can never enqueue into the real
`~/.claude/vault-outbox` store, RED or GREEN. `run_invariant` is always a
plain lambda/function returning a fixed fake `AdapterHealth` -- never the
real corpus (`_load_corpus_sessions` / `check_adapter_health`) -- keeping
these tests fast and independent of `~/.claude/projects`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from auditk.adapters.health import AdapterHealth
from auditk.vault.writeback import (
    WritebackOutcome,
    result_for,
    set_grade_binding_result,
    sync_bound_notes,
)

# A portable stand-in for `vault-outbox.py enqueue`, written into tmp_path per
# test (see the `outbox_bin` fixture). The real script lives in the vault repo
# and is not importable here, so these tests must not hardcode its on-disk path
# (doing so passed locally but failed CI, which has no vault checkout). This
# fake honours the same enqueue contract `sync_bound_notes` relies on: parse
# `enqueue --target/--base-sha`, read the new content from stdin, and write a
# `pending/<id>.json` entry carrying the fields the assertions read. The real
# end-to-end against the actual `vault-outbox.py` is exercised separately, not
# in this unit module.
_FAKE_OUTBOX_SCRIPT = """\
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enqueue")
    e.add_argument("--target", required=True)
    e.add_argument("--base-sha")
    e.add_argument("--new", action="store_true")
    e.add_argument("--note")
    args = ap.parse_args()
    content = sys.stdin.buffer.read()
    pending = Path(os.environ["VAULT_OUTBOX"]) / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    csha = hashlib.sha256(content).hexdigest()
    entry = {
        "id": csha[:12],
        "target": args.target,
        "expect": "absent" if args.new else "sha",
        "base_sha": None if args.new else args.base_sha,
        "content_sha": csha,
        "content": content.decode("utf-8"),
    }
    (pending / (csha[:12] + ".json")).write_text(json.dumps(entry))
    print(entry["id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

BOUND_NOTE_FRONTMATTER = [
    'title: "Bound Note"',
    'grade_binding: "verified live by the auditk cc-adapter canary"',
    "grade_binding_id: auditk-cc-adapter-canary",
]


# --- fixture builders ---------------------------------------------------


def _build_fixture_vault(base: Path) -> Path:
    """A temp vault directory shaped like the real vault as far as
    `sync_bound_notes` cares: a `Permanent/` dir. Lives under pytest's
    `tmp_path`, so cleanup is automatic."""
    vault = base / "vault"
    (vault / "Permanent").mkdir(parents=True)
    return vault


def _write_note(path: Path, frontmatter_lines: list[str], body: str = "Body text.\n") -> None:
    """Write a Markdown note with a `---`-delimited frontmatter fence."""
    text = "---\n" + "\n".join(frontmatter_lines) + "\n---\n\n" + body
    path.write_text(text)


def _outbox_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `VAULT_OUTBOX` to a tmp_path subdirectory for the duration of
    one test, so `sync_bound_notes` (once implemented) can never reach the
    real `~/.claude/vault-outbox` store. `monkeypatch.setenv` restores the
    prior environment automatically at test teardown."""
    outbox = tmp_path / "outbox"
    monkeypatch.setenv("VAULT_OUTBOX", str(outbox))
    return outbox


def _pending_entries(outbox: Path) -> list[dict[str, object]]:
    """Every pending outbox entry, parsed from `pending/*.json`, per the
    entry schema in `vault-outbox.py:enqueue` (`id`, `target`, `expect`,
    `base_sha`, `content_sha`, `content`, `created`, `note`)."""
    pending_dir = outbox / "pending"
    if not pending_dir.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(pending_dir.glob("*.json"))]


@pytest.fixture
def outbox_bin(tmp_path: Path) -> Path:
    """A portable `vault-outbox.py` stand-in, written per test so this module
    never depends on a path outside the repo. Honours the enqueue contract
    `sync_bound_notes` uses; its store is still redirected via `VAULT_OUTBOX`."""
    script = tmp_path / "fake-vault-outbox.py"
    script.write_text(_FAKE_OUTBOX_SCRIPT)
    return script


# --- result_for -----------------------------------------------------------


def test_result_for_maps_ok_true_to_pass() -> None:
    """Case 1: `AdapterHealth(ok=True)` maps to `"pass"` (W4), pure -- no
    vault, no filesystem."""
    health = AdapterHealth(ok=True, breaches=[])

    assert result_for(health) == "pass", (
        f"expected result_for(AdapterHealth(ok=True, ...)) == 'pass', got {result_for(health)!r}"
    )


def test_result_for_maps_ok_false_to_fail() -> None:
    """Case 2: `AdapterHealth(ok=False)` maps to `"fail"` (W4), regardless of
    the specific breach messages -- breach content is never consulted."""
    health = AdapterHealth(ok=False, breaches=["session[0]: some breach"])

    assert result_for(health) == "fail", (
        f"expected result_for(AdapterHealth(ok=False, ...)) == 'fail', got {result_for(health)!r}"
    )


# --- sync_bound_notes -------------------------------------------------------


def test_sync_bound_notes_happy_path_enqueues_pass_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbox_bin: Path
) -> None:
    """Case 3: a bound note + an injected `run_invariant` returning
    `ok=True` -> exactly one pending outbox entry, targeting the note's
    vault-relative path, whose content carries `grade_binding_result: pass`
    and `grade_binding_checked: <checked iso>`. The outbox is never drained
    in this test."""
    outbox = _outbox_dir(tmp_path, monkeypatch)
    vault = _build_fixture_vault(tmp_path)
    note_path = vault / "Permanent" / "bound-note.md"
    _write_note(note_path, BOUND_NOTE_FRONTMATTER)
    checked = date(2026, 7, 31)

    outcomes = sync_bound_notes(
        vault,
        checked,
        run_invariant=lambda invariant_id: AdapterHealth(ok=True, breaches=[]),
        outbox_bin=outbox_bin,
    )

    entries = _pending_entries(outbox)
    assert len(entries) == 1, f"expected exactly one pending outbox entry, got {entries!r}"
    assert entries[0]["target"] == "Permanent/bound-note.md", (
        f"expected the pending entry's target to be the note's vault-relative path "
        f"'Permanent/bound-note.md', got {entries[0]['target']!r}"
    )
    content = entries[0]["content"]
    assert isinstance(content, str) and "grade_binding_result: pass" in content, (
        f"expected the enqueued content to contain 'grade_binding_result: pass', got {content!r}"
    )
    assert (
        isinstance(content, str) and f"grade_binding_checked: {checked.isoformat()}" in content
    ), (
        f"expected the enqueued content to contain "
        f"'grade_binding_checked: {checked.isoformat()}', got {content!r}"
    )
    assert outcomes == [
        WritebackOutcome(target="Permanent/bound-note.md", result="pass", enqueued=True)
    ], f"expected one enqueued 'pass' outcome for the bound note, got {outcomes!r}"


def test_sync_bound_notes_fail_verdict_enqueues_fail_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbox_bin: Path
) -> None:
    """Case 4: an injected `run_invariant` returning `ok=False` -> the
    enqueued content carries `grade_binding_result: fail`."""
    outbox = _outbox_dir(tmp_path, monkeypatch)
    vault = _build_fixture_vault(tmp_path)
    note_path = vault / "Permanent" / "bound-note.md"
    _write_note(note_path, BOUND_NOTE_FRONTMATTER)
    checked = date(2026, 7, 31)

    sync_bound_notes(
        vault,
        checked,
        run_invariant=lambda invariant_id: AdapterHealth(
            ok=False, breaches=["session[0]: some breach"]
        ),
        outbox_bin=outbox_bin,
    )

    entries = _pending_entries(outbox)
    assert len(entries) == 1, f"expected exactly one pending outbox entry, got {entries!r}"
    content = entries[0]["content"]
    assert isinstance(content, str) and "grade_binding_result: fail" in content, (
        f"expected the enqueued content to contain 'grade_binding_result: fail', got {content!r}"
    )


def test_sync_bound_notes_dry_run_enqueues_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbox_bin: Path
) -> None:
    """Case 5: `dry_run=True` -> every outcome has `enqueued=False` and the
    outbox store gains zero pending entries, even though the note would
    otherwise have changed."""
    outbox = _outbox_dir(tmp_path, monkeypatch)
    vault = _build_fixture_vault(tmp_path)
    note_path = vault / "Permanent" / "bound-note.md"
    _write_note(note_path, BOUND_NOTE_FRONTMATTER)
    checked = date(2026, 7, 31)

    outcomes = sync_bound_notes(
        vault,
        checked,
        run_invariant=lambda invariant_id: AdapterHealth(ok=True, breaches=[]),
        outbox_bin=outbox_bin,
        dry_run=True,
    )

    assert _pending_entries(outbox) == [], (
        "expected zero pending outbox entries when dry_run=True, but the store gained "
        f"entries: {_pending_entries(outbox)!r}"
    )
    assert all(not outcome.enqueued for outcome in outcomes), (
        f"expected every outcome to have enqueued=False under dry_run=True, got {outcomes!r}"
    )


def test_sync_bound_notes_noop_when_result_already_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbox_bin: Path
) -> None:
    """Case 6: a note that already carries the same `grade_binding_result`
    and `grade_binding_checked` the injected `run_invariant` would produce
    -> the splice is a byte-identical no-op, so nothing is enqueued and the
    outcome has `enqueued=False`."""
    outbox = _outbox_dir(tmp_path, monkeypatch)
    vault = _build_fixture_vault(tmp_path)
    note_path = vault / "Permanent" / "bound-note.md"
    checked = date(2026, 7, 31)
    already_current_text = set_grade_binding_result(
        "---\n" + "\n".join(BOUND_NOTE_FRONTMATTER) + "\n---\n\nBody text.\n",
        "pass",
        checked,
    )
    note_path.write_text(already_current_text)

    outcomes = sync_bound_notes(
        vault,
        checked,
        run_invariant=lambda invariant_id: AdapterHealth(ok=True, breaches=[]),
        outbox_bin=outbox_bin,
    )

    assert _pending_entries(outbox) == [], (
        "expected zero pending outbox entries for a note whose result is already current, "
        f"but the store gained entries: {_pending_entries(outbox)!r}"
    )
    assert outcomes == [
        WritebackOutcome(target="Permanent/bound-note.md", result="pass", enqueued=False)
    ], (
        f"expected a single no-op outcome (enqueued=False) for the already-current note, "
        f"got {outcomes!r}"
    )


def test_sync_bound_notes_ignores_note_without_grade_binding_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outbox_bin: Path
) -> None:
    """Case 7: a Permanent note with no `grade_binding_id` is not discovered
    by P1's `discover_bound_notes`, so `sync_bound_notes` must never consider
    it -- it produces no outcome for this note and enqueues nothing."""
    outbox = _outbox_dir(tmp_path, monkeypatch)
    vault = _build_fixture_vault(tmp_path)
    unbound_note = vault / "Permanent" / "unbound-note.md"
    _write_note(unbound_note, ['title: "Unbound Note"', "tags: [auditk]"])
    checked = date(2026, 7, 31)

    def _run_invariant_must_not_be_called(invariant_id: str) -> AdapterHealth:
        raise AssertionError(
            "run_invariant must not be called for an unbound vault "
            f"(got invariant_id={invariant_id!r})"
        )

    outcomes = sync_bound_notes(
        vault,
        checked,
        run_invariant=_run_invariant_must_not_be_called,
        outbox_bin=outbox_bin,
    )

    assert outcomes == [], f"expected no outcomes for a vault with no bound notes, got {outcomes!r}"
    assert _pending_entries(outbox) == [], (
        f"expected zero pending outbox entries for a vault with no bound notes, got "
        f"{_pending_entries(outbox)!r}"
    )
