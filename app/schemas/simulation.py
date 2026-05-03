from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
from typing import Any


class SimulationType(str, Enum):
    PARAMETER = "A"       # What-if threshold change
    CLAIM = "B"           # Counterfactual claim mutation
    POLICY = "C"          # New policy impact
    STRESS_TEST = "D"     # Pattern at scale


class DateRange(BaseModel):
    start: datetime
    end: datetime


class DecisionSnapshot(BaseModel):
    verdict: str
    confidence: float
    composite_risk_score: float
    fraud_score: float
    approved_amount: float | None
    routing_decision: str


class DeltaAnalysis(BaseModel):
    decision_changed: bool
    original_verdict: str | None
    simulated_verdict: str
    risk_score_delta: float
    confidence_delta: float
    key_driver: str


class ImpactReport(BaseModel):
    total_claims_analyzed: int
    decisions_flipped: int
    flip_rate_pct: float
    approve_to_reject: int
    reject_to_approve: int
    avg_risk_score_delta: float
    projected_financial_impact: float


class SimulationRequest(BaseModel):
    simulation_type: SimulationType
    base_claim_id: str | None = None
    parameter_deltas: dict[str, Any] = Field(default_factory=dict)
    claim_field_overrides: dict[str, Any] = Field(default_factory=dict)
    historical_scope: DateRange | None = None
    policy_document: str | None = None
    stress_test_count: int = Field(default=100, ge=1, le=10000)
    description: str = ""


class SimulationResult(BaseModel):
    simulation_id: str
    simulation_type: SimulationType
    description: str
    original_decision: DecisionSnapshot | None
    simulated_decision: DecisionSnapshot
    delta_analysis: DeltaAnalysis
    impact_report: ImpactReport | None
    parameter_deltas_applied: dict[str, Any]
    generated_at: datetime
    duration_ms: int
