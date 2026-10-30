"""
Offline tests for the dedicated monthly-summary table (FUEL_MONTHLY_SUMMARY_TABLE=1 with FUEL_IMPORT_REPLACE=1).

  - imports: a monthly summary is stored in fuel_monthly_summaries (staged, no date columns) and committed with
    fuel_commit_import_v2; it supersedes the vehicle-month's dated rows and older summary; dated imports skip
    vehicle-months that have a stored summary; ids of the two tables never mix; failures change nothing visible
  - reads: fuel_transactions is read as dated rows only (NULL-safe filter) and summaries come from the new table
    by the same reporting-date window, so every KPI endpoint returns the same result for the legacy layout and for
    the copied layout (both tables populated, the state after migration script 04)
  - API: /transactions lists dated rows only, /monthly-summaries and vehicle-detail report summaries by month
  - request shapes of the new database helpers, and the review-only SQL drafts (textual checks only)

FakeSummaryTableDB and PostgrestEmulator are in-memory models. Every outbound HTTP call is blocked by OfflineGuard.
These tests prove the application logic on fixtures. They do NOT prove database behaviour: the table, its RLS
policies, fuel_commit_import_v2 and the migration SQL have not been run against any database.

Run from Backend/:  python scripts/test_fuel_monthly_summary_table.py
"""

import copy
import os
import re
import sys
import traceback
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_import_replacement import (  # noqa: E402
    BASE_DIR, DATED_HEADERS, DATED_MAP, SUMMARY_HEADERS, SUMMARY_MAP, USER, FakeReplaceDB, replace_env, xlsx,
)
from test_fuel_scope import Api, reset_fuel_read_caches  # noqa: E402

import app.fuel.db as fuel_db  # noqa: E402
from app.fuel.db import FuelCommitError  # noqa: E402
from app.fuel.importer import (  # noqa: E402
    execute_fuel_import, generate_fuel_preview, reconcile_fuel_import, replacement_vehicle_key,
)
from app.fuel.reporting_period import (  # noqa: E402
    DATE_BASIS_MONTH_END, SUMMARY_TABLE, month_bounds, summary_months_for_range, summary_scope_row,
)
from app.utils.datetime_helpers import parse_tx_wallclock  # noqa: E402


# ─── In-memory model of both tables and fuel_commit_import_v2 ─────────────────

class FakeSummaryTableDB(FakeReplaceDB):
    """FakeReplaceDB plus fuel_monthly_summaries (its own id sequence, starting at 1 like fuel_transactions)."""

    def __init__(self):
        super().__init__()
        self.summaries, self.next_summary = [], 1

    def insert_summaries(self, rows, batch_size=100, bearer_token=None, deadline=None, outcome=None):
        if self.insert_fail:
            outcome.update(failed_rows=len(rows), failed_source_rows=[(r["source_row_number"], "rejected") for r in rows])
            return 0, ["Batch 1 rejected by the database: HTTP 400"]
        for r in rows:
            assert "transaction_date" not in r and "date_basis" not in r, f"a summary row carries a date: {r}"
            assert r["reporting_month"].endswith("-01") and r["staged"] is True, r
            self.summaries.append({**r, "id": self.next_summary, "superseded_by_import_id": None})
            self.next_summary += 1
        return len(rows), []

    def visible_summaries(self):
        return [s for s in self.summaries if s.get("superseded_by_import_id") is None and s.get("staged") is not True]

    def fetch_visible(self, date_from, date_to, bearer_token=None, deadline=None):
        dated = [dict(r) for r in super().fetch_visible(date_from, date_to) if r.get("date_basis") != DATE_BASIS_MONTH_END]
        months = summary_months_for_range(date_from, date_to)
        if months is None:
            return dated
        lo, hi = months
        return dated + [summary_scope_row(dict(s)) for s in self.visible_summaries()
                        if lo <= date.fromisoformat(s["reporting_month"]) <= hi]

    def fetch_import_rows(self, import_id, source_rows=None, bearer_token=None, deadline=None, table="fuel_transactions"):
        if table == SUMMARY_TABLE:
            return [{"id": s["id"], "source_row_number": s["source_row_number"], "reporting_month": s["reporting_month"],
                     "staged": s.get("staged")} for s in self.summaries if s["import_id"] == import_id]
        return super().fetch_import_rows(import_id, source_rows, bearer_token, deadline)

    def commit(self, import_id, supersede_ids, expected_rows, bearer_token=None, deadline=None,
               supersede_summary_ids=None):
        assert supersede_summary_ids is not None, "with the table flag every commit goes through fuel_commit_import_v2"
        self.commit_calls.append((import_id, list(supersede_ids), list(supersede_summary_ids), expected_rows))
        if self.commit_mode == "refuse":
            raise FuelCommitError("rejected by the database (HTTP 400)")
        rows, summaries = copy.deepcopy(self.rows), copy.deepcopy(self.summaries)
        for ids, table in ((supersede_ids, rows), (supersede_summary_ids, summaries)):
            by_id = {r["id"]: r for r in table}
            for i in ids:
                if by_id[i].get("superseded_by_import_id") or by_id[i].get("staged") or by_id[i]["import_id"] == import_id:
                    raise FuelCommitError("replacement scope changed")
                by_id[i]["superseded_by_import_id"] = import_id
        staged = [r for r in rows + summaries if r["import_id"] == import_id and r.get("staged") is True]
        if len(staged) != expected_rows:
            raise FuelCommitError("staged count mismatch")
        for r in staged:
            r["staged"] = False
        problem = invariant_violation(rows, summaries)
        if problem:
            raise FuelCommitError(problem)  # the v2 function raises and nothing changes
        self.rows, self.summaries = rows, summaries
        return {"superseded": len(supersede_ids) + len(supersede_summary_ids), "committed": len(staged)}

    def visible_summary_totals(self, vehicle=None, month=None):
        rows = [s for s in self.visible_summaries() if (vehicle is None or s["vehicle_identifier"] == vehicle)
                and (month is None or s["reporting_month"].startswith(month))]
        return round(sum(s["litres"] for s in rows), 2), round(sum(s["total_cost"] for s in rows), 2), len(rows)


