"""Pydantic models corresponding to glasshouse-spec v0.1.

JSON Schema files in glasshouse-spec are the normative source of truth; this
module mirrors them for ergonomic Python use. When the spec changes, this
module changes in lockstep and a new minor version is released.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from auditk.adapters.protocols import Stimulus


class Actor(str, Enum):
    """Who performed the action in a Step."""

    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    ENVIRONMENT = "environment"


class FlowType(str, Enum):
    """The class of agent producing the trace."""

    VOICE = "voice"
    BROWSER = "browser"
    CODE = "code"
    MCP = "mcp"
    GENERIC = "generic"


class RiskTier(str, Enum):
    """EU AI Act risk classification."""

    MINIMAL = "minimal"
    LIMITED = "limited"
    HIGH = "high"
    UNACCEPTABLE = "unacceptable"


class ActionType(str, Enum):
    """The kind of action a Step represents."""

    UTTERANCE = "utterance"
    TOOL_CALL = "tool_call"
    STATE_TRANSITION = "state_transition"
    ENV_EFFECT = "env_effect"


class Action(BaseModel):
    """What actually happened at this step."""

    type: ActionType
    payload: dict[str, Any] = Field(default_factory=dict)


class ContextRef(BaseModel):
    """A reference to context used by the agent at this step."""

    source: str
    identifier: str
    excerpt: str | None = None


class BeliefState(BaseModel):
    """The agent's view of the world at this step.

    Optional but encouraged; the field exists to make the world-model framing
    legible. Semantics are flow-specific but the shape is stable across flows.
    """

    declared_goal: str | None = None
    active_plan: list[str] | None = None
    salient_facts: dict[str, Any] = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class Step(BaseModel):
    """Atomic unit of an agent trace."""

    step_id: str
    parent_step_id: str | None = None
    trace_id: str
    timestamp: datetime
    actor: Actor
    declared_intent: str | None = None
    action: Action
    context_used: list[ContextRef] = Field(default_factory=list)
    belief_state: BeliefState | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Outcome(BaseModel):
    """The end-state of a trace."""

    status: Literal["success", "failure", "abandoned", "interrupted"]
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Trace(BaseModel):
    """A complete record of one agent run."""

    trace_id: str
    tenant_id: str | None = None
    flow_type: FlowType
    agent_config_ref: str
    steps: list[Step]
    source_adapter: str
    outcome: Outcome | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Goal(BaseModel):
    """A declared goal of the agent."""

    goal_id: str
    description: str
    success_criteria: list[str] = Field(default_factory=list)


class ToolDef(BaseModel):
    """Canonical declaration of a tool the agent can call."""

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    side_effects: list[str] = Field(default_factory=list)


class KBRef(BaseModel):
    """Reference to a knowledge base or retrieval source."""

    name: str
    kind: Literal["vector", "sql", "graph", "document", "api"]
    identifier: str


class Constraint(BaseModel):
    """A declared constraint on agent behaviour."""

    constraint_id: str
    description: str
    enforcement: Literal["soft", "hard", "advisory"]


class AgentConfig(BaseModel):
    """Canonical agent configuration shape."""

    config_id: str
    version: str
    flow_type: FlowType
    system_prompt: str
    declared_goals: list[Goal] = Field(default_factory=list)
    tools: list[ToolDef] = Field(default_factory=list)
    knowledge_bases: list[KBRef] = Field(default_factory=list)
    declared_constraints: list[Constraint] = Field(default_factory=list)
    jurisdiction: list[str] = Field(default_factory=list)
    risk_tier: RiskTier = RiskTier.LIMITED
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProbeResult(BaseModel):
    """Outcome of running a single probe against an agent."""

    probe_id: str
    probe_family: str
    probe_version: str
    succeeded: bool
    severity: Literal["info", "low", "medium", "high", "critical"]
    finding: str | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DriftReport(BaseModel):
    """Aggregate intent-enactment drift signal for a set of traces."""

    drift_score: float
    drift_per_trace: dict[str, float] = Field(default_factory=dict)
    flagged_steps: list[str] = Field(default_factory=list)
    method: str
    method_version: str


class CounterfactualResult(BaseModel):
    """Result of a counterfactual replay diff."""

    original_trace_id: str
    counterfactual_policy: str
    diff_summary: str
    divergence_step: str | None = None


class ComplianceClaim(BaseModel):
    """A single claim mapping evidence to a regulatory clause."""

    regime: str
    article: str
    claim: str
    evidence_refs: list[str] = Field(default_factory=list)


class Signature(BaseModel):
    """A signature over the evidence-pack manifest."""

    signer: str
    algorithm: Literal["ed25519", "ecdsa-p256", "rsa-pss-sha256"]
    public_key: str
    signature: str
    issued_at: datetime


class Subject(BaseModel):
    """What the evidence pack is attesting about."""

    agent_config_ref: str
    agent_version: str
    tenant_id: str | None = None


class Issuer(BaseModel):
    """Who is issuing the evidence pack."""

    name: str
    organization: str | None = None
    contact: str | None = None


class TraceSummary(BaseModel):
    """Aggregated digest of traces (not the raw traces themselves)."""

    trace_count: int = Field(ge=0)
    step_count: int = Field(ge=0)
    flow_types: list[FlowType] = Field(default_factory=list)
    time_range: tuple[datetime, datetime]


class EvidencePack(BaseModel):
    """Signed, regulator-portable evidence pack."""

    pack_id: UUID
    spec_version: Literal["v0.1"] = "v0.1"
    issued_at: datetime
    issuer: Issuer
    subject: Subject
    jurisdiction_claims: list[ComplianceClaim] = Field(default_factory=list)
    probe_results: list[ProbeResult] = Field(default_factory=list)
    drift_metrics: DriftReport | None = None
    counterfactual_diffs: list[CounterfactualResult] = Field(default_factory=list)
    trace_summary: TraceSummary
    signatures: list[Signature] = Field(default_factory=list)


class ExpectedBehavior(BaseModel):
    """What a correctly-behaving agent should do when given a probe stimulus."""

    should_refuse: bool = True
    must_not_contain: list[str] = Field(default_factory=list)
    must_invoke_tools: list[str] = Field(default_factory=list)
    must_not_invoke_tools: list[str] = Field(default_factory=list)


class ProbeDefinition(BaseModel):
    """A single adversarial or alignment probe, matching spec/v0.1/probe.schema.json."""

    probe_id: str
    family: str
    version: str
    kind: Literal[
        "jailbreak",
        "prompt_injection",
        "social_engineering",
        "ambiguity",
        "voice_cloning",
        "screen_poisoning",
        "tool_misuse",
        "exfiltration",
        "alignment_drift",
        "pii_leak",
    ]
    stimulus: Stimulus
    expected_behavior: ExpectedBehavior
    applicable_flows: list[FlowType] = Field(default_factory=list)
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ProbeDefinition references Stimulus from adapters.protocols.
# Rebuild is deferred to consumers that call model_validate (e.g. probes.loader).