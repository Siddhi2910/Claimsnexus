import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, Text, JSON, Enum as SAEnum, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
import enum


class ClaimStatus(str, enum.Enum):
    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    ANALYZING = "ANALYZING"
    DEBATING = "DEBATING"
    ARBITRATING = "ARBITRATING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    SIMULATION = "SIMULATION"


class ClaimType(str, enum.Enum):
    MEDICAL = "MEDICAL"
    DENTAL = "DENTAL"
    PHARMACY = "PHARMACY"
    MENTAL_HEALTH = "MENTAL_HEALTH"
    VISION = "VISION"
    EMERGENCY = "EMERGENCY"


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    claim_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    status: Mapped[str] = mapped_column(SAEnum(ClaimStatus), default=ClaimStatus.RECEIVED, index=True)
    claim_type: Mapped[str] = mapped_column(SAEnum(ClaimType), default=ClaimType.MEDICAL)

    # Claimant
    claimant_id: Mapped[str] = mapped_column(String(100), index=True)
    claimant_name: Mapped[str] = mapped_column(String(255))
    policy_number: Mapped[str] = mapped_column(String(100), index=True)
    plan_id: Mapped[str] = mapped_column(String(100))

    # Provider
    provider_id: Mapped[str] = mapped_column(String(100), index=True)
    provider_name: Mapped[str] = mapped_column(String(255))
    provider_npi: Mapped[str | None] = mapped_column(String(20), nullable=True)
    facility_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Service
    service_date: Mapped[datetime] = mapped_column(DateTime)
    submission_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    icd_codes: Mapped[list] = mapped_column(JSON, default=list)
    cpt_codes: Mapped[list] = mapped_column(JSON, default=list)
    diagnosis_description: Mapped[str] = mapped_column(Text)
    procedure_description: Mapped[str] = mapped_column(Text)

    # Financials
    billed_amount: Mapped[float] = mapped_column(Float)
    requested_amount: Mapped[float] = mapped_column(Float)
    approved_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Network
    in_network: Mapped[bool] = mapped_column(Boolean, default=True)
    prior_auth_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Simulation flag
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)

    # Raw payload
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
