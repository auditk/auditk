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
from datetime import date
from pathlib import Path

import pytest

from auditk.vault.writeback import (
    KNOWN_INVARIANT_IDS,
    discover_bound_notes,
    set_grade_binding_result,
)

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


# =========================================================================
# RED-phase tests for `set_grade_binding_result` (P2 of
# docs/proposals/phase-grade-binding-writeback.md).
#
# `set_grade_binding_result` is currently a typed stub (module imports and
# mypy --strict passes; the body unconditionally raises `NotImplementedError`).
# Every test below is expected to FAIL right now:
# - The non-raising-contract tests (inserts/replaces/idempotence/preservation)
#   fail because the stub raises `NotImplementedError` instead of returning
#   spliced text.
# - The `ValueError`-contract tests (invalid `result`, missing fence) fail
#   because `pytest.raises(ValueError)` does not catch the stub's
#   `NotImplementedError` -- it propagates and the test errors, which is the
#   correct RED failure mode, not a bug in the test.
#
# These are pure string tests: no filesystem, no fixture vault. Note text is
# built inline so each test's expected splice is visible next to its input.
# =========================================================================


def test_set_grade_binding_result_inserts_both_fields_when_absent() -> None:
    """Case 1: neither field present -> both inserted immediately before the
    closing `---`, in the order result then checked, with every other
    frontmatter line and the body preserved verbatim."""
    text = '---\ntitle: "Some Note"\ntags: [auditk]\n---\n\nBody text.\n'

    result = set_grade_binding_result(text, "pass", date(2026, 7, 31))

    expected = (
        '---\ntitle: "Some Note"\ntags: [auditk]\n'
        "grade_binding_result: pass\n"
        "grade_binding_checked: 2026-07-31\n"
        "---\n\nBody text.\n"
    )
    assert result == expected, (
        "expected both grade_binding_result and grade_binding_checked inserted, in that "
        f"order, immediately before the closing fence; got {result!r}"
    )


def test_set_grade_binding_result_replaces_both_fields_in_place_when_present() -> None:
    """Case 2: both fields already present with stale values -> both replaced
    in place, preserving their existing line position and the order of every
    other frontmatter field; body untouched."""
    text = (
        '---\ntitle: "Some Note"\n'
        "grade_binding_result: fail\n"
        "grade_binding_checked: 2026-01-01\n"
        "tags: [auditk]\n"
        "---\n\nBody text.\n"
    )

    result = set_grade_binding_result(text, "pass", date(2026, 7, 31))

    expected = (
        '---\ntitle: "Some Note"\n'
        "grade_binding_result: pass\n"
        "grade_binding_checked: 2026-07-31\n"
        "tags: [auditk]\n"
        "---\n\nBody text.\n"
    )
    assert result == expected, (
        "expected both stale fields replaced in place at their existing line position, "
        f"with 'tags' untouched and unmoved; got {result!r}"
    )


def test_set_grade_binding_result_replaces_present_and_inserts_absent() -> None:
    """Case 3: one field present (stale), the other absent -> the present one
    is replaced in place, the absent one is inserted before the closing
    fence; no duplicate keys result."""
    text = (
        '---\ntitle: "Some Note"\ngrade_binding_result: fail\ntags: [auditk]\n---\n\nBody text.\n'
    )

    result = set_grade_binding_result(text, "pass", date(2026, 7, 31))

    expected = (
        '---\ntitle: "Some Note"\n'
        "grade_binding_result: pass\n"
        "tags: [auditk]\n"
        "grade_binding_checked: 2026-07-31\n"
        "---\n\nBody text.\n"
    )
    assert result == expected, (
        "expected the present 'grade_binding_result' replaced in place and the absent "
        f"'grade_binding_checked' inserted before the closing fence, with no duplicate "
        f"keys; got {result!r}"
    )
    assert result.count("grade_binding_result:") == 1, (
        f"expected exactly one 'grade_binding_result:' line, got {result!r}"
    )
    assert result.count("grade_binding_checked:") == 1, (
        f"expected exactly one 'grade_binding_checked:' line, got {result!r}"
    )


def test_set_grade_binding_result_is_idempotent() -> None:
    """Case 4: applying the splice twice with the same result/checked yields
    byte-identical output to applying it once."""
    text = '---\ntitle: "Some Note"\ntags: [auditk]\n---\n\nBody text.\n'

    once = set_grade_binding_result(text, "pass", date(2026, 7, 31))
    twice = set_grade_binding_result(once, "pass", date(2026, 7, 31))

    assert once == twice, (
        f"expected idempotent splice (applying twice == applying once); once={once!r} "
        f"twice={twice!r}"
    )


