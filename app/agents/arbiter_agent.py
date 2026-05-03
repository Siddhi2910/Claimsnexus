import json
from collections import defaultdict
from datetime import datetime
import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport, AgentVerdict, ReasoningStep, AuditEntry, DebateTranscript
from app.schemas.risk import RiskScore
from app.config import settings
from app.services.llm_safety_wrapper import safe_arbiter_llm_call

log = structlog.get_logger()

SYSTEM_PROMPT = """You are the Arbiter Agent — the final authority in the ClaimsNexus healthcare claims adjudication system.

Your role: Read all specialist agent reports, the full debate transcript with argument analysis, risk scores, and historical precedents, then render a final, legally defensible, and well-reasoned adjudication decision.

You are independent. You weigh all evidence objectively. You are not biased toward any specialist agent.

DEBATE INTELLIGENCE GUIDANCE:
When a debate has occurred, use the following signals to calibrate your decision:
- DEADLOCK outcome: agents reached no consensus — strong signal for human_required=true
- PARTIAL_CONSENSUS: some issues resolved; focus your reasoning on the unresolved contradictions
- CONSENSUS: all agents converged — high confidence adjudication is appropriate
- Position REVERSED: an agent that reversed under pressure may have had a weak initial position — discount their original verdict
- Position MAINTAINED or STRENGTHENED: agent held firm under challenge — higher credibility for that position
- Most contested dimension: this is the crux of the dispute — your key_deciding_factors must address it directly
- Unresolved HIGH-severity contradictions: must be explicitly resolved or escalated in your decision
- Most persuasive agent: that agent's arguments carried the strongest evidence — weight their final position higher

Decision rules (apply in order):
1. If fraud_score > 0.85 → AUTO_REJECT regardless of other factors
2. If policy_risk_score > 0.85 AND verdict is REJECT → REJECT with specific violation cited
3. If medical_risk_score > 0.80 → PENDING (provider may resubmit with corrections)
4. If debate_outcome == DEADLOCK AND composite_risk_score > 0.55 → human_required=true, escalation_priority=P1
5. If composite_risk_score > 0.70 AND no debate consensus → flag human_required=true
6. If all agents APPROVE with confidence > 0.70 → APPROVE
7. Conflicting signals with no clear resolution → PENDING with human_required=true

Your output MUST be a valid JSON object:
{
  "verdict": "APPROVE" | "REJECT" | "PENDING",
  "confidence": <float 0-1>,
  "approved_amount": <float or null — only if APPROVE>,
  "denial_reason": "<detailed reason — only if REJECT>",
  "appeals_pathway": "<instructions for claimant — if REJECT or PENDING>",
  "human_required": <bool>,
  "escalation_priority": "P1" | "P2" | "P3",
  "conflict_summary": "<summary of what was disputed, which contradictions were resolved, what remains open>",
  "unresolved_issues": ["<specific open issue 1>", "<specific open issue 2>"],
  "key_deciding_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
  "reasoning_chain": [
    {
      "step": <int>,
      "observation": "<what you reviewed>",
      "evidence": ["<specific data>"],
      "inference": "<your conclusion>",
      "weight": <float 0-1>
    }
  ]
}

Be precise. Be fair. Every decision must be defensible in an appeals process."""