def invariant_violation(rows, summaries):
    """fuel_commit_import_v2's invariants: per vehicle key and month, summaries of one import and no visible dated row."""
    def visible(r):
        return r.get("superseded_by_import_id") is None and r.get("staged") is not True

    summary_imports, dated = {}, set()
    for s in filter(visible, summaries):
        summary_imports.setdefault((replacement_vehicle_key(s["vehicle_identifier"]), s["reporting_month"][:7]),
                                   set()).add(s["import_id"])
    for r in filter(visible, rows):
        if r.get("date_basis") != DATE_BASIS_MONTH_END:
            dated.add((replacement_vehicle_key(r["vehicle_identifier"]), str(r["transaction_date"])[:7]))
    for key, imports in summary_imports.items():
        if len(imports) > 1:
            return f"summaries of {len(imports)} imports visible for {key}"
        if key in dated:
            return f"visible summary and dated rows for {key}"
    return None


@contextmanager
def table_env(db, dated_replace=True):
    with replace_env(db, dated_replace=dated_replace), \
         patch.dict(os.environ, {"FUEL_MONTHLY_SUMMARY_TABLE": "1"}), \
         patch("app.fuel.importer.insert_monthly_summaries_batch", side_effect=db.insert_summaries):
        yield


def tdated(db, rows, **kwargs):
    with table_env(db):
        return execute_fuel_import(xlsx(DATED_HEADERS, rows), "dated.xlsx", DATED_MAP, USER, **kwargs)


def tsummary(db, rows, month, **kwargs):
    with table_env(db):
        return execute_fuel_import(xlsx(SUMMARY_HEADERS, rows), "summary.xlsx", SUMMARY_MAP, USER,
                                   reporting_month=month, **kwargs)


def jul(day, vehicle, litres, inv, hour=9):
    return [f"2026-07-{day:02d} {hour:02d}:00:00", vehicle, litres, 3.0, round(litres * 3.0, 2), inv]


# ─── Imports ───────────────────────────────────────────────────────────────────

def test_monthly_summary_goes_to_its_own_table():
    db = FakeSummaryTableDB()
    res = tsummary(db, [["AUH 44120", 900, 2700.0], ["DXB 10293", 450, 1350.0]], "2026-07")
    assert res.committed and res.rows_imported == 2 and res.status == "completed", res
    assert db.rows == [], "nothing is written to fuel_transactions"
    assert db.visible_summary_totals() == (1350, 4050.0, 2)
    assert {s["reporting_month"] for s in db.summaries} == {"2026-07-01"}
    assert res.monthly_summary_table is True and res.assigned_transaction_date is None, res
    assert (res.date_from, res.date_to) == ("2026-07-01", "2026-07-31"), "the import covers its month, no refuel date"
    assert db.commit_calls == [(res.import_id, [], [], 2)], db.commit_calls
    assert db.imports[res.import_id]["import_mode"] == "monthly_summary"
    print("[PASS] A monthly summary is stored in fuel_monthly_summaries (staged, then committed by v2), never dated.")


def test_summary_replaces_dated_rows_and_older_summary_of_its_vehicle_month():
    db = FakeSummaryTableDB()
    dated_res = tdated(db, [jul(3, "AUH 44120", 50, "A3"), jul(20, "AUH 44120", 60, "A20"),
                            jul(5, "DXB 10293", 40, "D5"), ["2026-06-10 09:00:00", "AUH 44120", 70, 3.0, 210.0, "J10"]])
    assert dated_res.committed and dated_res.rows_imported == 4, dated_res
    first = tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")
    assert first.committed and first.rows_replaced == 2, first     # AUH's two July rows
    second = tsummary(db, [["AUH 44120", 820, 2460.0]], "2026-07")  # corrected statement
    assert second.committed and second.rows_replaced == 1, second   # the first summary
    assert db.visible_summary_totals("AUH 44120", "2026-07") == (820, 2460.0, 1)
    vis = sorted((r["vehicle_identifier"], str(r["transaction_date"])[:10]) for r in db.visible())
    assert vis == [("AUH 44120", "2026-06-10"), ("DXB 10293", "2026-07-05")], vis
    assert len(db.rows) == 4 and len(db.summaries) == 2, "superseded rows are kept, nothing is deleted"
    assert db.commit_calls[-1][2] == [db.summaries[0]["id"]], "the older summary is superseded in its own table"
    print("[PASS] A summary supersedes its vehicle-month's dated rows and older summary only; nothing is deleted.")


def test_dated_import_skips_vehicle_months_with_a_stored_summary():
    db = FakeSummaryTableDB()
    tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")
    res = tdated(db, [jul(3, "AUH 44120", 50, "A3"), jul(4, "DXB 10293", 40, "D4"),
                      ["2026-08-02 09:00:00", "AUH 44120", 30, 3.0, 90.0, "A-AUG"]])
    assert res.committed and res.rows_imported == 2 and res.rows_skipped_monthly_summary == 1, res
    assert db.visible_summary_totals("AUH 44120", "2026-07") == (800, 2400.0, 1), "the summary is kept"
    assert not [r for r in db.visible() if r["vehicle_identifier"] == "AUH 44120" and str(r["transaction_date"]).startswith("2026-07")]
    assert invariant_violation(db.rows, db.summaries) is None
    print("[PASS] A dated import skips only the vehicle-months that have a stored summary; the summary is kept.")


def test_ids_of_the_two_tables_never_mix():
    db = FakeSummaryTableDB()
    tdated(db, [jul(8, "DXB 10293", 40, "D8")])                       # fuel_transactions id 1
    tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")              # fuel_monthly_summaries id 1
    assert db.rows[0]["id"] == 1 and db.summaries[0]["id"] == 1
    res = tsummary(db, [["AUH 44120", 800, 2400.0], ["DXB 10293", 300, 900.0]], "2026-07")
    # AUH's identical summary (summary id 1) is kept; DXB's dated row (transaction id 1) must still be replaced
    assert (res.rows_unchanged, res.rows_imported, res.rows_replaced) == (1, 1, 1), res
    assert db.commit_calls[-1][1:3] == ([1], []), db.commit_calls[-1]
    assert db.rows[0]["superseded_by_import_id"] == res.import_id
    assert db.visible_summary_totals() == (1100, 3300.0, 2) and db.visible() == []
    print("[PASS] Transaction id 1 and summary id 1 are told apart: kept and superseded rows are never confused.")


def test_reimporting_a_summary_changes_nothing():
    db = FakeSummaryTableDB()
    tsummary(db, [["AUH 44120", 800, 2400.0], ["DXB 10293", 300, 900.0]], "2026-07")
    before = copy.deepcopy(db.summaries)
    again = tsummary(db, [["AUH 44120", 800, 2400.0], ["DXB 10293", 300, 900.0]], "2026-07")
    assert again.rows_imported == 0 and again.rows_unchanged == 2 and again.rows_replaced == 0, again
    assert db.summaries == before, "no row added or superseded"
    print("[PASS] Re-importing the same monthly summary inserts and supersedes nothing.")


