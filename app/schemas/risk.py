from pydantic import BaseModel, Field
from app.models.decision import RiskClassification, RoutingDecision


class ComponentScores(BaseModel):
    fraud_score: float = Field(ge=0.0, le=1.0)
    medical_risk_score: float = Field(ge=0.0, le=1.0)
    policy_risk_score: float = Field(ge=0.0, le=1.0)


class WeightsApplied(BaseModel):
    fraud_weight: float
    medical_weight: float
    policy_weight: float


class RiskScore(BaseModel):
    composite_score: float = Field(ge=0.0, le=1.0)
    classification: RiskClassification
    component_scores: ComponentScores
    weights_applied: WeightsApplied
    complexity_multiplier: float
    routing_decision: RoutingDecision
    rationale: str


class ConflictDimension(BaseModel):
    dimension: str
    description: str
    severity: str  # LOW | MEDIUM | HIGH


class ConflictAnalysis(BaseModel):
    has_conflict: bool
    conflict_type: str  # VERDICT_CONFLICT | SCORE_CONFLICT | EVIDENCE_CONFLICT | NONE
    conflicting_agents: list[str]
    conflict_dimensions: list[ConflictDimension]
    debate_recommended: bool
    debate_scope: list[str]
