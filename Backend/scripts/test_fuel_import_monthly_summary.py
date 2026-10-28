"""
Offline tests for statements without a transaction-date column (monthly summaries): reporting-month validation,
month-end assignment, the preview flow (month selector, warnings, ready/blocking), the end-to-end import with
FUEL_IMPORT_REPLACE=1 (in-memory FakeReplaceDB from test_fuel_import_replacement), and refusal while the flag is off.

Every outbound HTTP call is blocked by OfflineGuard. Fixtures only; no production data is read or claimed.

Run from Backend/:  python scripts/test_fuel_import_monthly_summary.py
"""

import os
import sys
import traceback
from datetime import date, datetime
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_import_replacement import SUMMARY_HEADERS, SUMMARY_MAP, USER, FakeReplaceDB, replace_env, xlsx  # noqa: E402

from app.fuel.importer import FuelImportError, execute_fuel_import, generate_fuel_preview  # noqa: E402
from app.fuel.reporting_period import is_partial_month, parse_reporting_month  # noqa: E402
from app.utils.datetime_helpers import UAE_TZ  # noqa: E402

TODAY = date(2026, 10, 27)
ROWS = [["AUH 44120", 512.5, 1588.75], ["DXB 10293", 300, 930.0], ["SHJ 88211", 120, None]]  # last: no total cost


def test_month_end_dates():
    cases = {"2024-02": "2024-02-29", "2025-02": "2025-02-28", "2026-04": "2026-04-30", "2025-12": "2025-12-31",
             "2026-01": "2026-01-31"}
    for month, last in cases.items():
        first, end = parse_reporting_month(month, TODAY)
        assert end.isoformat() == last and first.isoformat() == f"{month}-01", (month, first, end)
    first, end = parse_reporting_month("2026-12", date(2026, 12, 5))
    assert end == date(2026, 12, 31) and is_partial_month(end, date(2026, 12, 5))
    assert not is_partial_month(date(2026, 9, 30), TODAY)
    print("[PASS] Month end: leap and non-leap February, April, December; current month flagged partial.")


def test_invalid_and_future_months():
    for bad in ("2026-13", "2026-00", "26-08", "2026-8", "2026/08", "", "abcd-ef", "1999-12", None, "2026-08-15"):
        try:
            parse_reporting_month(bad, TODAY)
            raise AssertionError(f"accepted {bad!r}")
        except ValueError:
            pass
    try:
        parse_reporting_month("2026-11", TODAY)
        raise AssertionError("future month accepted")
    except ValueError as exc:
        assert "future" in str(exc)
    assert parse_reporting_month("2026-10", TODAY)[1] == date(2026, 10, 31)  # current month: allowed (decision 3)
    print("[PASS] Malformed months and future months are rejected; the current month is accepted.")


def test_preview_without_month_asks_for_it():
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", today=TODAY)
    assert p.import_mode is None and p.monthly_summary_enabled and not p.ready_to_import, p
    assert "transaction_date" in p.missing_required_fields and p.rows_fuel is None
    assert any("select the reporting month" in b for b in p.blocking_reasons), p.blocking_reasons
    assert p.date_column_candidates == [] and "transaction_date" not in p.mapping_confirmation_required
    print("[PASS] Undated statement without a month: preview asks for the reporting month; import not ready.")


