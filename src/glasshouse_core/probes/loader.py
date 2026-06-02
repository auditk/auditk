"""Load ProbeDefinition objects from YAML or JSON files on disk."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from glasshouse_core.adapters.protocols import Stimulus  # noqa: F401
from glasshouse_core.schema import ProbeDefinition

logger = logging.getLogger(__name__)


def load_probes(path: Path) -> list[ProbeDefinition]:
    """Load all probe definitions from a directory or single file.

    Skips files that fail validation with a logged warning.
    Accepts .yaml, .yml, and .json files.
    """
    files = _collect_files(path)
    probes: list[ProbeDefinition] = []
    for f in files:
        probe = _load_one(f)
        if probe is not None:
            probes.append(probe)
    return probes


def _collect_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(
        f for f in path.iterdir()
        if f.suffix in (".yaml", ".yml", ".json") and f.is_file()
    )


def _load_one(path: Path) -> ProbeDefinition | None:
    try:
        raw = _parse_file(path)
        return ProbeDefinition.model_validate(raw)
    except Exception as exc:
        logger.warning("Skipping %s: %s", path.name, exc)
        return None


def _parse_file(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if path.suffix == ".json":
        result: dict[str, Any] = json.loads(text)
        return result
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


# Stimulus must be imported above so model_rebuild() can resolve the forward ref.
ProbeDefinition.model_rebuild()
