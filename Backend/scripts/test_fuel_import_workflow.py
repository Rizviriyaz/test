"""
Offline end-to-end workflow for the fuel import (FUEL_IMPORT_REPLACE=1, in-memory FakeReplaceDB standing in for the two
Supabase tables and the commit function): realistic statement workbooks -> preview -> import -> stored rows ->
monthly totals and daily trend, including re-importing an existing period and a failed import.

  - dated statement: real Excel datetimes, several vehicles and days; each row keeps its own date
  - monthly summary (no date column): reporting month required, every row dated on the month's last day, marked
    reporting_month_end, never placed on a day in the daily trend
  - re-import: a corrected dated statement replaces only its vehicles' rows on its days; a re-imported summary replaces
    only its vehicles' month; totals never double count; vehicles absent from the new file are untouched
  - failure: an import whose rows are rejected leaves the previously visible data exactly as it was

Every outbound HTTP call is blocked by OfflineGuard. Fixtures only; no production data is read or claimed.

Run from Backend/:  python scripts/test_fuel_import_workflow.py
"""

import os
import sys
import traceback
from datetime import date, datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_import_replacement import USER, FakeReplaceDB, replace_env, xlsx  # noqa: E402

from app.fuel.analytics import calculate_fuel_summary, calculate_fuel_trend  # noqa: E402
from app.fuel.importer import execute_fuel_import, generate_fuel_preview  # noqa: E402

TODAY = date(2026, 10, 27)
# A daily statement as a fuel card provider exports it (date-time cells, unit price and amount)
DATED_HEADERS = ["Transaction Date", "Plate No", "Product", "Qty (Ltr)", "Unit Price", "Amount (AED)", "Invoice No"]
DATED_MAP = {"transaction_date": "Transaction Date", "vehicle_identifier": "Plate No", "fuel_type": "Product",
             "litres": "Qty (Ltr)", "unit_price": "Unit Price", "total_cost": "Amount (AED)", "invoice_number": "Invoice No"}
SEPT = [
    [datetime(2026, 9, 2, 7, 15), "AUH 44120", "Diesel", 120.0, 3.10, 372.0, "INV-0902-1"],
    [datetime(2026, 9, 2, 16, 40), "DXB 10293", "Diesel", 80.0, 3.10, 248.0, "INV-0902-2"],
    [datetime(2026, 9, 9, 8, 5), "AUH 44120", "Diesel", 110.0, 3.05, 335.5, "INV-0909-1"],
    [datetime(2026, 9, 9, 12, 0), "SHJ 88211", "Special 95", 45.0, 2.80, 126.0, "INV-0909-2"],
    [datetime(2026, 9, 21, 6, 30), "DXB 10293", "Diesel", 95.0, 3.00, 285.0, "INV-0921-1"],
]
# A monthly summary as a supplier sends it: one total per vehicle, no date column
SUMMARY_HEADERS = ["Vehicle", "Litres", "Amount"]
SUMMARY_MAP = {"vehicle_identifier": "Vehicle", "litres": "Litres", "total_cost": "Amount"}
AUGUST = [["AUH 44120", 2310.5, 7162.55], ["DXB 10293", 1840.0, 5704.0], ["SHJ 88211", 610.25, 1708.7]]


def dated_import(db, rows, name="sept.xlsx"):
    with replace_env(db):
        return execute_fuel_import(xlsx(DATED_HEADERS, rows), name, DATED_MAP, USER)


def summary_import(db, rows, month, name="aug_summary.xlsx"):
    with replace_env(db):
        return execute_fuel_import(xlsx(SUMMARY_HEADERS, rows), name, SUMMARY_MAP, USER, reporting_month=month)


def month_rows(db, month):
    return [r for r in db.visible() if str(r["transaction_date"]).startswith(month)]


