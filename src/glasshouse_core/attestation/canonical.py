"""Canonical JSON serialisation (sort-keys, UTF-8, no whitespace).

Not RFC 8785 compliant in full, but byte-stable across runs and platforms
for the types we produce (strings, numbers, booleans, null, lists, dicts).
Float handling: use Python's default repr — sufficient for our use case.
"""

from __future__ import annotations

import json
from typing import Any


def canonicalize(obj: Any) -> bytes:
    """Return a deterministic UTF-8 JSON encoding with sorted keys."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
