"""
Offline regression tests for monthly-summary vs daily-transaction conflicts in the fuel import (FUEL_IMPORT_REPLACE=1).

Rules under test (per vehicle and reporting month, never for the whole file or fleet):
  - a stored monthly summary is kept: a daily file's rows for that vehicle-month are skipped, its other
    vehicle-months are imported;
  - a monthly summary replaces the stored daily transactions of its vehicle-month (superseded, kept for audit);
  - other months and vehicles are never touched; monthly totals and daily rows never count together;
  - a statement whose rows all carry one identical date is a monthly summary; its month is asked unless the date is a
    month end, and 'dated' is the user's explicit override;
  - a statement without a date column needs a reporting month and the total cost;
  - re-uploads insert nothing; failures leave the visible data unchanged; a concurrent import is refused.

Uses FakeReplaceDB from test_fuel_import_replacement (in-memory fuel_transactions / fuel_imports and the checks of
public.fuel_commit_import). Every outbound HTTP call is blocked by OfflineGuard. These tests prove the logic on
fixtures; they do not prove production data accuracy or the SQL function itself.

Run from Backend/:  python scripts/test_fuel_import_conflicts.py
"""

import os
import sys
import traceback
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_import_replacement import (  # noqa: E402
    DATED_HEADERS, DATED_MAP, SUMMARY_HEADERS, SUMMARY_MAP, USER, FakeReplaceDB, aug, dated, replace_env, summary, xlsx,
)

from app.fuel.importer import FuelImportError, execute_fuel_import, generate_fuel_preview  # noqa: E402


def row(day, vehicle, litres, inv, rate=3.0, hour=9):
    """Dated statement row on an ISO day ('2026-07-14')."""
    return [f"{day} {hour:02d}:00:00", vehicle, litres, rate, round(litres * rate, 2), inv]


def preview(db, headers, rows, **kwargs):
    with replace_env(db):
        return generate_fuel_preview(xlsx(headers, rows), "p.xlsx", **kwargs)


def refused(fn, status):
    try:
        fn()
    except FuelImportError as exc:
        assert exc.status_code == status, (exc.status_code, exc.message)
        return exc.message
    raise AssertionError(f"expected HTTP {status}")


def accounted(res):
    return (res.rows_imported + res.rows_duplicate + res.rows_failed + res.rows_ignored + res.rows_unverified
            + res.rows_not_processed + res.rows_skipped_monthly_summary)


def vm(db, vehicle, month):
    """Visible (litres, cost, rows) and the date bases of one vehicle-month."""
    rows = [r for r in db.visible() if r["vehicle_identifier"] == vehicle and str(r["transaction_date"]).startswith(month)]
    return db.totals(vehicle, month), sorted({r.get("date_basis") or "transaction" for r in rows})


# ─── 1. Same file uploaded twice ───────────────────────────────────────────────

def test_same_file_uploaded_twice():
    db = FakeReplaceDB()
    daily = [aug(d, v, 40 + d, f"{v}-{d}") for d in (4, 19) for v in ("AUH 44120", "DXB 10293")]
    monthly = [["SHJ 88211", 610.25, 1708.7], ["RAK 55001", 300.0, 930.0]]
    first_d, first_m = dated(db, daily), summary(db, monthly, "2026-07")
    rows, snap = len(db.rows), db.snapshot()
    again_d, again_m = dated(db, daily), summary(db, monthly, "2026-07")
    assert (first_d.rows_imported, first_m.rows_imported) == (4, 2), (first_d, first_m)
    for res, n in ((again_d, 4), (again_m, 2)):
        assert res.committed and res.rows_imported == 0 and res.rows_unchanged == n and res.rows_replaced == 0, res
        assert res.status == "completed", res
    assert len(db.rows) == rows and db.snapshot() == snap, "nothing inserted or superseded by the re-uploads"
    # With FUEL_SCHEMA_V2 an identical, completed file is refused by its hash before anything is written
    with replace_env(db, v2=True), patch("app.fuel.importer.find_import_by_sha256", return_value=first_d.import_id):
        msg = refused(lambda: execute_fuel_import(xlsx(DATED_HEADERS, daily), "d.xlsx", DATED_MAP, USER), 409)
    assert f"already imported as #{first_d.import_id}" in msg and len(db.rows) == rows
    print("[PASS] Same daily and monthly file uploaded twice: nothing inserted again; with V2 the hash refuses it.")


