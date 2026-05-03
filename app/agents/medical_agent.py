import json
import structlog
from app.agents.base_agent import BaseAgent
from app.schemas.agent import AgentReport, AgentVerdict, AuditEntry
from app.services.llm_safety_wrapper import safe_llm_call

log = structlog.get_logger()

# ── System Prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Medical Validation Agent — a board-certified clinical decision expert.

Review the claim and return ONLY valid JSON matching this exact structure:
{
  "verdict": "APPROVE" | "REJECT" | "PENDING",
  "confidence": <float 0-1>,
  "risk_score": <float 0-1, 1 is high medical risk>,
  "reason": "<one clear sentence explaining the clinical verdict>",
  "extracted_signals": ["<clinical signal 1>", "<signal 2>"],
  "evidence": ["<specific evidence 1>", "<evidence 2>"],
  "source": "gemini" | "heuristic",
  "model": "<active model name>"
}

Rules:
- Verify diagnosis matches the procedure complexity.
- Reason must be specific: cite actual clinical mismatch (e.g., "Minor fever does not justify high-cost surgical package").
- Reject/Pending if high-complexity surgery is billed for minor outpatient diagnosis.
- Provide strict JSON with no markdown formatting around it.
"""


class MedicalValidationAgent(BaseAgent):
    agent_id = "medical_agent_v2"

    async def run(self, claim_data: dict, context: dict) -> AgentReport:
        claim_id = claim_data["id"]
        audit_log = []
        t0 = self._start_timer()

        audit_log.append(self._audit(
            claim_id, "AGENT_START",
            "Medical validation agent v2 starting clinical review",
            input_snapshot={
                "icd_codes": claim_data.get("icd_codes"),
                "cpt_codes": claim_data.get("cpt_codes"),
                "claim_type": claim_data.get("claim_type"),
            },
        ))

        # ── Build clinical context ────────────────────────────────────────────
        similar_cases = context.get("similar_cases", [])
        precedent_context = _format_precedents(similar_cases)
        clinical_hint = _build_clinical_hint(claim_data)

        user_prompt = f"""
Perform a complete clinical medical necessity review of this healthcare claim.
Work through all 6 reasoning phases before forming your verdict.

═══════════════════════════════════════
CLAIM UNDER CLINICAL REVIEW
═══════════════════════════════════════
{json.dumps(_sanitize_claim(claim_data), indent=2, default=str)}

═══════════════════════════════════════
CLINICAL REFERENCE NOTES
═══════════════════════════════════════
{clinical_hint}

═══════════════════════════════════════
SIMILAR HISTORICAL CLAIMS
═══════════════════════════════════════
{precedent_context}

═══════════════════════════════════════
CLINICAL REVIEWER CHECKLIST
═══════════════════════════════════════
Before forming your verdict, confirm you have assessed:
[ ] ICD-10 code validity and specificity
[ ] CPT code appropriateness for the diagnosis
[ ] Medical necessity of each procedure
[ ] Diagnosis-procedure alignment
[ ] E&M level justification (if E&M codes present)
[ ] Provider specialty vs. procedures billed
[ ] Any mutually exclusive or redundant procedures
[ ] Treatment matches diagnosis severity

Output your full clinical analysis as JSON.
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
                from app.services.unified_llm import _env
                result["model"] = _env("GEMINI_MODEL", "gemini-2.0-flash")
            log.info(
                "MEDICAL_AGENT_LLM_SUCCESS",
                duration_ms=llm_ms,
                verdict=result.get("verdict"),
            )
            audit_log.append(self._audit(
                claim_id, "LLM_CALL",
                f"Medical review complete: verdict={result.get('verdict')}, score={result.get('score')}",
                output_snapshot={
                    "verdict": result.get("verdict"),
                    "score": result.get("score"),
                    "clinical_scores": result.get("clinical_scores"),
                    "duration_ms": llm_ms,
                },
                duration_ms=llm_ms,
            ))
        else:
            log.error(
                "MEDICAL_AGENT_LLM_FAILED",
                reason=llm_response["reason"],
                error=llm_response["error"],
            )
            result = _heuristic_fallback(claim_data)
            result["source"] = "heuristic"
            result["model"] = "medical_heuristic_v1"
            audit_log.append(self._audit(
                claim_id, "LLM_CALL_FAILED",
                f"LLM unavailable — heuristic fallback: verdict={result.get('verdict')}",
                output_snapshot={"reason": llm_response["reason"]},
                duration_ms=llm_ms,
            ))
            log.warning("FALLBACK_TRIGGERED", agent="medical_agent", reason=llm_response["reason"])

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
            f"Medical validation complete: verdict={result.get('verdict')}, score={score}",
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


