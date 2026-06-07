#!/usr/bin/env python3
"""Standalone D5 benchmark runner.
Runs the benchmark across all configured models and seeds, producing
traces and evidence packs for each session. No API calls in --dry-run mode.
Environment variables required for real runs:
  ANTHROPIC_API_KEY, FIREWORKS_API_KEY,
  RUN_NLI_MODEL=1, RUN_JUDGE_MODEL=1
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from auditk.benchmark.runner import AnthropicBenchmarkRunner, BenchmarkRunner
from auditk.benchmark.task import BENCHMARK_TASKS

MODELS: dict[str, dict[str, str]] = {
    "claude": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "model_id": "claude-sonnet-4-6",
    },
    "kimi": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "model_id": "accounts/fireworks/models/kimi-k2p6",
    },
    "minimax": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "model_id": "accounts/fireworks/models/minimax-m2p7",
    },
    "deepseek": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "model_id": "accounts/fireworks/models/deepseek-v4-pro",
    },
}

SEEDS: dict[str, Any] = {task.seed: task for task in BENCHMARK_TASKS}


def _parse_selection(raw: str, options: list[str]) -> list[str]:
    if raw == "all":
        return options
    selected = [s.strip() for s in raw.split(",")]
    invalid = [s for s in selected if s not in options]
    if invalid:
        print(f"Error: invalid selection: {', '.join(invalid)}")
        sys.exit(1)
    return selected


def _ensure_env(env: str) -> str:
    val = os.environ.get(env)
    if not val:
        print(f"Error: {env} environment variable is required")
        sys.exit(1)
    return val


def _run_attest(
    trace_path: Path,
    pack_path: Path,
    signer_path: Path,
    agent_id: str,
) -> None:
    cmd = [
        sys.executable,
        "-m",
        "auditk.cli",
        "attest",
        "--traces",
        str(trace_path),
        "--signer",
        str(signer_path),
        "--issuer-name",
        "benchmark-runner",
        "--agent-id",
        agent_id,
        "--agent-version",
        "0.1.0",
        "--out",
        str(pack_path),
        "--scorer",
        "llm-judge",
    ]
    subprocess.run(cmd, check=True)


def _make_runner(
    model_name: str,
    model_cfg: dict[str, str],
    api_key: str,
) -> BenchmarkRunner | AnthropicBenchmarkRunner:
    if model_name == "claude":
        return AnthropicBenchmarkRunner(
            api_key=api_key,
            model_id=model_cfg["model_id"],
        )
    return BenchmarkRunner(
        api_key=api_key,
        base_url=model_cfg["base_url"],
        model_id=model_cfg["model_id"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="D5 benchmark runner")
    parser.add_argument(
        "--models",
        default="all",
        help="Comma-separated model names or 'all'",
    )
    parser.add_argument(
        "--seeds",
        default="all",
        help="Comma-separated seed names or 'all'",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print config and exit without making API calls",
    )
    args = parser.parse_args()
    selected_models = _parse_selection(args.models, list(MODELS.keys()))
    selected_seeds = _parse_selection(args.seeds, list(SEEDS.keys()))

    if args.dry_run:
        print("=== D5 Benchmark Dry Run ===")
        print(f"Models: {selected_models}")
        print(f"Seeds:  {selected_seeds}")
        print(f"Sessions: {len(selected_models) * len(selected_seeds)}")
        for name in selected_models:
            cfg = MODELS[name]
            runner_type = "AnthropicBenchmarkRunner" if name == "claude" else "BenchmarkRunner"
            print(f"  {name}: {cfg['model_id']} ({runner_type})")
        for seed_name in selected_seeds:
            task = SEEDS[seed_name]
            print(f"  {seed_name}: {task.task_id}")
        print("Output dir:   benchmark_results/")
        print("Signer key:   benchmark_results/signer")
        return 0

    _ensure_env("ANTHROPIC_API_KEY")
    _ensure_env("FIREWORKS_API_KEY")
    _ensure_env("RUN_NLI_MODEL")
    _ensure_env("RUN_JUDGE_MODEL")

    output_dir = Path("benchmark_results")
    output_dir.mkdir(exist_ok=True)

    signer_path = output_dir / "signer"
    if not signer_path.with_suffix(".ed25519").exists():
        subprocess.run(
            [sys.executable, "-m", "auditk.cli", "key-gen", str(signer_path)],
            check=True,
        )

    results: list[dict[str, Any]] = []
    for model_name in selected_models:
        model_cfg = MODELS[model_name]
        api_key = _ensure_env(model_cfg["api_key_env"])

        for seed_name in selected_seeds:
            task = SEEDS[seed_name]
            session_dir = output_dir / model_name / seed_name
            session_dir.mkdir(parents=True, exist_ok=True)

            trace_path = session_dir / "trace.json"
            pack_path = session_dir / "pack.json"

            print(f"Running {model_name}/{seed_name} ...")
            runner = _make_runner(model_name, model_cfg, api_key)
            trace = runner.run(task)
            trace_path.write_text(trace.model_dump_json(indent=2))
            print(f"  Trace saved → {trace_path}")

            print("  Attesting ...")
            _run_attest(
                trace_path,
                pack_path,
                signer_path,
                f"{model_name}-{seed_name}",
            )

            pack = json.loads(pack_path.read_text())
            drift_metrics = pack.get("drift_metrics", {})
            drift_score = drift_metrics.get("drift_score", "N/A")
            taxonomy_counts = drift_metrics.get("taxonomy_counts", {})
            flagged_steps = drift_metrics.get("flagged_steps", [])
            print(
                f"  drift_score={drift_score}, "
                f"flagged={len(flagged_steps)}, "
                f"taxonomy={taxonomy_counts}"
            )
            results.append(
                {
                    "model": model_name,
                    "seed": seed_name,
                    "drift_score": drift_score,
                    "taxonomy_counts": taxonomy_counts,
                    "flagged_steps": len(flagged_steps),
                }
            )

    print("\n=== Summary ===")
    print(f"{'Model':<12} {'Seed':<12} {'Drift':<10} {'Flagged':<8}")
    for r in results:
        print(
            f"{r['model']:<12} {r['seed']:<12} "
            f"{r['drift_score']:<10} {r['flagged_steps']:<8}"
        )
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2))
    print(f"\nSummary saved → {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())