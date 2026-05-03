from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db, db_available
from app.models.decision import Decision, HumanReviewTask
from app.schemas.decision import DecisionResponse, HumanOverrideRequest, HumanReviewTaskResponse
from app.schemas.agent import ReasoningTree, DebateTranscript
from app.schemas.risk import ConflictAnalysis
from app.utils.audit_logger import build_audit_entry, emit_event
from app.schemas.stream import EventTypes
from app.in_memory_store import get_decision_by_claim_id
from datetime import datetime
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/decisions", tags=["Decisions"])


@router.get("/{claim_id}", response_model=dict)
async def get_decision(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    try:
        decision = None
        if db_available() and db is not None:
            try:
                result = await db.execute(select(Decision).where(Decision.claim_id == claim_id))
                decision = result.scalar_one_or_none()
            except Exception as db_exc:
                log.warning("DB FAILED -> USING MEMORY", claim_id=claim_id, error=str(db_exc))
                decision = None

        if not decision:
            mem_decision = get_decision_by_claim_id(claim_id)
            if mem_decision:
                return mem_decision
            log.info("DECISION NOT FOUND", claim_id=claim_id)
            return {
                "status": "PENDING",
                "message": "Decision not ready yet",
            }

        return {
            "id": decision.id,
            "claim_id": decision.claim_id,
            "verdict": decision.verdict,
            "confidence": decision.confidence,
            "approved_amount": decision.approved_amount,
            "composite_risk_score": decision.composite_risk_score,
            "risk_classification": decision.risk_classification,
            "routing_decision": decision.routing_decision,
            "fraud_score": decision.fraud_score,
            "medical_risk_score": decision.medical_risk_score,
            "policy_risk_score": decision.policy_risk_score,
            "fraud_agent_report": decision.fraud_agent_report or {},
            "medical_agent_report": decision.medical_agent_report or {},
            "policy_agent_report": decision.policy_agent_report or {},
            "arbiter_report": decision.arbiter_report or {},
            "reasoning_tree": decision.reasoning_tree,
            "debate_occurred": decision.debate_occurred,
            "debate_transcript": decision.debate_transcript,
            "conflict_analysis": decision.conflict_analysis,
            "human_required": decision.human_required,
            "human_override": decision.human_override,
            "denial_reason": decision.denial_reason,
            "appeals_pathway": decision.appeals_pathway,
            "precedent_case_ids": decision.precedent_case_ids,
            "created_at": decision.created_at.isoformat(),
            "finalized_at": decision.finalized_at.isoformat() if decision.finalized_at else None,
        }
    except Exception as exc:
        log.error("DECISION FETCH FAILED", claim_id=claim_id, error=str(exc))
        return {
            "status": "ERROR",
            "human_required": True,
            "message": "Safe fallback triggered",
        }


@router.get("/{claim_id}/reasoning-tree", response_model=dict)
async def get_reasoning_tree(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Decision).where(Decision.claim_id == claim_id))
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decision.reasoning_tree


@router.get("/{claim_id}/debate", response_model=dict)
async def get_debate_transcript(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Decision).where(Decision.claim_id == claim_id))
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    if not decision.debate_transcript:
        return {"message": "No debate occurred for this claim", "debate_occurred": False}
    return decision.debate_transcript


@router.post("/{claim_id}/override", response_model=dict)
async def apply_human_override(
    claim_id: str,
    body: HumanOverrideRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Decision).where(Decision.claim_id == claim_id))
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")

    if decision.human_override and decision.human_override.get("is_final"):
        raise HTTPException(status_code=409, detail="Decision is already finalized by a human reviewer")

    override_record = {
        "override_id": f"OVR-{claim_id[:8]}",
        "task_id": claim_id,
        "reviewer_id": body.reviewer_id,
        "reviewer_role": body.reviewer_role,
        "original_decision": decision.verdict,
        "override_decision": body.override_decision,
        "override_reason": body.override_reason,
        "override_category": body.override_category,
        "supporting_docs": body.supporting_docs,
        "timestamp": datetime.utcnow().isoformat(),
        "is_final": body.is_final,
    }

    decision.human_override = override_record
    decision.verdict = body.override_decision
    decision.human_required = False
    decision.finalized_at = datetime.utcnow()

    # Update human review task
    task_result = await db.execute(
        select(HumanReviewTask).where(HumanReviewTask.claim_id == claim_id)
    )
    task = task_result.scalar_one_or_none()
    if task:
        task.status = "RESOLVED"
        task.resolved_at = datetime.utcnow()

    await db.commit()

    emit_event(claim_id, EventTypes.HUMAN_REVIEW_OVERRIDE_APPLIED, "human_review", {
        "reviewer_id": body.reviewer_id,
        "override_decision": body.override_decision,
        "category": body.override_category,
    })

    log.info("human_override_applied", claim_id=claim_id, reviewer=body.reviewer_id, new_verdict=body.override_decision)
    return {"message": "Override applied successfully", "override": override_record}


@router.get("/review-queue", response_model=list)
async def get_review_queue(
    priority: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list:
    query = select(HumanReviewTask).where(HumanReviewTask.status == "PENDING")
    if priority:
        query = query.where(HumanReviewTask.priority == priority)
    query = query.order_by(HumanReviewTask.created_at.asc())

    result = await db.execute(query)
    tasks = result.scalars().all()

    return [
        {
            "id": t.id,
            "claim_id": t.claim_id,
            "decision_id": t.decision_id,
            "priority": t.priority,
            "escalation_reason": t.escalation_reason,
            "status": t.status,
            "assigned_to": t.assigned_to,
            "deadline": t.deadline.isoformat() if t.deadline else None,
            "created_at": t.created_at.isoformat(),
        }
        for t in tasks
    ]