def _build_clinical_hint(claim_data: dict) -> str:
    """Generate clinical reference notes to help the LLM reason about codes."""
    icd = claim_data.get("icd_codes", [])
    cpt = claim_data.get("cpt_codes", [])
    lines = []

    # ICD-10 format hint
    icd_issues = [c for c in icd if not (len(c) >= 3 and c[0].isalpha() and c[1:3].isdigit())]
    if icd_issues:
        lines.append(f"⚠ ICD-10 FORMAT WARNING: Codes may be malformed: {icd_issues}")

    # E&M level detection
    em_codes = [c for c in cpt if c.startswith("992") or c.startswith("993")]
    if em_codes:
        em_levels = {
            "99211": "minimal", "99212": "straightforward", "99213": "low",
            "99214": "moderate", "99215": "high",
            "99231": "low (subsequent)", "99232": "moderate (subsequent)", "99233": "high (subsequent)",
        }
        for code in em_codes:
            level = em_levels.get(code, "unknown")
            lines.append(f"  E&M {code} requires '{level}' medical decision-making complexity")

    # Imaging codes
    imaging = [c for c in cpt if c.startswith("7")]
    if imaging:
        lines.append(f"  Radiology/imaging codes present: {imaging} — clinical indication should match diagnosis")

    # Surgery codes (10000-69999 range)
    surgery = [c for c in cpt if c.isdigit() and 10000 <= int(c) <= 69999]
    if surgery:
        lines.append(f"  Surgical procedure codes: {surgery} — verify pre-authorization and medical necessity criteria")

    # Lab codes
    lab = [c for c in cpt if c.isdigit() and 80000 <= int(c) <= 89999]
    if lab:
        lines.append(f"  Laboratory codes: {lab} — verify clinical indication for each test ordered")

    lines.append(f"\n  Claim type: {claim_data.get('claim_type', 'MEDICAL')}")
    lines.append(f"  In-network: {claim_data.get('in_network', 'Unknown')}")
    lines.append(f"  Prior auth: {'Present' if claim_data.get('prior_auth_number') else 'Not provided'}")

    return "\n".join(lines) if lines else "No specific clinical flags generated from code analysis."


def _format_precedents(cases: list[dict]) -> str:
    if not cases:
        return "No similar historical cases retrieved."
    lines = []
    for i, c in enumerate(cases[:3], 1):
        lines.append(
            f"  Case {i}: verdict={c.get('verdict', 'N/A')} | "
            f"ICD={c.get('icd_codes', [])} | CPT={c.get('cpt_codes', [])} | "
            f"Summary: {c.get('summary', '')[:150]}"
        )
    return "\n".join(lines)


def _heuristic_fallback(claim_data: dict) -> dict:
    """
    Deterministic medical heuristic using claim signal extractor.
    Checks diagnosis/procedure alignment and clinical appropriateness.
    """
    from app.core.claim_signal_extractor import extract_signals

    signals = extract_signals(claim_data)
    flags: list[str] = ["LLM_FALLBACK", "HEURISTIC_SAFETY_FALLBACK"] + signals.risk_flags
    evidence: list[dict] = []
    reasoning: list[dict] = []
    base_scores = {"diagnosis_procedure_alignment": 0.5, "severity_intensity_match": 0.5,
                   "coding_accuracy": 0.5, "provider_appropriateness": 0.5}

    # If all critical fields missing — UNCERTAIN
    if signals.billed_amount == 0 and signals.is_missing_diagnosis and signals.is_missing_procedure:
        return _med_uncertain("diagnosis, procedure, and amount all missing")

    # ── Rule 1: minor diagnosis + high-cost procedure → REVIEW (score 0.60)
    if signals.is_minor_diagnosis_high_cost:
        evidence.append({
            "evidence_id": "E1", "type": "CONTRADICTING",
            "description": "Minor diagnosis paired with high-cost surgical procedure or package",
            "source": "claim_data+text", "strength": 0.8,
            "cited_value": f"billed={signals.billed_amount}",
        })
        reasoning.append({
            "step": 1, "observation": "Minor diagnosis (e.g. fever) with high-cost surgery/package",
            "evidence": ["dx_proc_severity_mismatch"],
            "inference": "REVIEW — procedure severity does not match diagnosis",
            "weight": 0.85,
        })
        scores = {**base_scores, "diagnosis_procedure_alignment": 0.2, "severity_intensity_match": 0.15}
        return _build_med_result("PENDING", "REVIEW", "minor diagnosis + high-cost procedure mismatch",
                                 0.70, 0.60, scores, evidence, reasoning, flags)

    # ── Rule 2: missing diagnosis OR procedure → REVIEW (score 0.45)
    if signals.is_missing_diagnosis or signals.is_missing_procedure:
        missing = []
        if signals.is_missing_diagnosis:
            missing.append("diagnosis/ICD")
        if signals.is_missing_procedure:
            missing.append("procedure/CPT")
        missing_str = " and ".join(missing)
        evidence.append({
            "evidence_id": "E1", "type": "CONTRADICTING",
            "description": f"Missing {missing_str} — cannot confirm medical necessity",
            "source": "claim_data", "strength": 0.7,
            "cited_value": f"dx={not signals.is_missing_diagnosis}, proc={not signals.is_missing_procedure}",
        })
        reasoning.append({
            "step": 1, "observation": f"Missing {missing_str}",
            "evidence": ["incomplete_clinical_data"],
            "inference": "REVIEW — incomplete clinical data",
            "weight": 0.8,
        })
        scores = {**base_scores, "diagnosis_procedure_alignment": 0.2, "coding_accuracy": 0.2}
        return _build_med_result("PENDING", "REVIEW", f"missing {missing_str}",
                                 0.65, 0.45, scores, evidence, reasoning, flags)

    # ── Rule 3: diagnosis + procedure + reasonable amount → VALID (score ≤0.15)
    if not signals.is_missing_diagnosis and not signals.is_missing_procedure:
        if signals.billed_amount <= 100000:
            evidence.append({
                "evidence_id": "E1", "type": "SUPPORTING",
                "description": f"Diagnosis + procedure present, amount ${signals.billed_amount:,.0f} reasonable",
                "source": "claim_data", "strength": 0.75,
                "cited_value": f"billed={signals.billed_amount}",
            })
            reasoning.append({
                "step": 1, "observation": "Clinical data present and amount reasonable",
                "evidence": ["clinical_completeness"],
                "inference": "VALID — basic medical necessity met",
                "weight": 0.8,
            })
            scores = {**base_scores, "diagnosis_procedure_alignment": 0.75,
                      "coding_accuracy": 0.7, "severity_intensity_match": 0.65}
            return _build_med_result("APPROVE", "VALID", "diagnosis + procedure present, reasonable amount",
                                     0.70, 0.15, scores, evidence, reasoning, flags)

    # ── Rule 4: extreme amount with codes → REVIEW (score 0.55)
    evidence.append({
        "evidence_id": "E1", "type": "AMBIGUOUS",
        "description": f"High amount ${signals.billed_amount:,.0f} with clinical codes present",
        "source": "claim_data", "strength": 0.6,
        "cited_value": f"billed={signals.billed_amount}",
    })
    reasoning.append({
        "step": 1, "observation": f"Amount ${signals.billed_amount:,.0f} exceeds review threshold",
        "evidence": ["high_value_medical"],
        "inference": "REVIEW — medical necessity review needed for high-value claim",
        "weight": 0.7,
    })
    scores = {**base_scores, "diagnosis_procedure_alignment": 0.6, "severity_intensity_match": 0.5}
    return _build_med_result("PENDING", "REVIEW", "high-value claim needs necessity review",
                             0.65, 0.55, scores, evidence, reasoning, flags)


