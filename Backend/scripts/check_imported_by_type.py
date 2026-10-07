"""Check column types of fuel_imports."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

# Test if imported_by accepts text or requires uuid or if null is allowed
for val in ["test", "00000000-0000-0000-0000-000000000000", None]:
    res = requests.get(f"{url}/rest/v1/fuel_imports?imported_by=eq.{val}", headers=headers)
    print(f"Filter imported_by=eq.{val}: status={res.status_code}, text={res.text}")
