import json
import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport
from app.services.vector_store import vector_store

log = structlog.get_logger()


class MemoryAgent(BaseAgent):
    agent_id = "memory_agent_v1"

    def retrieve_similar_cases(self, claim_data: dict, limit: int = 5) -> list[dict]:
        query = (
            f"{claim_data.get('diagnosis_description', '')} "
            f"{' '.join(claim_data.get('icd_codes', []))} "
            f"{' '.join(claim_data.get('cpt_codes', []))} "
            f"provider={claim_data.get('provider_name', '')} "
            f"amount={claim_data.get('billed_amount', '')}"
        )
        cases = vector_store.find_similar_cases(query, limit=limit)
        log.info("memory_retrieved", claim_id=claim_data.get("id"), count=len(cases))
        return cases

    def store_case(
        self,
        claim_data: dict,
        decision_data: dict,
    ) -> str:
        case_payload = {
            "claim_id": claim_data.get("id", ""),
            "claim_number": claim_data.get("claim_number", ""),
            "verdict": decision_data.get("verdict", ""),
            "icd_codes": claim_data.get("icd_codes", []),
            "cpt_codes": claim_data.get("cpt_codes", []),
            "provider_id": claim_data.get("provider_id", ""),
            "provider_name": claim_data.get("provider_name", ""),
            "fraud_score": decision_data.get("fraud_score", 0.0),
            "risk_score": decision_data.get("composite_risk_score", 0.0),
            "billed_amount": claim_data.get("billed_amount", 0.0),
            "approved_amount": decision_data.get("approved_amount"),
            "diagnosis_description": claim_data.get("diagnosis_description", ""),
            "procedure_description": claim_data.get("procedure_description", ""),
            "denial_reason": decision_data.get("denial_reason"),
            "summary": (
                f"Claim {claim_data.get('claim_number')} | "
                f"Verdict: {decision_data.get('verdict')} | "
                f"ICD: {' '.join(claim_data.get('icd_codes', []))} | "
                f"CPT: {' '.join(claim_data.get('cpt_codes', []))}"
            ),
        }
        weaviate_id = vector_store.store_case(case_payload)
        log.info("memory_stored", claim_id=claim_data.get("id"), weaviate_id=weaviate_id)
        return weaviate_id

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        raise NotImplementedError("Use retrieve_similar_cases() or store_case() directly")
