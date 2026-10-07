"""Inspect all columns of public.vehicles table."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}",
    "Accept": "application/json"
}

# In PostgREST, inserting with an invalid column returns the list of valid columns or column error!
# Let's send a dummy insert with an invalid column to see PostgreSQL's error message
res = requests.post(
    f"{url}/rest/v1/vehicles",
    json={"__dummy_column_test__": 1},
    headers=headers
)
print("Dummy insert on vehicles response:")
print(res.status_code, res.text)
