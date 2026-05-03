from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.audit import AuditLog
from app.models.decision import Decision
import structlog

log = structlog.get_logger()
router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/claims/{claim_id}", response_model=list)
async def get_audit_log(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> list:
    # Pull agent-level audit entries from the stored decision
    result = await db.execute(select(Decision).where(Decision.claim_id == claim_id))
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="No decision found for this claim")

    all_entries = []
    for report_key in ("fraud_agent_report", "medical_agent_report", "policy_agent_report", "arbiter_report"):
        report = getattr(decision, report_key, {}) or {}
        entries = report.get("audit_log", [])
        all_entries.extend(entries)

    # Sort by timestamp
    all_entries.sort(key=lambda e: e.get("timestamp", ""))
    return all_entries


@router.get("/claims/{claim_id}/full", response_model=dict)
async def get_full_audit_package(
    claim_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Returns full audit package: all agent reports + reasoning tree + debate."""
    result = await db.execute(select(Decision).where(Decision.claim_id == claim_id))
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="No decision found")

    return {
        "claim_id": claim_id,
        "verdict": decision.verdict,
        "confidence": decision.confidence,
        "risk_scores": {
            "composite": decision.composite_risk_score,
            "fraud": decision.fraud_score,
            "medical": decision.medical_risk_score,
            "policy": decision.policy_risk_score,
        },
        "agent_reports": {
            "fraud": decision.fraud_agent_report,
            "medical": decision.medical_agent_report,
            "policy": decision.policy_agent_report,
            "arbiter": decision.arbiter_report,
        },
        "reasoning_tree": decision.reasoning_tree,
        "debate_occurred": decision.debate_occurred,
        "debate_transcript": decision.debate_transcript,
        "conflict_analysis": decision.conflict_analysis,
        "human_required": decision.human_required,
        "human_override": decision.human_override,
        "precedent_case_ids": decision.precedent_case_ids,
        "finalized_at": decision.finalized_at.isoformat() if decision.finalized_at else None,
    }
