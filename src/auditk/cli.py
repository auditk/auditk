"""auditk CLI — key-gen, ingest, attest, verify (probe/replay/diff stubs).

POC sprint (Phase 4): key-gen, ingest, attest, verify are fully implemented.
probe, replay, diff remain stubs (Phase 4b).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import typer

from auditk import __spec_version__, __version__
from auditk.analysis.scorers import DEFAULT_SCORER, get_scorer

app = typer.Typer(
    help="auditk — the open standard for agent alignment evidence.",
    no_args_is_help=True,
)

rules_app = typer.Typer(
    help="Manage local findings-engine rulesets.",
    no_args_is_help=True,
)
app.add_typer(rules_app, name="rules")

_SCORER_MAP: dict[str, str] = {
    "jaccard": DEFAULT_SCORER,
    "nli": "nli@0.2",
    "llm-judge": "llm-judge@0.3",
}


@app.command()
def version() -> None:
    """Show auditk and auditk-spec versions."""
    typer.echo(f"auditk {__version__}")
    typer.echo(f"auditk-spec {__spec_version__}")


@app.command(name="key-gen")
def key_gen(
    path: str = typer.Argument(..., help="Base path for key files (no extension)."),
) -> None:
    """Generate an Ed25519 keypair and write .ed25519 and .ed25519.pub files."""
    from auditk.attestation.signer import generate_keypair

    priv_path, pub_path = generate_keypair(Path(path))
    typer.echo(f"Private key: {priv_path}")
    typer.echo(f"Public key:  {pub_path}")


def _discover_sibling_subagents(in_path: Path) -> list[Any]:
    """Load subagent (delegate) transcripts from
    ``<in_path.parent>/<in_path.stem>/subagents/`` -- the sibling-directory
    layout `load_subagent_transcripts` expects (P3/D7). Only meaningful for
    a ``.jsonl`` parent transcript (the only shape with such a sibling);
    anything else (e.g. a combined ``.json`` session) has none to look for,
    so this returns ``[]`` without even trying `load_subagent_transcripts`,
    which itself already tolerates a missing directory gracefully.
    """
    from auditk.adapters.claude_code import load_subagent_transcripts

    if in_path.suffix != ".jsonl":
        return []
    session_dir = in_path.parent / in_path.stem
    return load_subagent_transcripts(session_dir)


def _to_subagent_health_inputs(transcripts: list[Any]) -> list[Any]:
    """Convert already-loaded `SubagentTranscript`s (P3) into
    `SubagentHealthInput`s (P4) so the adapter-health canary's
    unknown-record-type-share check also runs over each subagent's own
    events, not just the parent transcript's.

    Known limitation: only transcripts `load_subagent_transcripts` already
    loaded successfully reach here. A `subagents/` layout break (an
    `agent-*.jsonl` with no `meta.json`, or a `meta.json` missing
    `toolUseId`) is exactly what that function silently drops today rather
    than surfacing -- so `has_meta`/`has_tool_use_id` breach detection is
    exercised by this module's own tests, but not yet reachable from a real
    on-disk corpus via this conversion. Surfacing on-disk load failures
    (not just their absence) is a further increment.
    """
    from auditk.adapters.health import SubagentHealthInput

    return [SubagentHealthInput(agent_id=t.agent_id, events=t.events) for t in transcripts]


@app.command()
def ingest(
    adapter: str = typer.Option(..., help="Adapter name (e.g. claude-code)."),
    in_file: str = typer.Option(..., "--in", help="Session file to ingest (.jsonl or .json)."),
    out: str = typer.Option(..., help="Output trace file path."),
    strip_payloads: bool = typer.Option(False, help="Redact tool inputs and results."),
) -> None:
    """Ingest a raw session file and write a normalised Trace JSON."""
    from auditk.adapters import get_adapter
    from auditk.adapters.claude_code import ingest_claude_code_session

    in_path = Path(in_file)
    if in_path.suffix == ".jsonl":
        events = [json.loads(line) for line in in_path.read_text().splitlines() if line.strip()]
    else:
        events = json.loads(in_path.read_text())

    if adapter == "claude-code":
        subagents = _discover_sibling_subagents(in_path)
        trace = ingest_claude_code_session(
            events, strip_payloads=strip_payloads, subagents=subagents
        )
    else:
        trace_adapter = get_adapter(adapter)
        trace = trace_adapter.ingest(events)

    Path(out).write_text(trace.model_dump_json(indent=2))
    typer.echo(f"Trace written to {out}: {len(trace.steps)} steps")


@app.command()
def report(
    adapter: str = typer.Option("claude-code", help="Adapter name (e.g. claude-code)."),
    in_file: str = typer.Option(..., "--in", help="Session file to load (.jsonl or .json)."),
    out: str | None = typer.Option(None, help="Output file path (default: stdout)."),
    output_format: str = typer.Option(
        "md", "--format", help="Output format: md (markdown, default) or json."
    ),
    root: str = typer.Option(
        "",
        "--root",
        help="Comma-separated allowed write roots for scope-escape checking, e.g. /a,/b. "
        "Overrides the ruleset's roots (including auto-discovery).",
    ),
    rules: str | None = typer.Option(
        None,
        "--rules",
        help="Path to an explicit ruleset YAML file, taking precedence over the ruleset "
        "cascade (shipped default, per-user, per-project, $AUDITK_RULES).",
    ),
    plan_tasks: str | None = typer.Option(
        None,
        "--plan-tasks",
        help="Directory of persisted plan-store task files (claude-code adapter only).",
    ),
    no_policy_context: bool = typer.Option(
        False,
        "--no-policy-context",
        help="Skip CLAUDE.md policy-context discovery (for portability/privacy).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help=(
            "Emit the report even if the adapter-health check reports a breach "
            "(claude-code adapter only). Without this, a breach refuses to emit "
            "a report at all -- see 'auditk doctor'."
        ),
    ),
) -> None:
    """Produce a single-session post-mortem report (markdown or JSON)."""
    from auditk.adapters import get_adapter
    from auditk.adapters.claude_code import ingest_claude_code_session, load_plan_tasks
    from auditk.adapters.health import SessionHealthInput, check_adapter_health
    from auditk.analysis.findings import analyze_trace
    from auditk.analysis.policy_context import discover_policy_context
    from auditk.analysis.report import build_report, render_markdown
    from auditk.analysis.ruleset import RulesetError, load_ruleset

    if output_format not in ("md", "json"):
        typer.echo(f"Error: Unknown format {output_format!r}. Choose from: md, json")
        raise typer.Exit(1)

    if plan_tasks and adapter != "claude-code":
        typer.echo("Error: --plan-tasks is only supported with --adapter claude-code")
        raise typer.Exit(1)

    in_path = Path(in_file)
    if in_path.suffix == ".jsonl":
        events = [json.loads(line) for line in in_path.read_text().splitlines() if line.strip()]
    else:
        events = json.loads(in_path.read_text())

    if adapter == "claude-code":
        plan_tasks_list = load_plan_tasks(Path(plan_tasks)) if plan_tasks else None
        subagents = _discover_sibling_subagents(in_path)
        trace = ingest_claude_code_session(events, plan_tasks=plan_tasks_list, subagents=subagents)

        # Phase 5 canary (Finding A): refuse to emit a report over a session
        # the adapter may have silently mis-parsed, rather than printing a
        # confidently wrong drift score/report. See `auditk doctor` for the
        # corpus-level version of this check.
        health = check_adapter_health(
            [
                SessionHealthInput(
                    events=events,
                    has_plan_store=bool(plan_tasks_list),
                    subagents=_to_subagent_health_inputs(subagents),
                )
            ]
        )
        if not health.ok and not force:
            typer.echo(
                "Error: adapter health check failed -- refusing to emit a report "
                "(pass --force to override):"
            )
            for breach in health.breaches:
                typer.echo(f"  - {breach}")
            raise typer.Exit(1)
    else:
        trace_adapter = get_adapter(adapter)
        trace = trace_adapter.ingest(events)

    session_cwd = trace.metadata.get("cwd")
    start_dir = Path(session_cwd) if isinstance(session_cwd, str) and session_cwd else Path.cwd()
    try:
        config = load_ruleset(explicit_path=rules, start_dir=start_dir)
    except RulesetError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(1) from None

    explicit_roots = [r.strip() for r in root.split(",") if r.strip()]
    if explicit_roots:
        config = config.model_copy(update={"roots": explicit_roots})

    policy_context = None if no_policy_context else discover_policy_context(start_dir=start_dir)

    findings = analyze_trace(trace, config)
    report_model = build_report(trace, findings, config=config, policy_context=policy_context)

    content = (
        report_model.model_dump_json(indent=2)
        if output_format == "json"
        else render_markdown(report_model)
    )

    if out:
        Path(out).write_text(content)
        typer.echo(f"Report written to {out}")
    else:
        typer.echo(content)


def _load_corpus_sessions(
    root_path: Path, tasks_root_path: Path, anchor_tool_names: tuple[str, ...]
) -> tuple[list[Any], Counter[str]]:
    """Walk `root_path` and load every session transcript into a
    `SessionHealthInput` list, plus the plan-anchor tool-call histogram
    across all of them. Unreadable transcripts are skipped (best-effort
    forensic tooling; see `corpus_walk.iter_jsonl`).

    Built entirely from `auditk.analysis.corpus_walk`'s shared
    corpus-walking primitives -- the single source of truth also used by
    `scripts/corpus_stats.py`, so the two never drift out of sync on how
    the on-disk corpus layout is discovered or parsed.
    """
    from auditk.adapters.claude_code import load_subagent_transcripts
    from auditk.adapters.health import SessionHealthInput
    from auditk.analysis.corpus_walk import (
        count_tool_calls,
        discover_sessions,
        has_plan_store,
        iter_jsonl,
    )

    sessions: list[SessionHealthInput] = []
    histogram: Counter[str] = Counter()

    for session_paths in discover_sessions(root_path):
        try:
            events = iter_jsonl(session_paths.transcript)
        except OSError:
            continue
        histogram.update(count_tool_calls(events, anchor_tool_names))
        subagent_transcripts = (
            load_subagent_transcripts(session_paths.session_dir)
            if session_paths.session_dir is not None
            else []
        )
        sessions.append(
            SessionHealthInput(
                events=events,
                session_id=session_paths.session_id,
                has_plan_store=has_plan_store(tasks_root_path, session_paths.session_id),
                subagents=_to_subagent_health_inputs(subagent_transcripts),
            )
        )
    return sessions, histogram


@app.command()
def doctor(
    root: str = typer.Option(
        str(Path.home() / ".claude" / "projects"),
        "--root",
        help="Corpus root to walk, read-only (default: ~/.claude/projects).",
    ),
    tasks_root: str = typer.Option(
        str(Path.home() / ".claude" / "tasks"),
        "--tasks-root",
        help="Persisted plan-store root, read-only (default: ~/.claude/tasks).",
    ),
) -> None:
    """Run the adapter-health corpus-level invariant over a corpus root.

    Prints the plan-anchor (TodoWrite/TaskCreate/TaskUpdate) tool-call
    histogram plus persisted-plan-store coverage, then the overall health
    verdict. Read-only: nothing under --root or --tasks-root is written to.
    Exits non-zero if the corpus-level invariant (or any per-session check)
    breaches.
    """
    from auditk.adapters.health import PLAN_ANCHOR_TOOL_NAMES, check_adapter_health

    sessions, anchor_histogram = _load_corpus_sessions(
        Path(root), Path(tasks_root), PLAN_ANCHOR_TOOL_NAMES
    )
    health = check_adapter_health(sessions)

    typer.echo(f"Sessions discovered: {len(sessions)}")
    typer.echo("Plan-anchor tool-call histogram:")
    for tool in PLAN_ANCHOR_TOOL_NAMES:
        typer.echo(f"  {tool:<12} {anchor_histogram.get(tool, 0)}")
    sessions_with_plan_store = sum(1 for s in sessions if s.has_plan_store)
    typer.echo(f"  sessions with persisted plan store: {sessions_with_plan_store}")
    typer.echo("")

    if health.ok:
        typer.echo("Adapter health: OK")
    else:
        typer.echo("Adapter health: BREACH")
        for breach in health.breaches:
            typer.echo(f"  - {breach}")
        raise typer.Exit(1)


@app.command()
def attest(
    traces: str = typer.Option(..., help="Path to a trace file (.json or .jsonl)."),
    signer: str = typer.Option(..., help="Base path to Ed25519 key (no extension)."),
    issuer_name: str = typer.Option(..., help="Name of the issuing party."),
    agent_id: str = typer.Option(..., help="Agent configuration reference ID."),
    agent_version: str = typer.Option(..., help="Agent version string."),
    out: str = typer.Option("evidence-pack.json", help="Output evidence pack path."),
    jurisdiction: str = typer.Option("", help="Comma-separated jurisdictions, e.g. EU,UK."),
    risk_tier: str = typer.Option("limited", help="EU AI Act risk tier."),
    probe_results_file: str | None = typer.Option(
        None, "--probe-results", help="Path to probe results JSON."
    ),
    scorer: str = typer.Option(
        "nli",
        "--scorer",
        help=(
            "Scorer to use: nli (default; requires [nli] extra), "
            "llm-judge, or jaccard (deprecated lexical baseline)."
        ),
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Print each step's taxonomy label and reasoning as it's scored."
    ),
) -> None:
    """Build and sign an evidence pack from traces + optional probe results."""
    from auditk.attestation.pack import build
    from auditk.attestation.signer import LocalEd25519Signer, generate_keypair
    from auditk.schema import Issuer, ProbeResult, RiskTier, Subject, Trace

    traces_path = Path(traces)
    if traces_path.suffix == ".jsonl":
        trace_list = [
            Trace.model_validate(json.loads(line))
            for line in traces_path.read_text().splitlines()
            if line.strip()
        ]
    else:
        raw = json.loads(traces_path.read_text())
        trace_list = (
            [Trace.model_validate(raw)]
            if isinstance(raw, dict)
            else [Trace.model_validate(item) for item in raw]
        )

    probe_results: list[ProbeResult] = []
    if probe_results_file:
        raw_probes = json.loads(Path(probe_results_file).read_text())
        probe_results = [ProbeResult.model_validate(r) for r in raw_probes]

    # Resolve private key path; generate if missing
    priv_key_path = Path(signer).with_suffix(".ed25519")
    if not priv_key_path.exists():
        priv_key_path, _ = generate_keypair(Path(signer))
    signer_obj = LocalEd25519Signer(priv_key_path)

    jurisdictions = (
        [j.strip() for j in jurisdiction.split(",") if j.strip()] if jurisdiction else []
    )

    if scorer not in _SCORER_MAP:
        choices = ", ".join(sorted(_SCORER_MAP))
        typer.echo(f"Error: Unknown scorer '{scorer}'. Choose from: {choices}")
        raise typer.Exit(1)

    scorer_key = _SCORER_MAP[scorer]
    try:
        get_scorer(scorer_key)
    except ImportError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(1) from None

    pack = build(
        traces=trace_list,
        probe_results=probe_results,
        jurisdiction=jurisdictions,
        risk_tier=RiskTier(risk_tier),
        issuer=Issuer(name=issuer_name),
        subject=Subject(agent_config_ref=agent_id, agent_version=agent_version),
        signer=signer_obj,
        scorer_key=scorer_key,
        verbose=verbose,
    )
    Path(out).write_text(pack.model_dump_json(indent=2))
    typer.echo(f"Evidence pack written to {out}. Pack ID: {pack.pack_id}")


@app.command()
def replay(
    trace: str = typer.Option(..., help="Path to the recorded trace."),
    policy: str = typer.Option(..., help="Alternate policy / prompt file."),
    out: str | None = typer.Option(None, help="Path to write the diff report."),
) -> None:
    """Deterministically re-run a recorded trace against an alternate policy."""
    typer.echo(f"[stub] would replay {trace} against {policy}")
    if out:
        typer.echo(f"[stub] would write diff to {out}")
    raise typer.Exit(0)


@app.command()
def diff(
    a: str = typer.Argument(..., help="First evidence pack."),
    b: str = typer.Argument(..., help="Second evidence pack."),
) -> None:
    """Diff two evidence packs."""
    typer.echo(f"[stub] would diff {a} vs {b}")
    raise typer.Exit(0)


@app.command()
def verify(
    pack: str = typer.Argument(..., help="Evidence pack file to verify."),
    public_key: str = typer.Option(..., help="Path to the trusted public key (.ed25519.pub)."),
) -> None:
    """Verify all signatures on an evidence pack against a trusted public key."""
    from auditk.attestation.canonical import canonicalize
    from auditk.attestation.signer import LocalEd25519Verifier
    from auditk.schema import EvidencePack

    pack_path = Path(pack)
    if not pack_path.exists():
        typer.echo(f"✗ Verification failed: evidence pack not found: {pack}")
        raise typer.Exit(1)

    try:
        raw_pack = json.loads(pack_path.read_text())
    except json.JSONDecodeError as exc:
        typer.echo(f"✗ Verification failed: evidence pack contains invalid JSON: {exc}")
        raise typer.Exit(1) from None

    try:
        pack_obj = EvidencePack.model_validate(raw_pack)
    except Exception as exc:
        typer.echo(f"✗ Verification failed: evidence pack is malformed: {exc}")
        raise typer.Exit(1) from None

    if not pack_obj.signatures:
        typer.echo("✗ Verification failed: evidence pack has no signatures")
        raise typer.Exit(1)

    pub_key_path = Path(public_key)
    if not pub_key_path.exists():
        typer.echo(f"✗ Verification failed: public key not found: {public_key}")
        raise typer.Exit(1)

    try:
        trusted_pub_pem = pub_key_path.read_text()
    except Exception as exc:
        typer.echo(f"✗ Verification failed: could not read public key: {exc}")
        raise typer.Exit(1) from None

    try:
        verifier = LocalEd25519Verifier(trusted_pub_pem)
    except Exception as exc:
        typer.echo(f"✗ Verification failed: public key is malformed: {exc}")
        raise typer.Exit(1) from None

    manifest = {k: v for k, v in raw_pack.items() if k != "signatures"}
    canonical = canonicalize(manifest)

    for sig in pack_obj.signatures:
        try:
            verifier.verify(canonical, sig.signature)
        except Exception as exc:
            typer.echo(f"✗ Verification failed: {exc}")
            raise typer.Exit(1) from exc

    typer.echo(f"✓ Evidence pack verified. Pack ID: {pack_obj.pack_id}")


@rules_app.command("init")
def rules_init(
    from_dir: str = typer.Option(
        ".", "--from", help="Directory to discover the CLAUDE.md policy cascade from."
    ),
    out: str | None = typer.Option(
        None, "--out", help="Write the scaffold to this path (default: stdout)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite --out if it already exists."),
) -> None:
    """Scaffold a starter ruleset YAML from the local CLAUDE.md cascade.

    Nothing is written unless --out is given: by default the scaffold goes
    to stdout so nothing is auto-created. The scaffold is always the shipped
    default ruleset, annotated with a comment noting which CLAUDE.md files
    were scanned and which generic policy phrases they contain — review and
    edit it before using it as a real ruleset.
    """
    from auditk.analysis.policy_context import discover_policy_context
    from auditk.analysis.rules_scaffold import build_starter_ruleset

    policy_docs = discover_policy_context(start_dir=Path(from_dir))
    scaffold = build_starter_ruleset(policy_docs)

    if out is None:
        typer.echo(scaffold)
        return

    out_path = Path(out)
    if out_path.exists() and not force:
        typer.echo(f"Error: {out_path} already exists. Re-run with --force to overwrite.")
        raise typer.Exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(scaffold)
    typer.echo(f"Starter ruleset written to {out_path}")
    typer.echo(
        "Hint: the intended home is ~/.claude/auditk.rules.yaml (per-user) or "
        ".auditk/rules.yaml (per-project) — both meant to be gitignored."
    )


if __name__ == "__main__":
    app()
