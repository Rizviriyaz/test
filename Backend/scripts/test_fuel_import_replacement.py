"""
Offline tests for latest-import-wins (FUEL_IMPORT_REPLACE=1): replacement scope, staged inserts, the atomic commit,
repeated identical refuels, dated <-> monthly-summary switching, and failure paths that must leave the visible
dataset unchanged. Also checks the request shapes of the new database helpers and the review-only SQL.

FakeReplaceDB is an in-memory model of fuel_transactions with the new columns and of public.fuel_commit_import's
checks (every superseded row still visible, exactly the expected staged rows). Every outbound HTTP call is blocked
by OfflineGuard. These tests prove the logic on fixtures; they do not prove production data accuracy, and the SQL
function itself is only checked textually here (it has not been run against a database).

Run from Backend/:  python scripts/test_fuel_import_replacement.py
"""

import io
import os
import re
import sys
import traceback
from contextlib import contextmanager
from datetime import date
from unittest.mock import MagicMock, patch

import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.fuel.db as fuel_db  # noqa: E402
from app.fuel.db import FuelCommitError, FuelDataFetchError  # noqa: E402
from app.fuel.importer import FuelImportError, execute_fuel_import, generate_fuel_preview, plan_replacement  # noqa: E402

USER = "00000000-0000-0000-0000-000000000001"
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATED_HEADERS = ["Date", "Vehicle", "Litres", "Rate", "Amount", "Invoice"]
DATED_MAP = {"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres", "unit_price": "Rate",
             "total_cost": "Amount", "invoice_number": "Invoice"}
SUMMARY_HEADERS = ["Vehicle", "Litres", "Amount"]
SUMMARY_MAP = {"vehicle_identifier": "Vehicle", "litres": "Litres", "total_cost": "Amount"}


def xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeReplaceDB:
    """fuel_imports + fuel_transactions (with date_basis / staged / superseded_by_import_id) in memory."""

    def __init__(self):
        self.rows, self.imports = [], {}
        self.next_row, self.next_import = 1, 100
        self.insert_fail = False         # every insert batch is rejected
        self.commit_mode = "ok"          # ok | refuse | unknown_committed | unknown_not_committed
        self.visible_read_fails = False
        self.commit_calls = []

    # importer-facing fakes (same signatures as app.fuel.db)
    def create(self, file_name, imported_by, rows_found, bearer_token=None, deadline=None, replace_fields=None, **_):
        self.next_import += 1
        self.imports[self.next_import] = {"id": self.next_import, "file_name": file_name, "status": "processing",
                                          "rows_found": rows_found, **(replace_fields or {})}
        return self.next_import

    def update(self, import_id, updates, bearer_token=None, deadline=None):
        self.imports[import_id].update(updates)
        return True

    def insert(self, transactions, batch_size=100, bearer_token=None, deadline=None, outcome=None):
        if self.insert_fail:
            outcome.update(failed_rows=len(transactions),
                           failed_source_rows=[(t["source_row_number"], "rejected") for t in transactions])
            return 0, ["Batch 1 rejected by the database: HTTP 400"]
        for t in transactions:
            self.rows.append({**t, "id": self.next_row})
            self.next_row += 1
        return len(transactions), []

    def fetch_visible(self, date_from, date_to, bearer_token=None, deadline=None):
        if self.visible_read_fails:
            raise FuelDataFetchError("read failed")
        return [dict(r) for r in self.visible() if date_from <= str(r["transaction_date"])[:10] <= date_to]

    def fetch_import_rows(self, import_id, source_rows=None, bearer_token=None, deadline=None):
        return [{"id": r["id"], "source_row_number": r["source_row_number"], "transaction_date": r["transaction_date"],
                 "staged": r.get("staged")} for r in self.rows if r["import_id"] == import_id]

    def commit(self, import_id, supersede_ids, expected_rows, bearer_token=None, deadline=None):
        self.commit_calls.append((import_id, list(supersede_ids), expected_rows))
        if self.commit_mode == "refuse":
            raise FuelCommitError("rejected by the database (HTTP 400)")
        if self.commit_mode == "unknown_not_committed":
            return None
        by_id = {r["id"]: r for r in self.rows}
        # The SQL function's checks
        if any(by_id[i].get("superseded_by_import_id") or by_id[i].get("staged") for i in supersede_ids):
            raise FuelCommitError("replacement scope changed")
        staged = [r for r in self.rows if r["import_id"] == import_id and r.get("staged") is True]
        if len(staged) != expected_rows:
            raise FuelCommitError("staged count mismatch")
        for i in supersede_ids:
            by_id[i]["superseded_by_import_id"] = import_id
        for r in staged:
            r["staged"] = False
        if self.commit_mode == "unknown_committed":
            return None
        return {"superseded": len(supersede_ids), "committed": len(staged)}

    # inspection helpers
    def visible(self):
        return [r for r in self.rows if r.get("superseded_by_import_id") is None and r.get("staged") is not True]

    def snapshot(self):
        return sorted((r["id"], r["vehicle_identifier"], str(r["transaction_date"]), r["litres"], r["total_cost"])
                      for r in self.visible())

    def totals(self, vehicle=None, month=None):
        rows = [r for r in self.visible() if (vehicle is None or r["vehicle_identifier"] == vehicle)
                and (month is None or str(r["transaction_date"]).startswith(month))]
        return round(sum(r["litres"] for r in rows), 2), round(sum(r["total_cost"] for r in rows), 2), len(rows)


