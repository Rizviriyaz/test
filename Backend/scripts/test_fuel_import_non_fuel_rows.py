"""
Offline regression tests for non-fuel statement entries in the fuel import.

The 2,341-row statement (Transactions_09-02-26_09-09.xlsx) imported 2,335 rows and failed 6. The six were not
broken fuel purchases: they were account entries with no litres (FixedServiceCharge, Payment x2, ServiceCharge x2,
ManualServiceCharge). They are now skipped as ignored non-fuel rows, reported with their source rows, and never
inserted. Purchase rows keep the full validation.

Each test below reproduces one of the six rows with the cells the statement had (other columns empty).
Also covered: the preview classifies rows with the same rules as the import (same counts and rows), every row
is accounted for, and fuel_imports.rows_ignored / rows_non_fuel are written only with FUEL_IMPORT_IGNORED_COUNTS=1
while history records without them (older imports) stay readable and are never shown as 0.
Supabase calls are replaced by in-memory fakes; OfflineGuard blocks any outbound HTTP.

Run from Backend/:  python scripts/test_fuel_import_non_fuel_rows.py
"""

import io
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# These tests cover the import with FUEL_IMPORT_REPLACE off (the replacement path has its own suites); a local
# .env that enables it must not change them. load_dotenv never overrides a variable that is already set.
os.environ["FUEL_IMPORT_REPLACE"] = ""

from offline_guard import OfflineGuard  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.fuel import db as fuel_db  # noqa: E402
from app.fuel import importer  # noqa: E402
from app.fuel.db import FingerprintSet  # noqa: E402
from app.fuel.importer import (  # noqa: E402
    execute_fuel_import, generate_fuel_preview, is_non_fuel_entry, reconcile_fuel_import,
)
from app.fuel.mapper import map_headers  # noqa: E402
from app.fuel.schemas import FuelImportItem  # noqa: E402

AUTH = {"Authorization": "Bearer offline-test-token"}
FAKE_USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "t@ats.ae", "role": "admin"}

# Header row of the real statement
HEADERS = ["Transaction Number", "Transaction Date", "Customer Code", "Customer Name", "Group Code", "Group Name",
           "Plate Number", "PLATE", "Chassis", "Odometer", "Transaction Type", "Service Charge Description", "Origin",
           "ViP/Card Number", "Auth Code", "Status", "Site", "LOB", "Product Name", "Quantity", "Unit Price",
           "Amount Without VAT", "Vat Amount", "Total Amount", "Card Name", "Employee Name", "Remarks"]


def purchase(n, plate, qty, total, when="2026-08-31 21:32:02.9600534"):
    return {"Transaction Number": n, "Transaction Date": when, "Plate Number": plate, "Odometer": "0",
            "Transaction Type": "Purchase", "Origin": "RFIDCredit", "Status": "Completed", "Site": "0090",
            "Product Name": "SPECIAL", "Quantity": qty, "Unit Price": 3.49, "Total Amount": total}


# The six failed source rows (cells as on the statement)
ROW_8 = {"Transaction Number": "000000042760333", "Transaction Date": "2026-08-31 21:25:32.0000000", "PLATE": "#REF!",
         "Transaction Type": "FixedServiceCharge", "Service Charge Description": "ViP MONTHLY SERV CHG", "Origin": "ECS",
         "Auth Code": "QD4242", "Status": "Completed", "LOB": "FUEL", "Amount Without VAT": 2660, "Vat Amount": 133,
         "Total Amount": 2793, "Remarks": "Fixed Service Charge calculation, Level:Tag"}
ROW_863 = {"Transaction Number": "000000041773287", "Transaction Date": "2026-08-20 09:32:34.6881781", "PLATE": "#REF!",
           "Transaction Type": "Payment", "Origin": "SAP invoice Payment", "Status": "Completed",
           "Amount Without VAT": 0, "Total Amount": -360575, "Remarks": "Postpaid account payment"}
ROW_1196 = {"Transaction Number": "000000041418998", "Transaction Date": "2026-08-16 07:57:50.8313227",
            "Plate Number": "BB-DXB-39055", "PLATE": 39055, "Transaction Type": "ServiceCharge",
            "Service Charge Description": "ViP Tag charge", "Auth Code": "LO6836", "Status": "Completed", "Site": "1000",
            "LOB": "FUEL", "Amount Without VAT": 50, "Vat Amount": 2.5, "Total Amount": 52.5,
            "Remarks": "Purchase Tag for # BB-DXB-39055"}
