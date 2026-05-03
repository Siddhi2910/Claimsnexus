"""One-shot demo: run adjudication pipeline (simulation) and print summary."""
from __future__ import annotations

import asyncio
import sys
import uuid

if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
from datetime import datetime
from pathlib import Path

# Allow `python scripts/quick_demo.py` from repo root (same folder as `main.py`)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.workflow.pipeline import AdjudicationPipeline


async def main() -> None:
    p = AdjudicationPipeline()
    claim = {
        "id": str(uuid.uuid4()),
        "claim_number": "DEMO-001",
        "claim_type": "MEDICAL",
        "claimant_id": "c_demo",
        "claimant_name": "Demo Patient",
        "policy_number": "POL-DEMO",
        "plan_id": "PLAN-A",
        "provider_id": "prv_demo",
        "provider_name": "Demo Clinic",
        "service_date": datetime.utcnow().isoformat(),
        "icd_codes": ["J06.9"],
        "cpt_codes": ["99213"],
        "diagnosis_description": "Acute upper respiratory infection",
        "procedure_description": "Office visit established patient",
        "billed_amount": 200.0,
        "requested_amount": 200.0,
        "in_network": True,
        "prior_auth_number": None,
        "is_simulation": True,
    }
    print("--- ClaimsNexus: full pipeline demo (simulation) ---")
    r = await p.run(claim)
    fv = r.get("verdict")
    fv_s = getattr(fv, "value", fv)
    print("FINAL_VERDICT:", fv_s)
    print("CONFIDENCE:", round(float(r.get("confidence", 0)), 4))
    print("COMPOSITE_RISK:", round(float(r.get("composite_risk_score", 0)), 4))
    print("HUMAN_REQUIRED:", r.get("human_required"))
    rd = r.get("routing_decision")
    print("ROUTING:", getattr(rd, "value", rd))
    fa = r.get("fraud_agent_report")
    if isinstance(fa, dict):
        print("FRAUD_AGENT_VERDICT:", fa.get("verdict"))
        print("FRAUD_FLAGS:", fa.get("flags", [])[:8])


if __name__ == "__main__":
    asyncio.run(main())
