import json
import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport, AgentVerdict, AuditEntry
from app.services.llm_safety_wrapper import safe_llm_call
from app.services.unified_llm import _env

log = structlog.get_logger()

# ── System Prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Policy Compliance Agent — a senior insurance contract specialist.

Review the claim and return ONLY valid JSON matching this exact structure:
{
  "verdict": "APPROVE" | "REJECT" | "PENDING",
  "confidence": <float 0-1>,
  "risk_score": <float 0-1, 1 is high policy risk>,
  "reason": "<one clear sentence explaining the policy verdict>",
  "extracted_signals": ["<policy signal 1>", "<signal 2>"],
  "evidence": ["<specific evidence 1>", "<evidence 2>"],
  "source": "gemini" | "heuristic",
  "model": "<active model name>"
}

Rules:
- Verify network status and prior authorization.
- CRITICAL: Parse diagnosis_summary AND claim fields for the exact signals: "out-of-network", "out of network", "no prior authorization", "missing prior auth", "no auth".
- If amount > 10000 and any of these signals exist: verdict MUST be PENDING/REVIEW/NON_COMPLIANT and risk_score >= 0.65.
- If the claim is out-of-network or missing prior auth on a high-cost service, do NOT approve.
- Provide strict JSON with no markdown formatting around it.
"""


class PolicyComplianceAgent(BaseAgent):
    agent_id = "policy_agent_v2"

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        claim_id = claim_data["id"]
        audit_log = []
        t0 = self._start_timer()

        audit_log.append(self._audit(
            claim_id, "AGENT_START",
            "Policy compliance agent v2 starting contract analysis",
            input_snapshot={
                "policy_number": claim_data.get("policy_number"),
                "plan_id": claim_data.get("plan_id"),
                "in_network": claim_data.get("in_network"),
                "prior_auth": claim_data.get("prior_auth_number"),
                "billed_amount": claim_data.get("billed_amount"),
            },
        ))

        plan_analysis = _analyze_plan_type(claim_data)
        auth_analysis = _analyze_auth_requirement(claim_data)

        user_prompt = f"""
Perform a complete insurance policy compliance review of this healthcare claim.
Work through all 6 reasoning phases before forming your verdict.

═══════════════════════════════════════
CLAIM UNDER POLICY REVIEW
═══════════════════════════════════════
{json.dumps(_sanitize_claim(claim_data), indent=2, default=str)}

═══════════════════════════════════════
PLAN TYPE ANALYSIS
═══════════════════════════════════════
{plan_analysis}

═══════════════════════════════════════
PRIOR AUTHORIZATION ANALYSIS
═══════════════════════════════════════
{auth_analysis}

═══════════════════════════════════════
POLICY REVIEWER CHECKLIST
═══════════════════════════════════════
Before forming your verdict, confirm you have assessed:
[ ] Member eligibility on service date
[ ] Service coverage under plan type
[ ] Network status impact (in-network vs out-of-network benefit tier)
[ ] Prior authorization compliance
[ ] Any applicable exclusions for this service type
[ ] Coverage limits and frequency restrictions
[ ] Financial model: covered amount, adjustments, payable amount

