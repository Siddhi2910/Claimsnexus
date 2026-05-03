from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case

from app.database import get_db
from app.models.decision import Decision
from app.models.claim import Claim, ClaimStatus

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)) -> dict:
    total_claims = await db.scalar(select(func.count()).select_from(Claim))
    approved = await db.scalar(select(func.count()).select_from(Decision).where(Decision.verdict == "APPROVE"))
    rejected = await db.scalar(select(func.count()).select_from(Decision).where(Decision.verdict == "REJECT"))
    pending = await db.scalar(select(func.count()).select_from(Decision).where(Decision.verdict == "PENDING"))

    avg_risk = await db.scalar(select(func.avg(Decision.composite_risk_score)))
    avg_fraud = await db.scalar(select(func.avg(Decision.fraud_score)))
    total_approved_amount = await db.scalar(
        select(func.sum(Decision.approved_amount)).where(Decision.verdict == "APPROVE")
    )
    human_reviews = await db.scalar(
        select(func.count()).select_from(Decision).where(Decision.human_required == True)
    )
    debate_count = await db.scalar(
        select(func.count()).select_from(Decision).where(Decision.debate_occurred == True)
    )

    return {
        "summary": {
            "total_claims": total_claims or 0,
            "approved": approved or 0,
            "rejected": rejected or 0,
            "pending": pending or 0,
            "approval_rate_pct": round((approved or 0) / max(total_claims or 1, 1) * 100, 2),
            "human_reviews": human_reviews or 0,
            "debate_rate_pct": round((debate_count or 0) / max(total_claims or 1, 1) * 100, 2),
        },
        "risk": {
            "avg_composite_risk_score": round(float(avg_risk or 0), 4),
            "avg_fraud_score": round(float(avg_fraud or 0), 4),
        },
        "financial": {
            "total_approved_amount": round(float(total_approved_amount or 0), 2),
        },
    }


@router.get("/fraud-trends")
async def get_fraud_trends(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(
            Decision.risk_classification,
            func.count().label("count"),
            func.avg(Decision.fraud_score).label("avg_fraud"),
        ).group_by(Decision.risk_classification)
    )
    rows = result.all()
    return {
        "by_risk_class": [
            {
                "classification": r.risk_classification,
                "count": r.count,
                "avg_fraud_score": round(float(r.avg_fraud or 0), 4),
            }
            for r in rows
        ]
    }


@router.get("/decision-trends")
async def get_decision_trends(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(
            Decision.verdict,
            func.count().label("count"),
            func.avg(Decision.confidence).label("avg_confidence"),
            func.avg(Decision.composite_risk_score).label("avg_risk"),
        ).group_by(Decision.verdict)
    )
    rows = result.all()
    return {
        "by_verdict": [
            {
                "verdict": r.verdict,
                "count": r.count,
                "avg_confidence": round(float(r.avg_confidence or 0), 4),
                "avg_risk_score": round(float(r.avg_risk or 0), 4),
            }
            for r in rows
        ]
    }
