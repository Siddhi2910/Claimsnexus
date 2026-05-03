from pydantic import BaseModel, Field
from typing import Any, Literal
from datetime import datetime
from enum import Enum


class AgentVerdict(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    PENDING = "PENDING"
    UNCERTAIN = "UNCERTAIN"


# ── Specialist agent reasoning structures ──────────────────────────────────────

class ReasoningStep(BaseModel):
    step: int
    observation: str
    evidence: list[str]
    inference: str
    weight: float = Field(ge=0.0, le=1.0)


class InternalReasoning(BaseModel):
    phase_1_data_extraction: str
    phase_2_hypothesis: str
    phase_3_evidence_for: list[str]
    phase_4_evidence_against: list[str]
    phase_5_weighing: str
    phase_6_conclusion: str


class VerdictProbability(BaseModel):
    APPROVE: float = Field(ge=0.0, le=1.0)
    REJECT: float = Field(ge=0.0, le=1.0)
    PENDING: float = Field(ge=0.0, le=1.0)

    def dominant(self) -> str:
        d = {"APPROVE": self.APPROVE, "REJECT": self.REJECT, "PENDING": self.PENDING}
        return max(d, key=lambda k: d[k])

    def entropy(self) -> float:
        import math
        probs = [p for p in [self.APPROVE, self.REJECT, self.PENDING] if p > 0]
        return -sum(p * math.log2(p) for p in probs) / math.log2(3)


class EvidenceItem(BaseModel):
    evidence_id: str
    type: Literal["SUPPORTING", "CONTRADICTING", "AMBIGUOUS"]
    description: str
    source: Literal[
        "claim_data", "historical_case", "clinical_guideline",
        "policy_document", "statistical_benchmark", "knowledge_base"
    ]
    strength: float = Field(ge=0.0, le=1.0)
    cited_value: str


class RiskFactor(BaseModel):
    factor_id: str
    category: str
    description: str
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    probability: float = Field(ge=0.0, le=1.0)
    impact: str
    mitigating_factors: list[str] = Field(default_factory=list)


class DissentingView(BaseModel):
    alternative_verdict: str
    probability: float = Field(ge=0.0, le=1.0)
    strongest_argument_for_alternative: str
    what_would_change_my_verdict: str


# ── Core agent report ──────────────────────────────────────────────────────────

class AuditEntry(BaseModel):
    entry_id: str
    claim_id: str
    agent_id: str
    event_type: str
    event_detail: str
    input_snapshot: dict[str, Any] | None = None
    output_snapshot: dict[str, Any] | None = None
    duration_ms: int | None = None
    is_human_action: bool = False
    timestamp: datetime


class AgentReport(BaseModel):
    agent_id: str
    claim_id: str
    timestamp: datetime
    verdict: AgentVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    score: float = Field(ge=0.0, le=1.0)
    verdict_probability: VerdictProbability | None = None
    internal_reasoning: InternalReasoning | None = None
    key_evidence: list[EvidenceItem] = Field(default_factory=list)
    risk_factors: list[RiskFactor] = Field(default_factory=list)
    dissenting_view: DissentingView | None = None
    reasoning_chain: list[ReasoningStep] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    override_ready: bool = True
    audit_log: list[AuditEntry] = Field(default_factory=list)
    raw_llm_output: str | None = None


# ── Debate structures ──────────────────────────────────────────────────────────

class ArgumentStrength(BaseModel):
    """Four-dimensional scoring of a single debate argument."""
    logical_coherence: float = Field(ge=0.0, le=1.0,
        description="Is the argument internally consistent and logically valid?")
    evidence_quality: float = Field(ge=0.0, le=1.0,
        description="How strong and specific is the cited evidence?")
    specificity: float = Field(ge=0.0, le=1.0,
        description="Is the argument specific to this claim, not generic?")
    rebuttal_power: float = Field(ge=0.0, le=1.0,
        description="How effectively does it address the opposing view?")
    overall: float = Field(ge=0.0, le=1.0,
        description="Weighted composite strength score")


class Critique(BaseModel):
    """One agent's structured critique of a specific claim made by another agent."""
    target_agent: str
    target_claim: str = Field(description="The specific claim being challenged")
    critique_argument: str = Field(description="The challenge to that claim")
    evidence_challenging: list[str] = Field(default_factory=list)
    severity: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"


class DebateArgument(BaseModel):
    """A single contribution to the debate — opening, critique, defense, or counter."""
    argument_id: str
    agent_id: str
    round_number: int
    argument_type: Literal["OPENING", "CRITIQUE", "DEFENSE", "COUNTER_ARGUMENT", "CONCESSION"]
    position: AgentVerdict
    argument: str
    evidence_cited: list[str] = Field(default_factory=list)

    # Round 1 critiques — populated when argument_type == CRITIQUE
    critiques_of_others: list[Critique] = Field(default_factory=list)

    # Round 2 response fields — populated in counter-argument round
    responding_to: str | None = None          # agent_id being responded to
    defense_of: str | None = None             # argument_id being defended
    concession: str | None = None             # point being conceded (if any)
    position_shift: Literal[
        "MAINTAINED", "STRENGTHENED", "WEAKENED", "REVERSED"
    ] | None = None

    # Scoring — populated by post-debate analysis
    argument_strength: ArgumentStrength | None = None

    is_duplicate: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class Contradiction(BaseModel):
    """A direct factual or logical contradiction between two agents' arguments."""
    contradiction_id: str
    agent_a: str
    agent_b: str
    dimension: str = Field(description="What aspect they contradict on")
    agent_a_claim: str
    agent_b_claim: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    resolution_status: Literal["UNRESOLVED", "PARTIALLY_RESOLVED", "RESOLVED"]
    resolved_by: str | None = None
    resolution_summary: str | None = None


class DebateRound(BaseModel):
    round_number: int
    round_type: Literal["OPENING", "COUNTER_ARGUMENT"] = "OPENING"
    arguments: list[DebateArgument]
    active_contradiction_ids: list[str] = Field(default_factory=list)
    round_summary: str = ""
    consensus_reached: bool = False


class DebateAnalytics(BaseModel):
    """Summary statistics computed after all debate rounds complete."""
    most_persuasive_agent: str
    most_consistent_agent: str
    most_contested_dimension: str
    total_contradictions: int
    resolved_contradictions: int
    unresolved_contradictions: int
    position_shifts: dict[str, str]           # agent_id -> shift direction
    average_strength_by_agent: dict[str, float]
    debate_outcome: Literal["CONSENSUS", "PARTIAL_CONSENSUS", "DEADLOCK"]
    key_unresolved_issue: str


class DebateTranscript(BaseModel):
    session_id: str
    claim_id: str
    triggered_by: str
    scope: list[str]
    rounds: list[DebateRound]
    contradictions: list[Contradiction] = Field(default_factory=list)
    analytics: DebateAnalytics | None = None
    consensus_reached: bool
    final_positions: dict[str, str]
    duration_ms: int
    llm_calls_made: int


# ── Reasoning tree ─────────────────────────────────────────────────────────────

class ReasoningBranch(BaseModel):
    agent: str
    verdict: str
    weight_used: float
    key_factors: list[str]
    was_debated: bool


class ReasoningTree(BaseModel):
    decision: str
    confidence: float
    risk_score: float
    root_reason: str
    branches: list[ReasoningBranch]
    conflict_summary: str | None = None
    precedents_used: list[str] = []
    human_required: bool
    appeals_pathway: str | None = None
