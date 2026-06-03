"""Contract tests: Pydantic models must validate against the normative JSON Schemas in glasshouse-spec.

Configured via env var GLASSHOUSE_SPEC_PATH (default: ../glasshouse-spec).
Entire module is skipped when the spec directory does not exist — useful when
only glasshouse-core is checked out and the spec repo is absent.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import jsonschema
import pytest

from auditk.schema import (
    Action,
    ActionType,
    Actor,
    AgentConfig,
    EvidencePack,
    FlowType,
    Issuer,
    Step,
    Subject,
    Trace,
    TraceSummary,
)

_SPEC_PATH = Path(os.environ.get("GLASSHOUSE_SPEC_PATH", "../glasshouse-spec"))
_SCHEMA_DIR = _SPEC_PATH / "spec" / "v0.1"

# Skip the entire module if the spec directory is not present.
if not _SPEC_PATH.exists():
    pytest.skip(
        f"glasshouse-spec not found at {_SPEC_PATH}; "
        "set GLASSHOUSE_SPEC_PATH to run contract tests.",
        allow_module_level=True,
    )


def _load_schema(filename: str) -> dict:  # type: ignore[type-arg]
    return json.loads((_SCHEMA_DIR / filename).read_text())


def _minimal_trace() -> Trace:
    now = datetime.now(timezone.utc)
    return Trace(
        trace_id="t-contract-1",
        flow_type=FlowType.GENERIC,
        agent_config_ref="cfg-contract",
        source_adapter="test",
        steps=[
            Step(
                step_id="s-1",
                trace_id="t-contract-1",
                timestamp=now,
                actor=Actor.AGENT,
                action=Action(type=ActionType.UTTERANCE, payload={"text": "hello"}),
            )
        ],
    )


def _minimal_evidence_pack() -> EvidencePack:
    now = datetime.now(timezone.utc)
    return EvidencePack(
        pack_id=uuid4(),
        issued_at=now,
        issuer=Issuer(name="contract-test-issuer"),
        subject=Subject(agent_config_ref="cfg-contract", agent_version="0.1"),
        trace_summary=TraceSummary(
            trace_count=1,
            step_count=1,
            flow_types=[FlowType.GENERIC],
            time_range=(now, now),
        ),
    )


# Parametrize over both (model_instance, schema_filename) pairs.
_PARITY_CASES = [
    (_minimal_trace, "trace.schema.json"),
    (_minimal_evidence_pack, "evidence-pack.schema.json"),
    (lambda: AgentConfig(config_id="cfg-1", version="0.1", flow_type=FlowType.GENERIC, system_prompt="test"), "agent-config.schema.json"),
]


@pytest.mark.parametrize("build_instance,schema_file", _PARITY_CASES)
def test_pydantic_instance_validates_against_json_schema(
    build_instance: object, schema_file: str
) -> None:
    instance = build_instance()  # type: ignore[operator]
    assert hasattr(instance, "model_dump"), "Expected a Pydantic model instance"
    data = instance.model_dump(mode="json")  # type: ignore[union-attr]
    schema = _load_schema(schema_file)
    # Raises jsonschema.ValidationError on failure; passing means parity holds.
    jsonschema.validate(instance=data, schema=schema)