@contextmanager
def replace_env(db, v2=False, dated_replace=True):
    # dated_replace=False sets FUEL_IMPORT_REPLACE_DATED=0 (only monthly summaries replace)
    unused_dedup = MagicMock(side_effect=AssertionError("fingerprint dedup must not run in replacement mode"))
    unused_v2 = MagicMock(side_effect=AssertionError("on_conflict insert must not be used in replacement mode"))
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1", "FUEL_SCHEMA_V2": "1" if v2 else "",
                                 "FUEL_IMPORT_REPLACE_DATED": "" if dated_replace else "0"}), \
         patch("app.fuel.importer.fetch_running_imports", return_value=[]), \
         patch("app.fuel.importer.find_import_by_sha256", return_value=None), \
         patch("app.fuel.importer.create_fuel_import_record", side_effect=db.create), \
         patch("app.fuel.importer.update_fuel_import_record", side_effect=db.update), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=db.insert), \
         patch("app.fuel.importer.insert_fuel_transactions_ignore_duplicates", unused_v2), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", unused_dedup), \
         patch("app.fuel.importer.fetch_visible_rows_in_range", side_effect=db.fetch_visible), \
         patch("app.fuel.importer.fetch_import_rows", side_effect=db.fetch_import_rows), \
         patch("app.fuel.importer.commit_fuel_import", side_effect=db.commit):
        yield


def dated(db, rows, **kwargs):
    with replace_env(db, kwargs.pop("v2", False)):
        return execute_fuel_import(xlsx(DATED_HEADERS, rows), "dated.xlsx", DATED_MAP, USER, **kwargs)


def summary(db, rows, month, **kwargs):
    with replace_env(db):
        return execute_fuel_import(xlsx(SUMMARY_HEADERS, rows), "summary.xlsx", SUMMARY_MAP, USER,
                                   reporting_month=month, **kwargs)


def aug(day, vehicle, litres, inv=None, rate=3.0, hour=9):
    return [f"2026-08-{day:02d} {hour:02d}:00:00", vehicle, litres, rate, round(litres * rate, 2), inv]


AUGUST = [aug(d, v, 40 + d, f"{v}-{d}") for d in (3, 12, 18, 27) for v in ("AUH 44120", "DXB 10293")]


# ─── 13. Re-importing the same file ────────────────────────────────────────────

def test_reimport_same_file_changes_nothing():
    db = FakeReplaceDB()
    first = dated(db, AUGUST)
    assert first.committed and first.rows_imported == 8 and first.status == "completed", first
    before = db.snapshot()
    second = dated(db, AUGUST)
    assert second.committed and second.rows_imported == 0 and second.rows_unchanged == 8, second
    assert second.rows_replaced == 0 and second.status == "completed", second
    assert db.snapshot() == before and len(db.rows) == 8, "no new rows, the same rows stay visible"
    assert db.imports[second.import_id]["rows_unchanged"] == 8
    print("[PASS] Re-importing the same file: every row unchanged, nothing inserted or superseded, status completed.")


# ─── 14. A newer import replaces overlapping data without double counting ──────

