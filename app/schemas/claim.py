from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from typing import Any
from app.models.claim import ClaimStatus, ClaimType


class ClaimSubmitRequest(BaseModel):
    claim_type: ClaimType = ClaimType.MEDICAL

    # Claimant
    claimant_id: str
    claimant_name: str
    policy_number: str
    plan_id: str

    # Provider
    provider_id: str
    provider_name: str
    provider_npi: str | None = None
    facility_name: str | None = None

    # Service
    service_date: datetime
    icd_codes: list[str] = Field(min_length=1)
    cpt_codes: list[str] = Field(min_length=1)
    diagnosis_description: str
    procedure_description: str

    # Financials
    billed_amount: float = Field(gt=0)
    requested_amount: float = Field(gt=0)

    # Network
    in_network: bool = True
    prior_auth_number: str | None = None

    # Extra
    raw_payload: dict[str, Any] = {}

    @field_validator("icd_codes", "cpt_codes")
    @classmethod
    def codes_must_be_uppercase(cls, v: list[str]) -> list[str]:
        return [c.upper().strip() for c in v]


class ClaimStatusResponse(BaseModel):
    id: str
    claim_number: str
    status: ClaimStatus
    claimant_name: str
    billed_amount: float
    approved_amount: float | None
    created_at: datetime
    updated_at: datetime


class ClaimListResponse(BaseModel):
    total: int
    items: list[ClaimStatusResponse]