ROW_1197 = {"Transaction Number": "000000041418942", "Transaction Date": "2026-08-16 07:56:24.4387491",
            "Plate Number": "BB-DXB-39055", "PLATE": 39055, "Transaction Type": "ServiceCharge",
            "Service Charge Description": "Edit SubGroup", "Auth Code": "LO6782", "Status": "Completed", "LOB": "FUEL",
            "Amount Without VAT": 25, "Vat Amount": 1.25, "Total Amount": 26.25,
            "Remarks": "Edit Vehicle BB-Dubai-DXB-39055"}
ROW_2306 = {"Transaction Number": "000000040295895", "Transaction Date": "2026-08-03 13:12:20.8302811", "PLATE": "#REF!",
            "Transaction Type": "Payment", "Origin": "SAP invoice Payment", "Status": "Completed",
            "Amount Without VAT": 0, "Total Amount": -404185, "Remarks": "Postpaid account payment"}
ROW_2342 = {"Transaction Number": "000000040269467", "Transaction Date": "2026-08-03 08:37:35.5923770", "PLATE": "#REF!",
            "Transaction Type": "ManualServiceCharge", "Service Charge Description": "Late Payment Fee",
            "Auth Code": "HP4842", "Status": "Completed", "Amount Without VAT": 500, "Vat Amount": 25,
            "Total Amount": 525, "Remarks": "Payment against Jun 26 transferred on 31.07.2026 via SCB"}

SIX_CASES = [("row 8 FixedServiceCharge", ROW_8, "FixedServiceCharge"),
             ("row 863 Payment", ROW_863, "Payment"),
             ("row 1196 ServiceCharge (tag)", ROW_1196, "ServiceCharge"),
             ("row 1197 ServiceCharge (edit subgroup)", ROW_1197, "ServiceCharge"),
             ("row 2306 Payment", ROW_2306, "Payment"),
             ("row 2342 ManualServiceCharge", ROW_2342, "ManualServiceCharge")]


def make_xlsx(rows, headers=HEADERS):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def suggested_mapping():
    mapping, _, _, missing = map_headers(HEADERS)
    assert not missing, missing
    return {k: v for k, v in mapping.items() if v}


def run_import(rows, mapping=None, env=None, create=None):
    """Run the real import pipeline with the Supabase helpers faked; returns (result, rows sent, final update)."""
    sent = []

    def fake_insert(transactions, batch_size, bearer_token=None, deadline=None, outcome=None):
        sent.extend(transactions)
        return len(transactions), []

    with patch.dict(os.environ, {"FUEL_IMPORT_IGNORED_COUNTS": "", **(env or {})}), \
         patch.object(importer, "schema_v2_enabled", return_value=False), \
         patch.object(importer, "create_fuel_import_record", create or MagicMock(return_value=101)), \
         patch.object(importer, "fetch_running_imports", return_value=[]), \
         patch.object(importer, "fetch_existing_transaction_fingerprints", return_value=FingerprintSet()), \
         patch.object(importer, "insert_fuel_transactions_batch", side_effect=fake_insert), \
         patch.object(importer, "update_fuel_import_record", return_value=True) as update:
        result = execute_fuel_import(make_xlsx(rows), "statement.xlsx", mapping or suggested_mapping(), "tester")
    return result, sent, update.call_args.kwargs["updates"]


def assert_accounted(r):
    total = (r.rows_imported + r.rows_duplicate + r.rows_failed + r.rows_ignored + r.rows_unverified
             + r.rows_not_processed)
    assert total == r.rows_found, f"{total} accounted of {r.rows_found}"


# ─── Tests ─────────────────────────────────────────────────────────────────────

def test_mapper_detects_transaction_type_without_moving_other_columns():
    mapping = suggested_mapping()
    assert mapping["transaction_type"] == "Transaction Type"
    assert mapping["litres"] == "Quantity" and mapping["vehicle_identifier"] == "Plate Number"
    assert mapping["fuel_type"] == "Product Name" and mapping["total_cost"] == "Total Amount"
    assert mapping["notes"] == "Remarks"


