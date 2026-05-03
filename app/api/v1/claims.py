import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.database import db_available, db_mode
from app.models.claim import Claim, ClaimStatus
from app.schemas.claim import ClaimSubmitRequest, ClaimStatusResponse, ClaimListResponse
from app.utils.helpers import generate_claim_number
from app.utils.audit_logger import emit_event, get_events
from app.schemas.stream import EventTypes
from app.workflow.pipeline import AdjudicationPipeline
from app.models.decision import Decision, HumanReviewTask
from app.in_memory_store import (
    create_claim as create_claim_memory,
    get_claim as get_claim_memory,
    list_claims as list_claims_memory,
    update_claim as update_claim_memory,
    store_decision as store_decision_memory,
)
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/claims", tags=["Claims"])
_pipeline = AdjudicationPipeline()


async def _run_pipeline(claim_id: str, claim_data: dict) -> None:
    from app.database import AsyncSessionLocal
    log.info("claim_pipeline_started", claim_id=claim_id, db_mode=db_mode())
    try:
        decision_data = await _pipeline.run(claim_data)
        log.info("claim_pipeline_finished", claim_id=claim_id, verdict=decision_data["verdict"])

        if db_available():
            try:
                async with AsyncSessionLocal() as db:
                    # Update claim status
                    claim = await db.get(Claim, claim_id)
                    if claim:
                        if decision_data["verdict"] == "APPROVE":
                            claim.status = ClaimStatus.APPROVED
                            claim.approved_amount = decision_data.get("approved_amount")
                        elif decision_data["verdict"] == "REJECT":
                            claim.status = ClaimStatus.REJECTED
                        else:
                            claim.status = ClaimStatus.PENDING_REVIEW

                    # Persist decision
                    decision = Decision(
                        id=decision_data["id"],
                        claim_id=claim_id,
                        verdict=decision_data["verdict"],
                        confidence=decision_data["confidence"],
                        approved_amount=decision_data.get("approved_amount"),
                        composite_risk_score=decision_data["composite_risk_score"],
                        risk_classification=decision_data["risk_classification"],
                        routing_decision=decision_data["routing_decision"],
                        fraud_score=decision_data["fraud_score"],
                        medical_risk_score=decision_data["medical_risk_score"],
                        policy_risk_score=decision_data["policy_risk_score"],
                        complexity_multiplier=decision_data.get("complexity_multiplier", 1.0),
                        fraud_agent_report=decision_data["fraud_agent_report"],
                        medical_agent_report=decision_data["medical_agent_report"],
                        policy_agent_report=decision_data["policy_agent_report"],
                        arbiter_report=decision_data["arbiter_report"],
                        reasoning_tree=decision_data["reasoning_tree"],
                        debate_occurred=decision_data["debate_occurred"],
                        debate_transcript=decision_data.get("debate_transcript"),
                        conflict_analysis=decision_data.get("conflict_analysis"),
                        human_required=decision_data["human_required"],
                        appeals_pathway=decision_data.get("appeals_pathway"),
                        denial_reason=decision_data.get("denial_reason"),
                        precedent_case_ids=decision_data.get("precedent_case_ids", []),
                        is_simulation=decision_data.get("is_simulation", False),
                        finalized_at=datetime.utcnow() if not decision_data["human_required"] else None,
                    )
                    db.add(decision)

                    # Human review task
                    if decision_data["human_required"]:
                        task = HumanReviewTask(
                            id=str(uuid.uuid4()),
                            claim_id=claim_id,
                            decision_id=decision_data["id"],
                            priority="P1" if decision_data["composite_risk_score"] > 0.85 else "P2",
                            escalation_reason=decision_data.get("denial_reason") or "Risk threshold exceeded",
                            status="PENDING",
                            deadline=datetime.utcnow().replace(hour=0, minute=0, second=0) if False else None,
                        )
                        db.add(task)

                    await db.commit()
                    log.info("decision_persisted", claim_id=claim_id, verdict=decision_data["verdict"], mode="db")
                    return
            except Exception as db_exc:
                log.warning("DB FAILED -> USING MEMORY", claim_id=claim_id, error=str(db_exc))

        next_status = ClaimStatus.PENDING_REVIEW
        approved_amount = None
        if decision_data["verdict"] == "APPROVE":
            next_status = ClaimStatus.APPROVED
            approved_amount = decision_data.get("approved_amount")
        elif decision_data["verdict"] == "REJECT":
            next_status = ClaimStatus.REJECTED

        update_claim_memory(
            claim_id,
            {"status": next_status, "approved_amount": approved_amount},
        )
        store_decision_memory(decision_data)
        log.info("decision_persisted", claim_id=claim_id, verdict=decision_data["verdict"], mode="memory")

    except Exception as e:
        log.error("pipeline_background_error", claim_id=claim_id, error=str(e))
        if db_available():
            async with AsyncSessionLocal() as db:
                claim = await db.get(Claim, claim_id)
                if claim:
                    claim.status = ClaimStatus.PENDING_REVIEW
                    await db.commit()
        else:
            update_claim_memory(claim_id, {"status": ClaimStatus.PENDING_REVIEW})


