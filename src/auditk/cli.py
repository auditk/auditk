"""auditk CLI — key-gen, ingest, attest, verify (probe/replay/diff stubs).

POC sprint (Phase 4): key-gen, ingest, attest, verify are fully implemented.
probe, replay, diff remain stubs (Phase 4b).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from auditk import __spec_version__, __version__

app = typer.Typer(
    help="auditk — the open standard for agent alignment evidence.",
    no_args_is_help=True,
)


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


@app.command()
def ingest(
    adapter: str = typer.Option(..., help="Adapter name (e.g. claude-code)."),
    in_file: str = typer.Option(..., "--in", help="Session file to ingest (.jsonl or .json)."),
    out: str = typer.Option(..., help="Output trace file path."),
    strip_payloads: bool = typer.Option(False, help="Redact tool inputs and results."),
) -> None:
    """Ingest a raw session file and write a normalised Trace JSON."""
    from auditk.adapters import get_adapter
    from auditk.adapters.claude_code import ClaudeCodeTraceAdapter
    from auditk.adapters.protocols import TraceAdapter

    in_path = Path(in_file)
    if in_path.suffix == ".jsonl":
        events = [json.loads(line) for line in in_path.read_text().splitlines() if line.strip()]
    else:
        events = json.loads(in_path.read_text())

    trace_adapter: TraceAdapter
    if strip_payloads and adapter == "claude-code":
        trace_adapter = ClaudeCodeTraceAdapter(strip_payloads=True)
    else:
        trace_adapter = get_adapter(adapter)

    trace = trace_adapter.ingest(events)
    Path(out).write_text(trace.model_dump_json(indent=2))
    typer.echo(f"Trace written to {out}: {len(trace.steps)} steps")


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

    pack = build(
        traces=trace_list,
        probe_results=probe_results,
        jurisdiction=jurisdictions,
        risk_tier=RiskTier(risk_tier),
        issuer=Issuer(name=issuer_name),
        subject=Subject(agent_config_ref=agent_id, agent_version=agent_version),
        signer=signer_obj,
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

    manifest = pack_obj.model_dump(mode="json", exclude={"signatures"})
    canonical = canonicalize(manifest)

    for sig in pack_obj.signatures:
        try:
            verifier.verify(canonical, sig.signature)
        except Exception as exc:
            typer.echo(f"✗ Verification failed: {exc}")
            raise typer.Exit(1) from exc

    typer.echo(f"✓ Evidence pack verified. Pack ID: {pack_obj.pack_id}")


if __name__ == "__main__":
    app()
