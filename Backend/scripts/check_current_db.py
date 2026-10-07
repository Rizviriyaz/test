"""Check Supabase users / profiles for import ownership."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

res = requests.get(f"{url}/rest/v1/profiles?select=*", headers=headers)
print(f"Profiles count: {res.status_code}, data={res.json() if res.ok else res.text}")

res_imports = requests.get(f"{url}/rest/v1/fuel_imports?select=*", headers=headers)
print(f"Existing fuel_imports: {res_imports.status_code}, data={res_imports.json() if res_imports.ok else res_imports.text}")

res_txns = requests.get(f"{url}/rest/v1/fuel_transactions?select=id&limit=5", headers=headers)
print(f"Existing fuel_transactions: {res_txns.status_code}, count={len(res_txns.json()) if res_txns.ok else res_txns.text}")