def make_six_case_test(label, row, type_name):
    def test():
        good = purchase("1", "8-AUH-74428", 55.49, 193.66)
        r, sent, updates = run_import([good, row])
        assert r.rows_found == 2 and r.rows_imported == 1, r
        assert r.rows_failed == 0 and not r.failed_row_details, r.failed_row_details
        assert r.rows_ignored == 1 and r.rows_non_fuel == 1
        assert r.ignored_row_details == [{"source_row": 3, "reason": f"Non-fuel entry (type '{type_name}', no litres)"}]
        assert [t["source_row_number"] for t in sent] == [2], "the non-fuel row must not be inserted"
        assert r.status == "completed" and updates["status"] == "completed" and updates["rows_failed"] == 0
        assert_accounted(r)
    test.__name__ = f"test_non_fuel_{label}"
    return test


def test_all_six_together_with_purchases_account_for_every_row():
    purchases = [purchase(str(i), f"A-DXB-{1000 + i}", 20 + i, round((20 + i) * 3.49, 2),
                          when=f"2026-08-{10 + i:02d} 08:00:00") for i in range(5)]
    rows = purchases[:2] + [c[1] for c in SIX_CASES] + purchases[2:]
    r, sent, updates = run_import(rows)
    assert (r.rows_found, r.rows_imported, r.rows_duplicate, r.rows_failed, r.rows_ignored) == (11, 5, 0, 0, 6), r
    assert r.rows_non_fuel == 6 and len(r.ignored_row_details) == 6
    assert sorted(t["source_row_number"] for t in sent) == [2, 3, 10, 11, 12]
    assert r.status == "completed" and updates["rows_imported"] == 5
    assert_accounted(r)


def test_purchase_missing_litres_still_fails():
    bad = purchase("9", "A-DXB-1", None, 50.0)
    r, sent, _ = run_import([purchase("1", "A-DXB-2", 10, 34.9), bad])
    assert r.rows_failed == 1 and r.rows_ignored == 0 and r.rows_non_fuel == 0
    assert r.failed_row_details[0]["source_row"] == 3 and "litres" in r.failed_row_details[0]["reason"]
    assert r.status == "completed_with_errors"
    assert_accounted(r)


def test_purchase_missing_vehicle_still_fails():
    bad = purchase("9", None, 10, 34.9)
    r, _, _ = run_import([purchase("1", "A-DXB-2", 10, 34.9), bad])
    assert r.rows_failed == 1 and r.failed_row_details[0]["reason"] == "Missing vehicle identifier"
    assert r.rows_non_fuel == 0


def test_charge_type_with_litres_is_validated_not_skipped():
    row = {**purchase("9", "A-DXB-3", 12.5, 43.63), "Transaction Type": "ServiceCharge"}
    r, sent, _ = run_import([row])
    assert r.rows_imported == 1 and r.rows_ignored == 0 and len(sent) == 1


def test_charge_type_with_unparseable_litres_still_fails():
    row = {**ROW_1196, "Quantity": "abc"}
    r, sent, _ = run_import([row])
    assert r.rows_failed == 1 and r.rows_ignored == 0 and not sent


def test_without_transaction_type_mapping_behaviour_is_unchanged():
    mapping = {k: v for k, v in suggested_mapping().items() if k != "transaction_type"}
    r, sent, _ = run_import([purchase("1", "A-DXB-2", 10, 34.9), ROW_1196, ROW_863], mapping=mapping)
    assert r.rows_failed == 2 and r.rows_ignored == 0 and r.rows_non_fuel == 0 and len(sent) == 1


def test_is_non_fuel_entry_rules():
    assert is_non_fuel_entry("Payment", None) and is_non_fuel_entry("ServiceCharge", "  ")
    assert is_non_fuel_entry("Late payment FEE", 0) and is_non_fuel_entry("ManualServiceCharge", "0")
    assert not is_non_fuel_entry("Purchase", None)
    assert not is_non_fuel_entry(None, None)
    assert not is_non_fuel_entry("Payment", 5) and not is_non_fuel_entry("Payment", "abc")


# Mixed statement: purchases, the six non-fuel entries, invalid purchases, a blank and a total row, an in-file
# duplicate and a sign mismatch
def mixed_rows():
    ok = [purchase(str(i), f"A-DXB-{1000 + i}", 20 + i, round((20 + i) * 3.49, 2), when=f"2026-08-{10 + i:02d} 08:00:00")
          for i in range(4)]
    return [ok[0], ROW_8, ok[1], purchase("x1", None, 10, 34.9), ROW_863, {}, ok[2], ROW_1196, ROW_1197,
            purchase("x2", "B-DXB-1", None, 20.0), dict(ok[1]), ROW_2306, purchase("x3", "C-DXB-1", 10, -34.9),
            ok[3], {"Transaction Number": "Grand Total", "Total Amount": 9999}, ROW_2342]