def test_failures_leave_visible_data_unchanged():
    db = FakeSummaryTableDB()
    tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")
    before = db.visible_summary_totals()
    db.insert_fail = True
    res = tsummary(db, [["AUH 44120", 900, 2700.0]], "2026-07")
    assert res.committed is False and res.status == "failed" and db.visible_summary_totals() == before, res
    db.insert_fail, db.commit_mode = False, "refuse"
    res = tsummary(db, [["AUH 44120", 900, 2700.0]], "2026-07")
    assert res.committed is False and db.visible_summary_totals() == before, res
    assert any(s.get("staged") is True for s in db.summaries), "refused rows stay staged (hidden) for audit"
    print("[PASS] A failed insert or a refused commit leaves the visible summaries unchanged (new rows stay hidden).")


def test_commit_refuses_a_summary_racing_a_dated_import():
    # v2 invariant: a summary committed between a dated import's plan and its commit makes that commit fail
    db = FakeSummaryTableDB()
    original = db.commit

    def summary_lands_first(*args, **kwargs):
        db.summaries.append({"id": 99, "import_id": 1, "reporting_month": "2026-07-01", "vehicle_identifier": "AUH 44120",
                             "litres": 1.0, "total_cost": 3.0, "staged": False, "superseded_by_import_id": None,
                             "source_row_number": 2})
        return original(*args, **kwargs)

    db.commit = summary_lands_first
    res = tdated(db, [jul(3, "AUH 44120", 50, "A3")])
    assert res.committed is False and db.visible() == [], res
    print("[PASS] The v2 invariant refuses a dated commit once a summary for that vehicle-month is visible.")


def test_preview_reports_the_reporting_month_not_a_date():
    db = FakeSummaryTableDB()
    with table_env(db):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, [["AUH 44120", 800, 2400.0]]), "s.xlsx", SUMMARY_MAP, "2026-07")
    assert p.ready_to_import and p.monthly_summary_table and p.assigned_transaction_date is None, p
    assert all(s["transaction_date"] is None and s["reporting_month"] == "2026-07" for s in p.sample_rows), p.sample_rows
    assert any("without a refuelling date" in w for w in p.validation_warnings), p.validation_warnings
    tdated(db, [jul(3, "AUH 44120", 50, "A3")])
    with table_env(db):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, [["AUH 44120", 800, 2400.0]]), "s.xlsx", SUMMARY_MAP, "2026-07")
    recs = p.replacement["records"]
    assert len(recs) == 1 and recs[0]["transaction_date"].startswith("2026-07-03"), recs
    tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")
    with table_env(db):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, [["AUH 44120", 810, 2430.0]]), "s.xlsx", SUMMARY_MAP, "2026-07")
    recs = p.replacement["records"]
    assert recs[0]["transaction_date"] is None and recs[0]["reporting_month"] == "2026-07", recs
    assert recs[0]["record_type"] == "monthly_summary" and recs[0]["id"] is None and recs[0]["summary_id"], recs
    print("[PASS] Preview: reporting month only; replaced summaries are listed by month with their summary id.")


def test_reconcile_counts_the_summary_table_and_falls_back_to_legacy_rows():
    db = FakeSummaryTableDB()
    res = tsummary(db, [["AUH 44120", 800, 2400.0], ["DXB 10293", 300, 900.0]], "2026-07")
    record = {**db.imports[res.import_id], "status": "completed", "rows_imported": 1, "rows_found": 2,
              "rows_duplicate": 0, "rows_failed": 0, "completed_at": "x"}
    with table_env(db), patch("app.fuel.importer.get_fuel_import_record", return_value=record):
        out = reconcile_fuel_import(res.import_id)
    assert out["rows_imported"] == 2 and (out["date_from"], out["date_to"]) == ("2026-07-01", "2026-07-31"), out
    # A summary imported before the flag (rows only in fuel_transactions, not copied yet)
    legacy = FakeSummaryTableDB()
    legacy.rows = [{"id": 5, "import_id": 7, "source_row_number": 2, "transaction_date": "2026-06-30", "staged": False,
                    "vehicle_identifier": "AUH 44120", "litres": 1, "total_cost": 3, "date_basis": DATE_BASIS_MONTH_END}]
    rec = {"id": 7, "import_mode": "monthly_summary", "status": "completed", "rows_imported": 1, "rows_found": 1,
           "completed_at": "x"}
    legacy.imports[7] = dict(rec)
    with table_env(legacy), patch("app.fuel.importer.get_fuel_import_record", return_value=rec):
        out = reconcile_fuel_import(7)
    assert out["rows_imported"] == 1 and out["status"] == "completed", out
    print("[PASS] Reconcile counts a summary import in its table, and legacy summary imports in fuel_transactions.")


def test_flag_off_never_names_the_new_table():
    db = FakeReplaceDB()   # its commit takes no supersede_summary_ids: passing it would fail
    forbidden = MagicMock(side_effect=AssertionError("fuel_monthly_summaries must not be written without the flag"))
    with replace_env(db), patch.dict(os.environ, {"FUEL_MONTHLY_SUMMARY_TABLE": ""}), \
         patch("app.fuel.importer.insert_monthly_summaries_batch", forbidden):
        res = execute_fuel_import(xlsx(SUMMARY_HEADERS, [["AUH 44120", 800, 2400.0]]), "s.xlsx", SUMMARY_MAP, USER,
                                  reporting_month="2026-07")
    assert res.committed and db.rows[0]["date_basis"] == DATE_BASIS_MONTH_END and res.monthly_summary_table is False
    assert res.assigned_transaction_date == "2026-07-31", "legacy layout unchanged"
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "", "FUEL_MONTHLY_SUMMARY_TABLE": "1"}):
        assert fuel_db.summary_table_enabled() is False, "the table flag requires FUEL_IMPORT_REPLACE"
    print("[PASS] Without the flag the legacy layout is used and the new table/function are never named.")


# ─── Pure helpers and request shapes ───────────────────────────────────────────

