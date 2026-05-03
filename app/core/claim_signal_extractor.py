"""
ClaimsNexus Claim Signal Extractor
Parses structured claim fields + free-text diagnosis_summary to extract
risk signals used by heuristic fallback agents and the arbiter.
"""
import re
from dataclasses import dataclass, field


@dataclass
class ClaimSignals:
    """Extracted risk signals from a claim."""
    is_out_of_network: bool = False
    is_missing_prior_auth: bool = False
    has_amount_mismatch: bool = False
    is_high_amount: bool = False
    is_missing_diagnosis: bool = False
    is_missing_procedure: bool = False
    is_minor_diagnosis_high_cost: bool = False
    is_clean_low_cost: bool = False
    has_prior_auth: bool = False
    is_in_network: bool = False
    billed_amount: float = 0.0
    requested_amount: float = 0.0
    risk_flags: list[str] = field(default_factory=list)
    signal_summary: str = ""

    @property
    def max_agent_risk(self) -> float:
        """Quick composite risk estimate from signals."""
        risk = 0.10
        if self.has_amount_mismatch:
            risk = max(risk, 0.70)
        if self.is_minor_diagnosis_high_cost:
            risk = max(risk, 0.60)
        if self.is_out_of_network and self.is_missing_prior_auth:
            risk = max(risk, 0.75)
        elif self.is_out_of_network:
            risk = max(risk, 0.50)
        elif self.is_missing_prior_auth and self.billed_amount > 10000:
            risk = max(risk, 0.55)
        if self.is_high_amount:
            risk = max(risk, 0.45)
        if self.is_missing_diagnosis or self.is_missing_procedure:
            risk = max(risk, 0.45)
        return risk


# ── Minor diagnosis keywords ─────────────────────────────────────────────────
_MINOR_DX = re.compile(
    r"\b(fever|cold|cough|headache|minor|routine|checkup|check-up|sore\s*throat|"
    r"common\s*cold|mild|flu|influenza|allergy|allergies|rash|itch|fatigue|"
    r"upper\s*respiratory|runny\s*nose|nasal\s*congestion)\b",
    re.IGNORECASE,
)

# ── High-cost procedure keywords ─────────────────────────────────────────────
_HIGH_COST_PROC = re.compile(
    r"\b(surgery|surgical|arthroplasty|transplant|bypass|stent|implant|"
    r"reconstruction|resection|fusion|craniotomy|package|surgical\s*package|"
    r"hip\s*replacement|knee\s*replacement|cardiac|open\s*heart|spinal)\b",
    re.IGNORECASE,
)

# ── Out-of-network text patterns ─────────────────────────────────────────────
_OON_PATTERNS = re.compile(
    r"\b(out[\s-]*of[\s-]*network|non[\s-]*network|oon\b|external\s*provider|"
    r"out\s*of\s*plan)\b",
    re.IGNORECASE,
)

# ── Missing auth text patterns ────────────────────────────────────────────────
_NO_AUTH_PATTERNS = re.compile(
    r"\b(no\s*prior\s*auth|missing\s*prior\s*auth|without\s*auth|no\s*auth|"
    r"pre[\s-]*auth\s*missing|no\s*authorization|authorization\s*missing|"
    r"prior\s*auth\s*not\s*obtained)\b",
    re.IGNORECASE,
)

# ── Amount mismatch text patterns ─────────────────────────────────────────────
_AMOUNT_MISMATCH = re.compile(
    r"\b(amount\s*mismatch|exceeds\s*billed|over[\s-]*billed|over[\s-]*charged|"
    r"requested\s*amount\s*exceeds|amount\s*discrepancy)\b",
    re.IGNORECASE,
)


