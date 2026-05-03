import urllib.request
import urllib.error
import json
import uuid
import time
from datetime import datetime

url = "http://localhost:8000/api/v1/claims/submit"
headers = {"Content-Type": "application/json"}

claim_data = {
    "claim_type": "MEDICAL",
    "claimant_id": "P12345",
    "claimant_name": "Test User",
    "policy_number": "POL-123",
    "plan_id": "PLAN-A",
    "provider_id": "HOSP001",
    "provider_name": "AIIMS",
    "provider_npi": "NPI123",
    "facility_name": "AIIMS",
    "service_date": datetime.utcnow().isoformat() + "Z",
    "icd_codes": ["A01"],
    "cpt_codes": ["12345"],
    "diagnosis_description": "Fever",
    "procedure_description": "Test",
    "billed_amount": 4500,
    "requested_amount": 4500,
    "in_network": True,
    "prior_auth_number": "AUTH123",
    "raw_payload": {}
}

req = urllib.request.Request(url, data=json.dumps(claim_data).encode(), headers=headers, method="POST")

try:
    response = urllib.request.urlopen(req)
    result = json.loads(response.read().decode())
    print("Submit Response:", result)
    
    claim_id = result["id"]
    print("Polling for decision...")
    for _ in range(10):
        time.sleep(2)
        dec_req = urllib.request.Request(f"http://localhost:8000/api/v1/decisions/{claim_id}", headers=headers)
        try:
            dec_resp = urllib.request.urlopen(dec_req)
            dec_result = json.loads(dec_resp.read().decode())
            print("Decision status:", dec_result.get("status"), "Verdict:", dec_result.get("verdict"))
            if dec_result.get("verdict") or dec_result.get("status") == "ERROR":
                print("Final decision data:", json.dumps(dec_result, indent=2))
                break
        except urllib.error.HTTPError as e:
            print("HTTP Error:", e.code, e.read().decode())
            
except urllib.error.HTTPError as e:
    print("Submit Error:", e.code, e.read().decode())
except Exception as e:
    print("Error:", str(e))