# ─── 2. Monthly summary first, then daily transactions ─────────────────────────

def test_monthly_then_daily_keeps_the_summary():
    db = FakeReplaceDB()
    summary(db, [["AUH 44120", 500, 1500.0]], "2026-08")
    summary_row = dict(db.visible()[0])
    daily = [aug(3, "AUH 44120", 50, "a3"), aug(14, "AUH 44120", 40, "a14"), aug(14, "DXB 10293", 30, "d14")]
    p = preview(db, DATED_HEADERS, daily)
    assert p.ready_to_import and p.rows_skipped_monthly_summary == 2, (p.blocking_reasons, p)
    scope = p.monthly_summary_scopes_kept
    assert scope == [{"vehicle_identifier": "AUH 44120", "month": "2026-08", "summary_import_ids": [summary_row["import_id"]],
                      "summary_litres": 500.0, "summary_cost": 1500.0, "rows_skipped": 2, "skipped_source_rows": [2, 3]}], scope
    assert any("monthly summary is already stored for AUH 44120 2026-08" in w for w in p.validation_warnings)
    res = dated(db, daily, expected_replace_count=p.replacement["expected_replace_count"])
    assert res.committed and res.rows_imported == 1 and res.rows_skipped_monthly_summary == 2, res
    assert res.rows_found == accounted(res) and res.status == "completed", res
    assert "monthly summary is already stored" in res.error_message, "the skip is recorded in import history"
    assert db.imports[res.import_id]["error_message"] == res.error_message
    assert vm(db, "AUH 44120", "2026-08") == ((500, 1500.0, 1), ["reporting_month_end"]), "only the summary counts"
    kept = next(r for r in db.rows if r["id"] == summary_row["id"])
    assert kept.get("superseded_by_import_id") is None and kept["litres"] == 500, "the summary row itself is untouched"
    assert not any(r["vehicle_identifier"] == "AUH 44120" and r.get("date_basis") == "transaction" for r in db.rows), \
        "no conflicting daily row was inserted (not even hidden)"
    assert vm(db, "DXB 10293", "2026-08") == ((30, 90.0, 1), ["transaction"])
    print("[PASS] Monthly then daily: the summary is kept, conflicting daily rows are not inserted, others import.")


# ─── 3. Daily transactions first, then a monthly summary ───────────────────────

def test_daily_then_monthly_replaces_the_daily_rows():
    db = FakeReplaceDB()
    first = dated(db, [aug(3, "AUH 44120", 50, "a3"), aug(20, "AUH 44120", 45, "a20"), aug(9, "DXB 10293", 30, "d9")])
    daily_ids = sorted(r["id"] for r in db.rows if r["vehicle_identifier"] == "AUH 44120")
    p = preview(db, SUMMARY_HEADERS, [["AUH 44120", 520, 1560.0]], reporting_month="2026-08")
    assert p.ready_to_import and p.replacement["rows_to_replace"] == 2 and not p.replacement["partial_month_conflicts"]
    assert sorted(r["id"] for r in p.replacement["records"]) == daily_ids, p.replacement["records"]
    res = summary(db, [["AUH 44120", 520, 1560.0]], "2026-08", expected_replace_count=2)
    assert res.committed and res.rows_replaced == 2 and res.rows_imported == 1, res
    assert db.commit_calls[-1] == (res.import_id, daily_ids, 1), "one atomic commit: supersede + make visible"
    assert vm(db, "AUH 44120", "2026-08") == ((520, 1560.0, 1), ["reporting_month_end"]), "never counted together"
    assert vm(db, "DXB 10293", "2026-08") == ((30, 90.0, 1), ["transaction"]), "other vehicle untouched"
    audit = [r for r in db.rows if r["id"] in daily_ids]
    assert all(r["superseded_by_import_id"] == res.import_id for r in audit) and len(db.rows) == 4, \
        "daily rows are hidden from reporting, not deleted"
    assert db.imports[first.import_id]["status"] == "completed", "the earlier import's history is kept"
    print("[PASS] Daily then monthly: the vehicle-month's daily rows are superseded (kept for audit) by the summary.")


# ─── 4. Several vehicles, conflicting and non-conflicting scopes ───────────────