def test_newer_import_replaces_only_its_vehicles_and_period():
    db = FakeReplaceDB()
    dated(db, AUGUST)
    newer = [aug(12, "AUH 44120", 60, "NEW-12"), aug(15, "AUH 44120", 30, "NEW-15"), aug(18, "AUH 44120", 61, "NEW-18")]
    res = dated(db, newer)
    # AUH 44120 rows of Aug 12-18 (the new file's period) are replaced; Aug 3 and 27 stay; DXB 10293 untouched
    assert res.committed and res.rows_replaced == 2 and res.rows_imported == 3, res
    assert res.replacement_period == {"period_from": "2026-08-12", "period_to": "2026-08-18"}
    auh = sorted((str(r["transaction_date"])[:10], r["litres"]) for r in db.visible() if r["vehicle_identifier"] == "AUH 44120")
    assert auh == [("2026-08-03", 43), ("2026-08-12", 60), ("2026-08-15", 30), ("2026-08-18", 61), ("2026-08-27", 67)], auh
    assert db.totals("DXB 10293") == (round(sum(40 + d for d in (3, 12, 18, 27)), 2),
                                      round(sum((40 + d) * 3.0 for d in (3, 12, 18, 27)), 2), 4)
    superseded = [r for r in db.rows if r.get("superseded_by_import_id") == res.import_id]
    assert len(superseded) == 2 and len(db.rows) == 11, "replaced rows are kept in the table, only hidden"
    print("[PASS] Newer import supersedes only its vehicles' rows inside its period; others untouched; nothing deleted.")


def test_dated_import_replaces_only_the_days_it_contains():
    db = FakeReplaceDB()
    dated(db, AUGUST)
    # A newer statement for Aug 3 and Aug 27 only: AUH's Aug 12 and 18 rows are not in it and must stay
    res = dated(db, [aug(3, "AUH 44120", 50, "R-3"), aug(27, "AUH 44120", 70, "R-27")])
    assert res.committed and res.rows_replaced == 2 and res.rows_imported == 2, res
    auh = sorted((str(r["transaction_date"])[:10], r["litres"]) for r in db.visible() if r["vehicle_identifier"] == "AUH 44120")
    assert auh == [("2026-08-03", 50), ("2026-08-12", 52), ("2026-08-18", 58), ("2026-08-27", 70)], auh
    assert db.totals("DXB 10293")[2] == 4, "other vehicle untouched"
    # Re-importing the same corrected file again changes nothing (no duplicates, no double counting)
    before = db.totals()
    again = dated(db, [aug(3, "AUH 44120", 50, "R-3"), aug(27, "AUH 44120", 70, "R-27")])
    assert again.rows_imported == 0 and again.rows_replaced == 0 and db.totals() == before, again
    print("[PASS] Dated statement replaces only its vehicles' rows on the days it contains; other days and vehicles stay.")


def test_vehicle_spelling_variants_are_one_vehicle():
    db = FakeReplaceDB()
    dated(db, [aug(5, "AUH-44120", 50, "A")])
    res = dated(db, [aug(5, "auh 44120", 55, "B")])
    assert res.rows_replaced == 1 and db.totals() == (55, 165.0, 1), (res, db.totals())
    print("[PASS] 'AUH-44120' and 'auh 44120' are the same vehicle for replacement.")


# ─── 15. Corrected files ───────────────────────────────────────────────────────

def test_corrected_file_replaces_changed_rows_only():
    db = FakeReplaceDB()
    dated(db, AUGUST)
    corrected = [list(r) for r in AUGUST]
    corrected[2][4] = 160.0  # one total corrected (VAT), everything else identical
    res = dated(db, corrected)
    assert (res.rows_imported, res.rows_replaced, res.rows_unchanged) == (1, 1, 7), res
    assert db.totals() == (round(sum(r[2] for r in AUGUST), 2),
                           round(sum(r[4] for r in corrected), 2), 8), db.totals()
    print("[PASS] Corrected file: only the changed row is replaced; totals equal the corrected statement exactly.")


# ─── 16. Legitimate repeated refuels ───────────────────────────────────────────