def preview(rows, headers=HEADERS):
    return generate_fuel_preview(make_xlsx(rows, headers), "statement.xlsx")


def test_preview_counts_fuel_non_fuel_invalid_and_ignored():
    p = preview(mixed_rows())
    # 16 rows: 5 fuel (4 + 1 in-file duplicate), 3 invalid, 6 non-fuel, 1 blank, 1 total
    assert (p.rows_found, p.rows_fuel, p.rows_invalid, p.rows_non_fuel, p.rows_ignored) == (16, 5, 3, 6, 8), p
    assert p.rows_candidate == p.rows_fuel + p.rows_invalid
    assert p.rows_found == p.rows_fuel + p.rows_invalid + p.rows_ignored
    assert p.suggested_mapping["transaction_type"] == "Transaction Type"
    assert [d["source_row"] for d in p.non_fuel_row_details] == [3, 6, 9, 10, 13, 17]
    assert [d["source_row"] for d in p.invalid_row_details] == [5, 11, 14]
    assert all(s["_source_row"] not in (3, 6, 9, 10, 13, 17) for s in p.sample_rows), "non-fuel rows are not sampled"


def test_preview_and_import_classify_identically():
    rows = mixed_rows()
    p = preview(rows)
    r, sent, _ = run_import(rows, mapping={k: v for k, v in p.suggested_mapping.items() if v})
    assert r.rows_found == p.rows_found and r.rows_ignored == p.rows_ignored and r.rows_non_fuel == p.rows_non_fuel
    assert r.rows_failed == p.rows_invalid
    # Valid fuel rows are all imported: the identical in-file repeat is a repeated refuel, not a duplicate
    assert r.rows_imported + r.rows_duplicate == p.rows_fuel and (r.rows_imported, r.rows_duplicate) == (5, 0)
    assert r.ignored_row_details == p.non_fuel_row_details
    failed = sorted(r.failed_row_details, key=lambda d: d["source_row"])
    assert failed == p.invalid_row_details, (failed, p.invalid_row_details)
    assert len(sent) == 5
    assert_accounted(r)


def test_preview_without_type_column_matches_import():
    headers = [h for h in HEADERS if h != "Transaction Type"]
    rows = [purchase("1", "A-DXB-2", 10, 34.9), ROW_1196, ROW_863]
    p = preview(rows, headers)
    assert p.suggested_mapping.get("transaction_type") is None
    assert (p.rows_fuel, p.rows_invalid, p.rows_non_fuel) == (1, 2, 0)


def test_preview_without_required_columns_reports_no_split():
    headers = [h for h in HEADERS if h != "Quantity"]
    p = preview([purchase("1", "A-DXB-2", 10, 34.9), ROW_863], headers)
    assert p.missing_required_fields == ["litres"]
    assert p.rows_fuel is None and p.rows_invalid is None and p.invalid_row_details is None


def test_import_record_counts_written_only_with_flag():
    rows = [purchase("1", "A-DXB-2", 10, 34.9), ROW_863, {}, ROW_1196]
    create = MagicMock(return_value=101)
    run_import(rows, create=create)
    assert "rows_ignored" not in create.call_args.kwargs and "rows_non_fuel" not in create.call_args.kwargs
    create = MagicMock(return_value=101)
    run_import(rows, env={"FUEL_IMPORT_IGNORED_COUNTS": "1"}, create=create)
    assert create.call_args.kwargs["rows_ignored"] == 3 and create.call_args.kwargs["rows_non_fuel"] == 2

    ok = MagicMock(ok=True, json=MagicMock(return_value=[{"id": 1}]))
    for flag, expect in (("", False), ("1", True)):
        with patch.dict(os.environ, {"FUEL_IMPORT_IGNORED_COUNTS": flag}), \
             patch("app.fuel.db.requests.post", return_value=ok) as post:
            fuel_db.create_fuel_import_record("f.xlsx", "", 4, rows_ignored=3, rows_non_fuel=2)
        payload = post.call_args.kwargs["json"]
        assert (("rows_ignored" in payload) and ("rows_non_fuel" in payload)) == expect, payload
        if expect:
            assert payload["rows_ignored"] == 3 and payload["rows_non_fuel"] == 2


