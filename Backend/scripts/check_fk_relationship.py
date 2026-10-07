"""Check foreign key constraint on fuel_transactions.vehicle_id."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Prefer": "return=representation"
}

# First check if we can query fuel_transactions with embedded resource vehicles:
# In PostgREST, if a foreign key exists to vehicles, ?select=*,vehicles(*) will succeed.
# If no foreign key exists, it will return an error stating no relationship exists!
res_embed = requests.get(f"{url}/rest/v1/fuel_transactions?select=id,vehicles(id)&limit=1", headers=headers)
print("Query embedded vehicles:", res_embed.status_code, res_embed.text)

# Also check embedded fuel_imports:
res_import_embed = requests.get(f"{url}/rest/v1/fuel_transactions?select=id,fuel_imports(id)&limit=1", headers=headers)
print("Query embedded fuel_imports:", res_import_embed.status_code, res_import_embed.text)
