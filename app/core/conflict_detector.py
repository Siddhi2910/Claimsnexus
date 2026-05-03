from app.schemas.agent import AgentReport, AgentVerdict
from app.schemas.risk import ConflictAnalysis, ConflictDimension
from app.config import settings


def _verdicts_conflict(reports: list[AgentReport]) -> bool:
    verdicts = {r.verdict for r in reports}
    # If all the same (or same direction), no conflict
    if len(verdicts) == 1:
        return False
    # APPROVE vs REJECT is always a conflict
    if AgentVerdict.APPROVE in verdicts and AgentVerdict.REJECT in verdicts:
        return True
    # UNCERTAIN always triggers debate
    if AgentVerdict.UNCERTAIN in verdicts:
        return True
    return False


def _scores_conflict(reports: list[AgentReport]) -> bool:
    scores = [r.score for r in reports]
    return (max(scores) - min(scores)) > 0.35


def _high_stakes_override(risk_score: float) -> bool:
    return risk_score > 0.70


def detect_conflicts(
    fraud_report: AgentReport,
    medical_report: AgentReport,
    policy_report: AgentReport,
    composite_risk_score: float,
) -> ConflictAnalysis:
    reports = [fraud_report, medical_report, policy_report]

    conflict_type = "NONE"
    conflicting_agents: list[str] = []
    dimensions: list[ConflictDimension] = []
    debate_scope: list[str] = []

    verdict_conflict = _verdicts_conflict(reports)
    score_conflict = _scores_conflict(reports)
    high_stakes = _high_stakes_override(composite_risk_score)

    # Identify which agents are conflicting
    if verdict_conflict:
        conflict_type = "VERDICT_CONFLICT"
        verdicts_map = {
            "fraud_agent": fraud_report.verdict,
            "medical_agent": medical_report.verdict,
            "policy_agent": policy_report.verdict,
        }
        # Find the majority verdict
        counts: dict[str, int] = {}
        for v in verdicts_map.values():
            counts[v] = counts.get(v, 0) + 1
        majority = max(counts, key=lambda k: counts[k])
        conflicting_agents = [agent for agent, verdict in verdicts_map.items() if verdict != majority]

        if fraud_report.verdict != medical_report.verdict:
            dimensions.append(ConflictDimension(
                dimension="fraud_vs_medical",
                description=f"Fraud: {fraud_report.verdict} | Medical: {medical_report.verdict}",
                severity="HIGH" if fraud_report.verdict == AgentVerdict.REJECT else "MEDIUM",
            ))
            debate_scope.append("fraud_vs_medical")

        if medical_report.verdict != policy_report.verdict:
            dimensions.append(ConflictDimension(
                dimension="medical_vs_policy",
                description=f"Medical: {medical_report.verdict} | Policy: {policy_report.verdict}",
                severity="MEDIUM",
            ))
            debate_scope.append("medical_vs_policy")

        if fraud_report.verdict != policy_report.verdict:
            dimensions.append(ConflictDimension(
                dimension="fraud_vs_policy",
                description=f"Fraud: {fraud_report.verdict} | Policy: {policy_report.verdict}",
                severity="MEDIUM",
            ))
            debate_scope.append("fraud_vs_policy")

    elif score_conflict:
        conflict_type = "SCORE_CONFLICT"
        scores = {
            "fraud_agent": fraud_report.score,
            "medical_agent": medical_report.score,
            "policy_agent": policy_report.score,
        }
        max_agent = max(scores, key=lambda k: scores[k])
        min_agent = min(scores, key=lambda k: scores[k])
        conflicting_agents = [max_agent, min_agent]
        dimensions.append(ConflictDimension(
            dimension="score_spread",
            description=f"Score spread too wide: {min(scores.values()):.2f} to {max(scores.values()):.2f}",
            severity="LOW",
        ))
        debate_scope.append("score_alignment")

    elif high_stakes:
        conflict_type = "EVIDENCE_CONFLICT"
        conflicting_agents = ["all"]
        dimensions.append(ConflictDimension(
            dimension="high_risk_mandatory",
            description=f"Composite risk score {composite_risk_score:.2f} exceeds high-stakes threshold",
            severity="HIGH",
        ))
        debate_scope.append("all")

    has_conflict = conflict_type != "NONE"
    debate_recommended = has_conflict or high_stakes

    return ConflictAnalysis(
        has_conflict=has_conflict,
        conflict_type=conflict_type,
        conflicting_agents=conflicting_agents,
        conflict_dimensions=dimensions,
        debate_recommended=debate_recommended,
        debate_scope=debate_scope or (["all"] if debate_recommended else []),
    )
