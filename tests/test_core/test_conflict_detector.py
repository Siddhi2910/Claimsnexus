import pytest
from datetime import datetime
from app.core.conflict_detector import detect_conflicts
from app.schemas.agent import AgentReport, AgentVerdict, ReasoningStep


def make_report(agent_id: str, verdict: AgentVerdict, score: float) -> AgentReport:
    return AgentReport(
        agent_id=agent_id,
        claim_id="test-claim",
        timestamp=datetime.utcnow(),
        verdict=verdict,
        confidence=0.85,
        score=score,
        reasoning_chain=[
            ReasoningStep(step=1, observation="test", evidence=["e1"], inference="ok", weight=1.0)
        ],
    )


def test_no_conflict_all_approve():
    fraud = make_report("fraud", AgentVerdict.APPROVE, 0.1)
    medical = make_report("medical", AgentVerdict.APPROVE, 0.1)
    policy = make_report("policy", AgentVerdict.APPROVE, 0.1)
    result = detect_conflicts(fraud, medical, policy, 0.15)
    assert not result.has_conflict
    assert not result.debate_recommended


def test_verdict_conflict_detected():
    fraud = make_report("fraud", AgentVerdict.REJECT, 0.9)
    medical = make_report("medical", AgentVerdict.APPROVE, 0.1)
    policy = make_report("policy", AgentVerdict.APPROVE, 0.1)
    result = detect_conflicts(fraud, medical, policy, 0.4)
    assert result.has_conflict
    assert result.conflict_type == "VERDICT_CONFLICT"
    assert result.debate_recommended
    assert len(result.conflict_dimensions) > 0


def test_high_risk_forces_debate():
    fraud = make_report("fraud", AgentVerdict.APPROVE, 0.4)
    medical = make_report("medical", AgentVerdict.APPROVE, 0.4)
    policy = make_report("policy", AgentVerdict.APPROVE, 0.4)
    result = detect_conflicts(fraud, medical, policy, 0.80)
    assert result.debate_recommended


def test_score_conflict_detected():
    fraud = make_report("fraud", AgentVerdict.REJECT, 0.9)
    medical = make_report("medical", AgentVerdict.REJECT, 0.2)
    policy = make_report("policy", AgentVerdict.REJECT, 0.25)
    result = detect_conflicts(fraud, medical, policy, 0.4)
    assert result.has_conflict
