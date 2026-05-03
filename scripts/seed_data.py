"""
Seed script — inserts sample claims and fraud patterns into the system.
Run: python scripts/seed_data.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import AsyncSessionLocal, init_db
from app.models.claim import Claim, ClaimStatus, ClaimType
from app.services.vector_store import vector_store
from app.utils.helpers import generate_claim_number
from datetime import datetime, timedelta
import uuid
import random


SAMPLE_CLAIMS = [
    {
        "claim_type": ClaimType.MEDICAL,
        "claimant_id": "PAT-001",
        "claimant_name": "Alice Johnson",
        "policy_number": "POL-2024-001",
        "plan_id": "PLAN-PPO-GOLD",
        "provider_id": "PRV-001",
        "provider_name": "City Medical Center",
        "provider_npi": "1234567890",
        "service_date": datetime.utcnow() - timedelta(days=10),
        "icd_codes": ["J18.9", "J22"],
        "cpt_codes": ["99213", "94760"],
        "diagnosis_description": "Community acquired pneumonia, unspecified",
        "procedure_description": "Office visit with pulse oximetry",
        "billed_amount": 850.00,
        "requested_amount": 850.00,
        "in_network": True,
        "prior_auth_number": "AUTH-2024-1234",
    },
    {
        "claim_type": ClaimType.MEDICAL,
        "claimant_id": "PAT-002",
        "claimant_name": "Robert Chen",
        "policy_number": "POL-2024-002",
        "plan_id": "PLAN-HMO-SILVER",
        "provider_id": "PRV-002",
        "provider_name": "Riverside Clinic",
        "service_date": datetime.utcnow() - timedelta(days=5),
        "icd_codes": ["M54.5"],
        "cpt_codes": ["99215", "97110", "97010", "97140", "72148", "72141"],
        "diagnosis_description": "Low back pain",
        "procedure_description": "Complex office visit + excessive physical therapy + MRI lumbar + MRI cervical",
        "billed_amount": 8500.00,
        "requested_amount": 8500.00,
        "in_network": False,
    },
    {
        "claim_type": ClaimType.PHARMACY,
        "claimant_id": "PAT-003",
        "claimant_name": "Maria Santos",
        "policy_number": "POL-2024-003",
        "plan_id": "PLAN-PPO-SILVER",
        "provider_id": "PRV-003",
        "provider_name": "HealthFirst Pharmacy",
        "service_date": datetime.utcnow() - timedelta(days=2),
        "icd_codes": ["E11.9"],
        "cpt_codes": ["99213"],
        "diagnosis_description": "Type 2 diabetes mellitus without complications",
        "procedure_description": "Metformin 500mg, 90-day supply",
        "billed_amount": 45.00,
        "requested_amount": 45.00,
        "in_network": True,
        "prior_auth_number": "AUTH-2024-5678",
    },
]

SAMPLE_FRAUD_PATTERNS = [
    {
        "pattern_id": "FP-001",
        "pattern_name": "Upcoding — Evaluation and Management",
        "description": "Provider consistently bills Level 5 E&M codes (99215) for simple visits that warrant Level 3 (99213)",
        "indicators": "99215 billing rate > 70%, average visit duration < 15 min, all diagnoses simple",
        "severity": "HIGH",
    },
    {
        "pattern_id": "FP-002",
        "pattern_name": "Unbundling — Surgical Procedures",
        "description": "Billing component codes separately when a comprehensive code should be used",
        "indicators": "Multiple CPT codes billed on same date that have a bundling edit, no modifier -59",
        "severity": "HIGH",
    },
    {
        "pattern_id": "FP-003",
        "pattern_name": "Phantom Billing",
        "description": "Services billed but never rendered — provider has no corresponding medical records",
        "indicators": "High claim volume, no corresponding appointment records, patient denies service",
        "severity": "CRITICAL",
    },
    {
        "pattern_id": "FP-004",
        "pattern_name": "Duplicate Claims",
        "description": "Same service billed multiple times under slightly different claim numbers or dates",
        "indicators": "Same patient + provider + CPT within 14 days, amount identical or close",
        "severity": "MEDIUM",
    },
]


async def seed() -> None:
    print("Initializing database...")
    await init_db()

    async with AsyncSessionLocal() as db:
        print("Seeding sample claims...")
        for claim_data in SAMPLE_CLAIMS:
            claim = Claim(
                id=str(uuid.uuid4()),
                claim_number=generate_claim_number(),
                status=ClaimStatus.RECEIVED,
                **claim_data,
                raw_payload={},
            )
            db.add(claim)
        await db.commit()
        print(f"  ✓ {len(SAMPLE_CLAIMS)} claims seeded")

    # Seed fraud patterns into vector DB
    print("Seeding fraud patterns into vector DB...")
    try:
        for pattern in SAMPLE_FRAUD_PATTERNS:
            vector_store._ensure_schema()
            client = vector_store._get_client()
            import weaviate.classes as wvc
            collection = client.collections.get("FraudPattern")
            text = f"{pattern['pattern_name']} {pattern['description']} {pattern['indicators']}"
            collection.data.insert(
                properties=pattern,
                vector=vector_store._fake_embed(text),
            )
        print(f"  ✓ {len(SAMPLE_FRAUD_PATTERNS)} fraud patterns seeded")
    except Exception as e:
        print(f"  ⚠ Vector DB unavailable (run Weaviate first): {e}")

    print("\nSeed complete. Start the API with:")
    print("  uvicorn main:app --reload")
    print("  or: docker-compose up")


if __name__ == "__main__":
    asyncio.run(seed())
