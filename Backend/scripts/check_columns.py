"""Inspect column definitions via OPTIONS on PostgREST."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}"}

for tbl in ["vehicles", "fuel_transactions", "fuel_imports"]:
    res = requests.options(f"{url}/rest/v1/{tbl}", headers=headers)
    print(f"\n--- OPTIONS /rest/v1/{tbl} ---")
    print(f"Status: {res.status_code}")
    print(f"Headers: {dict(res.headers)}")
    if res.text:
        print(f"Body: {res.text[:500]}")