def test_reporting_window_matches_the_legacy_month_end_selection():
    m = lambda s: date.fromisoformat(s)  # noqa: E731
    cases = {
        ("2026-07-01", "2026-07-31"): (m("2026-07-01"), m("2026-07-01")),
        ("2026-07-01", "2026-08-15"): (m("2026-07-01"), m("2026-07-01")),     # August's month end is outside
        ("2026-07-10", "2026-08-31"): (m("2026-07-01"), m("2026-08-01")),     # July's month end is inside
        ("2026-07-31", "2026-07-31"): (m("2026-07-01"), m("2026-07-01")),     # the probe of one month-end day
        ("2026-07-31T10:00:00", "2026-08-31"): (m("2026-08-01"), m("2026-08-01")),  # after 00:00 of July 31
        ("2026-06-01", "2026-07-31T00:00:00"): (m("2026-06-01"), m("2026-07-01")),
        ("2026-06-01", "2026-07-30T23:00:00"): (m("2026-06-01"), m("2026-06-01")),
        (None, "2026-07-15"): (None, m("2026-06-01")),
        ("2026-07-15", None): (m("2026-07-01"), None),                        # July 31 is after July 15
        ("2026-08-01", None): (m("2026-08-01"), None),
        (None, None): (None, None),
    }
    for (lo, hi), expected in cases.items():
        assert summary_months_for_range(lo, hi) == expected, (lo, hi, summary_months_for_range(lo, hi))
    assert summary_months_for_range("2026-07-02", "2026-07-30") is None, "no month end inside: nothing is read"
    # Cross-check against the legacy rule on every day of a year: month end (00:00) within [lo, hi 23:59:59]
    for lo in (date(2026, 1, d) for d in (1, 15, 31)):
        for hi in (date(2026, mo, d) for mo in (2, 3) for d in (1, 15, 27, 28, 31) if not (mo == 2 and d == 31)):
            months = summary_months_for_range(lo.isoformat(), hi.isoformat())
            legacy = [date(2026, mo, 1) for mo in range(1, 13) if lo <= month_bounds(date(2026, mo, 1))[1] <= hi]
            assert (months is None) == (not legacy) and (not legacy or months == (legacy[0], legacy[-1])), (lo, hi)
    print("[PASS] The summary window selects exactly the months whose month-end date the legacy filter selected.")


def test_summary_row_adapter_and_insert_payload():
    row = summary_scope_row({"id": 7, "import_id": 3, "reporting_month": "2026-02-01", "vehicle_identifier": "AUH 1",
                             "litres": 10, "total_cost": 30, "staged": False, "superseded_by_import_id": None})
    assert row["id"] is None and row["summary_id"] == 7 and row["record_type"] == "monthly_summary", row
    assert row["date_basis"] == DATE_BASIS_MONTH_END and row["reporting_month"] == "2026-02"
    assert row["transaction_date"] == "2026-02-28" and "staged" not in row
    try:
        summary_scope_row({"id": 1, "reporting_month": "2026-02-15"})
    except ValueError:
        pass
    else:
        raise AssertionError("a reporting_month that is not a first day must be refused")
    print("[PASS] Stored summaries adapt to the KPI row shape with no transaction id; bad months are refused.")


def test_request_shapes_of_the_new_helpers():
    env = {"FUEL_IMPORT_REPLACE": "1", "FUEL_MONTHLY_SUMMARY_TABLE": "1", "SUPABASE_URL": "https://offline.invalid",
           "SUPABASE_SERVICE_ROLE_KEY": ""}
    with patch.dict(os.environ, env):
        assert fuel_db.apply_dated_only_filter({}) == {"or": "(date_basis.is.null,date_basis.neq.reporting_month_end)"}
        calls = []

        def pages(url, params, headers, what, deadline=None):
            calls.append((url.rsplit("/", 1)[1], dict(params)))
            if url.endswith(SUMMARY_TABLE):
                return [{"id": 4, "import_id": 9, "reporting_month": "2026-07-01", "vehicle_identifier": "AUH 1",
                         "litres": 5, "total_cost": 15}]
            return [{"id": 4, "import_id": 8, "transaction_date": "2026-07-31T08:00:00", "vehicle_identifier": "AUH 1",
                     "litres": 1, "total_cost": 3, "date_basis": None}]

        with patch("app.fuel.db._fetch_all_pages", side_effect=pages):
            rows = fuel_db.fetch_fuel_transactions_for_analytics(
                {"date_from": "2026-07-01", "date_to": "2026-08-15", "vehicle_identifier": "AUH", "import_id": 9})
        (t1, p1), (t2, p2) = calls
        assert t1 == "fuel_transactions" and p1["or"] == "(date_basis.is.null,date_basis.neq.reporting_month_end)"
        assert p1["superseded_by_import_id"] == "is.null" and p1["staged"] == "not.is.true"
        assert t2 == SUMMARY_TABLE and p2["and"] == "(reporting_month.gte.2026-07-01,reporting_month.lte.2026-07-01)", p2
        assert p2["vehicle_identifier"] == "ilike.*AUH*" and p2["import_id"] == "eq.9" and p2["staged"] == "not.is.true"
        assert "transaction_date" not in p2["select"] and "date_basis" not in p2["select"]
        assert [r.get("summary_id") for r in rows] == [4, None], "summary (00:00 of Jul 31) before the 08:00 refuel"

        calls.clear()
        with patch("app.fuel.db._fetch_all_pages", side_effect=pages):
            fuel_db.fetch_import_rows(5, [2, 3], table=SUMMARY_TABLE)
        assert calls[0][0] == SUMMARY_TABLE and calls[0][1]["select"] == "id,source_row_number,reporting_month,staged"

        posted = []
        ok = MagicMock(ok=True, status_code=200, json=lambda: {"superseded": 3, "committed": 2})
        with patch("app.fuel.db.requests.post", side_effect=lambda url, **kw: posted.append((url, kw["json"])) or ok):
            assert fuel_db.commit_fuel_import(5, [1, 2], 2, supersede_summary_ids=[1]) == {"superseded": 3, "committed": 2}
        assert posted[0][0].endswith("/rpc/fuel_commit_import_v2"), posted
        assert posted[0][1] == {"p_import_id": 5, "p_supersede_tx_ids": [1, 2], "p_supersede_summary_ids": [1],
                                "p_expected_rows": 2}, posted
    with patch.dict(os.environ, {**env, "FUEL_MONTHLY_SUMMARY_TABLE": ""}):
        assert fuel_db.apply_dated_only_filter({}) == {}
        try:
            fuel_db.commit_fuel_import(5, [], 1, supersede_summary_ids=[3])
        except FuelCommitError:
            pass
        else:
            raise AssertionError("summary ids without the flag must be refused before any request")
    print("[PASS] Request shapes: NULL-safe dated filter, summary window and filters, v2 RPC payload, read-back table.")


# ─── Reporting parity: legacy layout vs. copied layout, through the real endpoints ──