def test_identical_repeated_refuels_are_preserved():
    db = FakeReplaceDB()
    row = ["2026-08-07", "AUH 44120", 50, 3.0, 150.0, None]
    first = dated(db, [row, list(row)], single_date_mode="dated")  # one identical date: confirmed as refuels
    assert first.rows_imported == 2 and first.rows_identical_in_file == 1 and db.totals() == (100, 300.0, 2), first
    again = dated(db, [row, list(row)], single_date_mode="dated")
    assert again.rows_unchanged == 2 and again.rows_imported == 0 and db.totals() == (100, 300.0, 2), again
    third = dated(db, [row, list(row), list(row)], single_date_mode="dated")  # the provider's corrected statement shows a third identical fill
    assert (third.rows_unchanged, third.rows_imported, third.rows_replaced) == (2, 1, 0), third
    assert db.totals() == (150, 450.0, 3)
    fewer = dated(db, [row])  # and a later statement shows only one: the other two are replaced away
    assert (fewer.rows_unchanged, fewer.rows_replaced) == (1, 2) and db.totals() == (50, 150.0, 1), fewer
    print("[PASS] Identical refuels pair one-to-one: never collapsed, never doubled, follow the latest statement.")


def test_v2_fingerprints_stay_unique_for_repeats_and_reimports():
    db = FakeReplaceDB()
    row = ["2026-08-07", "AUH 44120", 50, 3.0, 150.0, None]
    dated(db, [row, list(row)], v2=True, single_date_mode="dated")
    dated(db, [["2026-08-07", "AUH 44120", 51, 3.0, 153.0, None]], v2=True)   # replaces both
    dated(db, [row, list(row)], v2=True, single_date_mode="dated")             # back to the original values
    fps = [r["fingerprint"] for r in db.rows]
    assert len(fps) == len(set(fps)) == 5 and all("|imp:" in f for f in fps), fps
    assert db.totals() == (100, 300.0, 2)
    print("[PASS] With FUEL_SCHEMA_V2 the stored fingerprints stay unique across repeats and re-imports.")


# ─── 17. Switching between dated transactions and monthly summaries ────────────

def test_summary_after_dated_replaces_the_month_for_its_vehicles():
    """Rule: a monthly summary replaces the daily transactions of its vehicle-month (superseded, never deleted)."""
    db = FakeReplaceDB()
    dated(db, AUGUST + [["2026-09-02", "AUH 44120", 20, 3.0, 60.0, "SEP"]])
    with replace_env(db):
        p = generate_fuel_preview(xlsx(SUMMARY_HEADERS, [["AUH 44120", 500, 1500.0]]), "s.xlsx", reporting_month="2026-08")
    assert p.ready_to_import and p.replacement["rows_to_replace"] == 4, (p.blocking_reasons, p.replacement)
    res = summary(db, [["AUH 44120", 500, 1500.0]], "2026-08",
                  expected_replace_count=p.replacement["expected_replace_count"])
    assert res.committed and res.rows_replaced == 4 and res.rows_imported == 1, res
    assert db.totals("AUH 44120", "2026-08") == (500, 1500.0, 1), "only the summary counts for AUH August"
    assert db.totals("AUH 44120", "2026-09") == (20, 60.0, 1), "September untouched"
    assert db.totals("DXB 10293", "2026-08")[2] == 4, "other vehicle untouched"
    assert sum(1 for r in db.rows if r.get("superseded_by_import_id") == res.import_id) == 4 and len(db.rows) == 10
    print("[PASS] Monthly summary over a vehicle-month with daily transactions replaces them; other scopes untouched.")


def test_dated_full_month_after_summary_keeps_it():
    """Rule: daily rows for a summarised vehicle-month are skipped (the summary is kept), even for whole months."""
    db = FakeReplaceDB()
    summary(db, [["AUH 44120", 500, 1500.0], ["DXB 10293", 300, 900.0]], "2026-08")
    full = [aug(1, "AUH 44120", 50, "x1"), aug(31, "AUH 44120", 40, "x31"),
            ["2026-09-05 08:00:00", "AUH 44120", 30, 3.0, 90.0, "S5"]]
    for extend in (False, True):
        before = db.snapshot()
        res = dated(db, full, extend_to_full_months=extend)
        assert res.committed and res.rows_skipped_monthly_summary == 2 and res.rows_replaced == 0, (extend, res)
        assert db.totals("AUH 44120", "2026-08") == (500, 1500.0, 1), "the August summary is kept"
        assert [g["month"] for g in res.monthly_summary_scopes_kept] == ["2026-08"], res.monthly_summary_scopes_kept
        assert all(r in db.snapshot() for r in before), "nothing visible was removed"
    assert db.totals("AUH 44120", "2026-09") == (30, 90.0, 1), "September imported once (re-import unchanged)"
    assert db.totals("DXB 10293") == (300, 900.0, 1)
    print("[PASS] Daily rows over a summarised vehicle-month are skipped, whole months or not; other months import.")