LEGACY_RECORD = {"id": 17, "file_name": "Transactions_09-02-26_09-09.xlsx", "imported_by": None,
                 "status": "completed_with_errors", "rows_found": 2341, "rows_imported": 2335, "rows_duplicate": 0,
                 "rows_failed": 6, "date_from": "2026-08-03", "date_to": "2026-08-31", "error_message": None,
                 "created_at": "2026-10-20T08:00:00+00:00", "completed_at": "2026-10-20T08:03:00+00:00"}
NEW_RECORD = {**LEGACY_RECORD, "id": 18, "status": "completed", "rows_failed": 0, "rows_ignored": 6,
              "rows_non_fuel": 6}


def test_history_records_with_and_without_counts():
    legacy = FuelImportItem(**LEGACY_RECORD)
    assert legacy.rows_ignored is None and legacy.rows_non_fuel is None, "unrecorded counts stay null, not 0"
    new = FuelImportItem(**NEW_RECORD)
    assert new.rows_ignored == 6 and new.rows_non_fuel == 6
    client = TestClient(app)
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.routes.get_fuel_imports_list", return_value=([NEW_RECORD, LEGACY_RECORD], 2)):
        res = client.get("/api/fuel/imports", headers=AUTH)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["count"] == 2
    assert (body["imports"][0]["rows_ignored"], body["imports"][0]["rows_non_fuel"]) == (6, 6)
    assert (body["imports"][1]["rows_ignored"], body["imports"][1]["rows_non_fuel"]) == (None, None)


def _reconcile(record, stored_rows):
    rows = [{"source_row_number": i + 2, "transaction_date": "2026-08-10T08:00:00"} for i in range(stored_rows)]
    with patch.object(importer, "get_fuel_import_record", return_value=dict(record)), \
         patch.object(importer, "fetch_import_rows", return_value=rows), \
         patch.object(importer, "update_fuel_import_record", return_value=True) as update:
        out = reconcile_fuel_import(record["id"])
    return out, update.call_args.kwargs["updates"]


def test_reconcile_legacy_and_new_records():
    # Legacy record: unchanged behaviour, no new keys written, counts stay unrecorded
    out, updates = _reconcile(LEGACY_RECORD, 2335)
    assert "rows_ignored" not in updates and "rows_non_fuel" not in updates
    assert updates["rows_imported"] == 2335 and updates["rows_failed"] == 6 and out.get("rows_ignored") is None
    # New record with stored ignored counts: completed, counts kept
    out, updates = _reconcile(NEW_RECORD, 2335)
    assert updates["status"] == "completed" and updates["rows_failed"] == 0
    assert out["rows_ignored"] == 6 and out["rows_non_fuel"] == 6
    # Ignored rows are not mistaken for uncounted rows: 3 extra stored rows were wrongly recorded as failed
    record = {**NEW_RECORD, "rows_imported": 2329, "rows_failed": 6, "status": "completed_with_errors"}
    _, updates = _reconcile(record, 2332)
    assert updates["rows_failed"] == 3, updates
    # Without the stored count the 6 ignored rows absorb the 3 extra rows (older, looser estimate)
    legacy = {k: v for k, v in record.items() if k not in ("rows_ignored", "rows_non_fuel")}
    _, updates = _reconcile(legacy, 2332)
    assert updates["rows_failed"] == 6, updates


TESTS = [test_mapper_detects_transaction_type_without_moving_other_columns]
TESTS += [make_six_case_test(*case) for case in SIX_CASES]
TESTS += [test_all_six_together_with_purchases_account_for_every_row, test_purchase_missing_litres_still_fails,
          test_purchase_missing_vehicle_still_fails, test_charge_type_with_litres_is_validated_not_skipped,
          test_charge_type_with_unparseable_litres_still_fails,
          test_without_transaction_type_mapping_behaviour_is_unchanged, test_is_non_fuel_entry_rules,
          test_preview_counts_fuel_non_fuel_invalid_and_ignored, test_preview_and_import_classify_identically,
          test_preview_without_type_column_matches_import, test_preview_without_required_columns_reports_no_split,
          test_import_record_counts_written_only_with_flag, test_history_records_with_and_without_counts,
          test_reconcile_legacy_and_new_records]


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in TESTS:
            try:
                t()
                print(f"[PASS] {t.__name__}")
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    assert not guard.attempts, f"Live calls attempted: {guard.attempts}"
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    sys.exit(1 if failures else 0)
