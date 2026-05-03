from pydantic import BaseModel
from datetime import datetime
from typing import Any


class StreamEvent(BaseModel):
    event_id: str
    claim_id: str
    event_type: str
    payload: dict[str, Any]
    stage: str
    timestamp: datetime
    sequence_number: int


# All valid event type constants
class EventTypes:
    CLAIM_RECEIVED = "claim.received"
    CLAIM_PLAN_GENERATED = "claim.plan_generated"
    AGENT_FRAUD_STARTED = "agent.fraud.started"
    AGENT_FRAUD_COMPLETED = "agent.fraud.completed"
    AGENT_MEDICAL_STARTED = "agent.medical.started"
    AGENT_MEDICAL_COMPLETED = "agent.medical.completed"
    AGENT_POLICY_STARTED = "agent.policy.started"
    AGENT_POLICY_COMPLETED = "agent.policy.completed"
    RISK_SCORE_COMPUTED = "risk.score.computed"
    DEBATE_CONFLICT_DETECTED = "debate.conflict_detected"
    DEBATE_ROUND_COMPLETED = "debate.round_{n}.completed"
    DEBATE_SKIPPED = "debate.skipped"
    ARBITER_DELIBERATING = "arbiter.deliberating"
    ARBITER_DECISION_RENDERED = "arbiter.decision_rendered"
    HUMAN_REVIEW_ESCALATED = "human_review.escalated"
    HUMAN_REVIEW_OVERRIDE_APPLIED = "human_review.override_applied"
    EXECUTION_PAYMENT_TRIGGERED = "execution.payment_triggered"
    EXECUTION_NOTIFICATION_SENT = "execution.notification_sent"
    EXECUTION_COMPLETED = "execution.completed"
    MEMORY_CASE_STORED = "memory.case_stored"
    SIMULATION_COMPLETED = "simulation.completed"
    ERROR = "system.error"
