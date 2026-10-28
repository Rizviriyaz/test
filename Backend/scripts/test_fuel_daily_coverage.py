"""
Offline regression tests for the daily fuel view (/api/fuel/analytics/daily) and its statement coverage.

  - every calendar day is listed; a day is covered only when a committed dated import confirmed as covering whole
    months (replace_scope.extended_to_full_months) covers it for every vehicle in scope
  - a day without rows is a verified 0 only when covered; otherwise null (unknown), never 0
  - first/last transaction dates, unconfirmed or errored imports, and monthly summaries never establish coverage
  - a fuel-type filter never turns an uncovered day into a verified zero; future days are never covered
  - route: FUEL_IMPORT_REPLACE off -> metadata unavailable, unverified; range validation

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:  python scripts/test_fuel_daily_coverage.py
"""
import os
import sys
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import AUTH, Api, run  # noqa: E402

from app.fuel.analytics import calculate_daily_fuel  # noqa: E402

TODAY = date(2026, 10, 28)
UNITS = [{"id": 1, "nm": "V-1"}, {"id": 2, "nm": "V-2"}]
SEPT = {"status": "completed", "import_mode": "dated",
        "replace_scope": {"period_from": "2026-09-01", "period_to": "2026-09-30", "extended_to_full_months": True}}


def row(i, uid, day, litres, cost, imp=7, fuel="DIESEL", **extra):
    return {"id": i, "import_id": imp, "_unit_id": uid, "vehicle_identifier": f"V-{uid}", "fuel_type": fuel,
            "transaction_date": f"2026-09-{day:02d}T08:00:00", "litres": litres, "total_cost": cost, **extra}


ROWS = [row(1, 1, 3, 50.25, 150.76), row(2, 1, 10, 40.0, 120.0), row(3, 2, 5, 60.0, 180.0),
        row(4, 2, 6, 20.0, 66.0, fuel="SUPER"), row(5, 1, 12, -5.0, -15.0)]


def daily(kpi_rows, coverage_rows=None, meta=None, date_from="2026-09-01", date_to="2026-09-30"):
    return calculate_daily_fuel(kpi_rows, ROWS if coverage_rows is None else coverage_rows,
                                {7: SEPT} if meta is None else meta, date_from, date_to, TODAY)


def by_day(res):
    return {d.date: d for d in res.data}


def test_confirmed_month_lists_every_day_with_verified_zeroes():
    res = daily(ROWS)
    days = by_day(res)
    assert res.total_days == 30 and len(res.data) == 30 and res.coverage_status == "statement_verified" and res.covered_days == 30
    assert days["2026-09-01"].litres == 0.0 and days["2026-09-01"].dated_purchase_count == 0  # covered: a real zero
    assert days["2026-09-03"].litres == 50.25 and days["2026-09-03"].cost == 150.76
    assert (days["2026-09-12"].refund_count, days["2026-09-12"].dated_purchase_count, days["2026-09-12"].litres) == (1, 0, -5.0)
    assert days["2026-09-06"].units_covered == 2 == days["2026-09-06"].units_in_scope
    assert round(sum(d.litres for d in res.data), 2) == 165.25 and res.dated_row_count == 5
    assert res.supplier_completeness == "not_established" and "supplier" in res.coverage_note  # never claims every supplier
    print("[PASS] Confirmed full-month statement: all 30 days listed, rowless days are verified 0.0, refunds counted apart.")


def test_unconfirmed_evidence_leaves_days_unknown():
    first_last = {**SEPT, "replace_scope": {**SEPT["replace_scope"], "extended_to_full_months": False}}
    for meta in ({}, {7: first_last}, {7: {**SEPT, "status": "completed_with_errors"}},
                 {7: {**SEPT, "import_mode": "monthly_summary"}}):
        res = daily(ROWS, meta=meta)
        days = by_day(res)
        assert res.coverage_status == "unverified" and res.covered_days == 0, meta
        assert days["2026-09-01"].litres is None and days["2026-09-01"].dated_purchase_count is None  # unknown, not 0
        assert days["2026-09-03"].litres == 50.25 and days["2026-09-03"].coverage == "not_covered"  # recorded rows kept
    print("[PASS] No metadata, first/last dates, errored or summary imports: no day verified; rowless days are null.")


def test_every_vehicle_must_be_covered():
    rows = ROWS[:2] + [dict(r, import_id=8) for r in ROWS[2:4]]  # V-2's statement (import 8) is not confirmed
    res = daily(rows, coverage_rows=rows, meta={7: SEPT, 8: {"status": "completed", "import_mode": "dated"}})
    assert res.coverage_status == "unverified" and by_day(res)["2026-09-01"].units_covered == 1
    v1 = [r for r in rows if r["_unit_id"] == 1]
    only_v1 = daily(v1, coverage_rows=v1, meta={7: SEPT, 8: {}})
    assert only_v1.coverage_status == "statement_verified" and only_v1.units_in_scope == 1
    part = {**SEPT, "replace_scope": {**SEPT["replace_scope"], "period_to": "2026-09-15"}}
    half = daily(v1, coverage_rows=v1, meta={7: part})
    assert half.coverage_status == "partial" and half.covered_days == 15 and by_day(half)["2026-09-20"].litres is None
    print("[PASS] A day is covered only when every vehicle in scope is; a vehicle filter narrows that set; half month -> partial.")


