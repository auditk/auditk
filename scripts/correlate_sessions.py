#!/usr/bin/env python3
"""Correlate session provenance with evidence packs."""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    provenance_path = Path(os.environ.get("AGENT_PROVENANCE_PATH", Path.home() / ".agent_provenance.jsonl"))
    pack_dir = Path(os.environ.get("PACK_DIR", "benchmark_results/real_sessions"))

    # Load provenance entries
    provenance = {}
    if provenance_path.exists():
        for line in provenance_path.read_text().strip().splitlines():
            if not line:
                continue
            entry = json.loads(line)
            provenance[entry.get("session_id")] = entry

    # Load packs
    packs = {}
    if pack_dir.exists():
        for pack_file in sorted(pack_dir.glob("*_pack.json")):
            pack = json.loads(pack_file.read_text())
            packs[pack.get("session_id")] = pack

    # Determine all session IDs
    all_ids = sorted(set(provenance.keys()) | set(packs.keys()))

    # Print header
    print(f"{'session_id':<16} | {'agent':<12} | {'model':<20} | {'project':<12} | {'branch':<10} | {'recent_commits':<20} | {'drift_score':<12} | {'flagged':<8} | {'timestamp'}")
    print("-" * 160)

    for sid in all_ids:
        prov = provenance.get(sid, {})
        pack = packs.get(sid, {})

        agent = prov.get("agent", "N/A")
        model = prov.get("model", "N/A")
        project = prov.get("project", "N/A")
        branch = prov.get("branch", "N/A")
        recent_commits = prov.get("recent_commits", [])
        if isinstance(recent_commits, list):
            recent_commits = ", ".join(recent_commits)
        timestamp = prov.get("timestamp", "N/A")

        drift_score = pack.get("drift_score", "N/A")
        flagged = pack.get("flagged", "N/A")

        print(f"{sid:<16} | {agent:<12} | {model:<20} | {project:<12} | {branch:<10} | {recent_commits:<20} | {str(drift_score):<12} | {str(flagged):<8} | {timestamp}")


if __name__ == "__main__":
    main()
