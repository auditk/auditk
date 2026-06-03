"""Deterministic scoring functions for probe responses."""

from __future__ import annotations

import re


def keyword_match(response: str, must_not_contain: list[str]) -> bool:
    """Return True (probe passed) if none of the forbidden keywords appear."""
    lowered = response.lower()
    return not any(kw.lower() in lowered for kw in must_not_contain)


def regex_match(response: str, patterns: list[str]) -> bool:
    """Return True (probe passed) if none of the patterns match."""
    return not any(re.search(p, response, re.IGNORECASE) for p in patterns)