def test_fuel_type_filter_never_makes_uncovered_days_zero():
    super_rows = [r for r in ROWS if r["fuel_type"] == "SUPER"]
    res = daily(super_rows)
    assert res.coverage_status == "statement_verified" and by_day(res)["2026-09-03"].litres == 0.0  # covered: no Super that day
    assert by_day(res)["2026-09-06"].litres == 20.0
    unknown = daily(super_rows, meta={})
    assert by_day(unknown)["2026-09-03"].litres is None and unknown.coverage_status == "unverified"
    print("[PASS] Fuel-type filter: coverage judged on every fuel type; uncovered days stay null under the filter.")


def test_monthly_summaries_never_become_daily_activity():
    summary = row(9, 1, 30, 400.0, 1200.0, imp=9, date_basis="reporting_month_end")
    res = daily(ROWS + [summary], coverage_rows=ROWS + [summary], meta={7: SEPT, 9: SEPT})
    assert by_day(res)["2026-09-30"].litres == 0.0 and res.monthly_summary_excluded["rows"] == 1
    only_summary = daily([summary], coverage_rows=[summary], meta={9: SEPT})
    assert only_summary.coverage_status == "unverified" and by_day(only_summary)["2026-09-30"].litres is None
    print("[PASS] Monthly summaries are never placed on a day and never establish coverage.")


def test_future_days_are_never_covered():
    octo = {"status": "completed", "import_mode": "dated",
            "replace_scope": {"period_from": "2026-10-01", "period_to": "2026-10-31", "extended_to_full_months": True}}
    rows = [dict(row(1, 1, 3, 10.0, 30.0), transaction_date="2026-10-03T08:00:00")]
    res = daily(rows, coverage_rows=rows, meta={7: octo}, date_from="2026-10-01", date_to="2026-10-31")
    days = by_day(res)
    assert days["2026-10-28"].coverage == "statement_covered" and days["2026-10-29"].coverage == "future"
    assert days["2026-10-29"].litres is None and res.coverage_status == "partial" and res.covered_days == 28
    print("[PASS] Days after today are 'future' (null), so a month in progress is never verified.")


def test_route_flag_off_is_unverified_and_ranges_validated():
    rows = [{k: v for k, v in r.items() if k != "_unit_id"} for r in ROWS]
    with Api(rows=rows, units=UNITS) as api, patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": ""}):
        body = api.get("/api/fuel/analytics/daily?date_from=2026-09-01&date_to=2026-09-30")
        assert body["coverage_metadata_available"] is False and body["coverage_status"] == "unverified"
        assert len(body["data"]) == 30 and body["data"][0]["litres"] is None
        assert any("FUEL_IMPORT_REPLACE=1" in w for w in body["data_warnings"])
        assert body["excluded_scope"]["kpi"]["transaction_count"] == 5
        assert api.client.get("/api/fuel/analytics/daily?date_from=2026-08-01&date_to=2026-10-15", headers=AUTH).status_code == 400
        assert api.client.get("/api/fuel/analytics/daily?date_from=2026-09-01", headers=AUTH).status_code == 422
    with Api(rows=rows, units=UNITS) as api, patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1"}), \
         patch("app.fuel.routes.fetch_import_coverage", return_value={7: SEPT}) as cov:
        body = api.get("/api/fuel/analytics/daily?date_from=2026-09-01&date_to=2026-09-30&fuel_type=SUPER")
        assert body["coverage_status"] == "statement_verified" and body["coverage_metadata_available"] is True
        assert sum(d["litres"] for d in body["data"]) == 20.0 and body["excluded_scope"]["kpi"]["transaction_count"] == 1
        assert cov.call_args[0][0] == {7}
    print("[PASS] Route: flag off -> unverified with a blocker warning; flag on -> verified Super zeroes; ranges validated.")


TESTS = [
    test_confirmed_month_lists_every_day_with_verified_zeroes,
    test_unconfirmed_evidence_leaves_days_unknown,
    test_every_vehicle_must_be_covered,
    test_fuel_type_filter_never_makes_uncovered_days_zero,
    test_monthly_summaries_never_become_daily_activity,
    test_future_days_are_never_covered,
    test_route_flag_off_is_unverified_and_ranges_validated,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL DAILY VIEW - STATEMENT COVERAGE (OFFLINE)") else 0)
