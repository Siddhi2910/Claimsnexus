#!/usr/bin/env python
import asyncio
import json

async def test_policy_agent():
    from app.agents.policy_agent import PolicyComplianceAgent, _heuristic_fallback
    
    # Test case: Bad claim with out-of-network and no prior auth
    bad_claim = {
        "id": "CLM-12345",
        "policy_number": "POL-001",
        "plan_id": "HMO",
        "billed_amount": 95000,
        "in_network": False,
        "prior_auth_number": None,
        "diagnosis_summary": "Patient presented with minor fever, referred to out-of-network specialist, no prior authorization obtained",
        "cpt_codes": ["99285", "93000"],
        "icd_codes": ["R50.9"],
    }
    
    # Test heuristic fallback
    result = _heuristic_fallback(bad_claim)
    print("Bad Claim (Out-of-network + No Auth) Test:")
    print(f"  Verdict: {result.get('verdict')}")
    print(f"  Risk Score: {result.get('score')}")
    print(f"  Reason: {result.get('reason')}")
    print(f"  Source: {result.get('source')}")
    print()
    
    # Test case: Good claim with in-network and prior auth
    good_claim = {
        "id": "CLM-67890",
        "policy_number": "POL-002",
        "plan_id": "PPO",
        "billed_amount": 4500,
        "in_network": True,
        "prior_auth_number": "AUTH-12345",
        "diagnosis_summary": "Patient with fever and blood test, in-network provider with prior authorization",
        "cpt_codes": ["99213", "85025"],
        "icd_codes": ["R50.9", "R03"],
    }
    
    result = _heuristic_fallback(good_claim)
    print("Good Claim (In-network + Auth) Test:")
    print(f"  Verdict: {result.get('verdict')}")
    print(f"  Risk Score: {result.get('score')}")
    print(f"  Reason: {result.get('reason')}")
    print(f"  Source: {result.get('source')}")

if __name__ == "__main__":
    asyncio.run(test_policy_agent())
