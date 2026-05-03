from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.claim import Claim
from app.schemas.simulation import SimulationRequest, SimulationResult
from app.core.simulation import run_simulation
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/simulate", tags=["Simulation"])


@router.post("", response_model=SimulationResult)
async def run_simulation_endpoint(
    body: SimulationRequest,
    db: AsyncSession = Depends(get_db),
) -> SimulationResult:
    base_claim_data: dict | None = None

    if body.base_claim_id:
        claim = await db.get(Claim, body.base_claim_id)
        if not claim:
            raise HTTPException(status_code=404, detail=f"Base claim {body.base_claim_id} not found")
        base_claim_data = {
            "id": claim.id,
            "claim_number": claim.claim_number,
            "claim_type": claim.claim_type,
            "claimant_id": claim.claimant_id,
            "claimant_name": claim.claimant_name,
            "policy_number": claim.policy_number,
            "plan_id": claim.plan_id,
            "provider_id": claim.provider_id,
            "provider_name": claim.provider_name,
            "service_date": claim.service_date.isoformat(),
            "icd_codes": claim.icd_codes,
            "cpt_codes": claim.cpt_codes,
            "diagnosis_description": claim.diagnosis_description,
            "procedure_description": claim.procedure_description,
            "billed_amount": claim.billed_amount,
            "requested_amount": claim.requested_amount,
            "in_network": claim.in_network,
            "prior_auth_number": claim.prior_auth_number,
            "is_simulation": True,
        }

    try:
        result = await run_simulation(body, base_claim_data)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.error("simulation_error", error=str(e))
        raise HTTPException(status_code=500, detail="Simulation failed — check logs")