def extract_signals(claim_data: dict) -> ClaimSignals:
    """
    Parse claim fields and free-text fields to produce risk signals.
    Works with both structured fields and diagnosis_summary text.
    """
    signals = ClaimSignals()

    # ── Numeric fields ────────────────────────────────────────────────────
    billed = claim_data.get("billed_amount")
    requested = claim_data.get("requested_amount")
    signals.billed_amount = float(billed or 0) if billed is not None else 0.0
    signals.requested_amount = float(requested or 0) if requested is not None else signals.billed_amount

    # ── Structured boolean fields ─────────────────────────────────────────
    in_network = claim_data.get("in_network")
    prior_auth = claim_data.get("prior_auth_number")

    if in_network is not None:
        signals.is_in_network = bool(in_network)
        signals.is_out_of_network = not bool(in_network)
    
    signals.has_prior_auth = bool(prior_auth)
    signals.is_missing_prior_auth = not bool(prior_auth)

    # ── Text fields to scan ───────────────────────────────────────────────
    diagnosis = (claim_data.get("diagnosis_description") or "").strip()
    procedure = (claim_data.get("procedure_description") or "").strip()
    summary = (claim_data.get("diagnosis_summary") or "").strip()
    all_text = f"{diagnosis} {procedure} {summary}".strip()

    icd_codes = claim_data.get("icd_codes") or []
    cpt_codes = claim_data.get("cpt_codes") or []

    # ── Missing clinical data ─────────────────────────────────────────────
    signals.is_missing_diagnosis = not diagnosis and len(icd_codes) == 0
    signals.is_missing_procedure = not procedure and len(cpt_codes) == 0

    # ── Text-based signal extraction ──────────────────────────────────────
    # Override structured fields if text mentions them
    if _OON_PATTERNS.search(all_text):
        signals.is_out_of_network = True
        signals.is_in_network = False

    if _NO_AUTH_PATTERNS.search(all_text):
        signals.is_missing_prior_auth = True
        signals.has_prior_auth = False

    if _AMOUNT_MISMATCH.search(all_text):
        signals.has_amount_mismatch = True

    # ── Amount-based signals ──────────────────────────────────────────────
    if signals.requested_amount > signals.billed_amount and signals.billed_amount > 0:
        signals.has_amount_mismatch = True

    if signals.billed_amount > 50000:
        signals.is_high_amount = True

    # ── Minor diagnosis + high-cost procedure detection ───────────────────
    has_minor_dx = bool(_MINOR_DX.search(all_text))
    has_high_cost_proc = bool(_HIGH_COST_PROC.search(all_text))
    if has_minor_dx and (has_high_cost_proc or signals.billed_amount > 40000):
        signals.is_minor_diagnosis_high_cost = True

    # ── Clean low-cost claim ──────────────────────────────────────────────
    if (
        signals.is_in_network
        and signals.has_prior_auth
        and not signals.has_amount_mismatch
        and not signals.is_high_amount
        and not signals.is_minor_diagnosis_high_cost
        and not signals.is_missing_diagnosis
        and not signals.is_missing_procedure
        and signals.billed_amount <= 25000
        and signals.billed_amount > 0
    ):
        signals.is_clean_low_cost = True

    # ── Build risk flags list ─────────────────────────────────────────────
    if signals.is_out_of_network:
        signals.risk_flags.append("OUT_OF_NETWORK")
    if signals.is_missing_prior_auth:
        signals.risk_flags.append("MISSING_PRIOR_AUTH")
    if signals.has_amount_mismatch:
        signals.risk_flags.append("AMOUNT_MISMATCH")
    if signals.is_high_amount:
        signals.risk_flags.append("HIGH_AMOUNT")
    if signals.is_missing_diagnosis:
        signals.risk_flags.append("MISSING_DIAGNOSIS")
    if signals.is_missing_procedure:
        signals.risk_flags.append("MISSING_PROCEDURE")
    if signals.is_minor_diagnosis_high_cost:
        signals.risk_flags.append("MINOR_DX_HIGH_COST_PROC")
    if signals.is_clean_low_cost:
        signals.risk_flags.append("CLEAN_LOW_COST")

    # ── Summary ───────────────────────────────────────────────────────────
    if signals.risk_flags:
        signals.signal_summary = "Extracted signals: " + ", ".join(signals.risk_flags)
    else:
        signals.signal_summary = "No risk signals detected"

    return signals