def test_set_grade_binding_result_preserves_lookalike_body_lines() -> None:
    """Case 5: body preservation stress. The body itself contains a line that
    looks like a frontmatter field (`grade_binding_result: something`) and a
    `---` horizontal rule. Only the actual frontmatter fence may be touched;
    both body look-alike lines must survive byte-for-byte."""
    text = (
        '---\ntitle: "Some Note"\ntags: [auditk]\n---\n\n'
        "Body text with a look-alike line: grade_binding_result: something\n"
        "\n---\n\n"
        "More body after a horizontal rule.\n"
    )

    result = set_grade_binding_result(text, "pass", date(2026, 7, 31))

    expected = (
        '---\ntitle: "Some Note"\ntags: [auditk]\n'
        "grade_binding_result: pass\n"
        "grade_binding_checked: 2026-07-31\n"
        "---\n\n"
        "Body text with a look-alike line: grade_binding_result: something\n"
        "\n---\n\n"
        "More body after a horizontal rule.\n"
    )
    assert result == expected, (
        "expected only the frontmatter fence touched; the body's look-alike "
        f"'grade_binding_result:' line and its '---' horizontal rule must be untouched "
        f"verbatim; got {result!r}"
    )


def test_set_grade_binding_result_preserves_trailing_newline_when_present() -> None:
    """Case 6a: input text ending with a single trailing newline stays ending
    with exactly one trailing newline."""
    text = '---\ntitle: "Some Note"\n---\n\nBody text.\n'

    result = set_grade_binding_result(text, "pass", date(2026, 7, 31))

    assert result.endswith("Body text.\n"), (
        f"expected exactly one trailing newline preserved after the body, got {result!r}"
    )
    assert not result.endswith("Body text.\n\n"), (
        f"expected no extra trailing newline appended, got {result!r}"
    )


def test_set_grade_binding_result_preserves_absent_trailing_newline() -> None:
    """Case 6b: input text with NO trailing newline stays without one."""
    text = '---\ntitle: "Some Note"\n---\n\nBody text without trailing newline'

    result = set_grade_binding_result(text, "pass", date(2026, 7, 31))

    assert not result.endswith("\n"), (
        f"expected no trailing newline to be added when the input had none, got {result!r}"
    )
    assert result.endswith("Body text without trailing newline"), (
        f"expected the body's final line preserved exactly, got {result!r}"
    )


def test_set_grade_binding_result_preserves_other_fields_and_order() -> None:
    """Case 7: other frontmatter fields (tags, grade, grade_binding,
    grade_binding_id) and their relative order are preserved verbatim when
    both target fields are inserted."""
    text = (
        "---\n"
        'title: "Some Note"\n'
        "tags: [auditk, vault]\n"
        "grade: A\n"
        'grade_binding: "verified live by the auditk cc-adapter canary"\n'
        "grade_binding_id: auditk-cc-adapter-canary\n"
        "---\n\nBody text.\n"
    )

    result = set_grade_binding_result(text, "fail", date(2026, 7, 31))

    expected = (
        "---\n"
        'title: "Some Note"\n'
        "tags: [auditk, vault]\n"
        "grade: A\n"
        'grade_binding: "verified live by the auditk cc-adapter canary"\n'
        "grade_binding_id: auditk-cc-adapter-canary\n"
        "grade_binding_result: fail\n"
        "grade_binding_checked: 2026-07-31\n"
        "---\n\nBody text.\n"
    )
    assert result == expected, (
        "expected 'title', 'tags', 'grade', 'grade_binding', and 'grade_binding_id' "
        f"preserved verbatim and in their original relative order; got {result!r}"
    )


def test_set_grade_binding_result_rejects_invalid_result_value() -> None:
    """Case 8: `result` values other than 'pass' or 'fail' raise ValueError --
    the write boundary fails closed rather than writing an unknown verdict."""
    text = '---\ntitle: "Some Note"\n---\n\nBody text.\n'

    with pytest.raises(ValueError, match="pass|fail"):
        set_grade_binding_result(text, "error", date(2026, 7, 31))


def test_set_grade_binding_result_rejects_text_with_no_frontmatter_fence() -> None:
    """Case 9: text with no leading `---` frontmatter fence raises ValueError
    -- this function must never fabricate a fence."""
    text = "No frontmatter here.\nJust body text.\n"

    with pytest.raises(ValueError, match="frontmatter|fence|---"):
        set_grade_binding_result(text, "pass", date(2026, 7, 31))


def test_set_grade_binding_result_renders_checked_date_as_iso_with_zero_padding() -> None:
    """Case 10: `checked` is rendered as `YYYY-MM-DD`, zero-padded (not e.g.
    '2026-1-5' for a single-digit month/day)."""
    text = '---\ntitle: "Some Note"\n---\n\nBody text.\n'

    result = set_grade_binding_result(text, "pass", date(2026, 1, 5))

    assert "grade_binding_checked: 2026-01-05" in result, (
        f"expected zero-padded ISO date 'grade_binding_checked: 2026-01-05' in output, "
        f"got {result!r}"
    )
