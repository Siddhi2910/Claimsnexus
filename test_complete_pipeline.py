#!/usr/bin/env python
from main import app
from fastapi.testclient import TestClient

client = TestClient(app)

# Test with a bad claim that should be PENDING/REVIEW
bad_claim = {
    'policy_number': 'POL-001',
    'plan_id': 'HMO',
    'claim_type': 'surgery',
    'billed_amount': 95000,
    'requested_amount': 95000,
    'in_network': False,
    'prior_auth_number': None,
    'diagnosis_summary': 'Minor fever, referred to out-of-network specialist, no prior authorization',
    'icd_codes': ['R50.9'],
    'cpt_codes': ['99285'],
    'provider_name': 'Out of Network Surgical Center',
    'diagnosis_description': 'Fever - out of network',
}

response = client.post('/api/v1/claims/submit', json=bad_claim)
result = response.json()

print('BAD CLAIM (high-value, out-of-network, no auth):')
print(f"  Final Verdict: {result.get('decision', {}).get('verdict')}")
print(f"  Fraud: {result.get('agents', {}).get('fraud', {}).get('verdict')} (risk {result.get('agents', {}).get('fraud', {}).get('score')})")
print(f"  Medical: {result.get('agents', {}).get('medical', {}).get('verdict')} (risk {result.get('agents', {}).get('medical', {}).get('score')})")
print(f"  Policy: {result.get('agents', {}).get('policy', {}).get('verdict')} (risk {result.get('agents', {}).get('policy', {}).get('score')})")
print(f"  Arbiter: {result.get('agents', {}).get('arbiter', {}).get('verdict')}")
print()

# Test with a good claim that should APPROVE
good_claim = {
    'policy_number': 'POL-002',
    'plan_id': 'PPO',
    'claim_type': 'office',
    'billed_amount': 4500,
    'requested_amount': 4500,
    'in_network': True,
    'prior_auth_number': 'AUTH-12345',
    'diagnosis_summary': 'Fever and blood test, in-network provider with prior authorization',
    'icd_codes': ['R50.9'],
    'cpt_codes': ['99213'],
    'provider_name': 'Community Health Center',
    'diagnosis_description': 'Fever with valid tests',
}

response = client.post('/api/v1/claims/submit', json=good_claim)
result = response.json()

print('GOOD CLAIM (low-value, in-network, with auth):')
print(f"  Final Verdict: {result.get('decision', {}).get('verdict')}")
print(f"  Fraud: {result.get('agents', {}).get('fraud', {}).get('verdict')} (risk {result.get('agents', {}).get('fraud', {}).get('score')})")
print(f"  Medical: {result.get('agents', {}).get('medical', {}).get('verdict')} (risk {result.get('agents', {}).get('medical', {}).get('score')})")
print(f"  Policy: {result.get('agents', {}).get('policy', {}).get('verdict')} (risk {result.get('agents', {}).get('policy', {}).get('score')})")
print(f"  Arbiter: {result.get('agents', {}).get('arbiter', {}).get('verdict')}")
