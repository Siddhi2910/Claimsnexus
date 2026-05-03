#!/usr/bin/env python
from main import app
from fastapi.testclient import TestClient
import json

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

print('BAD CLAIM Response:')
print(json.dumps(result, indent=2, default=str)[:1000])
