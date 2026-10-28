"""
Offline tests for statements that repeat a column group (e.g. 'Litres | Amount' once per product): every repeated
column gets its own label, the preview asks which one is meant instead of silently taking one, and the import reads
exactly the chosen columns. Also: undated (monthly-summary) rows need a vehicle, litres and a total cost.

Every outbound HTTP call is blocked by OfflineGuard. Fixtures only; no production data is read or claimed.

Run from Backend/:  python scripts/test_fuel_import_repeated_columns.py
"""

import os
import sys
import traceback
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_import_replacement import USER, FakeReplaceDB, replace_env, xlsx  # noqa: E402

from app.fuel.importer import (  # noqa: E402
    FuelImportError, column_indexes, execute_fuel_import, generate_fuel_preview, label_repeated_headers,
)
from app.fuel.mapper import repeated_header_fields  # noqa: E402

TODAY = date(2026, 10, 27)
# Two product groups: Diesel (B, C) and Petrol (D, E), with the same titles
HEADERS = ["Vehicle", "Litres", "Amount", "Litres", "Amount"]
ROWS = [["AUH 44120", 100, 300.0, 5, 15.0], ["DXB 10293", 200, 600.0, 7, 21.0]]
LABELS = ["Vehicle", "Litres (col B)", "Amount (col C)", "Litres (col D)", "Amount (col E)"]
PETROL = {"vehicle_identifier": "Vehicle", "litres": "Litres (col D)", "total_cost": "Amount (col E)"}


def test_repeated_titles_get_column_labels():
    assert label_repeated_headers(HEADERS) == LABELS
    assert label_repeated_headers(["Vehicle", "Litres", "Amount"]) == ["Vehicle", "Litres", "Amount"], "unique: unchanged"
    assert label_repeated_headers(["Amount", "amount ", ""]) == ["Amount (col A)", "amount  (col B)", ""]
    # Each label now addresses its own column (before, both 'Amount' lookups resolved to the last one)
    assert column_indexes(LABELS, PETROL) == {"vehicle_identifier": 0, "litres": 3, "total_cost": 4}
    assert column_indexes(LABELS, {"litres": "Litres (col B)", "total_cost": "Amount (col C)"}) == {"litres": 1, "total_cost": 2}
    assert repeated_header_fields(LABELS, PETROL) == ["litres", "total_cost"]
    assert repeated_header_fields(["Vehicle", "Litres", "Amount"], {"litres": "Litres"}) == []
    print("[PASS] Repeated column titles get their column letter; each label reads its own column.")


def test_preview_asks_which_repeated_column():
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(HEADERS, ROWS), "groups.xlsx", reporting_month="2026-09", today=TODAY)
    assert p.detected_columns == LABELS, p.detected_columns
    assert {"litres", "total_cost"} <= set(p.mapping_confirmation_required), p.mapping_confirmation_required
    assert not p.ready_to_import and any("Confirm the column mapping" in b for b in p.blocking_reasons), p.blocking_reasons
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(HEADERS, ROWS), "groups.xlsx", mapping=PETROL, mapping_confirmed=True,
                                  reporting_month="2026-09", today=TODAY)
    # Still listed (the UI keeps marking them), but confirmed: nothing blocks the import
    assert p.ready_to_import and p.blocking_reasons == [], (p.blocking_reasons, p.mapping_confirmation_required)
    assert [(s["litres"], s["total_cost"]) for s in p.sample_rows] == [(5.0, 15.0), (7.0, 21.0)], p.sample_rows
    print("[PASS] Preview: repeated columns must be confirmed; the chosen group's values are shown.")


def test_import_reads_the_chosen_group_and_refuses_unconfirmed():
    db = FakeReplaceDB()
    try:
        with replace_env(db):
            execute_fuel_import(xlsx(HEADERS, ROWS), "groups.xlsx", PETROL, USER, reporting_month="2026-09")
        raise AssertionError("expected 422 for an unconfirmed repeated column")
    except FuelImportError as exc:
        assert exc.status_code == 422 and "ambiguous" in exc.message, exc.message
    assert not db.rows and not db.imports, "nothing written"
    with replace_env(db):
        res = execute_fuel_import(xlsx(HEADERS, ROWS), "groups.xlsx", PETROL, USER, reporting_month="2026-09",
                                  mapping_confirmed=True)
    assert res.committed and res.rows_imported == 2, res
    got = sorted((r["vehicle_identifier"], r["litres"], r["total_cost"], r["transaction_date"], r["date_basis"]) for r in db.rows)
    assert got == [("AUH 44120", 5.0, 15.0, "2026-09-30", "reporting_month_end"),
                   ("DXB 10293", 7.0, 21.0, "2026-09-30", "reporting_month_end")], got
    print("[PASS] Import: refused until confirmed; then stores exactly the chosen columns at the month end.")


def test_undated_rows_need_vehicle_litres_and_total():
    headers = ["Vehicle", "Litres", "Amount"]
    rows = [["AUH 44120", 100, 300.0], ["", 50, 150.0], ["DXB 10293", -5, 20.0], ["SHJ 1", "abc", 10.0], ["RAK 2", 40, None]]
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(headers, rows), "s.xlsx", reporting_month="2026-09", today=TODAY)
    assert p.rows_fuel == 1 and p.rows_invalid == 4, (p.rows_fuel, p.rows_invalid, p.invalid_row_details)
    reasons = " | ".join(d["reason"] for d in p.invalid_row_details)
    assert "total cost" in reasons.lower(), reasons
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(["Vehicle", "Litres", "Rate"], [["AUH 44120", 100, 3.0]]), "s.xlsx",
                                  reporting_month="2026-09", today=TODAY)
    assert not p.ready_to_import and "total_cost" in p.missing_required_fields, (p.missing_required_fields, p.blocking_reasons)
    print(f"[PASS] Undated rows: missing vehicle, invalid/negative litres and missing total cost are rejected ({reasons}).")


TESTS = [
    test_repeated_titles_get_column_labels,
    test_preview_asks_which_repeated_column,
    test_import_reads_the_chosen_group_and_refuses_unconfirmed,
    test_undated_rows_need_vehicle_litres_and_total,
]


def main():
    print("=" * 60)
    print("FUEL IMPORT - REPEATED COLUMN GROUPS (OFFLINE)")
    print("=" * 60)
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in TESTS:
            try:
                t()
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
    print("=" * 60)
    print(f"{len(TESTS) - failures}/{len(TESTS)} test functions passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
