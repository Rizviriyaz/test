"""
Offline route-level regression tests: the fuel API routes wire the shared rules together consistently.

  - fleet idle fuel and cost agree across /efficiency, /efficiency/vehicles and /waste (the per-vehicle estimates)
  - the idle ranking keeps vehicles with known idle hours but unknown distance (no status filter needed)
  - /efficiency/trend passes the per-month mixed-fuel map: mixed-fuel exclusion follows the month
  - fuel reporting uses monthly summaries only: dated rows are never combined with them or used as a fallback

Wialon and Supabase are mocked (test_fuel_scope.Api); OfflineGuard blocks every outbound HTTP call.
Run from Backend/:  python scripts/test_fuel_route_consistency.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import Api, run  # noqa: E402

import app.fuel.routes as routes  # noqa: E402

UAE = timezone(timedelta(hours=4))
UNITS = [{"id": 1, "nm": "V-1"}, {"id": 2, "nm": "V-2"}, {"id": 3, "nm": "V-3"}]
AUG = "date_from=2026-08-01&date_to=2026-08-31"
FULL_AUG = {"status": "completed", "import_mode": "dated",
            "replace_scope": {"period_from": "2026-08-01", "period_to": "2026-08-31", "extended_to_full_months": True}}


def tx(i, vid, date, litres, cost, fuel="DIESEL", imp=1, **extra):
    return {"id": i, "import_id": imp, "vehicle_identifier": vid, "transaction_date": date, "litres": litres,
            "total_cost": cost, "fuel_type": fuel, "invoice_number": f"INV-{i}", "odometer_km": None, **extra}


def summary_tx(i, vid, litres, cost, fuel="DIESEL", month_end="2026-08-31", imp=1):
    """A monthly-summary row (legacy layout) dated at its reporting month end: the fuel reporting source."""
    return tx(i, vid, f"{month_end}T00:00:00", litres, cost, fuel=fuel, imp=imp, date_basis="reporting_month_end")


def ts(y, m, d, hh=12):
    return int(datetime(y, m, d, hh, tzinfo=UAE).timestamp())


IDLE_ROWS = [summary_tx(1, "V-1", 100.0, 300.0), summary_tx(2, "V-2", 60.0, 185.4), summary_tx(3, "V-3", 40.0, 121.0)]
IDLE_BUNDLE = {
    "by_name": {
        "V-1": {"mileage_km": 500.0, "driving_seconds": 36000, "idle_seconds": 3755, "engine_hours_seconds": 39755},
        "V-2": {"driving_seconds": 1790, "idle_seconds": 7210, "engine_hours_seconds": 9000},  # distance unknown
        "V-3": {"mileage_km": 300.0, "driving_seconds": 18000, "idle_seconds": 1801, "engine_hours_seconds": 19801},
    },
    "duplicate_names": [], "status": "complete", "errors": [], "warnings": [],
}


def test_fleet_idle_agrees_across_efficiency_rankings_and_waste():
    with Api(rows=IDLE_ROWS, units=UNITS) as api:
        api.bundle.return_value = IDLE_BUNDLE
        eff = api.get(f"/api/fuel/analytics/efficiency?{AUG}&idle_rate_lph=2.5")
        ranked = api.get(f"/api/fuel/analytics/efficiency/vehicles?{AUG}&idle_rate_lph=2.5&sort_by=estimated_idle_fuel&page_size=100")
        matched_only = api.get(f"/api/fuel/analytics/efficiency/vehicles?{AUG}&idle_rate_lph=2.5&status=matched&page_size=100")
        waste = api.get(f"/api/fuel/analytics/waste?{AUG}&idle_rate_lph=2.5&idle_threshold_hours=0.5&idle_threshold_mode=total")
    litres = [v["estimated_idle_fuel_litres"] for v in ranked["data"] if v["estimated_idle_fuel_litres"] is not None]
    costs = [v["estimated_idle_cost"] for v in ranked["data"] if v["estimated_idle_cost"] is not None]
    assert len(litres) == 3, ranked["data"]
    assert eff["estimated_idle_fuel_litres"] == waste["fleet_estimated_idle_fuel_litres"] == round(sum(litres), 1), (eff, waste)
    assert eff["estimated_idle_cost"] == round(sum(costs), 2) == waste["total_idle_waste_cost"]
    assert waste["total_idle_waste_litres"] == round(sum(litres), 2)  # all three are above the 0.5 h threshold
    v2 = next(v for v in ranked["data"] if v["vehicle_identifier"] == "V-2")
    assert v2["status"] == "no_distance_data" and v2["distance_km"] is None and v2["estimated_idle_fuel_litres"] == 5.01
    assert ranked["data"][0]["vehicle_identifier"] == "V-2"  # largest idle estimate ranks first without a status filter
    assert "V-2" not in {v["vehicle_identifier"] for v in matched_only["data"]}  # why the ranking must not filter status
    assert eff["vehicles_idle_estimated"] == 3 and eff["idle_rate_lph"] == 2.5
    assert (eff["vehicles_hours_known"], eff["vehicles_hours_unknown"]) == (3, 0)
    print(f"[PASS] Routes: fleet idle {eff['estimated_idle_fuel_litres']} L / AED {eff['estimated_idle_cost']} = waste = sum of rankings; "
          "V-2 (unknown distance) ranked first with its estimate.")


def test_trend_route_applies_month_specific_mixed_fuel():
    rows = [summary_tx(1, "V-1", 100.0, 300.0), summary_tx(2, "V-1", 20.0, 70.0, fuel="SUPER"),
            summary_tx(3, "V-1", 80.0, 240.0, month_end="2026-09-30")]
    trips = {1: {"status": "complete", "error": None, "trips": [
        {"start_time": ts(2026, 8, 5), "end_time": ts(2026, 8, 5, 13), "distance_km": 500.0},
        {"start_time": ts(2026, 9, 5), "end_time": ts(2026, 9, 5, 13), "distance_km": 400.0}]}}
    spy = MagicMock(wraps=routes.calculate_efficiency_trend)
    with Api(rows=rows, units=UNITS) as api, patch("app.fuel.routes.calculate_efficiency_trend", spy):
        api.trips.side_effect = lambda ids, f, t, sid, units=None: {i: trips[i] for i in ids if i in trips}
        body = api.get("/api/fuel/analytics/efficiency/trend?group_by=month&date_from=2026-08-01&date_to=2026-09-30&fuel_type=DIESEL")
    assert spy.call_args.kwargs["mixed_fuel_unit_months"] == {1: {"2026-08"}}, spy.call_args.kwargs
    by = {r["period"]: r for r in body["data"]}
    assert by["2026-08"]["distance_km"] is None and by["2026-08"]["fuel_efficiency_l_per_100km"] is None  # mixed in August
    assert by["2026-09"]["distance_km"] == 400.0 and by["2026-09"]["fuel_efficiency_l_per_100km"] == 20.0
    print("[PASS] Trend route passes mixed_fuel_unit_months: August (mixed) distance null, September 400 km / 20.0.")


OVERLAP_ROWS = [tx(1, "V-1", "2026-08-31", 500.0, 1500.0, imp=9, date_basis="reporting_month_end"),
                tx(2, "V-1", "2026-08-10T08:00:00", 70.0, 210.0, imp=7, date_basis="transaction"),
                tx(3, "V-2", "2026-08-11T08:00:00", 30.0, 90.0, imp=7, date_basis="transaction")]


def test_reporting_uses_monthly_summaries_only():
    # V-1 has a 500 L August summary and a 70 L dated row; V-2 has only a 30 L dated row. Reporting counts the summary
    # alone (500 L): dated rows are never combined with it, and V-2's dated row is never a fallback. Coverage is
    # irrelevant, so no summary/dated overlap can arise.
    for meta in ({7: FULL_AUG}, {}):
        with Api(rows=OVERLAP_ROWS, units=UNITS) as api, patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1"}), \
             patch("app.fuel.routes.fetch_import_coverage", return_value=meta):
            api.bundle.return_value = IDLE_BUNDLE
            body = api.get(f"/api/fuel/analytics/summary?{AUG}")
            eff = api.get(f"/api/fuel/analytics/efficiency?{AUG}")
            ranked = api.get(f"/api/fuel/analytics/efficiency/vehicles?{AUG}&page_size=100")
            waste = api.get(f"/api/fuel/analytics/waste?{AUG}")
        assert body["total_litres"] == 500.0 and body["monthly_summary_rows"] == 1, body
        scope = body["excluded_scope"]
        assert scope["summary_dated_overlap"]["transaction_count"] == 0 and scope["overlap_conflicts"] == []
        assert (scope["dated_not_reported"]["transaction_count"], scope["dated_not_reported"]["litres"]) == (2, 100.0)
        assert eff["total_litres"] == 500.0 and eff["vehicle_count"] == 1 and eff["unmatched_litres"] == 0.0
        assert [v["vehicle_identifier"] for v in ranked["data"]] == ["V-1"], "V-2 has no monthly summary: not listed"
        types = {a["anomaly_type"] for a in waste["anomalies"]}
        assert "summary_dated_overlap" not in types and "unmatched_vehicle" not in types, types
    print("[PASS] Reporting uses monthly summaries only (500 L, never 530/600 L); dated rows are counted as not "
          "reported, never combined or used as a fallback; no overlap is reported.")


def test_vehicle_detail_idle_estimate_uses_raw_seconds():
    with Api(rows=IDLE_ROWS, units=UNITS) as api:
        api.bundle.return_value = IDLE_BUNDLE
        api.detail.return_value = {**api.detail.return_value, "driving_duration_hours": 10.0, "idle_duration_hours": 1.04,
                                   "engine_hours": 11.04, "driving_seconds": 36000, "idle_seconds": 3755,
                                   "engine_hours_seconds": 39755}
        detail = api.get(f"/api/fuel/analytics/vehicle-detail?vehicle_identifier=V-1&{AUG}")
        ranked = api.get(f"/api/fuel/analytics/efficiency/vehicles?{AUG}&vehicle_filter=V-1")
    # 3755 s / 3600 x 2.0 = 2.0861 -> 2.09 L (from 1.04 h it would be 2.08 L); same value as the ranking row here,
    # because this fixture gives both reports the same seconds (live, the per-unit and group reports can differ)
    assert detail["vehicle_summary"]["estimated_idle_fuel_litres"] == 2.09 == ranked["data"][0]["estimated_idle_fuel_litres"]
    print("[PASS] Vehicle detail estimates idle fuel from the report's raw seconds (2.09 L), not 0.01 h rounded hours (2.08 L).")


TESTS = [
    test_vehicle_detail_idle_estimate_uses_raw_seconds,
    test_fleet_idle_agrees_across_efficiency_rankings_and_waste,
    test_trend_route_applies_month_specific_mixed_fuel,
    test_reporting_uses_monthly_summaries_only,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL ROUTES - CROSS-ENDPOINT CONSISTENCY (OFFLINE)") else 0)