Output your full policy compliance analysis as JSON.
"""
        llm_start = self._start_timer()
        llm_response = await safe_llm_call(
            prompt=user_prompt,
            system=SYSTEM_PROMPT,
            use_json=True,
            max_tokens=1200,
        )
        llm_ms = self._elapsed_ms(llm_start)
        
        if llm_response["status"] == "SUCCESS":
            result = llm_response["result"]
            # Ensure source and model fields for audit trail
            if "source" not in result:
                result["source"] = "gemini"
            if "model" not in result:
                result["model"] = _env("GEMINI_MODEL", "gemini-2.0-flash")
            log.info(
                "POLICY_AGENT_LLM_SUCCESS",
                duration_ms=llm_ms,
                verdict=result.get("verdict"),
            )
            audit_log.append(self._audit(
                claim_id, "LLM_CALL",
                f"Policy review complete: verdict={result.get('verdict')}, score={result.get('score')}",
                output_snapshot={
                    "verdict": result.get("verdict"),
                    "score": result.get("score"),
                    "estimated_payable": result.get("financial_model", {}).get("estimated_payable"),
                    "duration_ms": llm_ms,
                },
                duration_ms=llm_ms,
            ))
        else:
            log.error(
                "POLICY_AGENT_LLM_FAILED",
                reason=llm_response["reason"],
                error=llm_response["error"],
            )
            result = _heuristic_fallback(claim_data)
            result["source"] = "heuristic"
            result["model"] = "policy_heuristic_v1"
            audit_log.append(self._audit(
                claim_id, "LLM_CALL_FAILED",
                f"LLM unavailable — heuristic fallback: verdict={result.get('verdict')}",
                output_snapshot={"reason": llm_response["reason"]},
                duration_ms=llm_ms,
            ))
            log.warning("FALLBACK_TRIGGERED", agent="policy_agent", reason=llm_response["reason"])

        # ── Parse simplified output ─────────────────────────────────────────────
        score = float(result.get("risk_score", result.get("score", 0.5)))
        reason = result.get("reason", "No reason provided")
        evidence_list = result.get("evidence", [])
        signals = result.get("extracted_signals", [])

        # Reconstruct missing financial model for Arbiter requirement
        billed = float(claim_data.get("billed_amount") or 0.0)
        covered = billed if result.get("verdict") == "APPROVE" else 0.0
        network_adj = 0.0 if claim_data.get("in_network") else 20.0
        prior_auth = claim_data.get("prior_auth_number")
        auth_penalty = 0.0 if prior_auth else 0.2
        denial_basis = ""
        if not prior_auth:
            denial_basis = "missing_prior_auth"
        elif not claim_data.get("in_network"):
            denial_basis = "out_of_network"
        from app.agents.policy_agent import _build_financial_model
        fin_model = _build_financial_model(billed, covered, network_adj, auth_penalty, denial_basis)

        enhanced = {
            "reasoning_chain": [
                {
                    "step": 1,
                    "observation": reason,
                    "evidence": evidence_list,
                    "inference": f"Verdict: {result.get('verdict')}",
                    "weight": 1.0
                }
            ],
            "key_evidence": [
                {
                    "evidence_id": f"E{i}",
                    "type": "SUPPORTING",
                    "description": e,
                    "source": "policy_document",
                    "strength": 0.8,
                    "cited_value": "extracted"
                } for i, e in enumerate(evidence_list)
            ],
            "risk_factors": [],
            "flags": signals,
            "financial_model": fin_model,
        }

        audit_log.append(self._audit(
            claim_id, "AGENT_COMPLETE",
            f"Policy compliance complete: verdict={result.get('verdict')}, score={score}",
            output_snapshot={
                "verdict": result.get("verdict"),
                "score": score,
                "flags": signals,
            },
            duration_ms=self._elapsed_ms(t0),
        ))

        from app.schemas.agent import AuditEntry as AuditEntrySchema
        return self._make_report(
            claim_id=claim_id,
            verdict=AgentVerdict(result.get("verdict", "UNCERTAIN")),
            confidence=float(result.get("confidence", 0.5)),
            score=score,
            override_ready=True,
            audit_log=[AuditEntrySchema(**e) for e in audit_log],
            raw_llm_output=json.dumps(result),
            **enhanced,
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sanitize_claim(claim_data: dict) -> dict:
    skip = {"raw_payload", "is_simulation"}
    return {k: v for k, v in claim_data.items() if k not in skip}


def _analyze_plan_type(claim_data: dict) -> str:
    """Derive plan type rules from plan_id and context clues."""
    plan_id = (claim_data.get("plan_id") or "").upper()
    in_network = claim_data.get("in_network", True)
    lines = []

    if "HMO" in plan_id:
        lines.append("Plan type: HMO (Health Maintenance Organization)")
        lines.append("HMO rules apply: out-of-network services generally NOT COVERED except emergencies")
        if not in_network:
            lines.append("⚠ CRITICAL: Provider is OUT-OF-NETWORK under HMO plan → likely full denial unless emergency")
    elif "PPO" in plan_id:
        lines.append("Plan type: PPO (Preferred Provider Organization)")
        lines.append("PPO rules: in-network preferred; out-of-network covered at reduced rate (typically 60%)")
        if not in_network:
            lines.append("⚠ NOTE: Out-of-network under PPO → apply out-of-network benefit tier (typically 60% coinsurance)")
    elif "EPO" in plan_id:
        lines.append("Plan type: EPO (Exclusive Provider Organization)")
        lines.append("EPO rules: similar to HMO — out-of-network NOT COVERED except emergencies")
        if not in_network:
            lines.append("⚠ CRITICAL: Provider out-of-network under EPO plan → likely full denial unless emergency")
    elif "HDHP" in plan_id:
        lines.append("Plan type: HDHP (High Deductible Health Plan)")
        lines.append("HDHP rules: higher deductible applies; HSA-compatible; preventive care covered pre-deductible")
    else:
        lines.append(f"Plan type: Unspecified (plan_id={plan_id}) — applying standard commercial PPO rules as default")

    tier = "GOLD" if "GOLD" in plan_id else ("SILVER" if "SILVER" in plan_id else ("BRONZE" if "BRONZE" in plan_id else "STANDARD"))
    benefit_map = {"GOLD": "80-90%", "SILVER": "70-80%", "BRONZE": "60-70%", "STANDARD": "70-80%"}
    lines.append(f"Plan tier: {tier} → expected coverage rate {benefit_map.get(tier, '70-80%')}")

    return "\n".join(lines)


def _analyze_auth_requirement(claim_data: dict) -> str:
    """Determine if prior auth is likely required for the procedures."""
    cpt_codes = claim_data.get("cpt_codes", [])
    has_auth = bool(claim_data.get("prior_auth_number"))
    lines = []

    # Surgery codes requiring auth
    surgery = [c for c in cpt_codes if c.isdigit() and 10000 <= int(c) <= 69999]
    # Complex imaging
    imaging_auth = [c for c in cpt_codes if c.startswith(("702", "703", "704", "717", "718", "721", "722", "723", "724"))]
    # High-cost procedures
    high_cost_cpt = ["93306", "93307", "93308", "43239", "43240", "43249", "27447", "27130", "29827"]
    high_cost = [c for c in cpt_codes if c in high_cost_cpt]

    auth_required_codes = surgery + imaging_auth + high_cost
    auth_required = len(auth_required_codes) > 0

    if auth_required:
        lines.append(f"⚠ PRIOR AUTHORIZATION LIKELY REQUIRED for: {auth_required_codes}")
        if has_auth:
            lines.append(f"✓ Authorization on file: {claim_data.get('prior_auth_number')} — requirement satisfied")
        else:
            lines.append("✗ No prior authorization number on claim — MISSING REQUIRED AUTH")
            lines.append("  Impact: Typically 50% reduction or full denial per plan terms")
    else:
        lines.append("Prior authorization likely NOT required for routine services in this CPT set")
        if has_auth:
            lines.append(f"Note: Auth provided ({claim_data.get('prior_auth_number')}) though not required — no penalty, no benefit")

    return "\n".join(lines)


def _heuristic_fallback(claim_data: dict) -> dict:
    """
    Deterministic policy heuristic using claim signal extractor.
    Checks in_network status and prior_auth presence against claim value.
    Also parses diagnosis_summary for network/auth keywords.
    """
    from app.core.claim_signal_extractor import extract_signals

    signals = extract_signals(claim_data)
    flags: list[str] = ["LLM_FALLBACK", "HEURISTIC_SAFETY_FALLBACK"] + signals.risk_flags
    evidence: list[dict] = []
    reasoning: list[dict] = []

    # Parse diagnosis_summary for network/auth signals
    diagnosis_text = (claim_data.get("diagnosis_summary") or "").lower()
    has_out_of_network_signal = any(keyword in diagnosis_text for keyword in ["out-of-network", "out of network", "oob"])
    has_missing_auth_signal = any(keyword in diagnosis_text for keyword in ["no prior authorization", "missing prior auth", "no auth", "unauthorized"])

    # If critical policy fields are missing — UNCERTAIN
    if claim_data.get("billed_amount") is None and claim_data.get("in_network") is None:
        return _pol_uncertain("billed_amount and in_network both missing", claim_data)

    # ── Rule 1: Out-of-network OR missing prior auth on high-cost claim → REVIEW / NON_COMPLIANT
    policy_violation = (
        signals.billed_amount > 10000
        and (
            signals.is_out_of_network
            or has_out_of_network_signal
            or signals.is_missing_prior_auth
            or has_missing_auth_signal
        )
    )
    if policy_violation:
        issues = []
        details = []
        if signals.is_out_of_network:
            issues.append("Out-of-network")
            details.append("structured field indicates provider is out-of-network")
        if has_out_of_network_signal:
            issues.append("Out-of-network")
            details.append("diagnosis_summary contains out-of-network signal")
        if signals.is_missing_prior_auth:
            issues.append("no prior authorization")
            details.append("prior auth number missing")
        if has_missing_auth_signal:
            issues.append("no prior authorization")
            details.append("diagnosis_summary contains missing auth signal")
        issue_str = "; ".join(dict.fromkeys(issues))
        evidence.append({
            "evidence_id": "E1",
            "type": "CONTRADICTING",
            "description": f"{issue_str} on high-value claim (${signals.billed_amount:,.0f}): {'; '.join(details)}",
            "source": "claim_data",
            "strength": 0.9,
            "cited_value": f"in_network={signals.is_in_network}, prior_auth={signals.has_prior_auth}, billed={signals.billed_amount}",
        })
        reasoning.append({
            "step": 1,
            "observation": f"{issue_str} detected on high-value claim",
            "evidence": ["network_or_auth_policy_violation"],
            "inference": "Policy violation — review required before approval",
            "weight": 0.95,
        })
        reason_text = f"{issue_str} detected on high-value claim (${signals.billed_amount:,.0f})."
        return _build_pol_result(
            "PENDING", "NON_COMPLIANT", reason_text,
            0.75, 0.75, evidence, reasoning, flags, signals.billed_amount
        )

    # ── Rule 2: In-network + prior auth exists → COMPLIANT (score ≤0.20)
    if signals.is_in_network and signals.has_prior_auth:
        evidence.append({
            "evidence_id": "E1", "type": "SUPPORTING",
            "description": "In-network provider with prior authorization on file",
            "source": "claim_data", "strength": 0.8,
            "cited_value": f"in_network=True, prior_auth=True",
        })
        reasoning.append({
            "step": 1, "observation": "Network and auth requirements met",
            "evidence": ["policy_compliance"],
            "inference": "COMPLIANT — policy requirements satisfied",
            "weight": 0.85,
        })
        return _build_pol_result(
            "APPROVE", "COMPLIANT", "in-network and auth present",
            0.70, 0.15, evidence, reasoning, flags, signals.billed_amount
        )

    # ── Rule 3: Missing auth on low cost claim → PENDING / MODERATE RISK
    if signals.is_missing_prior_auth:
        evidence.append({
            "evidence_id": "E1", "type": "AMBIGUOUS",
            "description": f"Missing prior auth on low-cost claim (${signals.billed_amount:,.0f})",
            "source": "claim_data", "strength": 0.6,
            "cited_value": f"auth=False, billed={signals.billed_amount}",
        })
        reasoning.append({
            "step": 1, "observation": "No prior auth but amount is low",
            "evidence": ["missing_auth_low_value"],
            "inference": "REVIEW — verify if auth was required",
            "weight": 0.7,
        })
        return _build_pol_result(
            "PENDING", "REVIEW", "missing auth on low cost claim",
            0.65, 0.50, evidence, reasoning, flags, signals.billed_amount
        )

    # ── Default ──
    evidence.append({
        "evidence_id": "E1", "type": "SUPPORTING",
        "description": "No critical policy violations detected",
        "source": "claim_data", "strength": 0.6,
        "cited_value": f"signals={signals.risk_flags}",
    })
    reasoning.append({
        "step": 1, "observation": "Basic policy compliance met",
        "evidence": ["no_violations"],
        "inference": "COMPLIANT by default",
        "weight": 0.7,
    })
    return _build_pol_result(
        "APPROVE", "COMPLIANT", "no critical policy violations",
        0.65, 0.25, evidence, reasoning, flags, signals.billed_amount
    )


def _build_pol_result(verdict: str, label: str, conclusion: str,
                      confidence: float, score: float,
                      evidence: list, reasoning: list, flags: list, billed: float) -> dict:
    covered = billed if verdict == "APPROVE" else 0.0
    return {
        "internal_reasoning": _pol_reasoning(label, conclusion),
        "verdict": verdict,
        "reason": conclusion,
        "verdict_probability": {
            "APPROVE": 0.70 if verdict == "APPROVE" else 0.15,
            "REJECT": 0.10,
            "PENDING": 0.50 if verdict == "PENDING" else 0.20,
        },
        "confidence": confidence, "score": score,
        "financial_model": _build_financial_model(billed, covered, 0.0, 0.0, conclusion),
        "key_evidence": evidence, "risk_factors": [],
        "reasoning_chain": reasoning,
        "extracted_signals": flags,
        "evidence": [e.get("description", "") for e in evidence],
        "dissenting_view": {
            "alternative_verdict": "PENDING" if verdict != "PENDING" else "APPROVE",
            "probability": 0.20,
            "strongest_argument_for_alternative": "Heuristic cannot parse complex policy rules",
            "what_would_change_my_verdict": "LLM review against plan documents",
        },
        "flags": flags,
        "override_ready": True,
    }


def _pol_reasoning(verdict: str, conclusion: str) -> dict:
    return {
        "phase_1_data_extraction": "LLM unavailable — heuristic safety fallback analyzing policy fields",
        "phase_2_hypothesis": f"Rule-based policy assessment: {verdict}",
        "phase_3_evidence_for": [f"Signal matched: {conclusion}"],
        "phase_4_evidence_against": ["Heuristic cannot parse complex policy document text"],
        "phase_5_weighing": "Rule-based network + auth evaluation",
        "phase_6_conclusion": f"Heuristic: {verdict} — {conclusion}",
    }


def _pol_uncertain(reason: str, claim_data: dict) -> dict:
    return {
        "internal_reasoning": _pol_reasoning("UNCERTAIN", reason),
        "verdict": "UNCERTAIN",
        "verdict_probability": {"APPROVE": 0.33, "REJECT": 0.33, "PENDING": 0.34},
        "confidence": 0.30, "score": 0.50,
        "financial_model": _build_financial_model(float(claim_data.get("billed_amount", 0) or 0), 0, 0, 0, reason),
        "key_evidence": [], "risk_factors": [],
        "reasoning_chain": [{"step": 1, "observation": reason, "evidence": ["missing_data"],
                             "inference": "Cannot complete policy analysis", "weight": 1.0}],
        "dissenting_view": {"alternative_verdict": "PENDING", "probability": 0.34,
                            "strongest_argument_for_alternative": "Missing data ≠ policy violation",
                            "what_would_change_my_verdict": "Complete policy data"},
        "flags": ["LLM_FALLBACK", "HEURISTIC_SAFETY_FALLBACK", "MISSING_DATA"],
        "override_ready": True,
    }


def _build_financial_model(billed: float, covered: float, network_adj: float, auth_penalty: float, denial_basis: str) -> dict:
    return {
        "billed_amount": billed,
        "covered_amount": covered,
        "network_adjustment_pct": network_adj,
        "auth_penalty_pct": auth_penalty,
        "estimated_payable": max(0.0, covered * (1 - network_adj/100) * (1 - auth_penalty/100)),
        "denial_basis": denial_basis,
    }


