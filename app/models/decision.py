import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Text, JSON, Enum as SAEnum, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
import enum


class DecisionVerdict(str, enum.Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    PENDING = "PENDING"
    UNCERTAIN = "UNCERTAIN"


class RiskClassification(str, enum.Enum):
    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RoutingDecision(str, enum.Enum):
    FAST_TRACK = "FAST_TRACK"
    STANDARD = "STANDARD"
    FULL = "FULL"
    ESCALATE = "ESCALATE"


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    claim_id: Mapped[str] = mapped_column(String(36), ForeignKey("claims.id"), index=True)

    # Final verdict
    verdict: Mapped[str] = mapped_column(SAEnum(DecisionVerdict))
    confidence: Mapped[float] = mapped_column(Float)
    approved_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Risk scoring
    composite_risk_score: Mapped[float] = mapped_column(Float)
    risk_classification: Mapped[str] = mapped_column(SAEnum(RiskClassification))
    routing_decision: Mapped[str] = mapped_column(SAEnum(RoutingDecision))
    fraud_score: Mapped[float] = mapped_column(Float)
    medical_risk_score: Mapped[float] = mapped_column(Float)
    policy_risk_score: Mapped[float] = mapped_column(Float)
    complexity_multiplier: Mapped[float] = mapped_column(Float, default=1.0)

    # Agent reports (full JSON)
    fraud_agent_report: Mapped[dict] = mapped_column(JSON, default=dict)
    medical_agent_report: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_agent_report: Mapped[dict] = mapped_column(JSON, default=dict)
    arbiter_report: Mapped[dict] = mapped_column(JSON, default=dict)

    # Reasoning tree
    reasoning_tree: Mapped[dict] = mapped_column(JSON, default=dict)

    # Debate
    debate_occurred: Mapped[bool] = mapped_column(Boolean, default=False)
    debate_transcript: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conflict_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Human review
    human_required: Mapped[bool] = mapped_column(Boolean, default=False)
    human_override: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Appeals
    appeals_pathway: Mapped[str | None] = mapped_column(Text, nullable=True)
    denial_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Precedents used
    precedent_case_ids: Mapped[list] = mapped_column(JSON, default=list)

    # Simulation
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HumanReviewTask(Base):
    __tablename__ = "human_review_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    claim_id: Mapped[str] = mapped_column(String(36), ForeignKey("claims.id"), index=True)
    decision_id: Mapped[str] = mapped_column(String(36), ForeignKey("decisions.id"))

    priority: Mapped[str] = mapped_column(String(2), default="P2")  # P1, P2, P3
    escalation_reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    assigned_to: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deadline: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
