# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""RED-phase tests for `auditk.vault.writeback.discover_bound_notes` (P1 of
docs/proposals/phase-grade-binding-writeback.md).

Specifies the read-only discovery contract documented on
`discover_bound_notes` itself: walk `<vault>/Permanent/*.md` only, parse each
note's `---` frontmatter fence, and return a `BoundNote` for every note whose
`grade_binding_id` field is a member of `known_ids`. A note that declares the
human-prose `grade_binding` field but no `grade_binding_id` is the
"declared but not auto-writable" case and must stay excluded (correct by
design, not a bug -- see the read side, `vault-doctor --claims`). Notes
outside `Permanent/` (e.g. under `Hypotheses/`) are never considered, even if
they carry a matching id. The function must never write anything, anywhere,
regardless of outcome.

Every test in this module is expected to FAIL right now with a
`NotImplementedError` -- `discover_bound_notes` is a typed stub (module
imports and mypy --strict passes; the body simply raises). This is the RED
phase: the real walk/parse logic lands in the next (GREEN) phase, after
human review of this file and the module's stub contract.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from auditk.vault.writeback import KNOWN_INVARIANT_IDS, discover_bound_notes

# --- fixture builders ---------------------------------------------------


def _build_fixture_vault() -> Path:
    """A temp vault directory shaped like the real vault as far as
    `discover_bound_notes` cares: an `.obsidian` marker dir, a `Permanent/`
    dir, and a `Hypotheses/` dir. Caller owns cleanup (`shutil.rmtree`)."""
    vault = Path(tempfile.mkdtemp(prefix="auditk-vault-writeback-test-"))
    (vault / ".obsidian").mkdir()
    (vault / "Permanent").mkdir()
    (vault / "Hypotheses").mkdir()
    return vault


def _write_note(path: Path, frontmatter_lines: list[str], body: str = "Body text.\n") -> None:
    """Write a Markdown note with a `---`-delimited frontmatter fence."""
    text = "---\n" + "\n".join(frontmatter_lines) + "\n---\n\n" + body
    path.write_text(text)