def test_partial_month_over_summary_is_refused_when_nothing_else_imports():
    db = FakeReplaceDB()
    summary(db, [["AUH 44120", 500, 1500.0]], "2026-08")
    before, commits = db.snapshot(), len(db.commit_calls)
    partial = [aug(3, "AUH 44120", 50, "p3"), aug(14, "AUH 44120", 40, "p14")]
    with replace_env(db):
        p = generate_fuel_preview(xlsx(DATED_HEADERS, partial), "p.xlsx")
    assert not p.ready_to_import and p.rows_skipped_monthly_summary == 2, (p.blocking_reasons, p)
    try:
        dated(db, partial)
        raise AssertionError("expected 409")
    except FuelImportError as exc:
        assert exc.status_code == 409 and "monthly summary is already stored for AUH 44120 2026-08" in exc.message, exc.message
    assert db.snapshot() == before and len(db.commit_calls) == commits
    assert db.imports[max(db.imports)]["status"] == "failed", "the refused import is closed as failed (history kept)"
    print("[PASS] A dated statement wholly inside summarised vehicle-months is refused; nothing changes.")


def test_summary_over_summary_and_other_months():
    db = FakeReplaceDB()
    summary(db, [["AUH 44120", 500, 1500.0], ["DXB 10293", 300, 900.0]], "2026-07")
    summary(db, [["AUH 44120", 450, 1350.0]], "2026-08")
    res = summary(db, [["AUH 44120", 470, 1410.0]], "2026-08")  # corrected August summary
    assert res.rows_replaced == 1 and db.totals("AUH 44120", "2026-08") == (470, 1410.0, 1), res
    assert db.totals(month="2026-07") == (800, 2400.0, 2), "July untouched"
    print("[PASS] A newer monthly summary replaces the same vehicle-month only.")


# ─── 18. Failed imports leave the visible data intact ──────────────────────────

def test_failures_leave_visible_data_unchanged():
    newer = [aug(12, "AUH 44120", 60, "NEW")]
    for mode in ("insert_fail", "refuse", "unknown_not_committed", "read_fail", "count_changed"):
        db = FakeReplaceDB()
        dated(db, AUGUST)
        before = db.snapshot()
        db.insert_fail = mode == "insert_fail"
        db.commit_mode = mode if mode in ("refuse", "unknown_not_committed") else "ok"
        db.visible_read_fails = mode == "read_fail"
        try:
            res = dated(db, newer, expected_replace_count=5 if mode == "count_changed" else None)
        except FuelImportError as exc:
            assert mode in ("read_fail", "count_changed"), (mode, exc.message)
            assert exc.status_code == (503 if mode == "read_fail" else 409), (mode, exc.status_code)
            assert len(db.rows) == 8, "refused before any row was written"
        else:
            assert res.status == "failed" and res.committed is False and res.rows_imported == 0, (mode, res)
            assert "dashboard still shows the previously imported data" in res.error_message, (mode, res.error_message)
            assert res.rows_found == res.rows_imported + res.rows_duplicate + res.rows_failed + res.rows_ignored
        assert db.snapshot() == before, f"{mode}: visible data changed"
        assert all(r.get("staged") is True for r in db.rows if r["import_id"] != min(db.imports)), "new rows stay hidden"
    print("[PASS] Insert failure, refused or unanswered commit, unreadable scope and a changed scope: data unchanged.")


def test_unanswered_commit_is_settled_by_reading_back():
    db = FakeReplaceDB()
    dated(db, AUGUST)
    db.commit_mode = "unknown_committed"
    res = dated(db, [aug(12, "AUH 44120", 60, "NEW")])
    assert res.committed and res.rows_imported == 1 and "verified by reading the import back" in res.error_message, res
    assert db.totals("AUH 44120", "2026-08")[2] == 4
    print("[PASS] A commit whose answer was lost is confirmed from the import's own rows (all visible).")