def test_import_without_month_is_refused():
    db = FakeReplaceDB()
    try:
        with replace_env(db):
            execute_fuel_import(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", SUMMARY_MAP, USER)
        raise AssertionError("expected 422")
    except FuelImportError as exc:
        assert exc.status_code == 422 and "transaction_date" in exc.message
    assert not db.rows and not db.imports
    print("[PASS] Undated import without a reporting month is refused before anything is written.")


def test_preview_with_month_assigns_month_end_and_warns():
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", reporting_month="2026-02", today=TODAY)
    assert p.import_mode == "monthly_summary" and p.reporting_month == "2026-02", p
    assert p.assigned_transaction_date == "2026-02-28" and not p.partial_month
    assert (p.rows_fuel, p.rows_invalid) == (2, 1) and p.missing_required_fields == [], p
    assert all(s["transaction_date"] == "2026-02-28" and s["date_basis"] == "reporting_month_end" for s in p.sample_rows)
    assert p.invalid_row_details[0]["reason"].startswith("Missing total cost (AED)")
    assert any("not when the vehicle refuelled" in w for w in p.validation_warnings), p.validation_warnings
    assert p.ready_to_import and p.replacement["period_from"] == "2026-02-01" and p.replacement["period_to"] == "2026-02-28"
    print("[PASS] Preview with a month: month-end reporting date on every row, monthly-summary warning, ready.")


def test_current_month_preview_warns_partial():
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", reporting_month="2026-10", today=TODAY)
    assert p.partial_month and p.assigned_transaction_date == "2026-10-31" and p.ready_to_import, p
    assert any("has not ended yet" in w for w in p.validation_warnings), p.validation_warnings
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", reporting_month="2026-11", today=TODAY)
    assert not p.ready_to_import and any("future" in b for b in p.blocking_reasons), p.blocking_reasons
    print("[PASS] Current month: allowed with a partial-month warning; a future month blocks the preview.")


def test_import_with_month_end_to_end():
    db = FakeReplaceDB()
    with replace_env(db):
        res = execute_fuel_import(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", SUMMARY_MAP, USER, reporting_month="2026-02")
    assert res.committed and res.import_mode == "monthly_summary" and res.assigned_transaction_date == "2026-02-28", res
    assert (res.rows_imported, res.rows_failed) == (2, 1) and res.date_from == res.date_to == "2026-02-28", res
    assert all(r["transaction_date"] == "2026-02-28" and r["date_basis"] == "reporting_month_end" and r["staged"] is False
               for r in db.rows), db.rows
    auh = next(r for r in db.rows if r["vehicle_identifier"] == "AUH 44120")
    assert auh["total_cost"] == 1588.75 and auh["unit_price"] == 3.1 and auh["litres"] == 512.5
    rec = db.imports[res.import_id]
    assert rec["import_mode"] == "monthly_summary" and rec["reporting_month"] == "2026-02-01" and rec["status"] == "completed_with_errors"
    assert any("not when the vehicle refuelled" in w for w in res.warnings)
    print("[PASS] Monthly-summary import: rows stored with the month-end reporting date and date_basis, then committed.")


def test_current_month_import_uses_uae_today():
    db = FakeReplaceDB()
    now = datetime(2026, 10, 27, 9, 0, tzinfo=UAE_TZ)
    with replace_env(db), patch("app.fuel.reporting_period.get_uae_now", return_value=now):
        res = execute_fuel_import(xlsx(SUMMARY_HEADERS, ROWS[:2]), "s.xlsx", SUMMARY_MAP, USER, reporting_month="2026-10")
        assert res.committed and any("has not ended yet" in w for w in res.warnings), res.warnings
        try:
            execute_fuel_import(xlsx(SUMMARY_HEADERS, ROWS[:2]), "s.xlsx", SUMMARY_MAP, USER, reporting_month="2026-11")
            raise AssertionError("future month imported")
        except FuelImportError as exc:
            assert exc.status_code == 422 and "future" in exc.message
    print("[PASS] Import: the current month is accepted with a warning; a future month is refused.")


def test_monthly_summary_refused_while_flag_off():
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": ""}):
        for month in ("2026-02", None):
            p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, ROWS), "summary.xlsx", reporting_month=month, today=TODAY)
            assert not p.monthly_summary_enabled and not p.ready_to_import, p
            assert any("migration" in b for b in p.blocking_reasons), (month, p.blocking_reasons)
        try:
            execute_fuel_import(xlsx(SUMMARY_HEADERS, ROWS), "s.xlsx", SUMMARY_MAP, USER, reporting_month="2026-02")
            raise AssertionError("expected 422")
        except FuelImportError as exc:
            assert exc.status_code == 422 and "monthly summaries" in exc.message
    print("[PASS] Without FUEL_IMPORT_REPLACE, monthly summaries are refused (they could not be marked as summaries).")


def test_dated_file_needs_no_month():
    headers = ["Date", "Vehicle", "Litres", "Amount"]
    rows = [["2026-02-03", "AUH 44120", 50, 155.0]]
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(headers, rows), "dated.xlsx", today=TODAY)
        assert p.import_mode == "dated" and p.ready_to_import and p.assigned_transaction_date is None, p.blocking_reasons
        p = generate_fuel_preview(xlsx(headers, rows), "dated.xlsx", reporting_month="2026-02", today=TODAY)
        assert not p.ready_to_import and any("date column" in b for b in p.blocking_reasons), p.blocking_reasons
    print("[PASS] A mapped date column needs no month; sending one anyway is refused rather than guessed.")


def test_user_can_declare_no_date_column():
    # A header was mapped as the date but the statement has no transaction dates: the user maps it to none
    headers = ["Vehicle", "Statement Date", "Litres", "Amount"]
    rows = [["AUH 44120", "2026-02-28", 500, 1550.0]]
    with replace_env(FakeReplaceDB()):
        p = generate_fuel_preview(xlsx(headers, rows), "s.xlsx", today=TODAY)
        assert p.active_mapping["transaction_date"] == "Statement Date" and "transaction_date" in p.mapping_confirmation_required
        mapping = {**p.active_mapping, "transaction_date": None}
        p = generate_fuel_preview(xlsx(headers, rows), "s.xlsx", mapping=mapping, reporting_month="2026-02",
                                  mapping_confirmed=True, today=TODAY)
    assert p.import_mode == "monthly_summary" and p.ready_to_import and p.assigned_transaction_date == "2026-02-28", p
    print("[PASS] The user can set the date column to none and import the statement as a monthly summary.")


TESTS = [
    test_month_end_dates,
    test_invalid_and_future_months,
    test_preview_without_month_asks_for_it,
    test_import_without_month_is_refused,
    test_preview_with_month_assigns_month_end_and_warns,
    test_current_month_preview_warns_partial,
    test_import_with_month_end_to_end,
    test_current_month_import_uses_uae_today,
    test_monthly_summary_refused_while_flag_off,
    test_dated_file_needs_no_month,
    test_user_can_declare_no_date_column,
]


def main():
    print("=" * 60)
    print("FUEL IMPORT - MONTHLY SUMMARIES (OFFLINE)")
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
