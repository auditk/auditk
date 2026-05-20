"""glasshouse CLI — probe, attest, replay, diff, verify.

Phase B MVP: command surface and argument shapes are stable; implementations
are stubs. Subcommands return non-zero exit codes when stub behaviour would
mask a real failure in CI.
"""

import typer

from glasshouse_core import __spec_version__, __version__

app = typer.Typer(
    help="glasshouse — the open standard for agent alignment evidence.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Show glasshouse-core and glasshouse-spec versions."""
    typer.echo(f"glasshouse-core {__version__}")
    typer.echo(f"glasshouse-spec {__spec_version__}")


@app.command()
def probe(
    endpoint: str = typer.Option(..., help="Agent endpoint URL to probe."),
    suite: str = typer.Option(
        ..., help="Probe suite identifier, e.g. glasshouse-probes-jailbreak."
    ),
    flow: str = typer.Option(
        "generic", help="Flow type (voice|browser|code|mcp|generic)."
    ),
    out: str | None = typer.Option(None, help="Path to write the probe report JSON."),
) -> None:
    """Run a probe suite against a deployed agent endpoint."""
    typer.echo(f"[stub] would probe {endpoint} with suite={suite} flow={flow}")
    if out:
        typer.echo(f"[stub] would write report to {out}")
    raise typer.Exit(0)


@app.command()
def attest(
    traces: str = typer.Option(..., help="Path to a traces.jsonl file."),
    jurisdiction: str = typer.Option(
        "", help="Comma-separated jurisdictions, e.g. EU,UK."
    ),
    risk_tier: str = typer.Option("limited", help="EU AI Act risk tier."),
    signer: str = typer.Option(..., help="Path to an Ed25519 signing key."),
    out: str = typer.Option("evidence-pack.json", help="Output evidence pack path."),
) -> None:
    """Build a signed evidence pack from traces + probe results."""
    typer.echo(f"[stub] would attest traces={traces} -> {out}")
    typer.echo(f"[stub] jurisdiction={jurisdiction} risk_tier={risk_tier} signer={signer}")
    raise typer.Exit(0)


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
    pack: str = typer.Argument(..., help="Evidence pack to verify."),
    public_key: str = typer.Option(..., help="Path to the signer's public key."),
) -> None:
    """Verify the signature on an evidence pack."""
    typer.echo(f"[stub] would verify {pack} against {public_key}")
    raise typer.Exit(0)


if __name__ == "__main__":
    app()