def test_flag_off_keeps_legacy_import_path():
    sent, fetched = [], []
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "", "FUEL_SCHEMA_V2": ""}), \
         patch("app.fuel.importer.fetch_running_imports", return_value=[]), \
         patch("app.fuel.importer.create_fuel_import_record", return_value=5) as create, \
         patch("app.fuel.importer.update_fuel_import_record", return_value=True) as update, \
         patch("app.fuel.importer.insert_fuel_transactions_batch",
               side_effect=lambda transactions, **_: (sent.extend(transactions) or len(transactions), [])), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints",
               side_effect=lambda **kw: fetched.append(kw) or set()), \
         patch("app.fuel.importer.fetch_visible_rows_in_range", side_effect=AssertionError("no scope read")), \
         patch("app.fuel.importer.commit_fuel_import", side_effect=AssertionError("no commit")):
        res = execute_fuel_import(xlsx(DATED_HEADERS, AUGUST), "a.xlsx", DATED_MAP, USER)
    assert res.rows_imported == 8 and res.committed is None and res.replacement_enabled is False and fetched
    assert not ({"date_basis", "staged"} & set().union(*map(set, sent))), "no new columns sent"
    assert "rows_replaced" not in update.call_args.kwargs["updates"]
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "", "SUPABASE_URL": "https://offline.invalid"}), \
            patch("app.fuel.db.requests.post") as post:
        post.return_value = MagicMock(ok=True, json=MagicMock(return_value=[{"id": 1}]))
        fuel_db.create_fuel_import_record("f.xlsx", "", 1, replace_fields={"import_mode": "dated"})
    assert "import_mode" not in post.call_args.kwargs["json"]
    print("[PASS] FUEL_IMPORT_REPLACE unset: legacy duplicate check, no new columns, no scope read, no commit.")


# ─── Planner and database helpers ──────────────────────────────────────────────

def test_plan_replacement_is_pure_and_scoped():
    cands = [{"fingerprint": "x", "date_basis": "transaction",
              "fields": {"vehicle_identifier": "V1", "transaction_date": "2026-08-10"}}]
    stored = [
        {"id": 1, "vehicle_identifier": "V1", "transaction_date": "2026-08-10T08:00:00", "litres": 5, "total_cost": 15},
        {"id": 2, "vehicle_identifier": "V2", "transaction_date": "2026-08-10", "litres": 5, "total_cost": 15},
        {"id": 3, "vehicle_identifier": "V1", "transaction_date": "2026-08-11", "litres": 5, "total_cost": 15},
        {"id": 4, "vehicle_identifier": "V1", "transaction_date": "garbage", "litres": 5, "total_cost": 15},
    ]
    plan = plan_replacement(cands, (date(2026, 8, 10), date(2026, 8, 10)), stored)
    assert [r["id"] for r in plan["supersede"]] == [1] and [r["id"] for r in plan["conflicts"]] == [4], plan
    print("[PASS] Planner: only the file's vehicles inside the period; an unreadable stored date blocks (never guessed).")


def test_db_helpers_request_shapes():
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1", "SUPABASE_URL": "https://offline.invalid"}):
        with patch("app.fuel.db._fetch_all_pages", return_value=[]) as pages:
            fuel_db.fetch_visible_rows_in_range("2026-08-01", "2026-08-31")
            fuel_db.fetch_fuel_transactions_for_analytics({"date_from": "2026-08-01", "date_to": "2026-08-31"})
            fuel_db.fetch_fuel_vehicle_identifiers()
            fuel_db.fetch_existing_transaction_fingerprints("2026-08-01", "2026-08-31")
        for call in pages.call_args_list:
            params = call.args[1]
            assert params["superseded_by_import_id"] == "is.null" and params["staged"] == "not.is.true", params
        assert "date_basis" in pages.call_args_list[1].args[1]["select"]
        assert pages.call_args_list[0].args[1]["and"] == ("(transaction_date.gte.2026-08-01T00:00:00,"
                                                          "transaction_date.lte.2026-08-31T23:59:59.999999)")
        ok = MagicMock(ok=True, status_code=200, json=MagicMock(return_value={"superseded": 2, "committed": 3}))
        with patch("app.fuel.db.requests.post", return_value=ok) as post:
            assert fuel_db.commit_fuel_import(7, [1, 2], 3) == {"superseded": 2, "committed": 3}
        assert post.call_args.args[0].endswith("/rest/v1/rpc/fuel_commit_import")
        assert post.call_args.kwargs["json"] == {"p_import_id": 7, "p_supersede_ids": [1, 2], "p_expected_rows": 3}
        with patch("app.fuel.db.requests.post", return_value=MagicMock(ok=False, status_code=400, text="scope changed")):
            try:
                fuel_db.commit_fuel_import(7, [1], 1)
                raise AssertionError("expected FuelCommitError")
            except FuelCommitError:
                pass
        with patch("app.fuel.db.requests.post", return_value=MagicMock(ok=False, status_code=504, text="")):
            assert fuel_db.commit_fuel_import(7, [1], 1) is None, "gateway timeout: outcome unknown"
        with patch("app.fuel.db.requests.post", side_effect=fuel_db.requests.ReadTimeout("t")):
            assert fuel_db.commit_fuel_import(7, [1], 1) is None
        with patch("app.fuel.db.requests.post", side_effect=fuel_db.requests.exceptions.ConnectTimeout("c")):
            try:
                fuel_db.commit_fuel_import(7, [1], 1)
                raise AssertionError("expected FuelCommitError (never sent)")
            except FuelCommitError:
                pass
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "", "SUPABASE_URL": "https://offline.invalid"}), \
            patch("app.fuel.db._fetch_all_pages", return_value=[]) as pages:
        fuel_db.fetch_fuel_transactions_for_analytics({})
    assert "staged" not in pages.call_args.args[1] and "date_basis" not in pages.call_args.args[1]["select"]
    print("[PASS] DB helpers: visible-only filters on every read with the flag (none without); commit RPC outcomes.")