def test_mixed_vehicles_and_months_are_processed_independently():
    db = FakeReplaceDB()
    summary(db, [["V1", 100, 300.0]], "2026-07")
    summary(db, [["V2", 200, 600.0]], "2026-08")
    daily = [row("2026-07-05", "V1", 10, "1a"), row("2026-08-05", "V1", 11, "1b"),     # V1: Jul skipped, Aug ok
             row("2026-07-06", "V2", 12, "2a"), row("2026-08-06", "V2", 13, "2b"),     # V2: Jul ok, Aug skipped
             row("2026-07-07", "V3", 14, "3a"), row("2026-08-07", "V3", 15, "3b")]     # V3: no summaries
    res = dated(db, daily)
    assert res.committed and res.rows_imported == 4 and res.rows_skipped_monthly_summary == 2, res
    assert res.rows_found == accounted(res)
    assert [(g["vehicle_identifier"], g["month"]) for g in res.monthly_summary_scopes_kept] == [("V1", "2026-07"), ("V2", "2026-08")]
    expected = {("V1", "2026-07"): (100, "reporting_month_end"), ("V1", "2026-08"): (11, "transaction"),
                ("V2", "2026-07"): (12, "transaction"), ("V2", "2026-08"): (200, "reporting_month_end"),
                ("V3", "2026-07"): (14, "transaction"), ("V3", "2026-08"): (15, "transaction")}
    for (v, m), (litres, basis) in expected.items():
        assert vm(db, v, m) == ((litres, db.totals(v, m)[1], 1), [basis]), (v, m, vm(db, v, m))
    # An August summary for V2 and V3: V3's August daily row is replaced, V2's earlier summary is replaced,
    # V1 (not in the file) and every July row stay exactly as they are
    before_july = sorted(r["id"] for r in db.visible() if str(r["transaction_date"]).startswith("2026-07"))
    res2 = summary(db, [["V2", 210, 630.0], ["V3", 90, 270.0]], "2026-08")
    assert res2.committed and res2.rows_replaced == 2 and res2.rows_imported == 2, res2
    assert vm(db, "V2", "2026-08")[0] == (210, 630.0, 1) and vm(db, "V3", "2026-08")[0] == (90, 270.0, 1)
    assert vm(db, "V1", "2026-08") == ((11, 33.0, 1), ["transaction"]), "V1 is not in the file: untouched"
    assert sorted(r["id"] for r in db.visible() if str(r["transaction_date"]).startswith("2026-07")) == before_july
    print("[PASS] Mixed vehicles and months: each vehicle-month resolved on its own; non-conflicting scopes import.")


# ─── 5. Other months must remain untouched ─────────────────────────────────────

def test_other_months_remain_untouched():
    db = FakeReplaceDB()
    dated(db, [row("2026-07-31", "AUH 44120", 21, "j"), row("2026-08-01", "AUH 44120", 22, "a1"),
               row("2026-08-31", "AUH 44120", 23, "a31", hour=23), row("2026-09-01", "AUH 44120", 24, "s", hour=0)])
    outside = {r["id"]: dict(r) for r in db.rows if not str(r["transaction_date"]).startswith("2026-08")}
    res = summary(db, [["AUH 44120", 400, 1200.0]], "2026-08")
    assert res.committed and res.rows_replaced == 2, res
    for rid, r in outside.items():
        now = next(x for x in db.rows if x["id"] == rid)
        assert now == r and now.get("superseded_by_import_id") is None, ("month boundary row changed", r)
    assert db.totals("AUH 44120") == (400 + 21 + 24, 1200.0 + 63.0 + 72.0, 3)
    print("[PASS] A monthly summary changes only its own month: the last day before and first day after stay.")


# ─── 6. Files without dates ────────────────────────────────────────────────────

