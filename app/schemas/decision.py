from pydantic import BaseModel, Field
from datetime import datetime
from app.models.decision import DecisionVerdict
from app.schemas.agent import ReasoningTree, DebateTranscript, AgentReport
from app.schemas.risk import RiskScore, ConflictAnalysis


class DecisionResponse(BaseModel):
    id: str
    claim_id: str
    verdict: DecisionVerdict
    confidence: float
    approved_amount: float | None
    composite_risk_score: float
    risk_classification: str
    reasoning_tree: ReasoningTree
    fraud_agent_report: dict
    medical_agent_report: dict
    policy_agent_report: dict
    debate_occurred: bool
    debate_transcript: DebateTranscript | None
    conflict_analysis: ConflictAnalysis | None
    human_required: bool
    human_override: dict | None
    denial_reason: str | None
    appeals_pathway: str | None
    precedent_case_ids: list[str]
    created_at: datetime
    finalized_at: datetime | None


class HumanOverrideRequest(BaseModel):
    reviewer_id: str
    reviewer_role: str = Field(pattern="^(ANALYST|SENIOR_ANALYST|MEDICAL_DIRECTOR|COMPLIANCE_OFFICER)$")
    override_decision: DecisionVerdict
    override_reason: str = Field(min_length=20)
    override_category: str = Field(
        pattern="^(AGENT_ERROR|POLICY_EXCEPTION|REGULATORY|CLINICAL_JUDGMENT|OTHER)$"
    )
    supporting_docs: list[str] = []
    is_final: bool = True


class HumanReviewTaskResponse(BaseModel):
    id: str
    claim_id: str
    decision_id: str
    priority: str
    escalation_reason: str
    status: str
    assigned_to: str | None
    deadline: datetime | None
    created_at: datetime
