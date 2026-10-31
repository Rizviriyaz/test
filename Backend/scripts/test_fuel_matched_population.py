"""
Offline regression tests for the Fuel Dashboard redesign: Excel statement totals versus the matched fleet population.

  - total_* (Excel statement) = matched + unmatched + ambiguous; suspected duplicates, partial-month summaries and
    dated rows outside monthly-summary reporting are never included
  - matched_* (matched fleet) = vehicles resolving to exactly one Wialon unit only, equal to excluded_scope.kpi
  - unmatched / ambiguous identifiers never reach distance, hours or idle values, even when Wialon reports a unit
    under the same name
  - the telematics window is the selected month; an unknown distance stays None, never 0
  - matched_only lists matched Wialon units only; omitting it keeps the previous list
  - the idle waster ranking is the top 5 of the whole matched fleet and sums with the rest to the fleet KPI
  - without a determinable period the statement still totals every eligible record
  - Group Trips (template 5) stays the distance source

Wialon and Supabase are mocked (test_fuel_scope.Api); OfflineGuard blocks every outbound HTTP call.
Run from Backend/:  python scripts/test_fuel_matched_population.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import BUNDLE, QM, SUMMARY_ROWS, UNITS, Api, run, summary  # noqa: E402

# SUMMARY_ROWS (August): matched units 1 and 2 = 300 L / AED 900 in 3 rows; unmatched ZZZ-999 25 L / 75;
# ambiguous PLATE-1234 40 L / 120; a suspected duplicate 160 L / 480. Added here: a July summary (partial for an
# August range) and a dated August row (not reported: monthly summaries are the only fuel source).
JULY_SUMMARY = dict(summary(20, 1, "7-AUH-55943", 70.0, 210.0), transaction_date="2026-07-31T00:00:00", invoice_number="SUM-JUL")
DATED_ROW = {"id": 21, "import_id": 1, "vehicle_identifier": "7-AUH-55943", "transaction_date": "2026-08-10T08:00:00",
             "litres": 33.0, "total_cost": 99.0, "fuel_type": "DIESEL", "invoice_number": "INV-21", "odometer_km": None}
ROWS = SUMMARY_ROWS + [JULY_SUMMARY, DATED_ROW]

# Wialon also reports units named like the unmatched and ambiguous identifiers: they must never be attributed
CONTAMINATED_BUNDLE = {
    **BUNDLE,
    "by_name": {
        **BUNDLE["by_name"],
        "ZZZ999": {"mileage_km": 9999.0, "driving_seconds": 99999, "idle_seconds": 99999, "engine_hours_seconds": 199998},
        "ZZZ-999": {"mileage_km": 9999.0, "driving_seconds": 99999, "idle_seconds": 99999, "engine_hours_seconds": 199998},
        "1234": {"mileage_km": 9999.0, "driving_seconds": 99999, "idle_seconds": 99999, "engine_hours_seconds": 199998},
    },
}


def test_statement_totals_reconcile_with_matched_and_excluded_records():
    with Api(rows=ROWS, units=UNITS) as api:
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}")
    scope = eff["excluded_scope"]
    # Excel statement: matched 300 + unmatched 25 + ambiguous 40
    assert (eff["total_litres"], eff["total_cost"], eff["transaction_count"]) == (365.0, 1095.0, 5), eff
    # Matched fleet: exactly the KPI scope
    assert (eff["matched_litres"], eff["matched_cost"], eff["matched_row_count"]) == (300.0, 900.0, 3), eff
    assert (scope["kpi"]["litres"], scope["kpi"]["cost"], scope["kpi"]["transaction_count"]) == (300.0, 900.0, 3)
    assert eff["vehicle_count"] == scope["kpi"]["unit_count"] == 2
    # Statement = matched + unmapped, for litres, cost and rows separately
    assert round(eff["matched_litres"] + eff["unmatched_litres"], 2) == eff["total_litres"]
    assert round(eff["matched_cost"] + eff["unmatched_cost"], 2) == eff["total_cost"]
    assert eff["matched_row_count"] + eff["unmatched_transactions_count"] == eff["transaction_count"]
    assert (eff["unmatched_litres"], eff["unmatched_vehicle_count"]) == (65.0, 2)
    # Excluded from both: suspected duplicate 160 L, partial-month summary 70 L, unreported dated row 33 L
    assert scope["suspected_duplicates"]["litres"] == 160.0 and scope["partial_period_summary"]["litres"] == 70.0
    assert scope["dated_not_reported"]["litres"] == 33.0
    assert round(scope["all_litres"] - scope["suspected_duplicates"]["litres"]
                 - scope["partial_period_summary"]["litres"] - scope["summary_dated_overlap"]["litres"], 2) == eff["total_litres"]
    print("[PASS] Statement 365 L / AED 1,095 (5 rows) = matched 300 L / AED 900 (3 rows) + unmatched/ambiguous 65 L; "
          "duplicate, partial-month and dated rows excluded from both.")


def test_matched_kpis_never_use_unmapped_identifiers():
    with Api(rows=ROWS, units=UNITS) as api:
        api.bundle.return_value = CONTAMINATED_BUNDLE
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}&idle_rate_lph=2.0")
        ranked = api.get(f"/api/fuel/analytics/efficiency/vehicles?{QM}&idle_rate_lph=2.0&status=matched&page_size=100")
    # Units 1 (500 km, 2 h idle) and 2 (200 km, 1 h idle) only
    assert eff["total_distance_km"] == 700.0 and eff["tracked_litres"] == 300.0, eff
    assert (eff["total_driving_hours"], eff["total_idle_hours"]) == (15.0, 3.0), eff
    assert eff["estimated_idle_fuel_litres"] == 6.0 and eff["idle_basis_litres"] == 300.0, eff
    assert eff["fuel_efficiency_l_per_100km"] == round(300.0 / 700.0 * 100, 2)
    idle = [v["estimated_idle_fuel_litres"] for v in ranked["data"]]
    assert sorted(v["wialon_unit_id"] for v in ranked["data"]) == [1, 2] and round(sum(idle), 1) == 6.0, ranked["data"]
    print("[PASS] Unmatched/ambiguous identifiers never reach distance, hours or idle fuel (700 km, 3.0 h idle, 6.0 L), "
          "even when Wialon reports units of the same name.")


def test_telematics_window_is_the_selected_month_and_unknown_distance_stays_unknown():
    unknown = {**BUNDLE, "by_name": {name: {k: v for k, v in e.items() if k != "mileage_km"}
                                     for name, e in BUNDLE["by_name"].items()}}
    with Api(rows=ROWS, units=UNITS) as api:
        api.bundle.return_value = unknown
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}")
        ranked = api.get(f"/api/fuel/analytics/efficiency/vehicles?{QM}&page_size=100")
    assert api.bundle.call_args.args[:2] == ("2026-08-01", "2026-08-31"), api.bundle.call_args
    assert eff["total_distance_km"] is None and eff["fuel_efficiency_l_per_100km"] is None, eff
    assert eff["vehicles_telemetry_unknown"] == 2 and eff["matched_litres"] == 300.0
    rows = [v for v in ranked["data"] if v["wialon_unit_id"] is not None]
    assert rows and all(v["distance_km"] is None and v["status"] == "no_distance_data" for v in rows), rows
    print("[PASS] Telematics run for 2026-08-01..2026-08-31; unknown Wialon distance stays null (fleet and per vehicle), "
          "never 0 km.")


# ─── Phase 2: matched_only lists and the fleet-wide idle ranking ───────────────
# Seven matched units (200 L / AED 600 each). Idle at 2.0 L/h: UNIT<i> idles i x 30 min -> i L. UNIT7 has no
# distance (no_distance_data, still ranked by idle); UNIT3 has no idle hours (unknown estimate, never ranked as 0).
# Plus one unmatched (NOPE-999) and one ambiguous (PLATE-1234: two units named 1234) identifier, both with Wialon
# entries of the same name that must never be used.
FLEET_UNITS = [{"id": i, "nm": f"UNIT{i}"} for i in range(1, 8)] + [{"id": 8, "nm": "1234"}, {"id": 9, "nm": "1234"}]
FLEET_ROWS = ([summary(i, 1, f"UNIT{i}", 200.0, 600.0) for i in range(1, 8)]
              + [summary(30, 1, "NOPE-999", 80.0, 240.0), summary(31, 1, "PLATE-1234", 50.0, 150.0)])


def _fleet_entry(i):
    e = {"mileage_km": 100.0 * i, "driving_seconds": 3600 * i, "idle_seconds": 1800 * i,
         "engine_hours_seconds": 5400 * i}
    if i == 7:
        e.pop("mileage_km")
    if i == 3:
        e.pop("idle_seconds")
    return e


NOISE = {"mileage_km": 9999.0, "driving_seconds": 99999, "idle_seconds": 99999, "engine_hours_seconds": 199998}
FLEET_BUNDLE = {"by_name": {**{f"UNIT{i}": _fleet_entry(i) for i in range(1, 8)}, "NOPE999": NOISE, "NOPE-999": NOISE,
                            "1234": NOISE},
                "duplicate_names": [], "status": "complete", "errors": [], "warnings": []}
LIST = f"/api/fuel/analytics/efficiency/vehicles?{QM}&idle_rate_lph=2.0"
# The exact request the Highest Idle Fuel Wasters panel sends (fleetRules.fuelIdleRankingParams)
RANKING = f"{LIST}&page=1&page_size=5&sort_by=estimated_idle_fuel&sort_order=desc&matched_only=true"


def _ids(body):
    return [v["vehicle_identifier"] for v in body["data"]]


def test_matched_only_lists_matched_wialon_vehicles_only():
    with Api(rows=FLEET_ROWS, units=FLEET_UNITS) as api:
        api.bundle.return_value = FLEET_BUNDLE
        default = api.get(f"{LIST}&page_size=100")
        matched = api.get(f"{LIST}&page_size=100&matched_only=true")
        not_tracked = api.get(f"{LIST}&matched_only=true&status=not_tracked")
        ambiguous = api.get(f"{LIST}&matched_only=true&status=ambiguous")
        no_distance = api.get(f"{LIST}&matched_only=true&status=no_distance_data")
        searched = api.get(f"{LIST}&matched_only=true&vehicle_identifier=NOPE")
        legacy_unmapped = api.get(f"{LIST}&status=not_tracked")
    # Parameter omitted: unchanged, unmapped identifiers listed with their mapping status
    statuses = {v["vehicle_identifier"]: v["status"] for v in default["data"]}
    assert default["total"] == 9 and statuses["NOPE-999"] == "not_tracked" and statuses["PLATE-1234"] == "ambiguous", statuses
    assert _ids(legacy_unmapped) == ["NOPE-999"]
    # matched_only: exactly the seven matched Wialon units, nothing unmapped
    assert matched["total"] == 7 and sorted(v["wialon_unit_id"] for v in matched["data"]) == list(range(1, 8)), matched["data"]
    assert {v["status"] for v in matched["data"]} == {"matched", "no_distance_data"}
    assert not_tracked["total"] == ambiguous["total"] == searched["total"] == 0
    assert _ids(no_distance) == ["UNIT7"] and no_distance["data"][0]["distance_km"] is None
    print("[PASS] matched_only lists the 7 matched Wialon units only (not_tracked / ambiguous never, even when asked "
          "for); omitting it keeps the previous list of 9.")


def test_idle_ranking_covers_the_whole_matched_fleet():
    with Api(rows=FLEET_ROWS, units=FLEET_UNITS) as api:
        api.bundle.return_value = FLEET_BUNDLE
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}&idle_rate_lph=2.0")
        ranking = api.get(RANKING)
        full = api.get(f"{LIST}&page_size=500&matched_only=true&sort_by=estimated_idle_fuel")
        # Leaderboard requests with another page, sort, search and status
        page2 = api.get(f"{LIST}&matched_only=true&page=2&page_size=3&sort_by=litres")
        api.get(f"{LIST}&matched_only=true&vehicle_identifier=UNIT1&status=matched")
        ranking_again = api.get(RANKING)
    # Ranked over every matched vehicle: the top 5 of the full list, including UNIT7 (no distance, largest idle)
    assert _ids(ranking) == ["UNIT7", "UNIT6", "UNIT5", "UNIT4", "UNIT2"], _ids(ranking)
    assert [v["estimated_idle_fuel_litres"] for v in ranking["data"]] == [7.0, 6.0, 5.0, 4.0, 2.0]
    assert ranking["data"] == full["data"][:5] and ranking["total"] == 7
    assert ranking_again["data"] == ranking["data"] and _ids(page2) != _ids(ranking)[:3]
    # UNIT3 (idle unknown) sorts last with a null estimate, never ranked as 0
    assert full["data"][-1]["vehicle_identifier"] == "UNIT3" and full["data"][-1]["estimated_idle_fuel_litres"] is None
    # The same per-vehicle values add up to the matched fleet KPI
    known = [v["estimated_idle_fuel_litres"] for v in full["data"] if v["estimated_idle_fuel_litres"] is not None]
    assert eff["estimated_idle_fuel_litres"] == round(sum(known), 1) == 25.0 and eff["vehicles_idle_estimated"] == 6, eff
    assert eff["total_idle_hours"] == 12.5 and eff["matched_litres"] == 1400.0 and eff["vehicle_count"] == 7
    print("[PASS] Idle ranking = top 5 of all 7 matched vehicles (7, 6, 5, 4, 2 L), unchanged by leaderboard page/"
          "search/status; the per-vehicle values sum to the KPI (25.0 L).")


def test_missing_period_keeps_every_eligible_statement_record():
    with Api(rows=ROWS, units=UNITS) as api, patch("app.fuel.routes._resolve_effective_dates", return_value=None):
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}")
    scope = eff["excluded_scope"]
    # Statement totals keep matched + unmatched + ambiguous (before the fix only the 65 L unmapped)
    assert (eff["total_litres"], eff["total_cost"], eff["transaction_count"]) == (365.0, 1095.0, 5), eff
    assert (eff["matched_litres"], eff["matched_cost"], eff["matched_row_count"], eff["vehicle_count"]) == (300.0, 900.0, 3, 2)
    # Excluded records stay excluded
    assert scope["suspected_duplicates"]["litres"] == 160.0 and scope["partial_period_summary"]["litres"] == 70.0
    assert scope["dated_not_reported"]["litres"] == 33.0
    # No period: Wialon is never asked, and every Wialon-based value is unknown, not 0
    api.bundle.assert_not_called()
    assert eff["telematics_status"] == "unavailable" and eff["total_distance_km"] is None, eff
    assert eff["total_idle_hours"] is None and eff["estimated_idle_fuel_litres"] is None
    assert any("could not be determined" in w for w in eff["data_warnings"]), eff["data_warnings"]
    print("[PASS] Without a period the statement still totals 365 L (matched 300 L in matched_*); exclusions stay; "
          "Wialon values unknown, not 0.")


def test_trips_template_5_stays_the_authoritative_distance():
    from datetime import datetime, timedelta, timezone

    import app.fuel.wialon_distance as wd
    import app.wialon_reports as wr
    from test_fuel_telematics_phase2 import Env
    from wialon_report_fixtures import (GROUPS, TEMPLATE_GROUP_ENGINE, TEMPLATE_GROUP_TRIPS, FakeReportEngine,
                                        default_tables, group_trip_row)

    tables = default_tables()
    tables[TEMPLATE_GROUP_TRIPS]["rows"] = [group_trip_row("UNIT-A", 250.75), group_trip_row("UNIT-B", 400.0)]
    for row in tables[TEMPLATE_GROUP_ENGINE]["rows"]:
        row["c"][9] = "999.00 km"  # Engine Hours mileage disagrees on purpose
    engine = FakeReportEngine(tables=tables)
    # Report executors discover the account's "All Unit" group: served from the fixture groups offline
    with Env(engine=engine), patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS):
        bundle = wd.get_fleet_telematics_bundle("2026-08-01", "2026-08-31", sid="s")
    e = bundle["by_name"]
    assert wr.TEMPLATE_ID_GROUP_TRIPS == 5
    assert (e["UNIT-A"]["mileage_km"], e["UNIT-A"]["distance_source"]) == (250.75, "wialon_group_trips_report"), e["UNIT-A"]
    assert (e["UNIT-B"]["mileage_km"], e["UNIT-B"]["distance_source"]) == (400.0, "wialon_group_trips_report"), e["UNIT-B"]
    # Driving and idle hours still come from the Engine Hours report
    assert e["UNIT-A"]["in_engine_report"] and e["UNIT-A"]["idle_seconds"] is not None
    # The Trips report ran over exactly the selected month (UAE)
    uae = timezone(timedelta(hours=4))
    trips_exec = [p for svc, p in engine.calls if svc == "report/exec_report" and p["reportTemplateId"] == TEMPLATE_GROUP_TRIPS]
    assert len(trips_exec) == 1, trips_exec
    assert trips_exec[0]["interval"]["from"] == int(datetime(2026, 8, 1, tzinfo=uae).timestamp())
    assert trips_exec[0]["interval"]["to"] == int(datetime(2026, 8, 31, 23, 59, 59, tzinfo=uae).timestamp())
    print("[PASS] Distance comes from Group Trips (template 5) over 2026-08-01 00:00:00..2026-08-31 23:59:59 UAE, "
          "not Engine Hours mileage; hours still from Engine Hours.")


# ─── Phase 4 O2: drill-down distance from the fleet Trips Report – All Units (template 5) ──────────────────────
DETAIL_Q = f"/api/fuel/analytics/vehicle-detail?vehicle_identifier=7%20AUH%2055943&{QM}"


def _per_unit_detail(api, km):
    """The per-unit reports' result (template 4 trips + template 1 engine) with a trip total that differs on purpose."""
    api.detail.return_value = {**api.detail.return_value, "distance_km": km, "trip_count": 89,
                               "distance_source": "wialon_unit_trips_report"}


