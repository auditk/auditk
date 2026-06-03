"""Smoke tests — verify the package, CLI, and schema models load cleanly."""

from datetime import datetime, timezone
from uuid import uuid4

from typer.testing import CliRunner

from auditk import __spec_version__, __version__
from auditk.cli import app
from auditk.schema import (
    Action,
    ActionType,
    Actor,
    AgentConfig,
    EvidencePack,
    FlowType,
    Issuer,
    RiskTier,
    Step,
    Subject,
    Trace,
    TraceSummary,
)


def test_version_constants_are_set() -> None:
    assert __version__
    assert __spec_version__ == "v0.1"


def test_cli_version_command_succeeds() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
    assert __spec_version__ in result.stdout


def test_cli_help_lists_core_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("probe", "attest", "replay", "diff", "verify", "version"):
        assert command in result.stdout


def test_minimal_trace_validates() -> None:
    trace = Trace(
        trace_id="t-1",
        flow_type=FlowType.GENERIC,
        agent_config_ref="cfg-abc",
        source_adapter="test",
        steps=[
            Step(
                step_id="s-1",
                trace_id="t-1",
                timestamp=datetime.now(timezone.utc),
                actor=Actor.AGENT,
                action=Action(type=ActionType.UTTERANCE, payload={"text": "hello"}),
            )
        ],
    )
    assert trace.trace_id == "t-1"
    assert len(trace.steps) == 1


def test_minimal_agent_config_defaults_to_limited_risk() -> None:
    config = AgentConfig(
        config_id="cfg-1",
        version="0.1",
        flow_type=FlowType.GENERIC,
        system_prompt="You are a helpful assistant.",
    )
    assert config.risk_tier == RiskTier.LIMITED


def test_minimal_evidence_pack_validates() -> None:
    now = datetime.now(timezone.utc)
    pack = EvidencePack(
        pack_id=uuid4(),
        issued_at=now,
        issuer=Issuer(name="glasshouse-core smoke test"),
        subject=Subject(agent_config_ref="cfg-abc", agent_version="0.1"),
        trace_summary=TraceSummary(
            trace_count=0,
            step_count=0,
            flow_types=[FlowType.GENERIC],
            time_range=(now, now),
        ),
    )
    assert pack.spec_version == "v0.1"
    assert pack.signatures == []
