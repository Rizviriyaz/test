"""Verify insert permissions and constraints on Supabase."""

import os
import requests
from dotenv import load_dotenv

load_dotenv("c:/Users/HP/Desktop/ATS-AI/Backend/.env")
url = os.getenv("SUPABASE_URL", "").rstrip("/")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
headers = {
    "apikey": key,
    "Authorization": f"Bearer {key}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Prefer": "return=representation"
}

# 1. Test insert into fuel_imports
res_imp = requests.post(
    f"{url}/rest/v1/fuel_imports",
    json={
        "file_name": "test_verification.xlsx",
        "status": "processing",
        "rows_found": 1,
        "rows_imported": 0,
        "rows_duplicate": 0,
        "rows_failed": 0
    },
    headers=headers
)
print("Test insert fuel_imports:", res_imp.status_code, res_imp.text)

if res_imp.ok:
    imp_id = res_imp.json()[0]["id"]
    print(f"Created test import ID: {imp_id}")
    
    # 2. Test insert into fuel_transactions with vehicle_id = None
    res_txn_null = requests.post(
        f"{url}/rest/v1/fuel_transactions",
        json=[{
            "import_id": imp_id,
            "vehicle_id": None,
            "transaction_date": "2026-08-31T23:39:39",
            "vehicle_identifier": "TEST-PLATE",
            "litres": 50.0,
            "total_cost": 150.0,
            "source_row_number": 2
        }],
        headers=headers
    )
    print("Test insert fuel_transactions (vehicle_id=None):", res_txn_null.status_code, res_txn_null.text)
    
    # 3. Test insert into fuel_transactions with vehicle_id = 600446403 (Wialon ID)
    res_txn_wialon = requests.post(
        f"{url}/rest/v1/fuel_transactions",
        json=[{
            "import_id": imp_id,
            "vehicle_id": 600446403,
            "transaction_date": "2026-08-31T23:39:39",
            "vehicle_identifier": "TEST-PLATE",
            "litres": 50.0,
            "total_cost": 150.0,
            "source_row_number": 2
        }],
        headers=headers
    )
    print("Test insert fuel_transactions (vehicle_id=600446403):", res_txn_wialon.status_code, res_txn_wialon.text)
    
    # 4. Clean up test records
    requests.delete(f"{url}/rest/v1/fuel_transactions?import_id=eq.{imp_id}", headers=headers)
    requests.delete(f"{url}/rest/v1/fuel_imports?id=eq.{imp_id}", headers=headers)
    print("Cleaned up verification test records.")
