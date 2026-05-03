import json
from datetime import datetime
import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport, AgentVerdict, AuditEntry
from app.services.vector_store import vector_store
from app.services.llm_safety_wrapper import safe_llm_call

log = structlog.get_logger()

# ── System Prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Fraud Detection Agent — a forensic investigator.

Review the claim and return ONLY valid JSON matching this exact structure:
{
  "verdict": "APPROVE" | "REJECT" | "PENDING",
  "confidence": <float 0-1>,
  "risk_score": <float 0-1, 1 is high fraud risk>,
  "reason": "<one clear sentence explaining the verdict>",
  "extracted_signals": ["<signal 1>", "<signal 2>"],
  "evidence": ["<specific evidence 1>", "<evidence 2>"],
  "source": "gemini" | "heuristic",
  "model": "<active model name>"
}

Rules:
- Be skeptical. Look for upcoding, unbundling, and anomalies.
- If amount is massive and unsupported, REJECT or PENDING.
- Reason must be specific: cite actual signals (e.g., "$95k high-value claim + requested amount mismatch").
- Provide strict JSON with no markdown formatting around it.
"""


class FraudDetectionAgent(BaseAgent):
    agent_id = "fraud_agent_v2"

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        claim_id = claim_data["id"]
        audit_log = []
        t0 = self._start_timer()

        audit_log.append(self._audit(
            claim_id, "AGENT_START",
            "Fraud detection agent v2 starting forensic analysis",
            input_snapshot={
                "claim_id": claim_id,
                "billed_amount": claim_data.get("billed_amount"),
                "cpt_codes": claim_data.get("cpt_codes"),
                "icd_codes": claim_data.get("icd_codes"),
                "provider_id": claim_data.get("provider_id"),
            },
        ))

        # ── Tool call 1: Retrieve similar flagged cases ────────────────────────
        similar_cases: list[dict] = []
        fraud_patterns: list[dict] = []
        try:
            query_text = (
                f"{claim_data.get('diagnosis_description', '')} "
                f"{' '.join(claim_data.get('icd_codes', []))} "
                f"{' '.join(claim_data.get('cpt_codes', []))} "
                f"provider={claim_data.get('provider_name', '')} "
                f"amount={claim_data.get('billed_amount', '')}"
            )
            similar_cases = vector_store.find_similar_cases(query_text, limit=4)
            fraud_patterns = vector_store.find_fraud_patterns(query_text, limit=3)
            audit_log.append(self._audit(
                claim_id, "TOOL_CALL",
                f"Vector DB: {len(similar_cases)} similar cases, {len(fraud_patterns)} fraud patterns retrieved",
                output_snapshot={"similar_count": len(similar_cases), "pattern_count": len(fraud_patterns)},
            ))
        except Exception as e:
            log.warning("fraud_agent_vector_error", error=str(e))

        # ── Build context-enriched user prompt ────────────────────────────────
        benchmark_hint = _estimate_benchmark(claim_data)
        similar_context = _format_similar_cases(similar_cases)
        pattern_context = _format_fraud_patterns(fraud_patterns)

        user_prompt = f"""
Perform a complete forensic fraud analysis on this healthcare claim.
Work through all 6 reasoning phases before forming your verdict.

═══════════════════════════════════════
CLAIM UNDER INVESTIGATION
═══════════════════════════════════════
{json.dumps(_sanitize_claim(claim_data), indent=2, default=str)}

═══════════════════════════════════════
STATISTICAL BENCHMARK
═══════════════════════════════════════
{benchmark_hint}

═══════════════════════════════════════
HISTORICAL SIMILAR CASES (from case database)
═══════════════════════════════════════
{similar_context}

═══════════════════════════════════════
KNOWN FRAUD PATTERNS (from pattern library)
═══════════════════════════════════════
{pattern_context}

═══════════════════════════════════════
INVESTIGATOR CHECKLIST
═══════════════════════════════════════
Before forming your verdict, confirm you have checked:
[ ] E&M level vs diagnosis complexity match
[ ] CPT code bundling opportunities
[ ] Billed amount vs benchmark
[ ] Code compatibility (no CCI conflicts)
[ ] Service date plausibility
[ ] Provider pattern indicators
[ ] Similar case comparison