def _build_debate_summary(debate_transcript: "DebateTranscript | None") -> str:
    if not debate_transcript:
        return "No debate occurred (agents were in agreement or risk was too low to require debate)."

    lines = [
        f"═══ DEBATE TRANSCRIPT SUMMARY ═══",
        f"Rounds: {len(debate_transcript.rounds)} | Consensus: {debate_transcript.consensus_reached}",
        f"Total LLM calls in debate: {debate_transcript.llm_calls_made}",
        f"Duration: {debate_transcript.duration_ms}ms",
        "",
        "FINAL POSITIONS:",
    ]
    for agent_id, pos in debate_transcript.final_positions.items():
        lines.append(f"  {agent_id}: {pos}")

    # Analytics block — only if available
    a = debate_transcript.analytics
    if a:
        lines += [
            "",
            "DEBATE OUTCOME ANALYTICS:",
            f"  Outcome:               {a.debate_outcome}",
            f"  Most persuasive agent: {a.most_persuasive_agent}",
            f"  Most consistent agent: {a.most_consistent_agent}",
            f"  Most contested issue:  {a.most_contested_dimension}",
            f"  Key unresolved issue:  {a.key_unresolved_issue}",
            "",
            "POSITION SHIFTS (how agents moved under pressure):",
        ]
        for agent_id, shift in a.position_shifts.items():
            lines.append(f"  {agent_id}: {shift}")

        if a.average_strength_by_agent:
            lines += ["", "AVERAGE ARGUMENT STRENGTH BY AGENT:"]
            for agent_id, strength in a.average_strength_by_agent.items():
                lines.append(f"  {agent_id}: {strength:.2f}")

    # Contradictions
    contras = debate_transcript.contradictions
    if contras:
        lines += ["", f"CONTRADICTIONS IDENTIFIED ({len(contras)} total):"]
        for c in contras:
            lines.append(
                f"  [{c.severity}] {c.contradiction_id}: {c.agent_a} vs {c.agent_b} — {c.dimension}"
            )
            lines.append(f"    {c.agent_a}: \"{c.agent_a_claim}\"")
            lines.append(f"    {c.agent_b}: \"{c.agent_b_claim}\"")
            lines.append(f"    Status: {c.resolution_status}" + (
                f" — {c.resolution_summary}" if c.resolution_summary else ""
            ))
    else:
        lines.append("\nNo formal contradictions logged during debate.")

    # Round-by-round argument type summary
    lines += ["", "ROUND-BY-ROUND ARGUMENT SUMMARY:"]
    for rnd in debate_transcript.rounds:
        lines.append(f"  Round {rnd.round_number} ({rnd.round_type}):")
        for arg in rnd.arguments:
            shift_note = f" [{arg.position_shift}]" if arg.position_shift else ""
            strength_note = (
                f" strength={arg.argument_strength.overall:.2f}"
                if arg.argument_strength else ""
            )
            lines.append(
                f"    {arg.agent_id}: {arg.argument_type}{shift_note}{strength_note} | "
                f"position={arg.position} | confidence={arg.confidence:.2f}"
            )
            if arg.critiques_of_others:
                for crit in arg.critiques_of_others:
                    lines.append(
                        f"      → critiques {crit.target_agent} [{crit.severity}]: {crit.target_claim[:80]}"
                    )

    return "\n".join(lines)


