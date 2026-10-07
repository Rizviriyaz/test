"""Probe all possible vehicle table columns."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

more_cols = ["vin", "make", "model", "year", "registration_no", "reg_no", "identifier", "asset_id", "title", "code", "description", "assigned_driver", "driver_id"]
for c in more_cols:
    res = requests.get(f"{url}/rest/v1/vehicles?select={c}&limit=1", headers=headers)
    if res.ok:
        print(f"vehicles.{c}: OK")
