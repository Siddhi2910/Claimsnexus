from app.config import settings
from app.schemas.risk import RiskScore, ComponentScores, WeightsApplied
from app.models.decision import RiskClassification, RoutingDecision
from app.utils.helpers import clamp


def compute_complexity_multiplier(claim_data: dict) -> float:
    multiplier = 1.0
    billed = claim_data.get("billed_amount", 0)
    if billed > 10_000:
        multiplier += 0.15
    if len(claim_data.get("cpt_codes", [])) > 3:
        multiplier += 0.10
    if claim_data.get("is_resubmission", False):
        multiplier += 0.20
    if claim_data.get("high_risk_provider", False):
        multiplier += 0.30
    return round(min(multiplier, 2.0), 3)


def classify_risk(score: float) -> RiskClassification:
    if score <= 0.25:
        return RiskClassification.VERY_LOW
    elif score <= 0.50:
        return RiskClassification.LOW
    elif score <= 0.70:
        return RiskClassification.MEDIUM
    elif score <= 0.85:
        return RiskClassification.HIGH
    return RiskClassification.CRITICAL


def determine_routing(score: float, classification: RiskClassification) -> RoutingDecision:
    if classification == RiskClassification.VERY_LOW:
        return RoutingDecision.FAST_TRACK
    elif classification == RiskClassification.LOW:
        return RoutingDecision.STANDARD
    elif classification in (RiskClassification.MEDIUM,):
        return RoutingDecision.FULL
    return RoutingDecision.ESCALATE


def compute_risk_score(
    fraud_score: float,
    medical_risk_score: float,
    policy_risk_score: float,
    claim_data: dict,
    weight_overrides: dict | None = None,
) -> RiskScore:
    w_fraud = weight_overrides.get("fraud", settings.risk_weight_fraud) if weight_overrides else settings.risk_weight_fraud
    w_medical = weight_overrides.get("medical", settings.risk_weight_medical) if weight_overrides else settings.risk_weight_medical
    w_policy = weight_overrides.get("policy", settings.risk_weight_policy) if weight_overrides else settings.risk_weight_policy

    # Normalize weights
    total_w = w_fraud + w_medical + w_policy
    w_fraud /= total_w
    w_medical /= total_w
    w_policy /= total_w

    multiplier = compute_complexity_multiplier(claim_data)
    raw_score = (
        w_fraud * fraud_score
        + w_medical * medical_risk_score
        + w_policy * policy_risk_score
    ) * multiplier

    composite = clamp(raw_score)
    classification = classify_risk(composite)
    routing = determine_routing(composite, classification)

    rationale_parts = []
    if fraud_score > 0.6:
        rationale_parts.append(f"elevated fraud indicators ({fraud_score:.2f})")
    if medical_risk_score > 0.6:
        rationale_parts.append(f"medical validation concerns ({medical_risk_score:.2f})")
    if policy_risk_score > 0.6:
        rationale_parts.append(f"policy compliance issues ({policy_risk_score:.2f})")
    if multiplier > 1.0:
        rationale_parts.append(f"complexity multiplier applied ({multiplier:.2f}x)")
    rationale = "Risk driven by: " + "; ".join(rationale_parts) if rationale_parts else "No significant risk factors."

    return RiskScore(
        composite_score=round(composite, 4),
        classification=classification,
        component_scores=ComponentScores(
            fraud_score=round(fraud_score, 4),
            medical_risk_score=round(medical_risk_score, 4),
            policy_risk_score=round(policy_risk_score, 4),
        ),
        weights_applied=WeightsApplied(
            fraud_weight=round(w_fraud, 4),
            medical_weight=round(w_medical, 4),
            policy_weight=round(w_policy, 4),
        ),
        complexity_multiplier=multiplier,
        routing_decision=routing,
        rationale=rationale,
    )