UNITS = [{"id": 1, "nm": "AUH 44120"}, {"id": 2, "nm": "DXB 10293"}, {"id": 3, "nm": "SHJ 77001"}]


def _tx(i, imp, vid, when, litres, cost, basis=None, **extra):
    return {"id": i, "import_id": imp, "vehicle_identifier": vid, "transaction_date": when, "litres": litres,
            "total_cost": cost, "unit_price": round(cost / litres, 4) if litres else None, "fuel_type": "DIESEL",
            "invoice_number": f"INV-{i}", "odometer_km": None, "station_name": None, "driver_name": None,
            "card_number": None, "date_basis": basis, "superseded_by_import_id": None, "staged": None,
            "source_row_number": i + 1, **extra}


LEGACY_TX = [
    _tx(1, 10, "AUH 44120", "2026-01-05T08:00:00", 50.0, 150.0),
    _tx(2, 10, "AUH 44120", "2026-01-20T08:00:00", 55.0, 165.0),
    _tx(3, 10, "AUH 44120", "2026-02-03T08:00:00", 60.0, 180.0),
    _tx(4, 10, "AUH 44120", "2026-02-18T08:00:00", 65.0, 195.0),
    _tx(5, 10, "AUH 44120", "2026-03-02T08:00:00", 70.0, 210.0),
    _tx(6, 10, "AUH 44120", "2026-03-25T08:00:00", -5.0, -15.0),                       # refund adjustment
    _tx(7, 10, "DXB 10293", "2026-02-10T08:00:00", 40.0, 120.0),
    _tx(8, 10, "DXB 10293", "2026-03-12T08:00:00", 45.0, 135.0),
    _tx(9, 9, "SHJ 77001", "2026-02-28", 380.0, 1140.0, DATE_BASIS_MONTH_END, superseded_by_import_id=11),
    _tx(10, 11, "SHJ 77001", "2026-02-28", 400.0, 1200.0, DATE_BASIS_MONTH_END),
    _tx(11, 11, "ZZZ 1", "2026-02-28", 90.0, 270.0, DATE_BASIS_MONTH_END),             # unmatched vehicle
    _tx(12, 12, "SHJ 77001", "2026-03-31", 450.0, 1350.0, DATE_BASIS_MONTH_END),
    _tx(13, 12, "DXB 10293", "2026-03-31", 300.0, 900.0, DATE_BASIS_MONTH_END),        # overlaps DXB's Mar 12 row
    _tx(14, 13, "SHJ 77001", "2026-01-31", 410.0, 1230.0, DATE_BASIS_MONTH_END, staged=True),  # uncommitted import
    _tx(15, 10, "SHJ 77001", "2026-01-14T00:00:00", 35.0, 105.0, "transaction"),
]
IMPORTS = [{"id": i, "status": "completed", "import_mode": "monthly_summary" if i in (9, 11, 12, 13) else "dated",
            "replace_scope": None} for i in (9, 10, 11, 12, 13)]


def copied_layout():
    """The state after migration script 04 step B: legacy rows untouched, every summary row copied with its import, row and visibility."""
    summaries, next_id = [], 1
    for r in LEGACY_TX:
        if r["date_basis"] != DATE_BASIS_MONTH_END:
            continue
        first = date.fromisoformat(r["transaction_date"][:10]).replace(day=1)
        summaries.append({k: r.get(k) for k in ("import_id", "vehicle_identifier", "fuel_type", "litres", "unit_price",
                                                "total_cost", "odometer_km", "station_name", "invoice_number",
                                                "card_number", "driver_name", "source_row_number",
                                                "superseded_by_import_id")}
                         | {"id": next_id, "reporting_month": first.isoformat(), "staged": bool(r["staged"]),
                            "migrated_from_transaction_id": r["id"]})
        next_id += 1
    return {"fuel_transactions": copy.deepcopy(LEGACY_TX), SUMMARY_TABLE: summaries, "fuel_imports": IMPORTS}


class PostgrestEmulator:
    """The subset of PostgREST filters the fuel reads send, applied to in-memory tables (NULL semantics included)."""

    def __init__(self, tables):
        self.tables = tables

    @staticmethod
    def _value(col, raw):
        if raw is None:
            return None
        if col == "transaction_date":
            parsed = parse_tx_wallclock(raw)
            return parsed[0] if parsed else None
        if col == "reporting_month":
            return date.fromisoformat(str(raw)[:10])
        return raw

    def _match(self, row, cond):
        col, rest = cond.split(".", 1)
        negate = rest.startswith("not.")
        if negate:
            rest = rest[4:]
        op, arg = rest.split(".", 1)
        val = row.get(col)
        if op == "is":
            hit = val is None if arg == "null" else val is (arg == "true")
        elif val is None:
            hit = False                                  # SQL: comparisons with NULL are never true
        elif op == "eq":
            hit = str(val) == arg
        elif op == "neq":
            hit = str(val) != arg
        elif op == "ilike":
            hit = arg.strip("*").lower() in str(val).lower()
        elif op in ("gte", "lte", "in"):
            if op == "in":
                hit = str(val) in arg.strip("()").split(",")
            else:
                a, b = self._value(col, val), self._value(col, arg)
                hit = a >= b if op == "gte" else a <= b
        else:
            raise AssertionError(f"unsupported filter {cond}")
        return not hit if negate else hit

    def __call__(self, url, params, headers, what, deadline=None):
        table = url.split("/rest/v1/", 1)[1]
        rows = [dict(r) for r in self.tables.get(table, [])]
        for key, val in params.items():
            if key in ("select", "order", "limit", "offset"):
                continue
            if key in ("and", "or"):
                conds = val.strip("()").split(",")
                test = all if key == "and" else any
                rows = [r for r in rows if test(self._match(r, c) for c in conds)]
            else:
                rows = [r for r in rows if self._match(r, f"{key}.{val}")]
        for field in reversed([o.split(".")[0] for o in params.get("order", "id.asc").split(",")]):
            rows.sort(key=lambda r: (r.get(field) is None, self._value(field, r.get(field)) if r.get(field) is not None else 0))
        select = params.get("select", "*")
        return rows if select == "*" else [{c: r.get(c) for c in select.split(",")} for r in rows]


ENDPOINTS = ["/api/fuel/analytics/summary", "/api/fuel/analytics/trend?group_by=month",
             "/api/fuel/analytics/trend?group_by=day", "/api/fuel/analytics/trend?group_by=week",
             "/api/fuel/analytics/vehicles", "/api/fuel/analytics/fuel-types", "/api/fuel/analytics/excluded",
             "/api/fuel/analytics/efficiency", "/api/fuel/analytics/efficiency/vehicles"]
