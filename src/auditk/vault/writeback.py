# Copyright 2026 Matt Dawson and the AuditK Project Contributors
# SPDX-License-Identifier: Apache-2.0
"""Grade-binding write-back: discover vault notes bound to an auditk invariant
(P1 of docs/proposals/phase-grade-binding-writeback.md; the write half
reciprocal to the vault's own read side, `vault-doctor --claims`).

A "bound" note is a Permanent note whose frontmatter carries a
`grade_binding_id` field naming an invariant auditk is responsible for (see
`KNOWN_INVARIANT_IDS`). This module owns the read-only discovery half of the
loop: finding which notes are bound, so a later phase (P2/P3, not built here)
can compute each invariant's live result and push `pass`/`fail` + a checked
date back into that note via the vault's outbox, never by writing the vault
working tree directly.

P1 scope is discovery only: no frontmatter mutation, no corpus access, no
outbox interaction. `discover_bound_notes` walks the vault read-only and
never writes anything; it is deliberately tolerant of malformed notes (a
missing fence, unparseable YAML, or a fence that isn't a mapping is skipped,
never raised) since a multi-writer vault will occasionally have a
half-written note mid-edit.

On frontmatter parsing: auditk already depends on `pyyaml` and has an
existing YAML-mapping-reading pattern (`_read_yaml_mapping` in
`analysis/ruleset.py`, using `yaml.safe_load` on a whole-file YAML mapping).
That helper is not reused as-is here because a note's frontmatter is only
the `---`-delimited fence at the top of a Markdown file, not a standalone
YAML document -- `_extract_frontmatter` below splits the fence out and then
calls `yaml.safe_load` on it, mirroring `_read_yaml_mapping`'s use of
`yaml.safe_load` + `yaml.YAMLError` handling rather than inventing a new
parsing approach. No local frontmatter/Markdown-fence helper existed
elsewhere in `src/auditk` prior to this module (grepping `frontmatter`
across `src/` turned up nothing); P2 (the actual splice, not built here)
is expected to extend this same helper rather than add a second one.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from auditk.adapters.health import AdapterHealth

# The invariant ids auditk currently owns a write-back for. A bound note's
# `grade_binding_id` frontmatter field must be a member of this set (or of
# whatever set a caller passes explicitly) to be discovered. Seeded with the
# one real invariant this write-back phase is built for; a future invariant
# registered elsewhere in auditk should extend this set, not replace it.
KNOWN_INVARIANT_IDS: frozenset[str] = frozenset({"auditk-cc-adapter-canary"})


@dataclass(frozen=True)
class BoundNote:
    """One vault note discovered to be bound to an auditk invariant: its
    file path and the `grade_binding_id` it declared."""

    path: Path
    invariant_id: str


def _extract_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse a note's leading `---`-delimited frontmatter fence as a YAML
    mapping, mirroring `analysis/ruleset.py`'s `_read_yaml_mapping` use of
    `yaml.safe_load` + `yaml.YAMLError` handling. Returns `None` -- never
    raises -- if there is no fence, the fence fails to parse, or it does not
    parse to a mapping; the caller treats `None` as "skip this note"."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    fence = text[4:end]
    try:
        loaded = yaml.safe_load(fence)
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def discover_bound_notes(
    vault: Path, known_ids: frozenset[str] = KNOWN_INVARIANT_IDS
) -> list[BoundNote]:
    """Read-only discovery of every Permanent note bound to a known invariant.

    - Walks `<vault>/Permanent/*.md` only. Notes anywhere else in the vault
      (e.g. `Hypotheses/`) are never considered, matching the scope of the
      read side (`vault-doctor --claims`), which is also Permanent-only.
    - For each note, parses its leading `---`-delimited frontmatter fence.
      A note with no frontmatter fence, an unparseable fence (e.g. a
      half-written note from a concurrent writer), or a fence that does not
      parse to a mapping is silently skipped -- this function never raises
      on a malformed note.
    - A note is discovered (returned as a `BoundNote`) iff its frontmatter
      has a `grade_binding_id` field whose value is a string member of
      `known_ids`.
    - A note that carries the human-prose `grade_binding` field but no
      `grade_binding_id` is excluded: that is the "declared but not
      auto-writable" case, and it is correct for it to stay unverified on
      the read side rather than being silently picked up here.
    - Performs no writes of any kind, to the note or anywhere else, and
      never touches anything outside `<vault>/Permanent/`.
    """
    found: list[BoundNote] = []
    for note_path in sorted((vault / "Permanent").glob("*.md")):
        try:
            text = note_path.read_text()
        except OSError:
            continue
        frontmatter = _extract_frontmatter(text)
        if frontmatter is None:
            continue
        invariant_id = frontmatter.get("grade_binding_id")
        if isinstance(invariant_id, str) and invariant_id in known_ids:
            found.append(BoundNote(path=note_path, invariant_id=invariant_id))
    return found


def set_grade_binding_result(text: str, result: str, checked: date) -> str:
    """Set/replace `grade_binding_result` and `grade_binding_checked` in a
    note's leading `---`-delimited frontmatter fence (P2 of
    docs/proposals/phase-grade-binding-writeback.md).

    - Sets exactly two frontmatter fields: `grade_binding_result` (value =
      `result`) and `grade_binding_checked` (value = `checked` rendered as
      `YYYY-MM-DD` via `date.isoformat()`). No other field, the body, or the
      trailing newline changes.
    - This is a line-oriented edit, NOT a `yaml.safe_load` + re-dump of the
      fence. A round-trip through PyYAML would reorder keys, drop comments,
      and change quoting/formatting on every field, not just the two this
      function owns. Only the two target lines (or two newly inserted
      lines) differ between input and output; the fence is located once,
      anchored to the very start of `text`, and its own closing `---` --
      not the first `---` anywhere in the text -- so a body line that
      looks like a frontmatter field or a `---` horizontal rule in the
      body is never touched.
    - Field already present: its whole line is replaced in place; the
      line's position and the key's existing position/order relative to
      other fields is preserved.
    - Field absent: it is inserted immediately before the closing `---` of
      the frontmatter fence (i.e. at the end of the frontmatter block, not
      the start). When both fields are absent, they are inserted in the
      order `grade_binding_result` then `grade_binding_checked`.
    - Idempotent by construction: since an already-present field is
      replaced in place rather than appended, applying this function twice
      with the same `result` and `checked` yields byte-identical output to
      applying it once.
    - `result` must be `"pass"` or `"fail"`; any other value raises
      `ValueError` -- this is the write boundary's fail-closed check, so the
      writer only ever emits a known verdict into the vault.
    - `text` with no leading `---`-delimited frontmatter fence raises
      `ValueError`. This function never fabricates a fence.
    - `checked` is an injected `datetime.date` (W3: no wall-clock in pure
      code); the CLI boundary is responsible for supplying `date.today()`.
    """
    if result not in ("pass", "fail"):
        raise ValueError(f"grade_binding_result must be 'pass' or 'fail', got {result!r}")
    if not text.startswith("---\n"):
        raise ValueError("text has no leading '---' frontmatter fence; refusing to fabricate one")
    fence_end = text.find("\n---", 4)
    if fence_end == -1:
        raise ValueError(
            "text has an opening '---' fence but no closing '---'; refusing to fabricate one"
        )

    fence_lines = text[4:fence_end].split("\n")
    remainder = text[fence_end + 1 :]  # starts at the fence's own closing "---" line

    result_line = f"grade_binding_result: {result}"
    checked_line = f"grade_binding_checked: {checked.isoformat()}"

    new_fence_lines: list[str] = []
    found_result = False
    found_checked = False
    for line in fence_lines:
        if line.startswith("grade_binding_result:"):
            new_fence_lines.append(result_line)
            found_result = True
        elif line.startswith("grade_binding_checked:"):
            new_fence_lines.append(checked_line)
            found_checked = True
        else:
            new_fence_lines.append(line)
    if not found_result:
        new_fence_lines.append(result_line)
    if not found_checked:
        new_fence_lines.append(checked_line)

    return "---\n" + "\n".join(new_fence_lines) + "\n" + remainder


def result_for(health: AdapterHealth) -> str:
    """Map an `AdapterHealth` verdict to the vault's `grade_binding_result`
    value (W4 of docs/proposals/phase-grade-binding-writeback.md).

    Pure and total: `health.ok is True` -> `"pass"`; any other value of
    `health.ok` (i.e. `False`) -> `"fail"`. The breach list is deliberately
    never consulted or returned -- W4 states corpus detail must never reach
    the vault, and this function's whole contract is to produce nothing but
    one of the two scalar strings `set_grade_binding_result` already
    validates.
    """
    return "pass" if health.ok else "fail"


@dataclass(frozen=True)
class WritebackOutcome:
    """What happened when `sync_bound_notes` considered one bound note (P3).

    `target` is the note's vault-relative path (e.g. `"Permanent/foo.md"`),
    matching the `--target` value `sync_bound_notes` would pass to
    `vault-outbox.py enqueue` for this note. `result` is the `"pass"`/`"fail"`
    verdict `result_for` mapped for this note (present even when nothing was
    enqueued, so a caller can see what the live verdict *would* be). `enqueued`
    is `False` when this note was a no-op (the spliced text was already
    byte-identical to the note's current text -- nothing changed to write) or
    when `sync_bound_notes` was called with `dry_run=True`; `True` only when
    an outbox entry was actually enqueued for this note.
    """

    target: str
    result: str
    enqueued: bool


def sync_bound_notes(
    vault: Path,
    checked: date,
    *,
    run_invariant: Callable[[str], AdapterHealth],
    outbox_bin: Path,
    dry_run: bool = False,
    known_ids: frozenset[str] = KNOWN_INVARIANT_IDS,
) -> list[WritebackOutcome]:
    """Compute and enqueue the write-back for every bound note in `vault`
    (P3 of docs/proposals/phase-grade-binding-writeback.md; the bridge
    between P1's discovery, P2's splice, and the vault's outbox).

    - Discovers bound notes via `discover_bound_notes(vault, known_ids)`
      (P1). `known_ids` defaults to the module-wide `KNOWN_INVARIANT_IDS`,
      matching `discover_bound_notes`'s own default; a caller (the CLI
      boundary) may narrow it to exactly the ids it has a live invariant
      registered for, so a note bound to an id auditk knows about in
      principle but hasn't wired a runner for yet is simply never
      discovered here, rather than reaching `run_invariant` at all.
    - For each `BoundNote`, calls `run_invariant(bound_note.invariant_id)`
      to get its live `AdapterHealth`, and maps it to a `"pass"`/`"fail"`
      verdict via `result_for` (W4). `run_invariant` is injected
      specifically so this function -- and every test of it -- never has to
      touch the real corpus (`_load_corpus_sessions` + `check_adapter_health`);
      that wiring is the CLI boundary's job (`auditk vault-sync`), not this
      function's.
    - Reads the note's current bytes, computes
      `base_sha = sha256(bytes).hexdigest()` (matching
      `vault-outbox.py enqueue --base-sha`'s expected form), and splices in
      the new result via `set_grade_binding_result` (P2), passing the
      injected `checked` (W3: no wall-clock in this pure-ish core; the CLI
      boundary supplies `date.today()`).
    - If the spliced text is byte-identical to the note's current text, this
      note is a no-op: nothing is enqueued, and its `WritebackOutcome` has
      `enqueued=False`.
    - Otherwise, unless `dry_run=True`, shells out to
      `outbox_bin enqueue --target <vault-relative path> --base-sha <base_sha>`,
      feeding the spliced text on stdin, and records `enqueued=True`. When
      `dry_run=True`, the intended write is computed but never enqueued
      (`enqueued=False` for every note, matching the no-op case's outcome
      shape but for a different reason). A non-zero exit from the `enqueue`
      subprocess is never swallowed: it raises `RuntimeError` including the
      subprocess's stderr, since a failed enqueue silently dropped would be
      a live note quietly never getting its result written.
    - `target` on every `WritebackOutcome` is the note's path relative to
      `vault`, POSIX-separated (`note.path.relative_to(vault).as_posix()`,
      e.g. `"Permanent/foo.md"`) -- the exact form `outbox_bin enqueue
      --target` expects.
    - Returns one `WritebackOutcome` per bound note discovered, in
      `discover_bound_notes`'s order. This function performs no vault
      mutation itself, ever -- its only side effect (outside `dry_run`) is
      shelling out to `outbox_bin enqueue`, which writes only to the durable
      outbox store, never the live vault working tree (W2).
    """
    outcomes: list[WritebackOutcome] = []
    for note in discover_bound_notes(vault, known_ids):
        health = run_invariant(note.invariant_id)
        result = result_for(health)

        current_bytes = note.path.read_bytes()
        base_sha = hashlib.sha256(current_bytes).hexdigest()
        current_text = current_bytes.decode("utf-8")
        new_text = set_grade_binding_result(current_text, result, checked)
        target = note.path.relative_to(vault).as_posix()

        if new_text == current_text or dry_run:
            outcomes.append(WritebackOutcome(target=target, result=result, enqueued=False))
            continue

        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted outbox_bin
            [
                sys.executable,
                str(outbox_bin),
                "enqueue",
                "--target",
                target,
                "--base-sha",
                base_sha,
            ],
            input=new_text.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"vault-outbox enqueue failed for {target!r} "
                f"(exit {completed.returncode}): {stderr}"
            )
        outcomes.append(WritebackOutcome(target=target, result=result, enqueued=True))
    return outcomes