def test_files_without_dates():
    db = FakeReplaceDB()
    rows = [["AUH 44120", 500, 1500.0], ["DXB 10293", 300, 900.0]]
    p = preview(db, SUMMARY_HEADERS, rows)
    assert not p.ready_to_import and p.import_mode is None
    assert any("select the reporting month" in b for b in p.blocking_reasons), p.blocking_reasons
    with replace_env(db):
        refused(lambda: execute_fuel_import(xlsx(SUMMARY_HEADERS, rows), "s.xlsx", SUMMARY_MAP, USER), 422)
        # Total cost is required for an undated monthly summary: unmapped -> refused; missing in a row -> that row fails
        no_cost = {"vehicle_identifier": "Vehicle", "litres": "Litres"}
        msg = refused(lambda: execute_fuel_import(xlsx(SUMMARY_HEADERS, rows), "s.xlsx", no_cost, USER,
                                                  reporting_month="2026-08"), 422)
        assert "total_cost" in msg, msg
    assert not db.rows and not db.imports, "refused before any record or row was written"
    res = summary(db, rows + [["SHJ 88211", 200, None]], "2026-08")
    assert res.rows_imported == 2 and res.rows_failed == 1 and "total cost" in res.failed_row_details[0]["reason"].lower()
    assert sorted((r["vehicle_identifier"], str(r["transaction_date"]), r["date_basis"]) for r in db.rows) == [
        ("AUH 44120", "2026-08-31", "reporting_month_end"), ("DXB 10293", "2026-08-31", "reporting_month_end")], \
        "one month-end row per statement row: no daily rows invented, totals not spread over days"
    print("[PASS] Undated statements need a reporting month and total cost; rows are stored as month totals only.")


# ─── 7. Identical dates: automatically a monthly summary ───────────────────────

SAME_DAY = [["2026-08-31", "AUH 44120", 500, 3.0, 1500.0, None], ["2026-08-31", "DXB 10293", 300, 3.0, 900.0, None],
            ["2026-08-31", "SHJ 88211", 200, 3.0, 600.0, None]]


def test_identical_month_end_date_is_automatically_a_monthly_summary():
    # Every row on Aug 31 (a month end): a monthly summary of August, no question asked, replacing August daily rows
    db = FakeReplaceDB()
    dated(db, [aug(10, "AUH 44120", 60, "d10"), aug(10, "RAK 55001", 10, "r10", hour=14)])
    p = preview(db, DATED_HEADERS, SAME_DAY)
    assert p.ready_to_import and not p.single_date_confirmation_required, p.blocking_reasons
    assert p.import_mode == "monthly_summary" and p.reporting_month == "2026-08" and p.single_date == "2026-08-31"
    assert p.single_date_mode == "monthly_summary" and p.replacement["rows_to_replace"] == 1
    assert all(s["date_basis"] == "reporting_month_end" for s in p.sample_rows)
    res = dated(db, SAME_DAY, expected_replace_count=1)
    assert res.committed and res.import_mode == "monthly_summary" and res.rows_imported == 3 and res.rows_replaced == 1
    assert vm(db, "AUH 44120", "2026-08") == ((500, 1500.0, 1), ["reporting_month_end"])
    assert vm(db, "RAK 55001", "2026-08") == ((10, 30.0, 1), ["transaction"]), "vehicle not in the summary: untouched"
    assert db.imports[res.import_id]["import_mode"] == "monthly_summary"
    assert db.imports[res.import_id]["reporting_month"] == "2026-08-01"
    # Different times on one day are real refuel timestamps, and a single row gives no evidence: stays dated
    for rows in ([row("2026-08-31", "AUH 44120", 50, "x", hour=7), row("2026-08-31", "DXB 10293", 30, "y", hour=15)],
                 [SAME_DAY[0]]):
        p = preview(FakeReplaceDB(), DATED_HEADERS, rows)
        assert p.import_mode == "dated" and not p.single_date_confirmation_required, p
    print("[PASS] Every row on one month-end date: automatically a monthly summary of that month.")


def test_identical_non_month_end_date_needs_the_reporting_month():
    db = FakeReplaceDB()
    sept_first = [["2026-09-01", v, l, 3.0, c, None] for _, v, l, _, c, _ in SAME_DAY]
    p = preview(db, DATED_HEADERS, sept_first)
    assert not p.ready_to_import and p.single_date_confirmation_required and p.single_date == "2026-09-01", p
    assert p.single_date_mode == "monthly_summary" and p.replacement is None, "nothing is planned before the month"
    with replace_env(db):
        msg = refused(lambda: execute_fuel_import(xlsx(DATED_HEADERS, sept_first), "s.xlsx", DATED_MAP, USER), 422)
        assert "same date (2026-09-01)" in msg and "Nothing was imported" in msg, msg
        refused(lambda: execute_fuel_import(xlsx(DATED_HEADERS, sept_first), "s.xlsx", DATED_MAP, USER,
                                            single_date_mode="weekly"), 422)
        refused(lambda: execute_fuel_import(xlsx(DATED_HEADERS, sept_first), "s.xlsx", DATED_MAP, USER,
                                            single_date_mode="dated", reporting_month="2026-08"), 422)
    assert not db.rows and not db.imports, "refused before anything was written"
    # The user confirms the month the statement reports (here the month before its print date)
    res = dated(db, sept_first, reporting_month="2026-08")
    assert res.reporting_month == "2026-08" and {str(r["transaction_date"]) for r in db.rows} == {"2026-08-31"}
    # The user's explicit override: these are individual refuels on that day
    daily_db = FakeReplaceDB()
    res = dated(daily_db, sept_first, single_date_mode="dated")
    assert res.import_mode == "dated" and res.rows_imported == 3 and {r["date_basis"] for r in daily_db.rows} == {"transaction"}
    print("[PASS] One identical non-month-end date: the reporting month is asked, never guessed; 'dated' overrides.")


