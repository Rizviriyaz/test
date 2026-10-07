"""Check type of vehicle_id in fuel_transactions."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

# Test int query
res_int = requests.get(f"{url}/rest/v1/fuel_transactions?vehicle_id=eq.123", headers=headers)
print(f"Filter vehicle_id=eq.123: status={res_int.status_code}, text={res_int.text}")

# Test uuid query
res_uuid = requests.get(f"{url}/rest/v1/fuel_transactions?vehicle_id=eq.00000000-0000-0000-0000-000000000000", headers=headers)
print(f"Filter vehicle_id=eq.uuid: status={res_uuid.status_code}, text={res_uuid.text}")
