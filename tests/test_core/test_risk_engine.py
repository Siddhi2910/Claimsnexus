import pytest
from app.core.risk_engine import compute_risk_score
from app.models.decision import RiskClassification, RoutingDecision


def make_claim(billed: float = 500.0) -> dict:
    return {"billed_amount": billed, "cpt_codes": ["99213"], "is_resubmission": False, "high_risk_provider": False}


def test_low_risk_fast_track():
    score = compute_risk_score(0.05, 0.05, 0.05, make_claim(500))
    assert score.classification == RiskClassification.VERY_LOW
    assert score.routing_decision == RoutingDecision.FAST_TRACK
    assert score.composite_score < 0.26


def test_high_fraud_escalates():
    # All agents high → composite should reach HIGH/CRITICAL
    score = compute_risk_score(0.90, 0.80, 0.75, make_claim(500))
    assert score.classification in (RiskClassification.HIGH, RiskClassification.CRITICAL)
    assert score.routing_decision == RoutingDecision.ESCALATE


def test_complexity_multiplier_applied():
    base = compute_risk_score(0.30, 0.30, 0.30, make_claim(500))
    high_val = compute_risk_score(0.30, 0.30, 0.30, make_claim(15_000))
    assert high_val.composite_score > base.composite_score
    assert high_val.complexity_multiplier > 1.0


def test_weight_normalization():
    score = compute_risk_score(0.5, 0.5, 0.5, make_claim(), weight_overrides={"fraud": 2, "medical": 1, "policy": 1})
    total = (
        score.weights_applied.fraud_weight
        + score.weights_applied.medical_weight
        + score.weights_applied.policy_weight
    )
    assert abs(total - 1.0) < 0.001


def test_composite_clamped():
    score = compute_risk_score(1.0, 1.0, 1.0, {"billed_amount": 100_000, "cpt_codes": ["1", "2", "3", "4"], "is_resubmission": True, "high_risk_provider": True})
    assert score.composite_score <= 1.0