RANGES = ["date_from=2026-01-01&date_to=2026-03-31", "date_from=2026-02-01&date_to=2026-02-28",
          "date_from=2026-02-10&date_to=2026-03-15", "date_from=2026-01-15&date_to=2026-03-31",
          "date_from=2026-02-01&date_to=2026-03-31&vehicle_identifier=SHJ%2077001", ""]


def _strip_ids(value):
    """Row ids are table-specific (summary ids are a new sequence); everything else must be identical."""
    if isinstance(value, dict):
        return {k: _strip_ids(v) for k, v in value.items() if k not in ("transaction_ids", "summary_ids", "id")}
    if isinstance(value, list):
        return [_strip_ids(v) for v in value]
    return value


def _responses(tables, flag):
    env = {"FUEL_IMPORT_REPLACE": "1", "FUEL_MONTHLY_SUMMARY_TABLE": "1" if flag else "", "FUEL_SCHEMA_V2": "",
           "SUPABASE_URL": "https://offline.invalid", "SUPABASE_SERVICE_ROLE_KEY": ""}
    fuel_db.clear_vehicle_identifier_cache()
    api = Api(units=UNITS)
    api.fetch = MagicMock(side_effect=lambda f, bearer_token=None: fuel_db.fetch_fuel_transactions_for_analytics(f, bearer_token))
    out = {}
    with patch.dict(os.environ, env), patch("app.fuel.db._fetch_all_pages", side_effect=PostgrestEmulator(tables)), api:
        for path in ENDPOINTS + ["/api/fuel/transactions?page_size=1000", "/api/fuel/monthly-summaries?page_size=1000"]:
            for q in RANGES:
                url = f"{path}{'&' if '?' in path else '?'}{q}" if q else path
                out[url] = api.get(url)
        out["detail"] = api.get("/api/fuel/analytics/vehicle-detail?vehicle_identifier=SHJ%2077001&"
                                "date_from=2026-01-01&date_to=2026-03-31")
    return out


def test_every_kpi_endpoint_is_identical_in_both_layouts():
    legacy = _responses({"fuel_transactions": copy.deepcopy(LEGACY_TX), "fuel_imports": IMPORTS}, flag=False)
    table = _responses(copied_layout(), flag=True)
    compared = 0
    for url, body in legacy.items():
        if not any(url.startswith(e) for e in ENDPOINTS):
            continue
        assert _strip_ids(body) == _strip_ids(table[url]), f"KPI response differs between layouts: {url}"
        compared += 1
    summary = legacy["/api/fuel/analytics/summary?date_from=2026-01-01&date_to=2026-03-31"]
    assert summary["monthly_summary_rows"] > 0 and summary["total_litres"] > 0, summary
    assert compared == len(ENDPOINTS) * len(RANGES)
    print(f"[PASS] {compared} KPI responses (summary, trends, vehicles, fuel types, excluded, efficiency) identical "
          "for the legacy layout and the copied layout: no double counting while both tables hold the summaries.")


def test_lists_report_summaries_by_month_only_with_the_flag():
    legacy = _responses({"fuel_transactions": copy.deepcopy(LEGACY_TX), "fuel_imports": IMPORTS}, flag=False)
    table = _responses(copied_layout(), flag=True)
    for q in RANGES:
        tx_url = f"/api/fuel/transactions?page_size=1000{'&' + q if q else ''}"
        old_rows, new_rows = legacy[tx_url]["transactions"], table[tx_url]["transactions"]
        assert all(r["date_basis"] != DATE_BASIS_MONTH_END for r in new_rows), "no summary listed as a transaction"
        assert [r for r in old_rows if r["date_basis"] != DATE_BASIS_MONTH_END] == new_rows, tx_url
        ms_url = f"/api/fuel/monthly-summaries?page_size=1000{'&' + q if q else ''}"
        key = lambda s: (s["reporting_month"], s["vehicle_identifier"], s["litres"], s["total_cost"], s["import_id"])  # noqa: E731
        old_ms, new_ms = legacy[ms_url]["monthly_summaries"], table[ms_url]["monthly_summaries"]
        assert sorted(map(key, old_ms)) == sorted(map(key, new_ms)), ms_url
        assert all(s["source_table"] == SUMMARY_TABLE for s in new_ms) and all("transaction_date" not in s for s in new_ms)
    detail_old, detail_new = legacy["detail"], table["detail"]
    assert detail_old["monthly_summaries"] == [] and any(r["date_basis"] == DATE_BASIS_MONTH_END for r in detail_old["transactions"])
    # Monthly-summary reporting: the vehicle detail lists no dated refuels, only the month's summaries
    assert detail_new["transactions"] == [], detail_new["transactions"]
    assert sorted(s["reporting_month"] for s in detail_new["monthly_summaries"]) == ["2026-02", "2026-03"], detail_new["monthly_summaries"]
    assert _strip_ids(detail_old["vehicle_summary"]) == _strip_ids(detail_new["vehicle_summary"]), "same vehicle KPIs"
    print("[PASS] With the flag, transaction lists hold dated rows only and summaries are listed by reporting month.")


# ─── Review-only SQL drafts (textual checks; nothing is executed) ─────────────

SQL_ORDER = ["01_preflight_checks.sql", "02_create_monthly_summaries_table.sql", "02a_existing_table_diagnostics.sql",
             "02b_realign_existing_table.sql", "03_rls_grants_commit_function.sql",
             "04_migrate_existing_data.sql", "05_verify.sql", "06_cutover.sql",
             "90_OPTIONAL_reset_fuel_test_data_DESTRUCTIVE.sql", "99_rollback.sql"]


def _sql(name):
    return open(os.path.join(BASE_DIR, "sql", "monthly_summaries", name), encoding="utf-8").read()