def _snapshot_vault(vault: Path) -> dict[str, str]:
    """sha256 hex digest of every file under `vault`, keyed by path relative
    to `vault` -- used by the read-only guard test to prove nothing changed."""
    return {
        str(path.relative_to(vault)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }


# --- tests ---------------------------------------------------------------


def test_discovers_permanent_note_with_matching_grade_binding_id() -> None:
    """Case 1: a Permanent note carrying both the human-prose `grade_binding`
    line and a `grade_binding_id` in `known_ids` must be discovered, with its
    path and invariant_id both surfaced on the returned `BoundNote`."""
    vault = _build_fixture_vault()
    try:
        note_path = vault / "Permanent" / "bound-note.md"
        _write_note(
            note_path,
            [
                'title: "Bound Note"',
                'grade_binding: "verified live by the auditk cc-adapter canary"',
                "grade_binding_id: auditk-cc-adapter-canary",
            ],
        )

        found = discover_bound_notes(vault)

        assert len(found) == 1, (
            f"expected exactly one bound note for a single matching Permanent note, got {found!r}"
        )
        assert found[0].path == note_path, (
            f"expected discovered BoundNote.path == {note_path}, got {found[0].path}"
        )
        assert found[0].invariant_id == "auditk-cc-adapter-canary", (
            "expected discovered BoundNote.invariant_id == 'auditk-cc-adapter-canary', "
            f"got {found[0].invariant_id!r}"
        )
    finally:
        shutil.rmtree(vault)


def test_excludes_note_with_grade_binding_id_not_in_known_ids() -> None:
    """Case 2: a Permanent note with a `grade_binding_id` that is NOT a
    member of `known_ids` must be excluded -- auditk only owns write-back for
    the invariants it knows about."""
    vault = _build_fixture_vault()
    try:
        note_path = vault / "Permanent" / "unrelated-check.md"
        _write_note(
            note_path,
            [
                'title: "Unrelated Check"',
                "grade_binding_id: some-other-check",
            ],
        )

        found = discover_bound_notes(vault, KNOWN_INVARIANT_IDS)

        assert note_path not in [n.path for n in found], (
            "a note bound to 'some-other-check' (outside known_ids) must be excluded, "
            f"but discover_bound_notes returned {found!r}"
        )
    finally:
        shutil.rmtree(vault)


def test_excludes_note_with_grade_binding_prose_but_no_id() -> None:
    """Case 3: a Permanent note that declares the human-prose `grade_binding`
    field but no `grade_binding_id` is the "declared but not auto-writable"
    case -- it must be excluded and stays `unverified` by design."""
    vault = _build_fixture_vault()
    try:
        note_path = vault / "Permanent" / "declared-not-bound.md"
        _write_note(
            note_path,
            [
                'title: "Declared Not Bound"',
                'grade_binding: "should be checked by the auditk canary one day"',
            ],
        )

        found = discover_bound_notes(vault)

        assert note_path not in [n.path for n in found], (
            "a note with a 'grade_binding' prose line but no 'grade_binding_id' must be "
            f"excluded (declared-but-not-auto-writable case), but got {found!r}"
        )
    finally:
        shutil.rmtree(vault)


def test_excludes_note_with_no_grade_binding_fields() -> None:
    """Case 4: an ordinary Permanent note with neither `grade_binding` nor
    `grade_binding_id` must be excluded."""
    vault = _build_fixture_vault()
    try:
        note_path = vault / "Permanent" / "ordinary-note.md"
        _write_note(
            note_path,
            [
                'title: "Ordinary Note"',
                "tags: [auditk]",
            ],
        )

        found = discover_bound_notes(vault)

        assert note_path not in [n.path for n in found], (
            f"an ordinary note with no grade-binding fields must never be discovered, got {found!r}"
        )
    finally:
        shutil.rmtree(vault)


def test_excludes_matching_note_outside_permanent() -> None:
    """Case 5: a note under `Hypotheses/` (NOT `Permanent/`) carrying a
    matching `grade_binding_id` must still be excluded -- discovery is
    Permanent/-only, matching the read side's own scope."""
    vault = _build_fixture_vault()
    try:
        note_path = vault / "Hypotheses" / "not-yet-permanent.md"
        _write_note(
            note_path,
            [
                'title: "Not Yet Permanent"',
                "grade_binding_id: auditk-cc-adapter-canary",
            ],
        )

        found = discover_bound_notes(vault)

        assert note_path not in [n.path for n in found], (
            "a matching note living outside Permanent/ (e.g. under Hypotheses/) must be "
            f"excluded, but got {found!r}"
        )
    finally:
        shutil.rmtree(vault)


def test_discovers_both_of_two_bound_notes() -> None:
    """Case 6: two distinct Permanent notes both bound to a known invariant
    must both be discovered -- assert the full set of discovered paths."""
    vault = _build_fixture_vault()
    try:
        first_path = vault / "Permanent" / "first-bound-note.md"
        second_path = vault / "Permanent" / "second-bound-note.md"
        _write_note(first_path, ["grade_binding_id: auditk-cc-adapter-canary"])
        _write_note(second_path, ["grade_binding_id: auditk-cc-adapter-canary"])

        found = discover_bound_notes(vault)

        assert {n.path for n in found} == {first_path, second_path}, (
            f"expected both bound notes discovered ({first_path}, {second_path}), "
            f"got paths {[n.path for n in found]!r}"
        )
    finally:
        shutil.rmtree(vault)


def test_discover_bound_notes_is_read_only() -> None:
    """Case 7: read-only guard. Snapshot every file's bytes under the vault
    before and after calling `discover_bound_notes`; nothing may change.

    In RED, `discover_bound_notes` raises `NotImplementedError` before the
    "after" snapshot is ever taken, so this test currently fails on that
    exception rather than on a snapshot mismatch -- that is expected and
    still correctly expresses the read-only intent for GREEN to satisfy.
    """
    vault = _build_fixture_vault()
    try:
        note_path = vault / "Permanent" / "bound-note.md"
        _write_note(note_path, ["grade_binding_id: auditk-cc-adapter-canary"])

        before = _snapshot_vault(vault)
        discover_bound_notes(vault)
        after = _snapshot_vault(vault)

        assert before == after, (
            "discover_bound_notes must never modify any file under the vault, "
            f"but the snapshot changed: before={before!r} after={after!r}"
        )
    finally:
        shutil.rmtree(vault)
