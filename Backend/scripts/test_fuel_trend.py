"""
Offline regression tests for the fuel purchase & cost trend (/api/fuel/analytics/trend).

  - wall-clock day bucketing (offsets discarded, never converted), Monday week starts, month/year rollover
  - refunds stay signed inside their period; refund-only periods report a 0.0 unit price
  - undated / unparseable rows skipped; group_by normalised; invalid group_by -> ValueError / HTTP 400
  - route: KPI scope only, periods reconcile with the summary, excluded_scope attached, no Wialon reports

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:  python scripts/test_fuel_trend.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import AUTH, Q, Api, assert_reconciles, run  # noqa: E402

from app.fuel.analytics import calculate_fuel_trend  # noqa: E402


def fuel(date, litres, cost):
    return {"vehicle_identifier": "V-1", "transaction_date": date, "litres": litres, "total_cost": cost}


def periods(trend):
    return [(d.period, d.litres, d.cost, d.transactions, d.average_unit_price) for d in trend.data]


# ─── Tests ─────────────────────────────────────────────────────────────────────

def test_wallclock_day_bucketing():
    rows = [
        fuel("2026-08-03T23:30:00Z", 10.0, 30.0),       # 'Z' discarded: stays on 08-03
        fuel("2026-08-03T23:59:59+04:00", 20.0, 60.0),  # offset discarded
        fuel("2026-08-04", 5.0, 15.0),                  # date-only
        fuel("2026-08-04T00:00:00", 5.0, 15.0),
    ]
    assert periods(calculate_fuel_trend(rows, "day")) == [
        ("2026-08-03", 30.0, 90.0, 2, 3.0),
        ("2026-08-04", 10.0, 30.0, 2, 3.0),
    ]
    print("[PASS] Day buckets use the stored wall clock; UTC offsets are discarded, not converted.")


def test_week_and_month_boundaries():
    rows = [
        fuel("2026-08-30T10:00:00", 10.0, 30.0),  # Sunday -> week of Mon 08-24
        fuel("2026-08-31T10:00:00", 20.0, 60.0),  # Monday -> week of 08-31
        fuel("2026-09-01T10:00:00", 30.0, 90.0),  # Tuesday, new month -> still week of 08-31
        fuel("2026-12-31T10:00:00", 40.0, 120.0),
        fuel("2027-01-01T10:00:00", 50.0, 150.0),
    ]
    week = calculate_fuel_trend(rows, "week")
    assert [(d.period, d.litres, d.transactions) for d in week.data] == [
        ("2026-08-24", 10.0, 1), ("2026-08-31", 50.0, 2), ("2026-12-28", 90.0, 2),
    ], periods(week)
    month = calculate_fuel_trend(rows, "month")
    assert [(d.period, d.litres) for d in month.data] == [
        ("2026-08", 30.0), ("2026-09", 30.0), ("2026-12", 40.0), ("2027-01", 50.0),
    ], periods(month)
    print("[PASS] Weeks start on Monday across month/year ends; months roll over the year correctly.")


def test_refunds_stay_signed():
    rows = [
        fuel("2026-08-03T08:00:00", 100.0, 300.0),
        fuel("2026-08-03T18:00:00", -10.0, -30.0),
        fuel("2026-08-04T09:00:00", -5.0, -15.0),  # refund-only period
    ]
    assert periods(calculate_fuel_trend(rows, "day")) == [
        ("2026-08-03", 90.0, 270.0, 2, 3.0),
        ("2026-08-04", -5.0, -15.0, 1, 0.0),
    ]
    print("[PASS] Refunds net inside their period; refund-only periods report unit price 0.0.")


def test_bad_rows_and_group_by():
    rows = [
        fuel(None, 99.0, 99.0),
        fuel("", 99.0, 99.0),
        fuel("not-a-date", 99.0, 99.0),
        fuel("2026-02-30", 99.0, 99.0),
        fuel("2026-08-03T08:00:00", 10.0, None),
        {"transaction_date": "2026-08-03T09:00:00"},
    ]
    trend = calculate_fuel_trend(rows, " Day ")
    assert trend.group_by == "day"
    assert periods(trend) == [("2026-08-03", 10.0, 0.0, 2, 0.0)], periods(trend)
    assert calculate_fuel_trend([], "MONTH").group_by == "month"
    try:
        calculate_fuel_trend(rows, "year")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported group_by must raise")
    print("[PASS] Undated / invalid rows skipped; missing amounts count as 0; group_by normalised and validated.")


def test_route_kpi_scope_and_reconciliation():
    with Api() as api:
        day = api.get(f"/api/fuel/analytics/trend?{Q}&group_by=day")
        week = api.get(f"/api/fuel/analytics/trend?{Q}&group_by=WEEK")
        summary = api.get(f"/api/fuel/analytics/summary?{Q}")
        bad = api.client.get(f"/api/fuel/analytics/trend?{Q}&group_by=year", headers=AUTH)
        fetches_after_bad = api.fetch.call_count
        bundle_calls, trip_calls = api.bundle.call_count, api.trips.call_count

    # KPI rows only (1, 2, 3, 4, 6): unmatched, ambiguous and the cross-import duplicate are excluded.
    assert [(d["period"], d["litres"], d["cost"], d["transactions"]) for d in day["data"]] == [
        ("2026-08-03", 260.0, 780.0, 3),
        ("2026-08-04", 50.0, 150.0, 1),
        ("2026-08-05", -10.0, -30.0, 1),
    ], day["data"]
    assert week["group_by"] == "week"
    assert [(d["period"], d["litres"], d["transactions"]) for d in week["data"]] == [("2026-08-03", 300.0, 5)]
    for body in (day, week):
        assert_reconciles(body["excluded_scope"])
        assert round(sum(d["litres"] for d in body["data"]), 2) == summary["total_litres"]
        assert round(sum(d["cost"] for d in body["data"]), 2) == summary["total_cost"]
        assert sum(d["transactions"] for d in body["data"]) == summary["transaction_count"]

    assert bad.status_code == 400 and "group_by" in bad.json()["detail"], bad.text
    assert fetches_after_bad == 3, "invalid group_by is rejected before any database read"
    assert bundle_calls == 0 and trip_calls == 0, "the purchase trend never runs Wialon reports"
    print("[PASS] Route: KPI scope only, reconciles with summary, excluded_scope attached, 400 before DB, no reports.")


TESTS = [
    test_wallclock_day_bucketing,
    test_week_and_month_boundaries,
    test_refunds_stay_signed,
    test_bad_rows_and_group_by,
    test_route_kpi_scope_and_reconciliation,
]


if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PURCHASE & COST TREND (OFFLINE)") else 0)