def _statements(text):
    """Uncommented SQL lines (comments and dollar-quoted bodies are kept; only '--' lines are dropped)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))


def test_sql_scripts_are_ordered_and_destructive_statements_are_isolated():
    assert sorted(os.listdir(os.path.join(BASE_DIR, "sql", "monthly_summaries"))) == SQL_ORDER
    texts = {n: _sql(n) for n in SQL_ORDER}
    # 06 (cutover) is read-only too: revoking the old commit function is an optional, commented lock-down, so a
    # backend without FUEL_MONTHLY_SUMMARY_TABLE can always still import
    for name in ("01_preflight_checks.sql", "02a_existing_table_diagnostics.sql", "05_verify.sql", "06_cutover.sql"):
        body = _statements(texts[name])
        assert "TRANSACTION READ ONLY" in body, name
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE)\b", body, re.I), name
    # Only the optional reset deletes rows; only the rollback drops (new) objects
    for name in SQL_ORDER:
        body = _statements(texts[name])
        if name != "90_OPTIONAL_reset_fuel_test_data_DESTRUCTIVE.sql":
            assert not re.search(r"\b(DELETE\s+FROM|TRUNCATE)\b", body, re.I), name
        if name != "99_rollback.sql":
            assert not re.search(r"^\s*DROP\s+(TABLE|FUNCTION|SCHEMA)", body, re.I | re.M), name
    reset = texts["90_OPTIONAL_reset_fuel_test_data_DESTRUCTIVE.sql"]
    assert "DESTRUCTIVE" in reset[:300] and "fuel.reset_confirm" in reset and "CREATE TABLE fuel_backup.%I AS TABLE" in reset
    assert "auth.users" in reset and "profiles" in reset, "the reset states what it keeps"
    # Table types come from the live columns; the vehicle key matches the backend's
    create = texts["02_create_monthly_summaries_table.sql"]
    assert "format_type(atttypid, atttypmod)" in create and "IF to_regclass('public.fuel_monthly_summaries') IS NOT NULL" in create
    # Generated column and helper normalise exactly like the backend: lower-case first, then keep a-z / 0-9
    assert "(regexp_replace(lower(vehicle_identifier), '[^a-z0-9]', '', 'g')) STORED" in create
    assert "SELECT regexp_replace(lower(coalesce(p_identifier, '')), '[^a-z0-9]', '', 'g')" in create
    for raw in ("A-12 345", "auh 44120", "AUH-44120", " dxb/10293 ", "Kab-1"):
        assert replacement_vehicle_key(raw) == re.sub(r"[^a-z0-9]", "", raw.lower())
    assert not any("lower(regexp_replace(" in _statements(t) for t in texts.values()), "one key rule only"
    migrate = texts["04_migrate_existing_data.sql"]
    assert re.search(r"^\s*ON CONFLICT \(migrated_from_transaction_id\) DO NOTHING", migrate, re.M), "the copy is idempotent"
    assert migrate.count("INSERT INTO public.fuel_migration_audit") == 5, "every changed existing row is logged"
    assert "A2_unsupersede_reclassified_rows" in migrate and "fuel.keep_dated_imports" in migrate
    assert "B2_sync_copy_state" in migrate, "a re-run carries later source changes over to the copies"
    assert "30" not in re.sub(r"--.*", "", migrate).replace("00:00:00", ""), "no hardcoded import ids"
    assert "review: summaries hidden by dated import" in migrate
    assert "SELECT 35, 'No monthly summary is hidden by a dated import" in texts["05_verify.sql"]
    assert "-- REVOKE EXECUTE ON FUNCTION public.fuel_commit_import" in texts["06_cutover.sql"]
    rollback = texts["99_rollback.sql"]
    for step in ("C_supersede_dated_by_summary", "A2_unsupersede_reclassified_rows", "A_reclassify_rows",
                 "A_reclassify_import", "R1_reverse_copy"):
        assert step in rollback and (step.startswith("R1") or step in migrate), step
    assert "R1b_supersede_source_of_superseded_copy" in rollback
    assert "GRANT EXECUTE ON FUNCTION public.fuel_commit_import(bigint, bigint[], integer) TO authenticated" in rollback
    # Every object scripts 02/03 create is dropped by the rollback (fuel_migration_audit is kept as the record)
    for obj in ("fuel_commit_import_v2(bigint, bigint[], bigint[], integer)", "fuel_monthly_summaries",
                "fuel_import_accepts_rows(bigint)", "fuel_vehicle_key(text)"):
        assert f"public.{obj}" in _statements(rollback).split("DROP", 1)[1], obj
    print("[PASS] SQL scripts: ordered, read-only checks, deletes only in the optional reset, drops only in the "
          "rollback, live column types, one vehicle-key rule.")


def test_commit_function_script_matches_the_backend_contract():
    from app.fuel.routes import FUEL_IMPORT_ROLES
    fn = _sql("03_rls_grants_commit_function.sql")
    body = _statements(fn)
    # One transaction for every write of the script; the verification queries come after COMMIT
    first = body.strip().splitlines()[0].strip()
    assert first == "BEGIN;", first
    assert body.count("BEGIN;") == 1 and body.count("COMMIT;") == 1
    assert body.index("COMMIT;") < body.index("SELECT p.oid::regprocedure")
    assert re.search(r"FUNCTION public\.fuel_commit_import_v2\(\s*p_import_id\s+bigint,\s*p_supersede_tx_ids\s+bigint\[\],"
                     r"\s*p_supersede_summary_ids\s+bigint\[\],\s*p_expected_rows\s+integer", fn)
    assert "SECURITY DEFINER" in fn and "pg_advisory_xact_lock(hashtext('public.fuel_commit_import'))" in fn
    # Authorization: the user's JWT (auth.uid()), the backend's import roles, the caller's own import
    roles = set(re.search(r"v_role NOT IN \(([^)]*)\)", fn).group(1).replace("'", "").replace(" ", "").split(","))
    assert roles == set(FUEL_IMPORT_ROLES), (roles, FUEL_IMPORT_ROLES)
    assert "v_import.imported_by IS DISTINCT FROM v_uid" in fn and "IF v_uid IS NULL" in fn
    # Days in the API session's time zone, never parsed from text (DateStyle) and never forced to another zone
    assert 'SET "TimeZone"' not in fn and "transaction_date::text" not in body
    assert body.count("transaction_date::date") >= 4
    # Replacement scope: the keys the backend writes, legacy month-end rows never superseded as dated rows
    assert "replace_scope -> 'vehicle_keys'" in fn and "replace_scope ->> 'period_from'" in fn
    assert body.count("date_basis IS DISTINCT FROM 'reporting_month_end'") == 2
    # Staging is allowed only through the security-definer check of the caller's own running import
    assert "public.fuel_import_accepts_rows(import_id)" in fn
    # Preconditions: every database/role time-zone setting, this session's zone, the realigned key, the sequence
    assert "FROM pg_db_role_setting d" in fn and "current_setting('TimeZone')" in fn
    assert "run 02b first" in fn and "has no identity sequence" in fn
    assert body.index("v_seq IS NULL OR to_regclass(v_seq) IS NULL") < body.index("EXECUTE format('GRANT USAGE ON SEQUENCE")
    assert "i.imported_by = auth.uid() AND i.status = 'processing'" in fn
    grants = re.findall(r"^GRANT ([A-Z, ]+) ON TABLE public\.fuel_monthly_summaries TO (\w+);", body, re.M)
    assert sorted(grants) == [("SELECT", "service_role"), ("SELECT, INSERT", "authenticated")], grants
    assert body.count("OWNER TO postgres") == 2
    assert re.search(r"GRANT EXECUTE ON FUNCTION public\.fuel_commit_import_v2\([^)]*\) TO authenticated;", body)
    print("[PASS] Script 03: one transaction, user-JWT authorization with the backend's roles, API-session days, "
          "the backend's replace_scope keys, legacy rows excluded, minimal grants.")


def test_expected_rows_and_replace_scope_match_what_the_backend_stored():
    db = FakeSummaryTableDB()
    tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")
    # One row skipped (vehicle-month with a summary), one unchanged on re-import, one new: only stored rows count
    tdated(db, [jul(5, "DXB 10293", 40, "D5")])
    res = tdated(db, [jul(3, "AUH-44120", 50, "A3"), jul(5, "DXB 10293", 40, "D5"), jul(9, "DXB 10293", 20, "D9")])
    assert (res.rows_skipped_monthly_summary, res.rows_unchanged, res.rows_imported) == (1, 1, 1), res
    assert db.commit_calls[-1][3] == 1 == res.rows_imported, db.commit_calls[-1]
    scope = db.imports[res.import_id]["replace_scope"]
    assert scope["period_from"] == "2026-07-03" and scope["period_to"] == "2026-07-09", scope
    assert scope["vehicle_keys"] == ["auh44120", "dxb10293"], "every file vehicle, keyed like fuel_vehicle_key"
    monthly = db.imports[db.commit_calls[0][0]]["replace_scope"]
    assert (monthly["period_from"], monthly["period_to"]) == ("2026-07-01", "2026-07-31"), monthly
    # A partial write is never committed: the RPC is not called at all
    db.insert_fail = True
    calls = len(db.commit_calls)
    res = tsummary(db, [["AUH 44120", 900, 2700.0]], "2026-07")
    assert res.committed is False and len(db.commit_calls) == calls
    print("[PASS] p_expected_rows is the number of rows stored by this import (skipped/unchanged excluded); "
          "replace_scope carries the file period and vehicle keys; partial writes never reach the RPC.")


def test_alias_with_another_vehicle_key_never_replaces_silently():
    # "AUH 44120" and "44120" may be one truck, but their keys differ: the summary must not supersede the dated rows
    db = FakeSummaryTableDB()
    tdated(db, [jul(3, "44120", 50, "A3")])
    res = tsummary(db, [["AUH 44120", 800, 2400.0]], "2026-07")
    assert res.committed and res.rows_replaced == 0, res
    assert len(db.visible()) == 1 and db.visible_summary_totals() == (800, 2400.0, 1)
    # Same key, different spelling: one vehicle, replaced as usual
    res = tsummary(db, [["auh-44120", 810, 2430.0]], "2026-07")
    assert res.rows_replaced == 1 and db.visible_summary_totals() == (810, 2430.0, 1)
    print("[PASS] Aliases with different keys never replace each other at import; the read rule then lets the "
          "summary take precedence for one Wialon unit, never counting both.")


def test_only_monthly_summaries_report_without_invented_daily_activity():
    tables = {"fuel_transactions": [], SUMMARY_TABLE: [s for s in copied_layout()[SUMMARY_TABLE]
                                                        if s["reporting_month"] == "2026-02-01"],
              "fuel_imports": IMPORTS}
    env = {"FUEL_IMPORT_REPLACE": "1", "FUEL_MONTHLY_SUMMARY_TABLE": "1", "FUEL_SCHEMA_V2": "",
           "SUPABASE_URL": "https://offline.invalid", "SUPABASE_SERVICE_ROLE_KEY": ""}
    fuel_db.clear_vehicle_identifier_cache()
    api = Api(units=UNITS)
    api.fetch = MagicMock(side_effect=lambda f, bearer_token=None: fuel_db.fetch_fuel_transactions_for_analytics(f, bearer_token))
    with patch.dict(os.environ, env), patch("app.fuel.db._fetch_all_pages", side_effect=PostgrestEmulator(tables)), api:
        trend = api.get("/api/fuel/analytics/trend?group_by=day&date_from=2026-02-01&date_to=2026-02-28")
        summary = api.get("/api/fuel/analytics/summary?date_from=2026-02-01&date_to=2026-02-28")
    assert all(not row.get("transaction_count") for row in trend["data"]), "no daily refuel events from a monthly total"
    assert summary["total_litres"] == 400.0 and summary["monthly_summary_rows"] == 1, summary
    print("[PASS] Only monthly summaries: KPIs count the total; the day trend shows no invented activity.")


TESTS = [
    test_monthly_summary_goes_to_its_own_table,
    test_summary_replaces_dated_rows_and_older_summary_of_its_vehicle_month,
    test_dated_import_skips_vehicle_months_with_a_stored_summary,
    test_ids_of_the_two_tables_never_mix,
    test_reimporting_a_summary_changes_nothing,
    test_failures_leave_visible_data_unchanged,
    test_commit_refuses_a_summary_racing_a_dated_import,
    test_preview_reports_the_reporting_month_not_a_date,
    test_reconcile_counts_the_summary_table_and_falls_back_to_legacy_rows,
    test_flag_off_never_names_the_new_table,
    test_reporting_window_matches_the_legacy_month_end_selection,
    test_summary_row_adapter_and_insert_payload,
    test_request_shapes_of_the_new_helpers,
    test_every_kpi_endpoint_is_identical_in_both_layouts,
    test_lists_report_summaries_by_month_only_with_the_flag,
    test_sql_scripts_are_ordered_and_destructive_statements_are_isolated,
    test_commit_function_script_matches_the_backend_contract,
    test_expected_rows_and_replace_scope_match_what_the_backend_stored,
    test_alias_with_another_vehicle_key_never_replaces_silently,
    test_only_monthly_summaries_report_without_invented_daily_activity,
]


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in TESTS:
            try:
                reset_fuel_read_caches()
                t()
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    print("=" * 60)
    print(f"{len(TESTS) - failures}/{len(TESTS)} test functions passed")
    print(f"[{'PASS' if not guard.attempts else 'FAIL'}] Network guard: {len(guard.attempts)} live network call(s) attempted.")
    sys.exit(1 if failures or guard.attempts else 0)