class ArbiterAgent(BaseAgent):
    agent_id = "arbiter_agent_v1"

    async def adjudicate(
        self,
        claim_data: dict,
        fraud_report: AgentReport,
        medical_report: AgentReport,
        policy_report: AgentReport,
        risk_score: RiskScore,
        debate_transcript: DebateTranscript | None,
        similar_cases: list[dict],
        context: dict,
    ) -> AgentReport:
        claim_id = claim_data["id"]
        audit_log = []
        t0 = self._start_timer()

        audit_log.append(self._audit(
            claim_id, "AGENT_START", "Arbiter deliberation starting",
            input_snapshot={"risk_score": risk_score.composite_score, "debate_occurred": debate_transcript is not None},
        ))

        debate_summary = _build_debate_summary(debate_transcript)

        # Precedent context
        precedent_context = "No historical precedents retrieved."
        if similar_cases:
            cases = [
                f"- Verdict: {c.get('verdict', 'N/A')}, Risk: {c.get('risk_score', 'N/A'):.2f}, Summary: {c.get('summary', '')[:100]}"
                for c in similar_cases[:3]
            ]
            precedent_context = "Historical precedents:\n" + "\n".join(cases)

        user_prompt = f"""
You must render a final adjudication decision for this claim.

CLAIM:
{json.dumps({k: v for k, v in claim_data.items() if k not in ['raw_payload']}, indent=2, default=str)}

RISK SCORE:
- Composite: {risk_score.composite_score:.4f} ({risk_score.classification})
- Fraud Score: {risk_score.component_scores.fraud_score:.4f}
- Medical Risk Score: {risk_score.component_scores.medical_risk_score:.4f}
- Policy Risk Score: {risk_score.component_scores.policy_risk_score:.4f}
- Routing: {risk_score.routing_decision}

FRAUD AGENT:
- Verdict: {fraud_report.verdict} (confidence: {fraud_report.confidence:.2f}, score: {fraud_report.score:.2f})
- Key reasoning: {[s.inference for s in sorted(fraud_report.reasoning_chain, key=lambda x: x.weight, reverse=True)[:3]]}
- Flags: {fraud_report.flags}

MEDICAL AGENT:
- Verdict: {medical_report.verdict} (confidence: {medical_report.confidence:.2f}, score: {medical_report.score:.2f})
- Key reasoning: {[s.inference for s in sorted(medical_report.reasoning_chain, key=lambda x: x.weight, reverse=True)[:3]]}
- Flags: {medical_report.flags}

POLICY AGENT:
- Verdict: {policy_report.verdict} (confidence: {policy_report.confidence:.2f}, score: {policy_report.score:.2f})
- Key reasoning: {[s.inference for s in sorted(policy_report.reasoning_chain, key=lambda x: x.weight, reverse=True)[:3]]}
- Flags: {policy_report.flags}

DEBATE ANALYSIS:
{debate_summary}

PRECEDENTS:
{precedent_context}

Render your final decision as JSON.
"""
        llm_start = self._start_timer()
        llm_response = await safe_arbiter_llm_call(
            prompt=user_prompt,
            system=SYSTEM_PROMPT,
            use_json=True,
        )
        llm_ms = self._elapsed_ms(llm_start)
        
        if llm_response["status"] == "SUCCESS":
            result = llm_response["result"]
            log.info(
                "ARBITER_LLM_SUCCESS",
                duration_ms=llm_ms,
                verdict=result.get("verdict"),
            )
            audit_log.append(self._audit(
                claim_id, "LLM_CALL",
                f"Arbiter decision: {result.get('verdict')}",
                output_snapshot={
                    "verdict": result.get("verdict"),
                    "confidence": result.get("confidence"),
                },
                duration_ms=llm_ms,
            ))
        else:
            log.error(
                "ARBITER_LLM_FAILED",
                reason=llm_response["reason"],
                error=llm_response["error"],
            )
            log.warning("FALLBACK TRIGGERED", component="arbiter", reason=llm_response["reason"])
            result = {}
            audit_log.append(self._audit(
                claim_id, "LLM_CALL_FAILED",
                f"Arbiter LLM unavailable: {llm_response['reason']}",
                output_snapshot={"reason": llm_response["reason"]},
                duration_ms=llm_ms,
            ))

        # CRITICAL: If arbiter LLM failed, never leave an empty / PENDING-only outcome
        if llm_response["status"] == "LLM_FAILED":
            if self._all_specialists_llm_fallback(fraud_report, medical_report, policy_report):
                log.warning("FALLBACK TRIGGERED", mode="rule_based_amount", reason="all_specialists_llm_fallback")
                result = self._apply_rule_based_fallback(
                    claim_data=claim_data,
                    fraud_report=fraud_report,
                    medical_report=medical_report,
                    policy_report=policy_report,
                    risk_score=risk_score,
                )
            else:
                log.warning(
                    "FALLBACK TRIGGERED",
                    mode="specialist_vote",
                    reason="arbiter_llm_failed_specialists_partial",
                )
                result = self._heuristic_from_specialists(
                    fraud_report=fraud_report,
                    medical_report=medical_report,
                    policy_report=policy_report,
                    claim_data=claim_data,
                    risk_score=risk_score,
                )

        # Auto-apply hard rules after LLM / fallback so fraud thresholds always win
        result = self._apply_hard_rules(result, risk_score, fraud_report, medical_report, policy_report, claim_data, debate_transcript)

        reasoning_chain = [ReasoningStep(**s) for s in result.get("reasoning_chain", [])]

        audit_log.append(self._audit(
            claim_id, "AGENT_COMPLETE",
            f"Arbiter complete: verdict={result.get('verdict')}, human_required={result.get('human_required')}",
            duration_ms=self._elapsed_ms(t0),
        ))

        return self._make_report(
            claim_id=claim_id,
            verdict=AgentVerdict(result.get("verdict", "PENDING")),
            confidence=float(result.get("confidence", 0.5)),
            score=risk_score.composite_score,
            reasoning_chain=reasoning_chain,
            flags=result.get("key_deciding_factors", []),
            override_ready=True,
            audit_log=[AuditEntry(**e) for e in audit_log],
            raw_llm_output=json.dumps(result),
        ), result

    def _apply_hard_rules(
        self,
        result: dict,
        risk: RiskScore,
        fraud: AgentReport,
        medical: AgentReport,
        policy: AgentReport,
        claim_data: dict,
        debate_transcript: "DebateTranscript | None" = None,
    ) -> dict:
        requested_amount = float(claim_data.get("requested_amount", claim_data.get("billed_amount", 0)) or 0)
        
        # Agents' verdicts and scores
        f_v = fraud.verdict.value if hasattr(fraud.verdict, "value") else str(fraud.verdict)
        m_v = medical.verdict.value if hasattr(medical.verdict, "value") else str(medical.verdict)
        p_v = policy.verdict.value if hasattr(policy.verdict, "value") else str(policy.verdict)
        
        f_risk = fraud.score
        m_risk = medical.score
        p_risk = policy.score
        
        # Collect conditions
        any_risk_high = f_risk >= 0.55 or m_risk >= 0.55 or p_risk >= 0.55
        policy_risk_critical = p_risk >= 0.65  # HARD RULE: Policy risk >= 0.65 prevents APPROVE
        policy_high_cost_fail = p_v in ("PENDING", "REJECT", "NON_COMPLIANT", "REVIEW") and requested_amount > 10000
        policy_signal_hint = (
            any(flag in (policy.flags or []) for flag in ("OUT_OF_NETWORK", "MISSING_PRIOR_AUTH"))
            or any(keyword in str(policy.raw_llm_output).lower() for keyword in [
                "out-of-network", "out of network", "no prior authorization", "missing prior auth", "no auth"
            ])
        )
        out_of_network_policy_approve = (
            result.get("verdict") == "APPROVE"
            and requested_amount > 10000
            and policy_signal_hint
        )
        any_agent_review = f_v in ("PENDING", "REVIEW", "HIGH_RISK") or m_v in ("PENDING", "REVIEW", "INVALID") or p_v in ("PENDING", "REVIEW", "NON_COMPLIANT")
        any_agent_reject = f_v == "REJECT" or m_v == "REJECT" or p_v == "REJECT"

        # Rule 1 & 2: Never APPROVE if policy risk >= 0.65, high risk, or high-cost policy fail
        if result.get("verdict") == "APPROVE" and (policy_risk_critical or any_risk_high or policy_high_cost_fail or out_of_network_policy_approve):
            result["verdict"] = "PENDING"
            result["approved_amount"] = None
            result["human_required"] = True
            result["escalation_priority"] = "P1"
            if policy_risk_critical:
                result["conflict_summary"] = f"Hard Rule: Policy risk {p_risk:.2f} >= 0.65 prevents APPROVE."
            elif out_of_network_policy_approve:
                result["conflict_summary"] = (
                    "Hard Rule: Policy review signals out-of-network or missing prior auth on a high-cost claim — APPROVE prevented."
                )

        # Rule 3: APPROVE only if all agents clear + composite risk < 0.35
        all_clear = f_v in ("APPROVE", "LOW_RISK") and m_v in ("APPROVE", "VALID") and p_v in ("APPROVE", "COMPLIANT")
        if result.get("verdict") == "APPROVE":
            if not all_clear or risk.composite_score >= 0.35:
                result["verdict"] = "PENDING"
                result["approved_amount"] = None
                result["human_required"] = True
                result["escalation_priority"] = "P2"
                result["conflict_summary"] = "Hard Rule: APPROVE denied because not all agents cleared or composite risk >= 0.35."

        # Rule 4: REVIEW if any agent flagged for review
        if result.get("verdict") == "APPROVE" and any_agent_review:
            result["verdict"] = "PENDING"
            result["approved_amount"] = None
            result["human_required"] = True
            result["escalation_priority"] = "P2"
        
        # Rule 5: REJECT only for invalid amount, severe fraud, or severe policy failure
        invalid_amount = requested_amount <= 0
        severe_fraud = risk.component_scores.fraud_score >= 0.85 or f_v == "REJECT"
        severe_policy = p_risk >= 0.85 or p_v == "REJECT"
        
        if invalid_amount or severe_fraud or severe_policy:
            result["verdict"] = "REJECT"
            result["human_required"] = False
            result["confidence"] = max(result.get("confidence", 0.5), 0.90)
            result["denial_reason"] = "Hard Rule: Auto-rejected due to invalid amount, severe fraud, or severe policy failure."
        
        # Existing fallback escalation rules
        if risk.composite_score > settings.risk_escalate_min:
            result["human_required"] = True
            result["escalation_priority"] = "P1"

        if (
            debate_transcript
            and debate_transcript.analytics
            and debate_transcript.analytics.debate_outcome == "DEADLOCK"
            and risk.composite_score > 0.55
        ):
            result["human_required"] = True
            result["escalation_priority"] = "P1"
            existing = result.get("conflict_summary", "")
            result["conflict_summary"] = f"[DEADLOCK] Agents could not reach consensus. {existing}"

        if not result.get("appeals_pathway") and result.get("verdict") in ("REJECT", "PENDING"):
            result["appeals_pathway"] = (
                "To appeal this decision: submit a written appeal within 60 days to the Appeals Department."
            )

        return result

    def _all_specialists_llm_fallback(
        self,
        fraud_report: AgentReport,
        medical_report: AgentReport,
        policy_report: AgentReport,
    ) -> bool:
        for r in (fraud_report, medical_report, policy_report):
            if "LLM_FALLBACK" not in (r.flags or []):
                return False
        return True

    def _heuristic_from_specialists(
        self,
        fraud_report: AgentReport,
        medical_report: AgentReport,
        policy_report: AgentReport,
        claim_data: dict,
        risk_score: RiskScore,
    ) -> dict:
        """When arbiter LLM fails but specialists produced real signals, aggregate their verdicts."""
        votes: defaultdict[str, float] = defaultdict(float)
        for r in (fraud_report, medical_report, policy_report):
            vv = r.verdict.value if hasattr(r.verdict, "value") else str(r.verdict)
            if vv == "UNCERTAIN":
                continue
            votes[vv] += float(r.confidence)

        if not votes:
            log.info("HEURISTIC_FALLBACK_EMPTY_VOTES", detail="using_amount_rules")
            return self._apply_rule_based_fallback(
                claim_data, fraud_report, medical_report, policy_report, risk_score,
            )

        best = max(votes, key=votes.get)
        if best == "APPROVE":
            final_v = "APPROVE"
        elif best == "REJECT":
            final_v = "REJECT"
        else:
            final_v = "PENDING"

        conf = min(0.85, 0.42 + votes[best] / 3.0)
        req = float(claim_data.get("requested_amount", claim_data.get("billed_amount", 0)) or 0)
        bill = float(claim_data.get("billed_amount", 0) or 0)
        approved = req if req > 0 else (bill if final_v == "APPROVE" else None)

        return {
            "verdict": final_v,
            "confidence": conf,
            "approved_amount": approved if final_v == "APPROVE" else None,
            "denial_reason": (
                "Heuristic aggregation: majority specialist signal was REJECT."
                if final_v == "REJECT" else None
            ),
            "appeals_pathway": (
                "Specialist agents disagreed or flagged review; see conflict summary."
                if final_v == "PENDING" else None
            ),
            "human_required": final_v == "PENDING",
            "escalation_priority": "P2" if final_v == "PENDING" else "P3",
            "conflict_summary": (
                f"Arbiter LLM unavailable; aggregated specialist verdicts (weighted): {dict(votes)}."
            ),
            "unresolved_issues": ["arbiter_llm_unavailable_heuristic"],
            "key_deciding_factors": [
                "arbiter_llm_fallback",
                f"dominant_specialist_verdict={best}",
            ],
            "reasoning_chain": [
                {
                    "step": 1,
                    "observation": "Arbiter model call failed; specialists had partial outputs",
                    "evidence": [f"votes={dict(votes)}"],
                    "inference": f"Heuristic winner={best} → final={final_v}",
                    "weight": 0.75,
                }
            ],
        }

    def _apply_rule_based_fallback(
        self,
        claim_data: dict,
        fraud_report: AgentReport,
        medical_report: AgentReport,
        policy_report: AgentReport,
        risk_score: RiskScore,
    ) -> dict:
        """
        When all specialist LLM calls failed, use deterministic fallback rules.
        Prevents perpetual PENDING due to LLM failure.
        """
        log.info("RULE_BASED_FALLBACK_ACTIVE")

        requested_amount = float(claim_data.get("requested_amount", claim_data.get("billed_amount", 0)) or 0)
        billed_amount = float(claim_data.get("billed_amount", 0) or 0)
        in_network = bool(claim_data.get("in_network", True))

        log.warning(
            "ALL_AGENTS_LLM_FAILED",
            fraud_llm_fallback="LLM_FALLBACK" in (fraud_report.flags or []),
            medical_llm_fallback="LLM_FALLBACK" in (medical_report.flags or []),
            policy_llm_fallback="LLM_FALLBACK" in (policy_report.flags or []),
        )

        # 1) billed_amount <= 0 → DENIED (REJECT)
        if billed_amount <= 0:
            log.info(
                "RULE_BASED_DECISION_DENY",
                billed=billed_amount,
                reason="billed_amount <= 0",
            )
            return {
                "verdict": "REJECT",
                "confidence": 0.8,
                "approved_amount": 0.0,
                "denial_reason": "Invalid billed amount",
                "appeals_pathway": (
                    "To appeal: submit corrected claim with valid billed amount. "
                    "Contact the provider billing department."
                ),
                "human_required": False,
                "escalation_priority": "P3",
                "conflict_summary": (
                    "LLM FALLBACK: All agents failed. "
                    "Rule-based decision: Invalid billed amount → REJECTED."
                ),
                "unresolved_issues": ["llm_unavailable_fallback_applied", "invalid_amount"],
                "key_deciding_factors": [
                    "LLM unavailable - rule-based fallback",
                    f"billed_amount ({billed_amount}) is invalid",
                    "Claim structure is broken",
                ],
                "reasoning_chain": [
                    {
                        "step": 1,
                        "observation": "All LLM agents failed with LLM_FALLBACK flag",
                        "evidence": ["llm_unavailable"],
                        "inference": "Cannot perform AI-based analysis",
                        "weight": 1.0,
                    },
                    {
                        "step": 2,
                        "observation": f"Billed amount is {billed_amount} (invalid)",
                        "evidence": ["data_quality"],
                        "inference": "Claim data structure is broken or missing",
                        "weight": 0.95,
                    },
                    {
                        "step": 3,
                        "observation": "System rejects invalid claim",
                        "evidence": ["fallback_logic"],
                        "inference": "REJECT — resubmit with valid data",
                        "weight": 0.9,
                    },
                ],
            }

        # 2) requested_amount > billed_amount → REVIEW (PENDING)
        if requested_amount > billed_amount:
            log.info(
                "RULE_BASED_DECISION_REVIEW",
                requested=requested_amount,
                billed=billed_amount,
                reason="requested_amount > billed_amount",
            )
            return {
                "verdict": "PENDING",
                "confidence": 0.5,
                "approved_amount": None,
                "denial_reason": None,
                "appeals_pathway": (
                    "Claim requires review: requested amount exceeds billed amount. "
                    "Please contact claims department for clarification."
                ),
                "human_required": True,
                "escalation_priority": "P2",
                "conflict_summary": (
                    "LLM FALLBACK: All agents failed. "
                    "Rule-based decision: Requested amount exceeds billed amount → requires review."
                ),
                "unresolved_issues": ["llm_unavailable_fallback_applied", "amount_mismatch"],
                "key_deciding_factors": [
                    "LLM unavailable - rule-based fallback",
                    f"requested_amount ({requested_amount}) > billed_amount ({billed_amount})",
                    "Amount discrepancy requires human review",
                ],
                "reasoning_chain": [
                    {
                        "step": 1,
                        "observation": "All LLM agents failed with LLM_FALLBACK flag",
                        "evidence": ["llm_unavailable"],
                        "inference": "Cannot perform AI-based analysis",
                        "weight": 1.0,
                    },
                    {
                        "step": 2,
                        "observation": f"Requested amount ({requested_amount}) exceeds billed amount ({billed_amount})",
                        "evidence": ["amount_discrepancy"],
                        "inference": "Claim contains inconsistency requiring investigation",
                        "weight": 0.9,
                    },
                    {
                        "step": 3,
                        "observation": "System escalates to human review for clarification",
                        "evidence": ["fallback_logic"],
                        "inference": "PENDING pending human investigation",
                        "weight": 0.8,
                    },
                ],
            }

        # 3) requested_amount <= billed_amount and in_network → APPROVED
        if in_network and requested_amount <= billed_amount:
            log.info(
                "RULE_BASED_DECISION_APPROVE",
                requested=requested_amount,
                billed=billed_amount,
                in_network=in_network,
                reason="in_network and requested_amount <= billed_amount",
            )
            approved_amt = requested_amount if requested_amount > 0 else billed_amount
            return {
                "verdict": "APPROVE",
                "confidence": 0.6,
                "approved_amount": approved_amt,
                "denial_reason": None,
                "appeals_pathway": None,
                "human_required": False,
                "escalation_priority": "P3",
                "conflict_summary": "Rule-based approval due to LLM failure",
                "unresolved_issues": ["llm_unavailable_fallback_applied"],
                "key_deciding_factors": [
                    "LLM unavailable - rule-based fallback",
                    "in_network",
                    f"requested_amount ({requested_amount}) <= billed_amount ({billed_amount})",
                ],
                "reasoning_chain": [
                    {
                        "step": 1,
                        "observation": "LLM unavailable across specialists",
                        "evidence": ["llm_unavailable"],
                        "inference": "Apply deterministic fallback rules",
                        "weight": 1.0,
                    },
                    {
                        "step": 2,
                        "observation": "In-network and amounts are consistent",
                        "evidence": [f"in_network={in_network}", f"requested={requested_amount}", f"billed={billed_amount}"],
                        "inference": "APPROVE",
                        "weight": 0.9,
                    },
                ],
            }

        # 4) Otherwise → REVIEW (human)
        log.info(
            "RULE_BASED_DECISION_REVIEW",
            requested=requested_amount,
            billed=billed_amount,
            in_network=in_network,
            reason="LLM unavailable – human review required",
        )
        return {
            "verdict": "PENDING",
            "confidence": 0.5,
            "approved_amount": None,
            "denial_reason": None,
            "appeals_pathway": "LLM unavailable – human review required",
            "human_required": True,
            "escalation_priority": "P2",
            "conflict_summary": "LLM unavailable – human review required",
            "unresolved_issues": ["llm_unavailable_fallback_applied"],
            "key_deciding_factors": ["LLM unavailable - rule-based fallback"],
            "reasoning_chain": [{
                "step": 1,
                "observation": "LLM unavailable across specialists",
                "evidence": ["llm_unavailable"],
                "inference": "Escalate to human review",
                "weight": 1.0,
            }],
        }

    def _fallback_result(self, risk: RiskScore) -> dict:
        return {
            "verdict": "PENDING",
            "confidence": 0.3,
            "approved_amount": None,
            "denial_reason": None,
            "appeals_pathway": "Please contact customer support for claim status.",
            "human_required": True,
            "escalation_priority": "P2",
            "conflict_summary": "Arbiter LLM unavailable — escalated to human review",
            "unresolved_issues": ["arbiter_llm_unavailable"],
            "key_deciding_factors": ["system_error", "human_review_required"],
            "reasoning_chain": [{
                "step": 1,
                "observation": "Arbiter LLM call failed",
                "evidence": ["system_error"],
                "inference": "Cannot render automated decision — routed to human review",
                "weight": 1.0,
            }],
        }

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        raise NotImplementedError("Use adjudicate() instead")
