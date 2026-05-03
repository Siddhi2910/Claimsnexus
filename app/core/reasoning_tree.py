from app.schemas.agent import AgentReport, ReasoningTree, ReasoningBranch
from app.schemas.risk import RiskScore, ConflictAnalysis


def build_reasoning_tree(
    verdict: str,
    confidence: float,
    risk_score: RiskScore,
    fraud_report: AgentReport,
    medical_report: AgentReport,
    policy_report: AgentReport,
    conflict_analysis: ConflictAnalysis | None,
    debate_occurred: bool,
    conflict_summary: str | None,
    precedent_ids: list[str],
    human_required: bool,
    denial_reason: str | None,
    appeals_pathway: str | None,
) -> ReasoningTree:
    # Build weight map — agents that drove the decision get higher weight
    agent_weights = _compute_branch_weights(verdict, fraud_report, medical_report, policy_report, risk_score)

    branches = [
        ReasoningBranch(
            agent="fraud_agent",
            verdict=fraud_report.verdict,
            weight_used=agent_weights["fraud"],
            key_factors=_top_factors(fraud_report, n=3),
            was_debated=debate_occurred and "fraud" in (conflict_analysis.debate_scope if conflict_analysis else []),
        ),
        ReasoningBranch(
            agent="medical_agent",
            verdict=medical_report.verdict,
            weight_used=agent_weights["medical"],
            key_factors=_top_factors(medical_report, n=3),
            was_debated=debate_occurred and "medical" in (conflict_analysis.debate_scope if conflict_analysis else []),
        ),
        ReasoningBranch(
            agent="policy_agent",
            verdict=policy_report.verdict,
            weight_used=agent_weights["policy"],
            key_factors=_top_factors(policy_report, n=3),
            was_debated=debate_occurred and "policy" in (conflict_analysis.debate_scope if conflict_analysis else []),
        ),
    ]

    root_reason = _compose_root_reason(verdict, risk_score, fraud_report, medical_report, policy_report)

    return ReasoningTree(
        decision=verdict,
        confidence=confidence,
        risk_score=risk_score.composite_score,
        root_reason=root_reason,
        branches=branches,
        conflict_summary=conflict_summary,
        precedents_used=precedent_ids,
        human_required=human_required,
        appeals_pathway=appeals_pathway,
    )


def _top_factors(report: AgentReport, n: int = 3) -> list[str]:
    sorted_steps = sorted(report.reasoning_chain, key=lambda s: s.weight, reverse=True)
    return [step.inference for step in sorted_steps[:n]]


def _compute_branch_weights(
    verdict: str,
    fraud_report: AgentReport,
    medical_report: AgentReport,
    policy_report: AgentReport,
    risk_score: RiskScore,
) -> dict[str, float]:
    # Agents whose verdict matches the final decision get proportionally more weight
    agent_map = {
        "fraud": fraud_report,
        "medical": medical_report,
        "policy": policy_report,
    }
    base_weights = {
        "fraud": risk_score.weights_applied.fraud_weight,
        "medical": risk_score.weights_applied.medical_weight,
        "policy": risk_score.weights_applied.policy_weight,
    }
    # Boost agents that agreed with final verdict
    boosted = {}
    for name, report in agent_map.items():
        if report.verdict == verdict:
            boosted[name] = round(base_weights[name] * 1.2, 3)
        else:
            boosted[name] = round(base_weights[name] * 0.8, 3)
    # Re-normalize
    total = sum(boosted.values())
    return {k: round(v / total, 3) for k, v in boosted.items()}


def _compose_root_reason(
    verdict: str,
    risk_score: RiskScore,
    fraud_report: AgentReport,
    medical_report: AgentReport,
    policy_report: AgentReport,
) -> str:
    """Compose root reason from actual agent signals, not just verdicts."""
    reasons = []
    
    # Extract actual reasons from agent reports
    fraud_reason = fraud_report.raw_llm_output
    medical_reason = medical_report.raw_llm_output
    policy_reason = policy_report.raw_llm_output
    
    try:
        import json
        fraud_obj = json.loads(fraud_reason) if fraud_reason else {}
        medical_obj = json.loads(medical_reason) if medical_reason else {}
        policy_obj = json.loads(policy_reason) if policy_reason else {}
        
        if fraud_obj.get("reason"):
            reasons.append(f"Fraud: {fraud_obj.get('reason')}")
        if medical_obj.get("reason"):
            reasons.append(f"Medical: {medical_obj.get('reason')}")
        if policy_obj.get("reason"):
            reasons.append(f"Policy: {policy_obj.get('reason')}")
    except (json.JSONDecodeError, TypeError):
        pass
    
    if not reasons:
        # Fallback to generic summary
        score_str = f"composite risk score {risk_score.composite_score:.2f} ({risk_score.classification})"
        agent_summary = (
            f"Fraud [{fraud_report.verdict}/{fraud_report.score:.2f}], "
            f"Medical [{medical_report.verdict}/{medical_report.score:.2f}], "
            f"Policy [{policy_report.verdict}/{policy_report.score:.2f}]"
        )
        return f"{verdict} — {score_str}. Agent verdicts: {agent_summary}."
    
    return f"{verdict} — " + ". ".join(reasons)