def test_sql_is_review_only_and_non_destructive():
    sql = open(os.path.join(BASE_DIR, "sql", "fuel_import_replacement.sql"), encoding="utf-8").read()
    live = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "REVIEW ONLY" in sql and "SECURITY INVOKER" in live and "pg_advisory_xact_lock" in live
    assert not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b", live, re.I), "no DELETE/TRUNCATE outside comments"
    assert "DROP COLUMN" not in live and "DROP TABLE" not in live, "drops only in the commented rollback"
    for col in ("date_basis", "superseded_by_import_id", "superseded_at", "staged", "import_mode", "reporting_month",
                "replace_scope", "rows_replaced", "rows_unchanged"):
        assert col in live, col
    assert "GRANT EXECUTE ON FUNCTION public.fuel_commit_import" in live and "FROM PUBLIC, anon" in live
    print("[PASS] Migration SQL: additive, review-only, no live DELETE/DROP, invoker-rights commit function.")


def test_monthly_only_flag_keeps_dated_imports_legacy():
    """FUEL_IMPORT_REPLACE=1 with FUEL_IMPORT_REPLACE_DATED=0: dated statements import as before, except that the
    visible rows are read (never changed) to skip vehicle-months that have a monthly summary."""
    sent, fetched, scope_reads = [], [], []
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1", "FUEL_IMPORT_REPLACE_DATED": "0", "FUEL_SCHEMA_V2": ""}), \
         patch("app.fuel.importer.fetch_running_imports", return_value=[]), \
         patch("app.fuel.importer.create_fuel_import_record", return_value=5), \
         patch("app.fuel.importer.update_fuel_import_record", return_value=True) as update, \
         patch("app.fuel.importer.insert_fuel_transactions_batch",
               side_effect=lambda transactions, **_: (sent.extend(transactions) or len(transactions), [])), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints",
               side_effect=lambda **kw: fetched.append(kw) or set()), \
         patch("app.fuel.importer.fetch_visible_rows_in_range",
               side_effect=lambda *a, **kw: scope_reads.append(a) or []), \
         patch("app.fuel.importer.commit_fuel_import", side_effect=AssertionError("no commit")):
        res = execute_fuel_import(xlsx(DATED_HEADERS, AUGUST), "a.xlsx", DATED_MAP, USER)
        p = generate_fuel_preview(xlsx(DATED_HEADERS, AUGUST), "a.xlsx")
    assert res.rows_imported == 8 and res.committed is None and fetched, res
    assert scope_reads == [("2026-08-03", "2026-08-31")] * 2, "summary check read by the import and the preview"
    assert not ({"staged", "date_basis"} & set().union(*map(set, sent))), "dated rows: no staging, nothing superseded"
    assert "rows_replaced" not in update.call_args.kwargs["updates"]
    assert p.replacement is None and p.monthly_summary_enabled, "preview: no replacement plan for dated files"
    print("[PASS] FUEL_IMPORT_REPLACE_DATED=0: dated imports keep the legacy path (duplicate check, no staging, no commit).")


