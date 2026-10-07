"""Probe columns of vehicles and fuel_transactions."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_ANON_KEY", "")
headers = {"apikey": key, "Authorization": f"Bearer {key}", "Accept": "application/json"}

# Probe vehicle table columns
test_cols = ["id", "wialon_id", "plate", "plate_number", "name", "created_at", "unit_id", "status"]
for c in test_cols:
    res = requests.get(f"{url}/rest/v1/vehicles?select={c}&limit=1", headers=headers)
    print(f"vehicles.{c}: status={res.status_code} {res.text if not res.ok else 'OK'}")

# Probe fuel_transactions table columns
ft_cols = ["id", "vehicle_id", "import_id", "transaction_date", "vehicle_identifier", "fuel_type", "litres", "unit_price", "total_cost", "odometer_km", "station_name", "invoice_number", "card_number", "driver_name", "reference_number", "notes", "source_row_number", "created_at"]
for c in ft_cols:
    res = requests.get(f"{url}/rest/v1/fuel_transactions?select={c}&limit=1", headers=headers)
    print(f"fuel_transactions.{c}: status={res.status_code} {res.text if not res.ok else 'OK'}")
