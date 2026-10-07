"""Check Supabase tables and vehicle_id relationship."""

import os
import sys
import requests
from dotenv import load_dotenv

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(backend_dir, ".env"))

url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")

headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}",
    "Accept": "application/json"
}

print(f"Testing Supabase endpoints at: {url}")

for endpoint in ["fuel_imports", "fuel_transactions", "vehicles", "profiles"]:
    try:
        res = requests.get(f"{url}/rest/v1/{endpoint}?limit=1", headers=headers, timeout=10)
        print(f"GET /rest/v1/{endpoint}: HTTP {res.status_code}")
        if res.ok:
            data = res.json()
            print(f"  Sample data: {data}")
        else:
            print(f"  Response: {res.text[:200]}")
    except Exception as e:
        print(f"  Error on {endpoint}: {e}")
