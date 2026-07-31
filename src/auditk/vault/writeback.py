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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

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
