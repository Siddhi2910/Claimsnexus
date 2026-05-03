import structlog
from datetime import datetime
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport
from app.services.payment_service import payment_service
from app.services.notification_service import notification_service

log = structlog.get_logger()


class ExecutionAgent(BaseAgent):
    agent_id = "execution_agent_v1"

    async def execute(
        self,
        claim_data: dict,
        verdict: str,
        approved_amount: float | None,
        denial_reason: str | None,
        appeals_pathway: str | None,
        is_simulation: bool = False,
    ) -> dict:
        claim_id = claim_data["id"]
        claimant_name = claim_data.get("claimant_name", "Claimant")
        provider_id = claim_data.get("provider_id", "")
        provider_name = claim_data.get("provider_name", "Provider")
        policy_number = claim_data.get("policy_number", "")

        if is_simulation:
            log.info("execution_skipped_simulation", claim_id=claim_id, verdict=verdict)
            return {
                "status": "SIMULATION",
                "claim_id": claim_id,
                "verdict": verdict,
                "actions_taken": [],
                "note": "Simulation mode — no real actions executed",
            }

        actions = []

        if verdict == "APPROVE":
            amount = approved_amount or claim_data.get("requested_amount", 0)
            payment_result = await payment_service.trigger_payment(
                claim_id=claim_id,
                provider_id=provider_id,
                amount=amount,
                policy_number=policy_number,
            )
            actions.append({"type": "payment", "detail": payment_result})

            await notification_service.send_approval(
                claim_id=claim_id,
                claimant_name=claimant_name,
                approved_amount=amount,
            )
            await notification_service.send_to_provider(
                claim_id=claim_id,
                provider_name=provider_name,
                verdict=verdict,
            )
            actions.append({"type": "notification_approval", "recipients": [claimant_name, provider_name]})

        elif verdict == "REJECT":
            await notification_service.send_denial(
                claim_id=claim_id,
                claimant_name=claimant_name,
                denial_reason=denial_reason or "Claim does not meet coverage criteria.",
                appeals_pathway=appeals_pathway or "Contact member services.",
            )
            await notification_service.send_to_provider(
                claim_id=claim_id,
                provider_name=provider_name,
                verdict=verdict,
            )
            actions.append({"type": "notification_denial", "recipients": [claimant_name, provider_name]})

        elif verdict == "PENDING":
            await notification_service.send_pending(
                claim_id=claim_id,
                claimant_name=claimant_name,
                reason="Your claim requires additional review.",
            )
            actions.append({"type": "notification_pending", "recipients": [claimant_name]})

        log.info("execution_complete", claim_id=claim_id, verdict=verdict, actions=len(actions))
        return {
            "status": "EXECUTED",
            "claim_id": claim_id,
            "verdict": verdict,
            "actions_taken": actions,
            "executed_at": datetime.utcnow().isoformat(),
        }

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        raise NotImplementedError("Use execute() instead")
