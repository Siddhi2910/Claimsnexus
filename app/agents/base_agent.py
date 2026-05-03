import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
import structlog
from app.services.llm_client import llm_client
from app.schemas.agent import (
    AgentReport, AgentVerdict, ReasoningStep,
    InternalReasoning, VerdictProbability, EvidenceItem, RiskFactor, DissentingView,
)
from app.utils.audit_logger import build_audit_entry
from app.utils.helpers import clamp

log = structlog.get_logger()


class BaseAgent(ABC):
    agent_id: str = "base_agent"

    def __init__(self) -> None:
        self.llm = llm_client

    @abstractmethod
    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        ...

    def _start_timer(self) -> float:
        return time.monotonic()

    def _elapsed_ms(self, start: float) -> int:
        return int((time.monotonic() - start) * 1000)

    def _audit(
        self,
        claim_id: str,
        event_type: str,
        event_detail: str,
        input_snapshot: dict | None = None,
        output_snapshot: dict | None = None,
        duration_ms: int | None = None,
    ) -> dict:
        return build_audit_entry(
            claim_id=claim_id,
            agent_id=self.agent_id,
            event_type=event_type,
            event_detail=event_detail,
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            duration_ms=duration_ms,
        )

    def _parse_enhanced_output(self, result: dict) -> dict:
        """
        Parse and validate the enhanced agent output into typed schema objects.
        Returns a dict of kwargs ready for AgentReport constructor.
        Tolerates partial / missing fields gracefully.
        """
        parsed: dict = {}

        # VerdictProbability
        vp_raw = result.get("verdict_probability", {})
        if isinstance(vp_raw, dict):
            try:
                total = sum([
                    float(vp_raw.get("APPROVE", 0)),
                    float(vp_raw.get("REJECT", 0)),
                    float(vp_raw.get("PENDING", 0)),
                ])
                # Normalize if they don't sum to ~1.0
                if total > 0:
                    parsed["verdict_probability"] = VerdictProbability(
                        APPROVE=clamp(float(vp_raw.get("APPROVE", 0)) / total),
                        REJECT=clamp(float(vp_raw.get("REJECT", 0)) / total),
                        PENDING=clamp(float(vp_raw.get("PENDING", 0)) / total),
                    )
            except Exception:
                pass

        # InternalReasoning
        ir_raw = result.get("internal_reasoning", {})
        if isinstance(ir_raw, dict) and ir_raw:
            try:
                parsed["internal_reasoning"] = InternalReasoning(
                    phase_1_data_extraction=ir_raw.get("phase_1_data_extraction", ""),
                    phase_2_hypothesis=ir_raw.get("phase_2_hypothesis", ""),
                    phase_3_evidence_for=ir_raw.get("phase_3_evidence_for", []),
                    phase_4_evidence_against=ir_raw.get("phase_4_evidence_against", []),
                    phase_5_weighing=ir_raw.get("phase_5_weighing", ""),
                    phase_6_conclusion=ir_raw.get("phase_6_conclusion", ""),
                )
            except Exception:
                pass

        # EvidenceItems
        key_evidence = []
        for i, e in enumerate(result.get("key_evidence", []), 1):
            if not isinstance(e, dict):
                continue
            try:
                key_evidence.append(EvidenceItem(
                    evidence_id=e.get("evidence_id", f"E{i}"),
                    type=e.get("type", "AMBIGUOUS"),
                    description=e.get("description", ""),
                    source=e.get("source", "claim_data"),
                    strength=clamp(float(e.get("strength", 0.5))),
                    cited_value=str(e.get("cited_value", "")),
                ))
            except Exception:
                continue
        if key_evidence:
            parsed["key_evidence"] = key_evidence

        # RiskFactors
        risk_factors = []
        for i, r in enumerate(result.get("risk_factors", []), 1):
            if not isinstance(r, dict):
                continue
            try:
                risk_factors.append(RiskFactor(
                    factor_id=r.get("factor_id", f"RF{i}"),
                    category=r.get("category", "UNCATEGORIZED"),
                    description=r.get("description", ""),
                    severity=r.get("severity", "MEDIUM"),
                    probability=clamp(float(r.get("probability", 0.5))),
                    impact=r.get("impact", ""),
                    mitigating_factors=r.get("mitigating_factors", []),
                ))
            except Exception:
                continue
        if risk_factors:
            parsed["risk_factors"] = risk_factors

        # DissentingView
        dv_raw = result.get("dissenting_view", {})
        if isinstance(dv_raw, dict) and dv_raw:
            try:
                parsed["dissenting_view"] = DissentingView(
                    alternative_verdict=dv_raw.get("alternative_verdict", "PENDING"),
                    probability=clamp(float(dv_raw.get("probability", 0.2))),
                    strongest_argument_for_alternative=dv_raw.get("strongest_argument_for_alternative", ""),
                    what_would_change_my_verdict=dv_raw.get("what_would_change_my_verdict", ""),
                )
            except Exception:
                pass

        # ReasoningChain
        reasoning_chain = []
        for s in result.get("reasoning_chain", []):
            if not isinstance(s, dict):
                continue
            try:
                reasoning_chain.append(ReasoningStep(
                    step=int(s.get("step", len(reasoning_chain) + 1)),
                    observation=s.get("observation", ""),
                    evidence=s.get("evidence", []),
                    inference=s.get("inference", ""),
                    weight=clamp(float(s.get("weight", 0.5))),
                ))
            except Exception:
                continue
        parsed["reasoning_chain"] = reasoning_chain

        return parsed

    def _make_report(self, **kwargs) -> AgentReport:
        return AgentReport(
            agent_id=self.agent_id,
            timestamp=datetime.utcnow(),
            **kwargs,
        )
