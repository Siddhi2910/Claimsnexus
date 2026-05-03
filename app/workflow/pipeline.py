"""
ClaimsNexus main adjudication pipeline.
Orchestrates all agents in the correct order with event streaming.
"""
import asyncio
import uuid
from datetime import datetime, timedelta
import structlog

from app.agents.planner_agent import PlannerAgent
from app.agents.fraud_agent import FraudDetectionAgent
from app.agents.medical_agent import MedicalValidationAgent
from app.agents.policy_agent import PolicyComplianceAgent
from app.agents.debate_agent import DebateAgent
from app.agents.arbiter_agent import ArbiterAgent
from app.agents.execution_agent import ExecutionAgent
from app.agents.memory_agent import MemoryAgent

from app.core.risk_engine import compute_risk_score
from app.core.conflict_detector import detect_conflicts
from app.core.reasoning_tree import build_reasoning_tree

from app.schemas.agent import DebateTranscript
from app.schemas.risk import RiskScore
from app.schemas.stream import EventTypes
from app.utils.audit_logger import emit_event
from app.config import settings

log = structlog.get_logger()


class AdjudicationPipeline:
    def __init__(self) -> None:
        self.planner = PlannerAgent()
        self.fraud_agent = FraudDetectionAgent()
        self.medical_agent = MedicalValidationAgent()
        self.policy_agent = PolicyComplianceAgent()
        self.debate_agent = DebateAgent()
        self.arbiter = ArbiterAgent()
        self.execution_agent = ExecutionAgent()
        self.memory_agent = MemoryAgent()

    async def run(self, claim_data: dict, weight_overrides: dict | None = None) -> dict:
        claim_id = claim_data["id"]
        is_simulation = claim_data.get("is_simulation", False)

        log.info("pipeline_start", claim_id=claim_id, simulation=is_simulation)
        emit_event(claim_id, EventTypes.CLAIM_RECEIVED, "ingestion", {"claim_id": claim_id})
        try:
            return await self._run_internal(claim_data=claim_data, weight_overrides=weight_overrides)
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            log.error("PIPELINE FAILED", claim_id=claim_id, error=str(exc), traceback=tb)
            log.error("pipeline_failed", claim_id=claim_id, error=str(exc))
            return self._fallback_decision(claim_data, str(exc))

    async def _run_internal(self, claim_data: dict, weight_overrides: dict | None = None) -> dict:
        claim_id = claim_data["id"]
        is_simulation = claim_data.get("is_simulation", False)

        # ── Step 1: Memory — retrieve similar cases ──────────────────────────
        log.info("pipeline_step_start", claim_id=claim_id, step="memory_retrieve")
        similar_cases = self.memory_agent.retrieve_similar_cases(claim_data, limit=5)
        log.info("pipeline_step_complete", claim_id=claim_id, step="memory_retrieve", cases=len(similar_cases))

        # ── Step 2: Planner — build execution plan ───────────────────────────
        log.info("pipeline_step_start", claim_id=claim_id, step="planner")
        plan = self.planner.build_execution_plan(claim_data, similar_cases)
        emit_event(claim_id, EventTypes.CLAIM_PLAN_GENERATED, "planning", {"plan": plan})
        log.info("pipeline_step_complete", claim_id=claim_id, step="planner")

        context = {"similar_cases": similar_cases, "plan": plan}

        # ── Step 3: Specialist agents (parallel) ─────────────────────────────
        log.info("pipeline_step_start", claim_id=claim_id, step="specialist_agents")
        emit_event(claim_id, EventTypes.AGENT_FRAUD_STARTED, "analysis", {})
        emit_event(claim_id, EventTypes.AGENT_MEDICAL_STARTED, "analysis", {})
        emit_event(claim_id, EventTypes.AGENT_POLICY_STARTED, "analysis", {})

        # Run sequentially to minimize burst LLM calls (rate-limit stability).
        fraud_report = await self.fraud_agent.run(claim_data, context)
        await asyncio.sleep(0.5)
        medical_report = await self.medical_agent.run(claim_data, context)
        await asyncio.sleep(0.5)
        policy_report = await self.policy_agent.run(claim_data, context)
        log.info("pipeline_step_complete", claim_id=claim_id, step="specialist_agents")

        emit_event(claim_id, EventTypes.AGENT_FRAUD_COMPLETED, "analysis", {
            "verdict": fraud_report.verdict, "score": fraud_report.score,
        })
        emit_event(claim_id, EventTypes.AGENT_MEDICAL_COMPLETED, "analysis", {
            "verdict": medical_report.verdict, "score": medical_report.score,
        })
        emit_event(claim_id, EventTypes.AGENT_POLICY_COMPLETED, "analysis", {
            "verdict": policy_report.verdict, "score": policy_report.score,
        })

        # ── Step 4: Risk Scoring ──────────────────────────────────────────────
        log.info("pipeline_step_start", claim_id=claim_id, step="risk_scoring")
        risk_score: RiskScore = compute_risk_score(
            fraud_score=fraud_report.score,
            medical_risk_score=medical_report.score,
            policy_risk_score=policy_report.score,
            claim_data=claim_data,
            weight_overrides=weight_overrides,
        )
        emit_event(claim_id, EventTypes.RISK_SCORE_COMPUTED, "risk", {
            "composite_score": risk_score.composite_score,
            "classification": risk_score.classification,
            "routing": risk_score.routing_decision,
        })
        log.info("pipeline_step_complete", claim_id=claim_id, step="risk_scoring", composite=risk_score.composite_score)

        # ── Step 5: Conflict detection ────────────────────────────────────────
        conflict_analysis = detect_conflicts(
            fraud_report, medical_report, policy_report, risk_score.composite_score
        )
        log.info("pipeline_step_complete", claim_id=claim_id, step="conflict_detection", debate=conflict_analysis.debate_recommended)

        debate_transcript: DebateTranscript | None = None

        if conflict_analysis.debate_recommended:
            emit_event(claim_id, EventTypes.DEBATE_CONFLICT_DETECTED, "debate", {
                "conflict_type": conflict_analysis.conflict_type,
                "scope": conflict_analysis.debate_scope,
            })
            # ── Step 6: Debate ────────────────────────────────────────────────
            log.info("pipeline_step_start", claim_id=claim_id, step="debate")
            debate_transcript = await self.debate_agent.run_debate(
                claim_data=claim_data,
                fraud_report=fraud_report,
                medical_report=medical_report,
                policy_report=policy_report,
                conflict_analysis=conflict_analysis,
                context=context,
            )
            for i, rnd in enumerate(debate_transcript.rounds, 1):
                emit_event(claim_id, f"debate.round_{i}.completed", "debate", {
                    "round": i, "args": len(rnd.arguments), "consensus": rnd.consensus_reached,
                })
            log.info("pipeline_step_complete", claim_id=claim_id, step="debate", rounds=len(debate_transcript.rounds))
        else:
            emit_event(claim_id, EventTypes.DEBATE_SKIPPED, "debate", {
                "reason": "All agents in agreement or risk below debate threshold",
            })

        # ── Step 7: Arbiter ───────────────────────────────────────────────────
        log.info("pipeline_step_start", claim_id=claim_id, step="arbiter")
        emit_event(claim_id, EventTypes.ARBITER_DELIBERATING, "arbitration", {
            "risk_score": risk_score.composite_score,
        })

        arbiter_report, arbiter_raw = await self.arbiter.adjudicate(
            claim_data=claim_data,
            fraud_report=fraud_report,
            medical_report=medical_report,
            policy_report=policy_report,
            risk_score=risk_score,
            debate_transcript=debate_transcript,
            similar_cases=similar_cases,
            context=context,
        )

        verdict = arbiter_report.verdict
        arbiter_meta = arbiter_raw

        emit_event(claim_id, EventTypes.ARBITER_DECISION_RENDERED, "arbitration", {
            "verdict": verdict,
            "confidence": arbiter_report.confidence,
            "human_required": arbiter_meta.get("human_required", False),
        })
        log.info("pipeline_step_complete", claim_id=claim_id, step="arbiter", verdict=verdict)

        # ── Step 8: Reasoning Tree ────────────────────────────────────────────
        log.info("pipeline_step_start", claim_id=claim_id, step="reasoning_tree")
        conflict_summary = None
        if debate_transcript and not debate_transcript.consensus_reached:
            conflict_summary = arbiter_meta.get("conflict_summary")

        precedent_ids = [c.get("claim_id", "") for c in similar_cases]

        reasoning_tree = build_reasoning_tree(
            verdict=verdict,
            confidence=arbiter_report.confidence,
            risk_score=risk_score,
            fraud_report=fraud_report,
            medical_report=medical_report,
            policy_report=policy_report,
            conflict_analysis=conflict_analysis,
            debate_occurred=debate_transcript is not None,
            conflict_summary=conflict_summary,
            precedent_ids=precedent_ids,
            human_required=arbiter_meta.get("human_required", False),
            denial_reason=arbiter_meta.get("denial_reason"),
            appeals_pathway=arbiter_meta.get("appeals_pathway"),
        )
        log.info("pipeline_step_complete", claim_id=claim_id, step="reasoning_tree")

        # ── Step 9: Build final decision record ───────────────────────────────
        human_required = arbiter_meta.get("human_required", False)
        approved_amount = arbiter_meta.get("approved_amount")
        if verdict == "APPROVE" and approved_amount is None:
            approved_amount = claim_data.get("requested_amount", claim_data.get("billed_amount", 0))

        # Use claim_id as decision id so clients can consistently query by claim id.
        decision_data = {
            "id": claim_id,
            "claim_id": claim_id,
            "verdict": verdict,
            "confidence": arbiter_report.confidence,
            "approved_amount": approved_amount,
            "composite_risk_score": risk_score.composite_score,
            "risk_classification": risk_score.classification,
            "routing_decision": risk_score.routing_decision,
            "fraud_score": risk_score.component_scores.fraud_score,
            "medical_risk_score": risk_score.component_scores.medical_risk_score,
            "policy_risk_score": risk_score.component_scores.policy_risk_score,
            "complexity_multiplier": risk_score.complexity_multiplier,
            "fraud_agent_report": fraud_report.model_dump(),
            "medical_agent_report": medical_report.model_dump(),
            "policy_agent_report": policy_report.model_dump(),
            "arbiter_report": arbiter_report.model_dump(),
            "reasoning_tree": reasoning_tree.model_dump(),
            "debate_occurred": debate_transcript is not None,
            "debate_transcript": debate_transcript.model_dump() if debate_transcript else None,
            "conflict_analysis": conflict_analysis.model_dump(),
            "human_required": human_required,
            "human_override": None,
            "appeals_pathway": arbiter_meta.get("appeals_pathway"),
            "denial_reason": arbiter_meta.get("denial_reason"),
            "precedent_case_ids": precedent_ids,
            "is_simulation": is_simulation,
            "created_at": datetime.utcnow().isoformat(),
            "finalized_at": datetime.utcnow().isoformat() if not human_required else None,
        }

        # ── Step 10: Human review queue (if needed) ───────────────────────────
        if human_required and settings.human_review_enabled:
            emit_event(claim_id, EventTypes.HUMAN_REVIEW_ESCALATED, "escalation", {
                "priority": arbiter_meta.get("escalation_priority", "P2"),
                "reason": arbiter_meta.get("conflict_summary", "Risk threshold exceeded"),
            })

        # ── Step 11: Execution ────────────────────────────────────────────────
        if not human_required or is_simulation:
            log.info("pipeline_step_start", claim_id=claim_id, step="execution")
            execution_result = await self.execution_agent.execute(
                claim_data=claim_data,
                verdict=verdict,
                approved_amount=approved_amount,
                denial_reason=arbiter_meta.get("denial_reason"),
                appeals_pathway=arbiter_meta.get("appeals_pathway"),
                is_simulation=is_simulation,
            )
            emit_event(claim_id, EventTypes.EXECUTION_COMPLETED, "execution", execution_result)
            log.info("pipeline_step_complete", claim_id=claim_id, step="execution")

        # ── Step 12: Memory — store case ──────────────────────────────────────
        if not is_simulation:
            log.info("pipeline_step_start", claim_id=claim_id, step="memory_store")
            self.memory_agent.store_case(claim_data, decision_data)
            emit_event(claim_id, EventTypes.MEMORY_CASE_STORED, "memory", {"claim_id": claim_id})
            log.info("pipeline_step_complete", claim_id=claim_id, step="memory_store")

        log.info("pipeline_complete", claim_id=claim_id, verdict=verdict, human_required=human_required)
        return decision_data

    def _fallback_decision(self, claim_data: dict, error: str) -> dict:
        claim_id = claim_data["id"]
        now = datetime.utcnow().isoformat()
        fallback_amount = claim_data.get("requested_amount", claim_data.get("billed_amount", 0))

        f_rep = {}
        m_rep = {}
        p_rep = {}
        
        try:
            # Use the actual heuristic logic for the agents so it doesn't crash the frontend
            from app.agents.fraud_agent import _heuristic_fallback as fraud_heuristic
            from app.agents.medical_agent import _heuristic_fallback as medical_heuristic
            from app.agents.policy_agent import _heuristic_fallback as policy_heuristic
            
            f_rep = fraud_heuristic(claim_data)
            m_rep = medical_heuristic(claim_data)
            p_rep = policy_heuristic(claim_data)
        except Exception as fb_exc:
            import traceback
            tb = traceback.format_exc()
            log.error("FALLBACK_HEURISTIC_CRASH", error=str(fb_exc), traceback=tb)
        
        # Ensure they have minimal properties needed by frontend
        for r in (f_rep, m_rep, p_rep):
            r.setdefault("verdict", "PENDING")
            r.setdefault("confidence", 0.5)
            r.setdefault("score", 0.5)
            r.setdefault("reasoning_chain", [{"inference": f"Pipeline Error Fallback: {error}", "weight": 1.0}])
            r.setdefault("key_evidence", [])
            r.setdefault("flags", ["PIPELINE_ERROR_FALLBACK"])

        return {
            "id": str(uuid.uuid4()),
            "claim_id": claim_id,
            "verdict": "PENDING",
            "confidence": 0.25,
            "approved_amount": None,
            "composite_risk_score": 0.5,
            "risk_classification": "MEDIUM",
            "routing_decision": "FULL",
            "fraud_score": f_rep.get("score", 0.5),
            "medical_risk_score": m_rep.get("score", 0.5),
            "policy_risk_score": p_rep.get("score", 0.5),
            "complexity_multiplier": 1.0,
            "fraud_agent_report": f_rep,
            "medical_agent_report": m_rep,
            "policy_agent_report": p_rep,
            "arbiter_report": {
                "verdict": "PENDING",
                "confidence": 0.5,
                "reasoning_chain": [{"inference": f"Arbiter fallback due to pipeline exception: {error}", "weight": 1.0}],
                "conflict_summary": f"Pipeline Error: {error}",
            },
            "reasoning_tree": {"decision": "PENDING", "root_reason": "pipeline_error", "fallback_amount": fallback_amount},
            "debate_occurred": False,
            "debate_transcript": None,
            "conflict_analysis": {"error": error},
            "human_required": True,
            "human_override": None,
            "appeals_pathway": "Please contact customer support for manual claim review.",
            "denial_reason": None,
            "precedent_case_ids": [],
            "is_simulation": claim_data.get("is_simulation", False),
            "created_at": now,
            "finalized_at": None,
        }