def _build_med_result(verdict: str, label: str, conclusion: str,
                      confidence: float, score: float, clinical_scores: dict,
                      evidence: list, reasoning: list, flags: list) -> dict:
    return {
        "internal_reasoning": _med_reasoning(label, conclusion),
        "verdict": verdict,
        "verdict_probability": {"APPROVE": 0.70 if verdict == "APPROVE" else 0.20,
                                "REJECT": 0.10, "PENDING": 0.50 if verdict == "PENDING" else 0.20},
        "confidence": confidence, "score": score,
        "clinical_scores": clinical_scores,
        "key_evidence": evidence, "risk_factors": [],
        "reasoning_chain": reasoning,
        "dissenting_view": {"alternative_verdict": "PENDING" if verdict != "PENDING" else "APPROVE",
                            "probability": 0.20,
                            "strongest_argument_for_alternative": "Heuristic cannot verify clinical alignment",
                            "what_would_change_my_verdict": "LLM clinical analysis"},
        "flags": flags, "override_ready": True,
    }


def _med_reasoning(verdict: str, conclusion: str) -> dict:
    return {
        "phase_1_data_extraction": "LLM unavailable — heuristic analyzing clinical signals",
        "phase_2_hypothesis": f"Rule-based clinical assessment: {verdict}",
        "phase_3_evidence_for": [f"Signal matched: {conclusion}"],
        "phase_4_evidence_against": ["Heuristic cannot verify ICD-CPT clinical alignment"],
        "phase_5_weighing": "Rule-based field-presence + text analysis",
        "phase_6_conclusion": f"Heuristic: {verdict} — {conclusion}",
    }


def _med_uncertain(reason: str) -> dict:
    return {
        "internal_reasoning": _med_reasoning("UNCERTAIN", reason),
        "verdict": "UNCERTAIN",
        "verdict_probability": {"APPROVE": 0.33, "REJECT": 0.33, "PENDING": 0.34},
        "confidence": 0.30, "score": 0.50,
        "clinical_scores": {"diagnosis_procedure_alignment": 0.0, "severity_intensity_match": 0.0,
                            "coding_accuracy": 0.0, "provider_appropriateness": 0.0},
        "key_evidence": [], "risk_factors": [],
        "reasoning_chain": [{"step": 1, "observation": reason, "evidence": ["missing_data"],
                             "inference": "Cannot complete medical validation", "weight": 1.0}],
        "dissenting_view": {"alternative_verdict": "PENDING", "probability": 0.34,
                            "strongest_argument_for_alternative": "Missing data ≠ invalid",
                            "what_would_change_my_verdict": "Complete clinical data"},
        "flags": ["LLM_FALLBACK", "HEURISTIC_SAFETY_FALLBACK", "MISSING_DATA"],
        "override_ready": True,
    }


