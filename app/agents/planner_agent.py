import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport
from app.models.decision import RoutingDecision
from app.config import settings

log = structlog.get_logger()


class PlannerAgent(BaseAgent):
    agent_id = "planner_agent_v1"

    def build_execution_plan(
        self,
        claim_data: dict,
        similar_cases: list[dict],
    ) -> dict:
        billed = claim_data.get("billed_amount", 0)
        claim_type = claim_data.get("claim_type", "MEDICAL")
        prior_auth = claim_data.get("prior_auth_number")
        is_simulation = claim_data.get("is_simulation", False)

        # Determine fast-track eligibility
        fast_track_eligible = (
            billed <= settings.claim_fast_track_value_max
            and not any(c.get("fraud_score", 0) > 0.4 for c in similar_cases)
            and prior_auth is not None
        )

        plan = {
            "claim_id": claim_data.get("id"),
            "claim_number": claim_data.get("claim_number"),
            "fast_track_eligible": fast_track_eligible,
            "is_simulation": is_simulation,
            "agents_required": ["fraud_agent", "medical_agent", "policy_agent"],
            "parallel_execution": True,
            "similar_cases_count": len(similar_cases),
            "routing_hint": RoutingDecision.FAST_TRACK if fast_track_eligible else RoutingDecision.FULL,
            "notes": [],
        }

        if fast_track_eligible:
            plan["notes"].append("Fast-track path: low value + clean history + prior auth present")
        if is_simulation:
            plan["notes"].append("SIMULATION MODE: Execution Agent will be skipped")
        if len(similar_cases) > 0:
            flagged = [c for c in similar_cases if c.get("fraud_score", 0) > 0.5]
            if flagged:
                plan["notes"].append(f"{len(flagged)} similar cases had elevated fraud scores")

        log.info("plan_built", claim_id=claim_data.get("id"), fast_track=fast_track_eligible, routing=plan["routing_hint"])
        return plan

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        raise NotImplementedError("Use build_execution_plan() instead")