def test_same_date_summary_needs_every_total_cost_and_the_replace_feature():
    no_cost = [["2026-08-31", v, l, 3.0, None, None] for _, v, l, _, _, _ in SAME_DAY]
    db = FakeReplaceDB()
    p = preview(db, DATED_HEADERS, no_cost)
    assert not p.ready_to_import and any("no total cost" in b for b in p.blocking_reasons), p.blocking_reasons
    with replace_env(db):
        msg = refused(lambda: execute_fuel_import(xlsx(DATED_HEADERS, no_cost), "s.xlsx", DATED_MAP, USER), 422)
    assert "never derived from a unit price" in msg and not db.rows, msg
    # Without FUEL_IMPORT_REPLACE a monthly summary cannot be stored: refused (the user may still choose 'dated')
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": ""}):
        msg = refused(lambda: generate_fuel_preview(xlsx(DATED_HEADERS, SAME_DAY), "s.xlsx").blocking_reasons
                      and execute_fuel_import(xlsx(DATED_HEADERS, SAME_DAY), "s.xlsx", DATED_MAP, USER), 422)
        assert "monthly summaries" in msg.lower(), msg
    print("[PASS] A same-date summary needs each row's total cost (no price assumed) and the replacement feature.")


# ─── 8. Import failure and rollback ────────────────────────────────────────────

def test_failure_rolls_back_and_keeps_existing_data():
    for mode in ("insert_fail", "refuse", "unknown_not_committed", "read_fail", "count_changed"):
        db = FakeReplaceDB()
        dated(db, [aug(3, "AUH 44120", 50, "a3"), aug(20, "AUH 44120", 45, "a20")])
        summary(db, [["DXB 10293", 300, 900.0]], "2026-08")
        before, stored = db.snapshot(), len(db.rows)
        db.insert_fail = mode == "insert_fail"
        db.commit_mode = mode if mode in ("refuse", "unknown_not_committed") else "ok"
        db.visible_read_fails = mode == "read_fail"
        try:
            res = summary(db, [["AUH 44120", 520, 1560.0]], "2026-08",
                          expected_replace_count=9 if mode == "count_changed" else None)
        except FuelImportError as exc:
            assert mode in ("read_fail", "count_changed") and exc.status_code == (503 if mode == "read_fail" else 409)
            assert len(db.rows) == stored, "refused before any row was written"
        else:
            assert res.status == "failed" and res.committed is False and res.rows_replaced == 0, (mode, res)
        assert db.snapshot() == before, f"{mode}: the visible daily rows and summary must stay as they were"
        assert db.imports[max(db.imports)]["status"] == "failed", f"{mode}: failed import kept in history"
        assert all(r.get("staged") is True for r in db.rows if r["import_id"] == max(db.imports)), "new rows hidden"
    print("[PASS] Insert failure, refused/unanswered commit, unreadable scope, changed scope: nothing visible changes.")


# ─── 9. Partial-month daily files and duplicate transaction detection ──────────