Output your full forensic analysis as JSON.
"""

        # ── LLM call ──────────────────────────────────────────────────────────
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
                from app.services.unified_llm import _env
                result["model"] = _env("GEMINI_MODEL", "gemini-2.0-flash")
            log.info(
                "FRAUD_AGENT_LLM_SUCCESS",
                duration_ms=llm_ms,
                verdict=result.get("verdict"),
            )
            audit_log.append(self._audit(
                claim_id, "LLM_CALL",
                f"Fraud forensic analysis complete: verdict={result.get('verdict')}, score={result.get('score')}",
                output_snapshot={
                    "verdict": result.get("verdict"),
                    "score": result.get("score"),
                    "confidence": result.get("confidence"),
                    "risk_factors_count": len(result.get("risk_factors", [])),
                    "evidence_count": len(result.get("key_evidence", [])),
                    "duration_ms": llm_ms,
                },
                duration_ms=llm_ms,
            ))
        else:
            log.error(
                "FRAUD_AGENT_LLM_FAILED",
                reason=llm_response["reason"],
                error=llm_response["error"],
                retries_used=llm_response["retries_used"],
            )
            result = _heuristic_fallback(claim_data)
            result["source"] = "heuristic"
            result["model"] = "fraud_heuristic_v1"
            audit_log.append(self._audit(
                claim_id, "LLM_CALL_FAILED",
                f"LLM unavailable — heuristic fallback: verdict={result.get('verdict')}",
                output_snapshot={"reason": llm_response["reason"], "error": llm_response["error"]},
                duration_ms=llm_ms,
            ))
            log.warning("FALLBACK_TRIGGERED", agent="fraud_agent", reason=llm_response["reason"])

        # ── Parse simplified output ─────────────────────────────────────────────
        score = float(result.get("risk_score", result.get("score", 0.5)))
        reason = result.get("reason", "No reason provided")
        evidence_list = result.get("evidence", [])
        signals = result.get("extracted_signals", [])

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
                    "source": "claim_data",
                    "strength": 0.8,
                    "cited_value": "extracted"
                } for i, e in enumerate(evidence_list)
            ],
            "risk_factors": [],
            "flags": signals,
        }

        audit_log.append(self._audit(
            claim_id, "AGENT_COMPLETE",
            f"Fraud analysis complete: verdict={result.get('verdict')}, score={score}",
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
    """Remove internal fields not useful to the LLM."""
    skip = {"raw_payload", "is_simulation"}
    return {k: v for k, v in claim_data.items() if k not in skip}


def _estimate_benchmark(claim_data: dict) -> str:
    """Produce a rough benchmark hint based on CPT codes and billed amount."""
    billed = claim_data.get("billed_amount", 0)
    cpt_codes = claim_data.get("cpt_codes", [])

    # Rough CPT → typical price benchmarks (simplified, for reasoning context)
    benchmarks = {
        "99213": 150, "99214": 220, "99215": 350,
        "99212": 90,  "99211": 60,  "99201": 80, "99202": 110, "99203": 160,
        "99204": 220, "99205": 290,
        "99281": 180, "99282": 250, "99283": 380, "99284": 550, "99285": 750,
        "97110": 85,  "97010": 25,  "97140": 90,  "97012": 35,
        "72148": 1200, "72141": 1100, "70553": 2500, "74177": 2200,
        "93000": 50,  "93306": 1800, "93010": 35,
        "36415": 20,  "85025": 30,  "80053": 55,
        "27447": 18000, "29827": 9000, "49505": 7500,
    }

    lines = []
    total_benchmark = 0
    for code in cpt_codes:
        bench = benchmarks.get(code)
        if bench:
            lines.append(f"  CPT {code}: typical ~${bench} | billed portion ~${billed / max(len(cpt_codes), 1):.0f}")
            total_benchmark += bench
        else:
            lines.append(f"  CPT {code}: benchmark not in local reference")

    if total_benchmark > 0:
        ratio = billed / total_benchmark
        flag = ""
        if ratio > 3.0:
            flag = "⚠ ANOMALY: billed amount is MORE THAN 3x benchmark total"
        elif ratio > 1.5:
            flag = "⚠ NOTE: billed amount is 50%+ above benchmark"
        elif ratio < 0.5:
            flag = "ℹ NOTE: billed amount is below typical benchmark (possible adjustment or partial bill)"
        lines.append(f"\n  Total benchmark for CPT set: ~${total_benchmark}")
        lines.append(f"  Billed amount: ${billed}")
        lines.append(f"  Ratio billed/benchmark: {ratio:.2f}x {flag}")

    return "\n".join(lines) if lines else "No benchmark data available for these CPT codes."


def _format_similar_cases(cases: list[dict]) -> str:
    if not cases:
        return "No similar historical cases retrieved."
    lines = []
    for i, c in enumerate(cases[:4], 1):
        fraud_score = c.get("fraud_score", "N/A")
        verdict = c.get("verdict", "N/A")
        summary = c.get("summary", "")[:180]
        lines.append(f"  Case {i}: verdict={verdict}, fraud_score={fraud_score} | {summary}")
    return "\n".join(lines)


def _format_fraud_patterns(patterns: list[dict]) -> str:
    if not patterns:
        return "No matching fraud patterns retrieved."
    lines = []
    for p in patterns[:3]:
        lines.append(
            f"  Pattern: {p.get('pattern_name', 'N/A')} [{p.get('severity', 'N/A')}]\n"
            f"  Indicators: {p.get('indicators', 'N/A')}\n"
        )
    return "\n".join(lines)

def _heuristic_fallback(claim_data: dict) -> dict:
    """
    Deterministic fraud heuristic using claim signal extractor.
    Uses extracted signals for meaningful LOW_RISK / REVIEW / HIGH_RISK verdicts.
    """
    from app.core.claim_signal_extractor import extract_signals

    billed = claim_data.get("billed_amount")
    if billed is None:
        return _uncertain_result("billed_amount is missing — cannot assess fraud risk")

    signals = extract_signals(claim_data)
    flags: list[str] = ["LLM_FALLBACK", "HEURISTIC_SAFETY_FALLBACK"] + signals.risk_flags
    evidence: list[dict] = []
    risk_factors: list[dict] = []
    reasoning: list[dict] = []

    # ── Rule 1: amount mismatch → HIGH_RISK (score 0.75) ──────────────────
    if signals.has_amount_mismatch:
        evidence.append({
            "evidence_id": "E1", "type": "SUPPORTING",
            "description": f"Requested (${signals.requested_amount:,.0f}) exceeds billed (${signals.billed_amount:,.0f})",
            "source": "claim_data", "strength": 0.9,
            "cited_value": f"requested={signals.requested_amount}, billed={signals.billed_amount}",
        })
        reasoning.append({
            "step": 1, "observation": f"Amount mismatch detected",
            "evidence": ["amount_discrepancy"], "inference": "HIGH_RISK — possible overbilling",
            "weight": 0.9,
        })
        return _build_result("REJECT", "HIGH_RISK", "amount mismatch — possible overbilling",
                             0.75, 0.75, evidence, risk_factors, reasoning, flags)

    # ── Rule 2: minor diagnosis + high-cost procedure → REVIEW (score 0.65)
    if signals.is_minor_diagnosis_high_cost:
        evidence.append({
            "evidence_id": "E1", "type": "SUPPORTING",
            "description": "Minor diagnosis paired with high-cost procedure or amount",
            "source": "claim_data+text", "strength": 0.75,
            "cited_value": f"billed={signals.billed_amount}",
        })
        reasoning.append({
            "step": 1, "observation": "Minor diagnosis + high-cost surgery/package detected",
            "evidence": ["dx_proc_mismatch"], "inference": "REVIEW — procedure may not match diagnosis severity",
            "weight": 0.8,
        })
        return _build_result("PENDING", "REVIEW", "minor diagnosis + high-cost procedure mismatch",
                             0.70, 0.65, evidence, risk_factors, reasoning, flags)

    # ── Rule 3: high amount > $50K → REVIEW (score 0.60) ──────────────────
    if signals.is_high_amount:
        evidence.append({
            "evidence_id": "E1", "type": "AMBIGUOUS",
            "description": f"High billed amount (${signals.billed_amount:,.0f})",
            "source": "claim_data", "strength": 0.6,
            "cited_value": f"billed={signals.billed_amount}",
        })
        reasoning.append({
            "step": 1, "observation": f"Billed amount ${signals.billed_amount:,.0f} > $50K",
            "evidence": ["high_value_claim"], "inference": "REVIEW — high-value claim warrants scrutiny",
            "weight": 0.7,
        })
        return _build_result("PENDING", "REVIEW", f"high amount ${signals.billed_amount:,.0f}",
                             0.70, 0.60, evidence, risk_factors, reasoning, flags)

    # ── Rule 4: clean low-cost claim → LOW_RISK (score ≤ 0.20) ────────────
    if signals.is_clean_low_cost:
        evidence.append({
            "evidence_id": "E1", "type": "CONTRADICTING",
            "description": f"Clean low-cost claim: ${signals.billed_amount:,.0f}, in-network, auth present",
            "source": "claim_data", "strength": 0.8,
            "cited_value": f"billed={signals.billed_amount}, in_network={signals.is_in_network}",
        })
        reasoning.append({
            "step": 1, "observation": "All fraud indicators clear — amounts consistent, no anomalies",
            "evidence": ["clean_claim"], "inference": "LOW_RISK — no fraud signals detected",
            "weight": 0.85,
        })
        return _build_result("APPROVE", "LOW_RISK", "clean low-cost claim, no fraud indicators",
                             0.75, 0.15, evidence, risk_factors, reasoning, flags)

    # ── Rule 5: moderate/default → LOW_RISK but moderate score ─────────────
    evidence.append({
        "evidence_id": "E1", "type": "AMBIGUOUS",
        "description": f"Amounts consistent (${signals.requested_amount:,.0f} ≤ ${signals.billed_amount:,.0f}), some signals present",
        "source": "claim_data", "strength": 0.6,
        "cited_value": f"risk_flags={signals.risk_flags}",
    })
    reasoning.append({
        "step": 1, "observation": f"No critical fraud indicators, risk flags: {signals.risk_flags or 'none'}",
        "evidence": ["partial_analysis"], "inference": "LOW_RISK with moderate confidence",
        "weight": 0.7,
    })
    # If other signals present (OON, missing auth) bump score slightly
    score = 0.25 if signals.risk_flags else 0.20
    return _build_result("APPROVE", "LOW_RISK", "amounts consistent, no strong fraud indicators",
                         0.65, score, evidence, risk_factors, reasoning, flags)


def _build_result(verdict: str, risk_label: str, conclusion: str,
                  confidence: float, score: float,
                  evidence: list, risk_factors: list, reasoning: list,
                  flags: list) -> dict:
    return {
        "internal_reasoning": _heuristic_reasoning(risk_label, conclusion),
        "verdict": verdict,
        "verdict_probability": {
            "APPROVE": 0.70 if verdict == "APPROVE" else 0.15,
            "REJECT": 0.65 if verdict == "REJECT" else 0.10,
            "PENDING": 0.50 if verdict == "PENDING" else 0.20,
        },
        "confidence": confidence, "score": score,
        "key_evidence": evidence, "risk_factors": risk_factors,
        "reasoning_chain": reasoning,
        "dissenting_view": {
            "alternative_verdict": "PENDING" if verdict != "PENDING" else "APPROVE",
            "probability": 0.20,
            "strongest_argument_for_alternative": "Heuristic cannot detect code-level patterns",
            "what_would_change_my_verdict": "LLM analysis of claim details",
        },
        "flags": flags,
        "override_ready": True,
    }


def _heuristic_reasoning(verdict: str, conclusion: str) -> dict:
    return {
        "phase_1_data_extraction": "LLM unavailable — heuristic safety fallback analyzing claim signals",
        "phase_2_hypothesis": f"Rule-based assessment: {verdict}",
        "phase_3_evidence_for": [f"Signal matched: {conclusion}"],
        "phase_4_evidence_against": ["Heuristic cannot assess code-level fraud patterns"],
        "phase_5_weighing": "Rule-based signal analysis — limited scope",
        "phase_6_conclusion": f"Heuristic: {verdict} — {conclusion}",
    }


def _uncertain_result(reason: str) -> dict:
    return {
        "internal_reasoning": _heuristic_reasoning("UNCERTAIN", reason),
        "verdict": "UNCERTAIN",
        "verdict_probability": {"APPROVE": 0.33, "REJECT": 0.33, "PENDING": 0.34},
        "confidence": 0.30, "score": 0.50,
        "key_evidence": [], "risk_factors": [],
        "reasoning_chain": [{"step": 1, "observation": reason, "evidence": ["missing_data"],
                             "inference": "Cannot complete fraud analysis", "weight": 1.0}],
        "dissenting_view": {"alternative_verdict": "PENDING", "probability": 0.34,
                            "strongest_argument_for_alternative": "Missing data ≠ fraud",
                            "what_would_change_my_verdict": "Complete claim data"},
        "flags": ["LLM_FALLBACK", "HEURISTIC_SAFETY_FALLBACK", "MISSING_DATA"],
        "override_ready": True,
    }
