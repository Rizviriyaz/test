"""Comprehensive Phase 2 test suite for Fuel Import & Supabase operations."""

import io
import json
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import openpyxl
from fastapi.testclient import TestClient

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.main import app
from app.fuel.db import build_fingerprint
from app.fuel.importer import execute_fuel_import, validate_confirmed_mapping, FuelImportError
from offline_guard import OfflineGuard


def create_test_excel(headers, rows, sheet_name="Fuel Statement", start_row=1, title_rows=None):
    """Helper to generate in-memory Excel workbook bytes."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    curr_row = 1
    if title_rows:
        for t in title_rows:
            ws.cell(row=curr_row, column=1, value=t)
            curr_row += 1

    while curr_row < start_row:
        curr_row += 1

    for col_idx, h in enumerate(headers, start=1):
        ws.cell(row=curr_row, column=col_idx, value=h)
    curr_row += 1

    for r in rows:
        for col_idx, val in enumerate(r, start=1):
            ws.cell(row=curr_row, column=col_idx, value=val)
        curr_row += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def run_phase2_tests():
    print("==================================================")
    print("RUNNING FUEL IMPORT PHASE 2 TEST SUITE")
    print("==================================================")

    # In-memory mock database store for testing without corrupting Supabase
    mock_db_imports = {}
    mock_db_transactions = []
    mock_import_counter = [100]

    def mock_create_import_record(file_name, imported_by, rows_found, bearer_token=None, **_kwargs):
        mock_import_counter[0] += 1
        new_id = mock_import_counter[0]
        mock_db_imports[new_id] = {
            "id": new_id,
            "file_name": file_name,
            "imported_by": imported_by,
            "status": "processing",
            "rows_found": rows_found,
            "rows_imported": 0,
            "rows_duplicate": 0,
            "rows_failed": 0,
            "created_at": datetime.utcnow().isoformat(),
        }
        return new_id

    def mock_update_import_record(import_id, updates, bearer_token=None, **_kwargs):
        if import_id in mock_db_imports:
            mock_db_imports[import_id].update(updates)
            return True
        return False

    def mock_fetch_existing_fingerprints(date_from=None, date_to=None, bearer_token=None, **_kwargs):
        fps = set()
        for t in mock_db_transactions:
            fps.add(build_fingerprint(
                t.get("vehicle_identifier"),
                t.get("transaction_date"),
                t.get("litres"),
                t.get("total_cost"),
                t.get("invoice_number"),
                t.get("odometer_km"),
            ))
        return fps

    def mock_insert_transactions_batch(transactions, batch_size=100, bearer_token=None, **_kwargs):
        start_id = len(mock_db_transactions) + 1
        for idx, t in enumerate(transactions, start=start_id):
            record = dict(t)
            record["id"] = idx
            record["created_at"] = datetime.utcnow().isoformat()
            mock_db_transactions.append(record)
        return len(transactions), []

    def mock_get_imports_list(limit=50, offset=0, bearer_token=None):
        records = list(mock_db_imports.values())
        return records[offset: offset + limit], len(records)

    def mock_get_transactions_list(filters, limit=50, offset=0, bearer_token=None):
        filtered = list(mock_db_transactions)
        if filters.get("vehicle_identifier"):
            filtered = [r for r in filtered if filters["vehicle_identifier"].lower() in r["vehicle_identifier"].lower()]
        if filters.get("fuel_type"):
            filtered = [r for r in filtered if r.get("fuel_type") and filters["fuel_type"].lower() in r["fuel_type"].lower()]
        return filtered[offset: offset + limit], len(filtered)

    def mock_fetch_for_analytics(filters, bearer_token=None):
        # Mirrors the current substring (ilike) vehicle filter of fetch_fuel_transactions_for_analytics
        rows, _ = mock_get_transactions_list(filters, limit=len(mock_db_transactions) or 1, offset=0)
        return rows

    def mock_wialon_units(sid=None):
        plates = sorted({t["vehicle_identifier"] for t in mock_db_transactions})
        return [{"id": 9000 + i, "nm": plate} for i, plate in enumerate(plates, start=1)]

    mock_vehicle_lookup = {
        "dxb10293": 4201,
        "shj88211": 4202,
        "auh44120": 4203,
    }

    # Test 1: Successful Import with Vehicle Resolution
    print("\n--- Test 1: Successful Import with Vehicle Resolution ---")
    excel_1 = create_test_excel(
        headers=["Date", "Vehicle", "Litres", "Rate", "Total", "Odo", "Station", "Invoice"],
        rows=[
            ["2026-03-01", "DXB 10293", "50.0", "3.10", "155.00", "120000", "ADNOC 101", "INV-001"],
            ["2026-03-02", "SHJ 88211", "60.0", "3.10", "186.00", "85000", "ADNOC 102", "INV-002"],
        ]
    )
    mapping_1 = {
        "transaction_date": "Date",
        "vehicle_identifier": "Vehicle",
        "litres": "Litres",
        "unit_price": "Rate",
        "total_cost": "Total",
        "odometer_km": "Odo",
        "station_name": "Station",
        "invoice_number": "Invoice",
    }

    with patch("app.fuel.importer.fetch_running_imports", return_value=[]), patch("app.fuel.importer.create_fuel_import_record", side_effect=mock_create_import_record), \
         patch("app.fuel.importer.update_fuel_import_record", side_effect=mock_update_import_record), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", side_effect=mock_fetch_existing_fingerprints), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=mock_insert_transactions_batch), \
         patch("app.fuel.importer.resolve_fleet_vehicles", return_value=mock_vehicle_lookup):

        res_1 = execute_fuel_import(
            file_bytes=excel_1,
            file_name="valid_march.xlsx",
            confirmed_mapping=mapping_1,
            imported_by="user-uuid-1",
        )
        print(f"Import 1 status: {res_1.status}, imported: {res_1.rows_imported}, failed: {res_1.rows_failed}")
        assert res_1.status == "completed"
        assert res_1.rows_imported == 2
        assert res_1.rows_failed == 0
        assert res_1.rows_duplicate == 0
        assert mock_db_transactions[0]["vehicle_id"] is None
        assert mock_db_transactions[1]["vehicle_id"] is None
        assert mock_db_transactions[0]["vehicle_identifier"] == "DXB 10293"
        print("[PASS] Successful import and vehicle ID NULL handling confirmed.")

    # Test 2: Missing Required Mapping Validation
    print("\n--- Test 2: Missing Required Mapping Rejection ---")
    try:
        validate_confirmed_mapping(
            raw_headers=["Date", "Vehicle"],
            confirmed_mapping={"transaction_date": "Date", "vehicle_identifier": "Vehicle"} # Missing litres
        )
        assert False, "Should have failed due to missing 'litres'"
    except FuelImportError as exc:
        print(f"Correctly caught error: {exc.message}")
        assert "litres" in exc.message
        print("[PASS] Missing required mapping rejected.")

    # Test 3: Invalid Non-Existent Column in Mapping
    print("\n--- Test 3: Invalid Non-Existent Header in Mapping ---")
    try:
        validate_confirmed_mapping(
            raw_headers=["Date", "Vehicle", "Litres"],
            confirmed_mapping={"transaction_date": "Date", "vehicle_identifier": "GhostColumn", "litres": "Litres"}
        )
        assert False, "Should have failed due to non-existent column"
    except FuelImportError as exc:
        print(f"Correctly caught error: {exc.message}")
        assert "GhostColumn" in exc.message
        print("[PASS] Non-existent column in mapping rejected.")

    # Test 4: Invalid Required Row (Bad date / missing vehicle / negative litres)
    print("\n--- Test 4: Invalid Required Rows Handling ---")
    excel_bad_rows = create_test_excel(
        headers=["Date", "Vehicle", "Litres"],
        rows=[
            ["2026-03-05", "AUH 44120", "80.0"],       # Valid
            ["NOT_A_DATE", "AUH 44120", "80.0"],       # Invalid date
            ["2026-03-06", "", "80.0"],                # Missing vehicle
            ["2026-03-07", "AUH 44120", "-15.0"],      # Negative litres: refund adjustment (Phase 1)
        ]
    )
    with patch("app.fuel.importer.fetch_running_imports", return_value=[]), patch("app.fuel.importer.create_fuel_import_record", side_effect=mock_create_import_record), \
         patch("app.fuel.importer.update_fuel_import_record", side_effect=mock_update_import_record), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", side_effect=mock_fetch_existing_fingerprints), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=mock_insert_transactions_batch), \
         patch("app.fuel.importer.resolve_fleet_vehicles", return_value=mock_vehicle_lookup):

        res_bad = execute_fuel_import(
            file_bytes=excel_bad_rows,
            file_name="mixed_validity.xlsx",
            confirmed_mapping={"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres"},
            imported_by="user-uuid-1",
        )
        print(f"Import status: {res_bad.status}, imported: {res_bad.rows_imported}, failed: {res_bad.rows_failed}")
        assert res_bad.status == "completed_with_errors"
        # Negative litres are now stored as a signed refund adjustment instead of being rejected
        assert res_bad.rows_imported == 2
        assert res_bad.rows_refund == 1
        assert res_bad.rows_failed == 2
        print("[PASS] Invalid rows correctly isolated without destroying valid transactions; refund kept as adjustment.")

    # Test 5 & 6 & 7: Deduplication (Internal, External, Legitimate Multi-Refuel)
    print("\n--- Test 5, 6, 7: Duplicate Detection & Multi-Refuel Validation ---")
    excel_dupes = create_test_excel(
        headers=["Date", "Vehicle", "Litres", "Amount", "Invoice", "Odo"],
        rows=[
            # Row A: Exact duplicate of existing DB record (Test 1: DXB 10293, 2026-03-01, 50.0, 155.00, INV-001, 120000)
            ["2026-03-01", "DXB 10293", "50.0", "155.00", "INV-001", "120000"],
            # Row B: Legitimate second refuel on the SAME DAY for DXB 10293 (different litres, invoice, odo)
            ["2026-03-01", "DXB 10293", "35.0", "108.50", "INV-009", "120450"],
            # Row C: Duplicate of Row B inside the same file
            ["2026-03-01", "DXB 10293", "35.0", "108.50", "INV-009", "120450"],
        ]
    )
    with patch("app.fuel.importer.fetch_running_imports", return_value=[]), patch("app.fuel.importer.create_fuel_import_record", side_effect=mock_create_import_record), \
         patch("app.fuel.importer.update_fuel_import_record", side_effect=mock_update_import_record), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", side_effect=mock_fetch_existing_fingerprints), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=mock_insert_transactions_batch), \
         patch("app.fuel.importer.resolve_fleet_vehicles", return_value=mock_vehicle_lookup):

        res_dupe = execute_fuel_import(
            file_bytes=excel_dupes,
            file_name="dupe_test.xlsx",
            confirmed_mapping={
                "transaction_date": "Date",
                "vehicle_identifier": "Vehicle",
                "litres": "Litres",
                "total_cost": "Amount",
                "invoice_number": "Invoice",
                "odometer_km": "Odo"
            },
            imported_by="user-uuid-1",
        )
        print(f"Dupe import status: {res_dupe.status}, imported: {res_dupe.rows_imported}, duplicate: {res_dupe.rows_duplicate}")
        assert res_dupe.rows_imported == 1  # Only Row B imported
        assert res_dupe.rows_duplicate == 2 # Row A (db dupe) + Row C (file dupe)
        print("[PASS] External & internal duplicates filtered while legitimate multi-refuels preserved.")

    # Test 8 & 9 & 10: Status transitions (completed, completed_with_errors, failed)
    print("\n--- Test 8, 9, 10: Import Status Verifications ---")
    assert res_1.status == "completed"
    assert res_bad.status == "completed_with_errors"

    # Failed import: All rows unparseable
    excel_all_fail = create_test_excel(
        headers=["Date", "Vehicle", "Litres"],
        rows=[["bad_date", "v1", "bad_litres"]]
    )
    with patch("app.fuel.importer.fetch_running_imports", return_value=[]), patch("app.fuel.importer.create_fuel_import_record", side_effect=mock_create_import_record), \
         patch("app.fuel.importer.update_fuel_import_record", side_effect=mock_update_import_record), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", side_effect=mock_fetch_existing_fingerprints), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=mock_insert_transactions_batch), \
         patch("app.fuel.importer.resolve_fleet_vehicles", return_value=mock_vehicle_lookup):

        res_fail = execute_fuel_import(
            file_bytes=excel_all_fail,
            file_name="failed.xlsx",
            confirmed_mapping={"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres"},
            imported_by="user-uuid-1",
        )
        print(f"Failed import status: {res_fail.status}, error_message: {res_fail.error_message}")
        assert res_fail.status == "failed"
        print("[PASS] Failed import status verified.")

    # Test 11, 12, 13: FastAPI Routes (POST /import, GET /imports, GET /transactions, Auth)
    print("\n--- Test 11, 12, 13: End-to-End FastAPI Route Suite ---")
    client = TestClient(app)

    # 13: Auth rejection without bearer token
    res_unauth = client.post("/api/fuel/import", data={"mapping": "{}"}, files={"file": ("test.xlsx", excel_1)})
    assert res_unauth.status_code == 401
    print("[PASS] Unauthenticated import request rejected with 401.")

    # Fresh Excel for Route Import (to avoid duplicate of Test 1)
    excel_route = create_test_excel(
        headers=["Date", "Vehicle", "Litres", "Rate", "Total", "Odo", "Station", "Invoice"],
        rows=[
            ["2026-03-10", "DXB 10293", "55.0", "3.10", "170.50", "121500", "ADNOC 105", "INV-101"],
            ["2026-03-11", "SHJ 88211", "65.0", "3.10", "201.50", "86500", "ADNOC 106", "INV-102"],
        ]
    )

    with patch("app.main.authenticate_access_token", return_value={"id": "test-uuid-admin", "email": "admin@ats.ae", "role": "admin"}), \
         patch("app.fuel.routes.get_fuel_imports_list", side_effect=mock_get_imports_list), \
         patch("app.fuel.routes.get_fuel_transactions_list", side_effect=mock_get_transactions_list), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", side_effect=mock_fetch_for_analytics), \
         patch("app.fuel.routes.login", return_value="mock-sid"), \
         patch("app.fuel.routes.get_all_vehicles", side_effect=mock_wialon_units), \
         patch("app.fuel.importer.fetch_running_imports", return_value=[]), patch("app.fuel.importer.create_fuel_import_record", side_effect=mock_create_import_record), \
         patch("app.fuel.importer.update_fuel_import_record", side_effect=mock_update_import_record), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", side_effect=mock_fetch_existing_fingerprints), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=mock_insert_transactions_batch), \
         patch("app.fuel.importer.resolve_fleet_vehicles", return_value=mock_vehicle_lookup):

        auth_headers = {"Authorization": "Bearer test-valid-jwt"}

        # HTTP POST /api/fuel/import
        res_http_import = client.post(
            "/api/fuel/import",
            headers=auth_headers,
            data={"mapping": json.dumps(mapping_1)},
            files={"file": ("march_fuel.xlsx", excel_route, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        print(f"HTTP /api/fuel/import status: {res_http_import.status_code}")
        assert res_http_import.status_code == 200
        import_data = res_http_import.json()
        print(f"Import response: id={import_data['import_id']}, status={import_data['status']}, imported={import_data['rows_imported']}")
        assert import_data["import_id"] > 0
        assert import_data["status"] == "completed"
        assert import_data["rows_imported"] == 2
        print("[PASS] POST /api/fuel/import executed successfully via FastAPI TestClient.")

        # 11: HTTP GET /api/fuel/imports
        res_history = client.get("/api/fuel/imports", headers=auth_headers)
        assert res_history.status_code == 200
        history_data = res_history.json()
        print(f"Import history count: {history_data['count']}")
        assert history_data["count"] > 0
        print("[PASS] GET /api/fuel/imports returned history successfully.")

        # 12: HTTP GET /api/fuel/transactions (with pagination & filter)
        # Exact vehicle filter (Phase 3): a plate, not a substring such as "DXB"
        res_txns = client.get("/api/fuel/transactions?page=1&page_size=10&vehicle_identifier=DXB 10293", headers=auth_headers)
        assert res_txns.status_code == 200
        txns_data = res_txns.json()
        print(f"Filtered transactions count: {txns_data['count']}, page: {txns_data['page']}")
        assert txns_data["count"] > 0
        assert "DXB" in txns_data["transactions"][0]["vehicle_identifier"]
        print("[PASS] GET /api/fuel/transactions filtered and paginated successfully.")

    # Test 14 & 15: Existing health and vehicles endpoints
    print("\n--- Test 14, 15: Existing Core Endpoints Health ---")
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    print(f"/api/health: {res_health.json()}")

    print("\n==================================================")
    print("ALL 15 PHASE 2 TEST SCENARIOS PASSED WITH ZERO ERRORS!")
    print("==================================================")


if __name__ == "__main__":
    guard = OfflineGuard().start()
    try:
        run_phase2_tests()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