def test_partial_month_daily_files_and_duplicates():
    db = FakeReplaceDB()
    first_half = [aug(d, "AUH 44120", 30 + d, f"h1-{d}") for d in range(1, 16)]
    dated(db, first_half)
    # A later partial statement for Aug 10-20: replaces AUH's rows only on the days it contains, adds new days
    later = [aug(10, "AUH 44120", 40, "h1-10"), aug(12, "AUH 44120", 99, "fix-12")] + \
            [aug(d, "AUH 44120", 30 + d, f"h2-{d}") for d in range(16, 21)]
    res = dated(db, later)
    assert (res.rows_unchanged, res.rows_replaced, res.rows_imported) == (1, 1, 6), res
    days = {int(str(r["transaction_date"])[8:10]): r["litres"] for r in db.visible()}
    assert sorted(days) == list(range(1, 21)) and days[12] == 99 and days[10] == 40 and days[11] == 41, days
    # Two refuels on one day with the same litres but different invoices are two transactions, not duplicates
    db = FakeReplaceDB()
    twins = [aug(5, "DXB 10293", 50, "INV-A", hour=8), aug(5, "DXB 10293", 50, "INV-B", hour=8)]
    res = dated(db, twins, single_date_mode="dated")  # one identical timestamp: the user confirms real refuels
    assert res.rows_imported == 2 and res.rows_duplicate == 0 and res.rows_identical_in_file == 0, res
    again = dated(db, twins, single_date_mode="dated")
    assert again.rows_imported == 0 and again.rows_unchanged == 2 and db.totals() == (100, 300.0, 2), again
    # Legacy dated path (FUEL_IMPORT_REPLACE_DATED=0): the fingerprint duplicate check still applies, and summarised
    # vehicle-months are still skipped
    db = FakeReplaceDB()
    summary(db, [["AUH 44120", 500, 1500.0]], "2026-08")
    stored_fps = set()

    def insert_legacy(transactions, **kw):
        for t in transactions:
            stored_fps.add("|".join(str(x) for x in (t["vehicle_identifier"], t["transaction_date"], t["litres"])))
        return db.insert(transactions, **kw)

    with replace_env(db, dated_replace=False), \
            patch("app.fuel.importer.fetch_existing_transaction_fingerprints", return_value=set()), \
            patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=insert_legacy):
        res = execute_fuel_import(xlsx(DATED_HEADERS, [aug(5, "AUH 44120", 50, "x"), aug(5, "DXB 10293", 30, "y", hour=15)]),
                                  "l.xlsx", DATED_MAP, USER)
    assert res.committed is None and res.rows_imported == 1 and res.rows_skipped_monthly_summary == 1, res
    assert vm(db, "AUH 44120", "2026-08") == ((500, 1500.0, 1), ["reporting_month_end"])
    assert len(stored_fps) == 1 and all(fp.startswith("DXB") for fp in stored_fps)
    print("[PASS] Partial-month files replace only their days; same-day refuels with other invoices are kept; "
          "the legacy dated path also keeps monthly summaries.")


# ─── 10. Concurrent imports ────────────────────────────────────────────────────

def test_concurrent_import_is_refused_before_writing():
    db = FakeReplaceDB()
    dated(db, [aug(3, "AUH 44120", 50, "a3")])
    before, rows = db.snapshot(), len(db.rows)
    running = [{"id": 999, "status": "processing", "created_at": "2999-01-01T00:00:00+00:00"}]
    with replace_env(db), patch("app.fuel.importer.fetch_running_imports", return_value=running):
        msg = refused(lambda: execute_fuel_import(xlsx(SUMMARY_HEADERS, [["AUH 44120", 9, 27.0]]), "s.xlsx",
                                                  SUMMARY_MAP, USER, reporting_month="2026-08"), 409)
    assert "#999" in msg and len(db.rows) == rows and db.snapshot() == before, msg
    assert db.imports[max(db.imports)]["status"] == "failed"
    print("[PASS] Another running import: refused after the claim, before any read of the scope or any write.")


TESTS = [
    test_same_file_uploaded_twice,
    test_monthly_then_daily_keeps_the_summary,
    test_daily_then_monthly_replaces_the_daily_rows,
    test_mixed_vehicles_and_months_are_processed_independently,
    test_other_months_remain_untouched,
    test_files_without_dates,
    test_identical_month_end_date_is_automatically_a_monthly_summary,
    test_identical_non_month_end_date_needs_the_reporting_month,
    test_same_date_summary_needs_every_total_cost_and_the_replace_feature,
    test_failure_rolls_back_and_keeps_existing_data,
    test_partial_month_daily_files_and_duplicates,
    test_concurrent_import_is_refused_before_writing,
]


def main():
    print("=" * 60)
    print("FUEL IMPORT - MONTHLY VS DAILY CONFLICTS (OFFLINE)")
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