def test_monthly_only_flag_still_commits_summaries():
    db = FakeReplaceDB()
    with replace_env(db, dated_replace=False):
        res = execute_fuel_import(xlsx(SUMMARY_HEADERS, [["AUH 44120", 500, 1500.0]]), "s.xlsx", SUMMARY_MAP, USER,
                                  reporting_month="2026-08")
        again = execute_fuel_import(xlsx(SUMMARY_HEADERS, [["AUH 44120", 520, 1560.0]]), "s2.xlsx", SUMMARY_MAP, USER,
                                    reporting_month="2026-08")
    assert res.committed and again.committed and again.rows_replaced == 1, (res, again)
    assert db.totals("AUH 44120", "2026-08") == (520, 1560.0, 1), "a re-imported month replaces its summary"
    print("[PASS] Monthly-only flag: summaries are staged and committed; a re-imported month replaces the old summary.")


def test_import_record_update_uses_service_role_and_detects_no_row():
    ok_row = MagicMock(ok=True, status_code=200, json=MagicMock(return_value=[{"id": 7}]))
    no_row = MagicMock(ok=True, status_code=200, json=MagicMock(return_value=[]))
    env = {"SUPABASE_URL": "https://offline.invalid", "SUPABASE_SERVICE_ROLE_KEY": "service-key-offline"}
    with patch.dict(os.environ, env), patch("app.fuel.db.requests.patch", return_value=ok_row) as patch_call:
        assert fuel_db.update_fuel_import_record(7, {"status": "completed"}, bearer_token="user-jwt") is True
    assert patch_call.call_args.kwargs["headers"]["Authorization"] == "Bearer service-key-offline", "service role used"
    with patch.dict(os.environ, env), patch("app.fuel.db.requests.patch", return_value=no_row):
        assert fuel_db.update_fuel_import_record(7, {"status": "completed"}, bearer_token="user-jwt") is False
    with patch.dict(os.environ, {"SUPABASE_URL": "https://offline.invalid", "SUPABASE_SERVICE_ROLE_KEY": ""}), \
            patch("app.fuel.db.requests.patch", return_value=ok_row) as patch_call:
        assert fuel_db.update_fuel_import_record(7, {"status": "completed"}, bearer_token="user-jwt") is True
    assert patch_call.call_args.kwargs["headers"]["Authorization"] == "Bearer user-jwt", "no service key: user token"
    print("[PASS] Import-record updates use the service-role key when configured; a 200 with no row is a failure.")


def test_definer_commit_sql_checks_the_caller():
    sql = open(os.path.join(BASE_DIR, "sql", "fuel_commit_import_definer.sql"), encoding="utf-8").read()
    live = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "REVIEW ONLY" in sql and "SECURITY DEFINER" in live and "SET search_path = public, pg_temp" in live
    for check in ("auth.uid()", "public.profiles", "'admin', 'manager', 'fleet_manager'",
                  "imported_by IS DISTINCT FROM v_uid", "replace_scope", "staged IS TRUE", "pg_advisory_xact_lock"):
        assert check in live, check
    assert "FROM PUBLIC, anon, authenticated" in live and "TO authenticated" in live and "service_role" not in live
    assert not re.search(r"\bDELETE\s+FROM\b|\bTRUNCATE\b|\bDROP\b|CREATE POLICY", live, re.I)
    print("[PASS] Definer commit SQL: review-only; checks sign-in, role, import owner and scope; no policy changes.")


TESTS = [
    test_monthly_only_flag_keeps_dated_imports_legacy,
    test_monthly_only_flag_still_commits_summaries,
    test_import_record_update_uses_service_role_and_detects_no_row,
    test_definer_commit_sql_checks_the_caller,
    test_reimport_same_file_changes_nothing,
    test_newer_import_replaces_only_its_vehicles_and_period,
    test_dated_import_replaces_only_the_days_it_contains,
    test_vehicle_spelling_variants_are_one_vehicle,
    test_corrected_file_replaces_changed_rows_only,
    test_identical_repeated_refuels_are_preserved,
    test_v2_fingerprints_stay_unique_for_repeats_and_reimports,
    test_summary_after_dated_replaces_the_month_for_its_vehicles,
    test_dated_full_month_after_summary_keeps_it,
    test_partial_month_over_summary_is_refused_when_nothing_else_imports,
    test_summary_over_summary_and_other_months,
    test_failures_leave_visible_data_unchanged,
    test_unanswered_commit_is_settled_by_reading_back,
    test_flag_off_keeps_legacy_import_path,
    test_plan_replacement_is_pure_and_scoped,
    test_db_helpers_request_shapes,
    test_sql_is_review_only_and_non_destructive,
]


def main():
    print("=" * 60)
    print("FUEL IMPORT - LATEST IMPORT WINS (OFFLINE)")
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