def test_drilldown_distance_is_the_fleet_template_5_distance():
    from app.wialon import WialonAPIError

    sourced = {**BUNDLE, "by_name": {n: dict(e, distance_source="wialon_group_trips_report") for n, e in BUNDLE["by_name"].items()}}
    with Api(rows=SUMMARY_ROWS, units=UNITS) as api:
        api.bundle.return_value = sourced
        _per_unit_detail(api, 480.0)
        detail = api.get(DETAIL_Q)
        row = api.get(f"/api/fuel/analytics/efficiency/vehicles?{QM}&matched_only=true&vehicle_filter=7%20AUH%2055943")["data"][0]
    act = detail["activity_breakdown"]
    # 500 km from the fleet bundle (= the leaderboard row), not the per-unit 480 km; the trip count stays per unit
    assert detail["vehicle_summary"]["distance_km"] == 500.0 == row["distance_km"], (detail["vehicle_summary"], row)
    assert (act["distance_km"], act["distance_source"], act["trip_count"]) == (500.0, "wialon_group_trips_report", 89), act
    assert "Template 5" in detail["calculation_transparency"]["telematics_source"]

    # Unit absent from the fleet reports: unknown (never the per-unit 480 km, never 0)
    absent = {**sourced, "by_name": {n: e for n, e in sourced["by_name"].items() if n != "7AUH55943"}}
    with Api(rows=SUMMARY_ROWS, units=UNITS) as api:
        api.bundle.return_value = absent
        _per_unit_detail(api, 480.0)
        missing = api.get(DETAIL_Q)
    assert missing["vehicle_summary"]["distance_km"] is None and missing["vehicle_summary"]["status"] == "no_distance_data"
    assert missing["activity_breakdown"]["distance_source"] == "unavailable"
    assert any("fleet trips report" in w for w in missing["activity_breakdown"]["warnings"])

    # Fleet reports failed: unknown with the failure named; the rest of the drill-down still answers
    with Api(rows=SUMMARY_ROWS, units=UNITS) as api:
        api.bundle.side_effect = WialonAPIError("down")
        _per_unit_detail(api, 480.0)
        failed = api.get(DETAIL_Q)
    assert failed["vehicle_summary"]["distance_km"] is None and failed["vehicle_summary"]["litres"] == 140.0
    assert any("Wialon reports failed" in w for w in failed["activity_breakdown"]["warnings"])

    # Recorded 0 km in the fleet report stays 0 km, not unknown
    zero = {**sourced, "by_name": {**sourced["by_name"], "7AUH55943": dict(sourced["by_name"]["7AUH55943"], mileage_km=0.0)}}
    with Api(rows=SUMMARY_ROWS, units=UNITS) as api:
        api.bundle.return_value = zero
        _per_unit_detail(api, 480.0)
        recorded_zero = api.get(DETAIL_Q)
    assert recorded_zero["vehicle_summary"]["distance_km"] == 0.0 and recorded_zero["activity_breakdown"]["distance_km"] == 0.0
    print("[PASS] Drill-down distance = fleet Trips template 5 (500 km = leaderboard, not per-unit 480 km); absent or "
          "failed -> null with a warning; recorded 0 km stays 0.")


TESTS = [
    test_drilldown_distance_is_the_fleet_template_5_distance,
    test_statement_totals_reconcile_with_matched_and_excluded_records,
    test_matched_kpis_never_use_unmapped_identifiers,
    test_telematics_window_is_the_selected_month_and_unknown_distance_stays_unknown,
    test_matched_only_lists_matched_wialon_vehicles_only,
    test_idle_ranking_covers_the_whole_matched_fleet,
    test_missing_period_keeps_every_eligible_statement_record,
    test_trips_template_5_stays_the_authoritative_distance,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL - STATEMENT VS MATCHED FLEET POPULATION (OFFLINE)") else 0)