def test_dated_and_summary_imports_store_the_right_dates_and_totals():
    db = FakeReplaceDB()
    with replace_env(db):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, AUGUST), "aug_summary.xlsx", today=TODAY)
    assert not p.ready_to_import and any("reporting month" in b for b in p.blocking_reasons), p.blocking_reasons

    res = dated_import(db, SEPT)
    assert res.committed and res.rows_imported == 5 and res.import_mode == "dated", res
    stored = sorted((str(r["transaction_date"])[:16], r["vehicle_identifier"]) for r in month_rows(db, "2026-09"))
    assert stored[0] == ("2026-09-02T07:15", "AUH 44120") and stored[-1] == ("2026-09-21T06:30", "DXB 10293"), stored
    assert all(r["date_basis"] == "transaction" for r in month_rows(db, "2026-09"))

    res = summary_import(db, AUGUST, "2026-08")
    assert res.committed and res.rows_imported == 3 and res.assigned_transaction_date == "2026-08-31", res
    aug = month_rows(db, "2026-08")
    assert {str(r["transaction_date"])[:10] for r in aug} == {"2026-08-31"}
    assert {r["date_basis"] for r in aug} == {"reporting_month_end"}

    sept_sum = calculate_fuel_summary(month_rows(db, "2026-09"))
    aug_sum = calculate_fuel_summary(month_rows(db, "2026-08"))
    assert round(sept_sum.total_litres, 2) == 450.0 and round(sept_sum.total_cost, 2) == 1366.5, sept_sum
    assert round(aug_sum.total_litres, 2) == 4760.75 and round(aug_sum.total_cost, 2) == 14575.25, aug_sum
    print("[PASS] Dated rows keep their own date-times; the summary is dated 2026-08-31 (reporting_month_end); "
          "monthly totals match the files.")


def test_daily_trend_shows_only_dated_activity():
    db = FakeReplaceDB()
    dated_import(db, SEPT)
    summary_import(db, AUGUST, "2026-08")
    daily = calculate_fuel_trend(db.visible(), "day")
    days = [p.period for p in daily.data]
    assert days == ["2026-09-02", "2026-09-09", "2026-09-21"], days
    assert "2026-08-31" not in days, "a monthly summary is never refuelling on its month-end date"
    assert (daily.monthly_summary_excluded or {}).get("rows") == 3, daily.monthly_summary_excluded
    monthly = {p.period: round(p.litres, 2) for p in calculate_fuel_trend(db.visible(), "month").data}
    assert monthly == {"2026-08": 4760.75, "2026-09": 450.0}, monthly
    print("[PASS] Daily trend: only dated days; the August summary is excluded from days but counted in its month.")


def test_reimports_replace_without_double_counting():
    db = FakeReplaceDB()
    dated_import(db, SEPT)
    summary_import(db, AUGUST, "2026-08")

    # Same files again: nothing new, nothing doubled
    again = dated_import(db, SEPT, "sept_again.xlsx")
    assert again.rows_imported == 0 and again.rows_replaced == 0, again
    assert round(calculate_fuel_summary(month_rows(db, "2026-09")).total_litres, 2) == 450.0

    # Corrected statement for 9 Sept only, AUH only: AUH's 9 Sept row replaced; its 2 Sept row, SHJ on 9 Sept and
    # every DXB row stay
    fixed = dated_import(db, [[datetime(2026, 9, 9, 8, 5), "AUH 44120", "Diesel", 100.0, 3.05, 305.0, "INV-0909-1R"]],
                         "sept_fix.xlsx")
    assert fixed.committed and fixed.rows_replaced == 1 and fixed.rows_imported == 1, fixed
    auh = sorted((str(r["transaction_date"])[:10], r["litres"]) for r in month_rows(db, "2026-09")
                 if r["vehicle_identifier"] == "AUH 44120")
    assert auh == [("2026-09-02", 120.0), ("2026-09-09", 100.0)], auh
    assert round(calculate_fuel_summary(month_rows(db, "2026-09")).total_litres, 2) == 440.0

    # Revised August summary for one vehicle: only that vehicle's August is replaced
    revised = summary_import(db, [["DXB 10293", 1900.0, 5890.0]], "2026-08", "aug_revised.xlsx")
    assert revised.committed and revised.rows_replaced == 1, revised
    aug = {r["vehicle_identifier"]: r["litres"] for r in month_rows(db, "2026-08")}
    assert aug == {"AUH 44120": 2310.5, "DXB 10293": 1900.0, "SHJ 88211": 610.25}, aug
    assert len([r for r in db.rows if r.get("superseded_by_import_id")]) == 2, "replaced rows are kept, only hidden"
    print("[PASS] Re-imports: identical files add nothing; a correction replaces only its vehicle-days / vehicle-month.")