@router.post("/submit", response_model=ClaimStatusResponse, status_code=202)
async def submit_claim(
    body: ClaimSubmitRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> ClaimStatusResponse:
    claim_id = str(uuid.uuid4())
    claim_number = generate_claim_number()
    created_at = datetime.utcnow()
    updated_at = created_at
    try:
        log.info("claim_submit_received", claimant_id=body.claimant_id, provider_id=body.provider_id, db_mode=db_mode())
        claim = Claim(
            id=claim_id,
            claim_number=claim_number,
            status=ClaimStatus.RECEIVED,
            claim_type=body.claim_type,
            claimant_id=body.claimant_id,
            claimant_name=body.claimant_name,
            policy_number=body.policy_number,
            plan_id=body.plan_id,
            provider_id=body.provider_id,
            provider_name=body.provider_name,
            provider_npi=body.provider_npi,
            facility_name=body.facility_name,
            service_date=body.service_date,
            icd_codes=body.icd_codes,
            cpt_codes=body.cpt_codes,
            diagnosis_description=body.diagnosis_description,
            procedure_description=body.procedure_description,
            billed_amount=body.billed_amount,
            requested_amount=body.requested_amount,
            in_network=body.in_network,
            prior_auth_number=body.prior_auth_number,
            raw_payload=body.raw_payload,
        )
        persisted_to_memory = False
        if db_available() and db is not None:
            try:
                db.add(claim)
                await db.commit()
                await db.refresh(claim)
                created_at = claim.created_at
                updated_at = claim.updated_at
                log.info("claim_persisted", claim_id=claim_id, mode="db")
            except Exception as db_exc:
                log.warning("DB FAILED -> USING MEMORY", claim_id=claim_id, error=str(db_exc))
                persisted_to_memory = True
        else:
            persisted_to_memory = True

        if persisted_to_memory:
            create_claim_memory(
                claim_id=claim_id,
                payload={
                    "claim_number": claim_number,
                    "status": ClaimStatus.RECEIVED,
                    "claim_type": body.claim_type,
                    "claimant_id": body.claimant_id,
                    "claimant_name": body.claimant_name,
                    "policy_number": body.policy_number,
                    "plan_id": body.plan_id,
                    "provider_id": body.provider_id,
                    "provider_name": body.provider_name,
                    "provider_npi": body.provider_npi,
                    "facility_name": body.facility_name,
                    "service_date": body.service_date,
                    "icd_codes": body.icd_codes,
                    "cpt_codes": body.cpt_codes,
                    "diagnosis_description": body.diagnosis_description,
                    "procedure_description": body.procedure_description,
                    "billed_amount": body.billed_amount,
                    "requested_amount": body.requested_amount,
                    "in_network": body.in_network,
                    "prior_auth_number": body.prior_auth_number,
                    "raw_payload": body.raw_payload,
                    "approved_amount": None,
                },
            )
            log.info("claim_persisted", claim_id=claim_id, mode="memory")

        emit_event(claim_id, EventTypes.CLAIM_RECEIVED, "ingestion", {"claim_number": claim_number})

        claim_data = {
            "id": claim_id,
            "claim_number": claim_number,
            "claim_type": body.claim_type,
            "claimant_id": body.claimant_id,
            "claimant_name": body.claimant_name,
            "policy_number": body.policy_number,
            "plan_id": body.plan_id,
            "provider_id": body.provider_id,
            "provider_name": body.provider_name,
            "provider_npi": body.provider_npi,
            "facility_name": body.facility_name,
            "service_date": body.service_date.isoformat(),
            "icd_codes": body.icd_codes,
            "cpt_codes": body.cpt_codes,
            "diagnosis_description": body.diagnosis_description,
            "procedure_description": body.procedure_description,
            "billed_amount": body.billed_amount,
            "requested_amount": body.requested_amount,
            "in_network": body.in_network,
            "prior_auth_number": body.prior_auth_number,
            "is_simulation": False,
        }

        background_tasks.add_task(_run_pipeline, claim_id, claim_data)
        log.info("claim_pipeline_enqueued", claim_id=claim_id)
    except Exception as exc:
        log.error("claim_submit_fallback", claim_id=claim_id, error=str(exc))

    return ClaimStatusResponse(
        id=claim_id,
        claim_number=claim_number,
        status=ClaimStatus.RECEIVED,
        claimant_name=body.claimant_name,
        billed_amount=body.billed_amount,
        approved_amount=None,
        created_at=created_at,
        updated_at=updated_at,
    )


@router.get("/{claim_id}/status", response_model=ClaimStatusResponse)
async def get_claim_status(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> ClaimStatusResponse:
    try:
        if db_available() and db is not None:
            claim = await db.get(Claim, claim_id)
            if not claim:
                raise HTTPException(status_code=404, detail="Claim not found")
            return ClaimStatusResponse(
                id=claim.id,
                claim_number=claim.claim_number,
                status=claim.status,
                claimant_name=claim.claimant_name,
                billed_amount=claim.billed_amount,
                approved_amount=claim.approved_amount,
                created_at=claim.created_at,
                updated_at=claim.updated_at,
            )
        claim = get_claim_memory(claim_id)
        if not claim:
            raise HTTPException(status_code=404, detail="Claim not found")
        return ClaimStatusResponse(
            id=claim["id"],
            claim_number=claim["claim_number"],
            status=claim["status"],
            claimant_name=claim["claimant_name"],
            billed_amount=claim["billed_amount"],
            approved_amount=claim.get("approved_amount"),
            created_at=claim["created_at"],
            updated_at=claim["updated_at"],
        )
    except Exception as exc:
        log.warning("claim_status_fallback", claim_id=claim_id, error=str(exc))
        claim = get_claim_memory(claim_id)
        if not claim:
            raise
        return ClaimStatusResponse(
            id=claim["id"],
            claim_number=claim["claim_number"],
            status=claim["status"],
            claimant_name=claim["claimant_name"],
            billed_amount=claim["billed_amount"],
            approved_amount=claim.get("approved_amount"),
            created_at=claim["created_at"],
            updated_at=claim["updated_at"],
        )


@router.get("/{claim_id}", response_model=dict)
async def get_claim(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Hardened claim fetch endpoint for UI.
    Returns 200 with PENDING status when claim not found (prevents UI hard errors).
    """
    try:
        if db_available() and db is not None:
            claim = await db.get(Claim, claim_id)
            if claim:
                return {
                    "status": "OK",
                    "id": claim.id,
                    "claim_number": claim.claim_number,
                    "status_value": str(getattr(claim.status, "value", claim.status)),
                    "claimant_name": claim.claimant_name,
                    "billed_amount": claim.billed_amount,
                    "approved_amount": claim.approved_amount,
                }
        claim = get_claim_memory(claim_id)
        if claim:
            return {
                "status": "OK",
                "id": claim["id"],
                "claim_number": claim["claim_number"],
                "status_value": str(getattr(claim.get("status"), "value", claim.get("status"))),
                "claimant_name": claim["claimant_name"],
                "billed_amount": claim["billed_amount"],
                "approved_amount": claim.get("approved_amount"),
            }
        return {"status": "PENDING", "message": "Decision not ready yet"}
    except Exception as exc:
        log.error("claim_fetch_failed", claim_id=claim_id, error=str(exc))
        return {"status": "PENDING", "message": "Decision not ready yet"}


@router.get("", response_model=ClaimListResponse)
async def list_claims(
    skip: int = 0,
    limit: int = 20,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> ClaimListResponse:
    if db_available() and db is not None:
        query = select(Claim).offset(skip).limit(limit).order_by(Claim.created_at.desc())
        if status:
            query = query.where(Claim.status == status)

        count_q = select(func.count()).select_from(Claim)
        if status:
            count_q = count_q.where(Claim.status == status)

        result = await db.execute(query)
        count_result = await db.execute(count_q)
        claims = result.scalars().all()
        total = count_result.scalar_one()

        return ClaimListResponse(
            total=total,
            items=[
                ClaimStatusResponse(
                    id=c.id,
                    claim_number=c.claim_number,
                    status=c.status,
                    claimant_name=c.claimant_name,
                    billed_amount=c.billed_amount,
                    approved_amount=c.approved_amount,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
                for c in claims
            ],
        )

    total, claims = list_claims_memory(status=status, skip=skip, limit=limit)
    return ClaimListResponse(
        total=total,
        items=[
            ClaimStatusResponse(
                id=c["id"],
                claim_number=c["claim_number"],
                status=c["status"],
                claimant_name=c["claimant_name"],
                billed_amount=c["billed_amount"],
                approved_amount=c.get("approved_amount"),
                created_at=c["created_at"],
                updated_at=c["updated_at"],
            )
            for c in claims
        ],
    )
