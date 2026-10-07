"""Test columns of public.vehicles by querying with select."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

# Test candidates
cols = [
    "id", "name", "plate", "plate_number", "plate_no", "reg_no", "registration",
    "wialon_id", "unit_id", "vin", "make", "model", "year", "type", "category",
    "status", "device_id", "sim", "imei", "chassis", "customer_id", "created_at", "updated_at"
]

found = []
for c in cols:
    r = requests.get(f"{url}/rest/v1/vehicles?select={c}&limit=1", headers=headers)
    if r.ok:
        found.append(c)

print(f"Existing columns in public.vehicles: {found}")
