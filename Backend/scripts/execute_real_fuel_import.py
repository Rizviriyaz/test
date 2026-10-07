"""Script to execute the real fuel import and perform independent Supabase verification."""

import os
import sys
import json
import requests
from dotenv import load_dotenv

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, backend_dir)
load_dotenv(os.path.join(backend_dir, ".env"))

from app.fuel.db import get_supabase_rest_url, get_headers
from app.fuel.importer import execute_fuel_import

REAL_FILE_PATH = r"C:\Users\HP\Downloads\Transactions_09-02-26_09-09 (1).xlsx"

VALIDATED_MAPPING = {
    "transaction_date": "Transaction Date",
    "vehicle_identifier": "Plate Number",
    "litres": "Quantity",
    "fuel_type": "Product Name",
    "unit_price": "Unit Price",
    "total_cost": "Total Amount",
    "odometer_km": "Odometer",
    "station_name": "Site",
    "card_number": "ViP/Card Number",
    "driver_name": "Employee Name",
    "reference_number": "Auth Code",
    "notes": "Remarks"
}


def run_pipeline():
    print("==================================================")
    print("PHASE 4: REAL FUEL DATA IMPORT EXECUTION")
    print("==================================================")

    # 1. Confirm secret key exists without printing its value
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    anon_key = os.getenv("SUPABASE_ANON_KEY", "").strip()

    if not supabase_url:
        print("ERROR: SUPABASE_URL is not set.")
        sys.exit(1)
    if not service_key:
        print("ERROR: SUPABASE_SERVICE_ROLE_KEY is not set.")
        sys.exit(1)

    print(f"Supabase URL: {supabase_url}")
    print(f"Service role key detected: True (Length: {len(service_key)} chars)")
    print(f"Anon key detected: True (Length: {len(anon_key)} chars)")

    # 2. Confirm fuel_imports and fuel_transactions are still empty
    headers = get_headers(None)
    res_check_imp = requests.get(get_supabase_rest_url("fuel_imports?select=id"), headers=headers)
    res_check_txn = requests.get(get_supabase_rest_url("fuel_transactions?select=id&limit=1"), headers=headers)

    if not res_check_imp.ok:
        print(f"ERROR: Cannot query fuel_imports ({res_check_imp.status_code}): {res_check_imp.text}")
        sys.exit(1)
    if not res_check_txn.ok:
        print(f"ERROR: Cannot query fuel_transactions ({res_check_txn.status_code}): {res_check_txn.text}")
        sys.exit(1)

    existing_imports_count = len(res_check_imp.json())
    existing_txns_count = len(res_check_txn.json())
    print(f"Existing fuel_imports count: {existing_imports_count}")
    print(f"Existing fuel_transactions count: {existing_txns_count}")

    if existing_imports_count > 0:
        print("WARNING: fuel_imports already has records! Verifying if this file was previously imported...")
        res_file_check = requests.get(get_supabase_rest_url("fuel_imports?file_name=eq.Transactions_09-02-26_09-09.xlsx"), headers=headers)
        if res_file_check.ok and len(res_file_check.json()) > 0:
            print("ABORTING: Transactions_09-02-26_09-09.xlsx has ALREADY been imported!")
            sys.exit(1)

    # 3. Read real file
    if not os.path.exists(REAL_FILE_PATH):
        print(f"ERROR: Real Excel file not found at {REAL_FILE_PATH}")
        sys.exit(1)

    with open(REAL_FILE_PATH, "rb") as f:
        file_bytes = f.read()

    print(f"Read {len(file_bytes)} bytes from real Excel file.")

    # 4. Execute Real Import via execute_fuel_import
    print("\nExecuting real fuel import...")
    import_result = execute_fuel_import(
        file_bytes=file_bytes,
        file_name="Transactions_09-02-26_09-09.xlsx",
        confirmed_mapping=VALIDATED_MAPPING,
        imported_by=None,
        bearer_token=None,  # Uses service role key
    )

    print("\n--- Raw Import Result ---")
    print(json.dumps(import_result.model_dump(), indent=2))

    # 5. Independent Database Verification
    print("\n==================================================")
    print("INDEPENDENT DATABASE VERIFICATION")
    print("==================================================")
    import_id = import_result.import_id

    # Verify fuel_imports record
    res_imp = requests.get(get_supabase_rest_url(f"fuel_imports?id=eq.{import_id}"), headers=headers)
    if not res_imp.ok or not res_imp.json():
        print(f"ERROR: Could not fetch fuel_imports record for id={import_id}")
        sys.exit(1)

    db_import_record = res_imp.json()[0]
    print("Database fuel_imports record:")
    for k, v in db_import_record.items():
        print(f"  {k}: {v}")

    # Fetch all inserted fuel_transactions for this import using pagination
    print("\nFetching all fuel_transactions for independent audit...")
    all_txns = []
    page_size = 1000
    offset = 0

    while True:
        url = get_supabase_rest_url(f"fuel_transactions?import_id=eq.{import_id}&select=*&limit={page_size}&offset={offset}&order=id.asc")
        res_page = requests.get(url, headers=headers)
        if not res_page.ok:
            print(f"ERROR fetching transactions page at offset={offset}: {res_page.status_code}")
            break
        page_data = res_page.json()
        if not page_data:
            break
        all_txns.extend(page_data)
        offset += len(page_data)
        if len(page_data) < page_size:
            break

    print(f"Total transactions fetched from Supabase: {len(all_txns)}")

    # Calculate metrics
    total_litres = sum(float(t["litres"] or 0) for t in all_txns)
    total_cost = sum(float(t["total_cost"] or 0) for t in all_txns)
    unique_vehicles = set(t["vehicle_identifier"] for t in all_txns if t.get("vehicle_identifier"))
    all_dates = [t["transaction_date"][:10] for t in all_txns if t.get("transaction_date")]
    min_date = min(all_dates) if all_dates else None
    max_date = max(all_dates) if all_dates else None

    # Check vehicle_id is NULL for all rows
    non_null_vehicle_ids = [t for t in all_txns if t.get("vehicle_id") is not None]
    
    # Check for empty/invalid vehicle_identifiers
    empty_vehicle_rows = [t for t in all_txns if not t.get("vehicle_identifier") or t.get("vehicle_identifier") == "#REF!"]

    # Check for duplicates
    fingerprints = set()
    db_dupes = 0
    for t in all_txns:
        fp = f"{t.get('vehicle_identifier')}|{t.get('transaction_date')[:10]}|{float(t.get('litres') or 0):.2f}|{float(t.get('total_cost') or 0):.2f}|{t.get('invoice_number') or ''}|{t.get('odometer_km') or ''}"
        if fp in fingerprints:
            db_dupes += 1
        fingerprints.add(fp)

    print("\n--- Independent Calculation Results ---")
    print(f"Total Transaction Rows: {len(all_txns)} (Expected: 2335)")
    print(f"Total Litres: {total_litres:,.2f} (Expected: 110,553.52)")
    print(f"Total Cost: {total_cost:,.2f} AED (Expected: 404,532.95 AED)")
    print(f"Unique Vehicle Identifiers: {len(unique_vehicles)} (Expected: 238)")
    print(f"Date Range: {min_date} to {max_date} (Expected: 2026-08-03 to 2026-08-31)")
    print(f"Non-null vehicle_id count: {len(non_null_vehicle_ids)} (Expected: 0)")
    print(f"Empty/Invalid vehicle rows: {len(empty_vehicle_rows)} (Expected: 0)")
    print(f"Duplicate transactions in DB: {db_dupes} (Expected: 0)")

    # Assertions
    assert len(all_txns) == 2335, f"Expected 2335 rows, got {len(all_txns)}"
    assert abs(total_litres - 110553.52) < 0.01, f"Litres mismatch: {total_litres} vs 110553.52"
    assert abs(total_cost - 404532.95) < 0.01, f"Cost mismatch: {total_cost} vs 404532.95"
    assert len(unique_vehicles) == 238, f"Unique vehicles mismatch: {len(unique_vehicles)} vs 238"
    assert min_date == "2026-08-03", f"Min date mismatch: {min_date}"
    assert max_date == "2026-08-31", f"Max date mismatch: {max_date}"
    assert len(non_null_vehicle_ids) == 0, f"Found {len(non_null_vehicle_ids)} non-null vehicle_id records"
    assert len(empty_vehicle_rows) == 0, f"Found {len(empty_vehicle_rows)} invalid vehicle records in DB"
    assert db_dupes == 0, f"Found {db_dupes} duplicates in DB"

    print("\n==================================================")
    print("DATABASE MATCHES EXCEL SOURCE GROUND TRUTH EXACTLY!")
    print("==================================================")


if __name__ == "__main__":
    run_pipeline()