def test_failed_import_leaves_visible_data_unchanged():
    db = FakeReplaceDB()
    dated_import(db, SEPT)
    summary_import(db, AUGUST, "2026-08")
    before = db.snapshot()
    db.insert_fail = True
    res = summary_import(db, [["AUH 44120", 9999.0, 30000.0]], "2026-08", "aug_bad.xlsx")
    assert res.status == "failed" and res.committed is False and res.rows_imported == 0, res
    assert db.snapshot() == before, "the old August summary is still the visible one"
    db.insert_fail = False
    db.commit_mode = "refuse"
    res = dated_import(db, [[datetime(2026, 9, 2, 7, 15), "AUH 44120", "Diesel", 1.0, 3.0, 3.0, "X"]], "sept_bad.xlsx")
    assert res.committed is False and db.snapshot() == before, res
    print("[PASS] A failed import (rows rejected, or commit refused) leaves the dashboard data exactly as before.")


def test_daily_and_summary_never_mix_for_a_vehicle_month():
    # Daily first, then a September summary for AUH 44120: its September daily rows are replaced (kept in the table)
    db = FakeReplaceDB()
    dated_import(db, SEPT)
    res = summary_import(db, [["AUH 44120", 900.0, 2790.0]], "2026-09", "sept_summary.xlsx")
    assert res.committed and res.rows_replaced == 2 and res.rows_imported == 1, res
    sept = sorted((r["vehicle_identifier"], r["date_basis"], r["litres"]) for r in month_rows(db, "2026-09"))
    assert sept == [("AUH 44120", "reporting_month_end", 900.0), ("DXB 10293", "transaction", 80.0),
                    ("DXB 10293", "transaction", 95.0), ("SHJ 88211", "transaction", 45.0)], sept
    assert len(db.rows) == 6, "superseded daily rows stay in the table for audit"

    # Summary first, then daily rows for that vehicle-month: the summary is kept and the daily rows are skipped,
    # while the file's other vehicle-month (September) is imported
    db = FakeReplaceDB()
    summary_import(db, AUGUST, "2026-08")
    aug_daily = [[datetime(2026, 8, 5, 9, 0), "DXB 10293", "Diesel", 60.0, 3.0, 180.0, "A-5"],
                 [datetime(2026, 9, 5, 9, 0), "DXB 10293", "Diesel", 70.0, 3.0, 210.0, "S-5"]]
    for extend in (False, True):
        with replace_env(db):
            res = execute_fuel_import(xlsx(DATED_HEADERS, aug_daily), "aug_daily.xlsx", DATED_MAP, USER,
                                      extend_to_full_months=extend)
        assert res.committed and res.rows_skipped_monthly_summary == 1 and res.rows_replaced == 0, res
    aug = sorted((r["vehicle_identifier"], r["date_basis"], r["litres"]) for r in month_rows(db, "2026-08"))
    assert aug == [("AUH 44120", "reporting_month_end", 2310.5), ("DXB 10293", "reporting_month_end", 1840.0),
                   ("SHJ 88211", "reporting_month_end", 610.25)], aug
    assert [r["litres"] for r in month_rows(db, "2026-09")] == [70.0]
    print("[PASS] Daily then summary: the summary replaces that vehicle-month; summary then daily: the summary is "
          "kept and only the file's other vehicle-months import.")


TESTS = [
    test_daily_and_summary_never_mix_for_a_vehicle_month,
    test_dated_and_summary_imports_store_the_right_dates_and_totals,
    test_daily_trend_shows_only_dated_activity,
    test_reimports_replace_without_double_counting,
    test_failed_import_leaves_visible_data_unchanged,
]


def main():
    print("=" * 60)
    print("FUEL IMPORT - END-TO-END WORKFLOW (OFFLINE)")
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
