"""
Regression test for H5: Dashboard driving time from Wialon Engine Hours "In motion" (MOCKED / offline).

/api/idle/fleet-analytics by_vehicle rows carry two additive fields:
  - in_motion_seconds  the Engine Hours report's "In motion" for the unit, or None when the row cannot be attributed,
                       the cell is unreadable / unlocated, the report's mileage is unknown, or the unit covered
                       distance with 0 s in motion. A unit with no distance and no motion keeps a real 0.
  - engine_mileage_km  the same report's mileage (None when unknown), passed through unchanged so the Dashboard can
                       apply its max(2 km, 5%) consistency check against its own mileage (Frontend knownTotals.js,
                       drivingSecondsByVehicle; covered by src/utils/knownTotals.test.js).
Unknown values are never cached as 0, and existing idle fields, totals and group filtering are unchanged.
Wialon is replaced by FakeReportEngine and every outbound HTTP call is blocked by OfflineGuard.

  python scripts/test_driving_time_in_motion.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUP_ENGINE_HEADER, GROUPS, TEMPLATE_GROUP_ENGINE, UNITS, FakeReportEngine,
    default_tables, group_engine_row,
)

import app.main as main  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 86400 - 1
CACHE_KEY = f"fleet-idle-v10-exact-{FROM_TS}-{TO_TS}-grp-None-veh-None-summary-uae"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def live_for(units):
    return [
        {"vehicle_id": u["id"], "vehicle_name": u["nm"], "status": "STOPPED", "idle_duration": 0, "idle_start": None,
         "latitude": 25.2, "longitude": 55.27, "address": f"Street {u['id']}", "registration_plate": f"DXB {u['id']}"}
        for u in units
    ]


def fleet_idle(tables=None, units=UNITS, groups=GROUPS, use_cache=False, include_header=True, **kwargs):
    engine = FakeReportEngine(tables=tables, include_header=include_header)
    patches = [
        patch("app.wialon_reports.wialon_request", engine),
        patch("app.main.login", return_value="sid"),
        patch("app.main.get_vehicle_groups", return_value=groups),
        patch("app.main.get_all_vehicles", return_value=units),
        patch("app.main.get_calculated_data", return_value=CALCULATED),
        patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_for(units)),
    ]
    for p in patches:
        p.start()
    try:
        main._cache.clear()
        return main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=False,
                                             skip_cache=not use_cache, **kwargs)
    finally:
        for p in reversed(patches):
            p.stop()


def rows_by_name(res):
    return {r["vehicle_name"]: r for r in res["by_vehicle"]}


def engine_tables(rows):
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE] = {"header": GROUP_ENGINE_HEADER, "rows": rows, "subrows": {}}
    return tables


def test_attributed_and_zero():
    res = fleet_idle()
    r = rows_by_name(res)
    check(r["UNIT-A"]["in_motion_seconds"] == 4 * 3600 + 30 * 60 and r["UNIT-A"]["engine_mileage_km"] == 180.25,
          "attributed unit: in_motion_seconds 16,200 s and engine_mileage_km 180.25 from the fixture")
    check(r["UNIT-B"]["in_motion_seconds"] == 8 * 3600 and r["UNIT-B"]["engine_mileage_km"] == 640.0,
          "attributed unit B: In motion 8 h, engine mileage 640 km")
    check(r["UNIT-C"]["in_motion_seconds"] == 0 and r["UNIT-C"]["engine_mileage_km"] == 0.0,
          "no distance and no motion: a legitimate 0 s stays 0, not unknown")
    check(r["UNIT-A"]["total_idle_seconds"] == 5400 and r["UNIT-B"]["total_idle_seconds"] == 4530
          and res["kpis"]["total_idle_seconds"] == 5400 + 4530 and res["kpis"]["total_idle_partial"] is False,
          "existing idle totals and partial flag unchanged")
    check({"engine_hours_seconds", "total_time_seconds", "idle_minutes", "longest_idle_seconds", "status"}
          <= set(r["UNIT-A"]), "existing by_vehicle keys kept (fields are additive)")


def test_inspected_discrepancies_pass_through():
    # Trips km (Dashboard) vs Engine Hours km for the six units flagged by the 25 Oct 2026 inspection. The backend
    # passes the engine-report values through unchanged; the Dashboard's max(2 km, 5%) check marks them unknown.
    cases = [("54836", 1.7, "0:04:54"), ("72031", 214.0, "3:59:00"), ("99238", 125.0, "2:26:00"),
             ("40630", 46.3, "1:06:00"), ("42897", 5.3, "0:07:00"), ("94181", 536.5, "10:22:00")]
    units = [{"id": 500 + i, "nm": name, "sens": {}} for i, (name, _, _) in enumerate(cases)]
    groups = [{"id": 1, "nm": "All Unit", "u": [u["id"] for u in units]}]
    rows = [group_engine_row(name, "24:00:00", "12:00:00", motion, "0:10:00", km) for name, km, motion in cases]
    r = rows_by_name(fleet_idle(engine_tables(rows), units=units, groups=groups))
    check(all(r[name]["engine_mileage_km"] == km for name, km, _ in cases),
          "six inspected discrepancy units: engine mileage passed through exactly for the Dashboard check")
    check(r["54836"]["in_motion_seconds"] == 294 and r["94181"]["in_motion_seconds"] == 37320,
          "six inspected discrepancy units: In motion exposed, trusted only after the Dashboard mileage check")


def test_unknown_cases():
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"][0]["c"][7] = ""  # UNIT-A In motion unreadable
    tables[TEMPLATE_GROUP_ENGINE]["rows"][1]["c"][9] = ""  # UNIT-B mileage unreadable
    r = rows_by_name(fleet_idle(tables))
    check(r["UNIT-A"]["in_motion_seconds"] is None and r["UNIT-A"]["engine_mileage_km"] == 180.25,
          "unreadable In motion cell: driving time unknown (None), not 0")
    check(r["UNIT-B"]["in_motion_seconds"] is None and r["UNIT-B"]["engine_mileage_km"] is None,
          "missing Engine Hours mileage: mileage and driving time unknown (no consistency check possible)")
    check(r["UNIT-C"]["in_motion_seconds"] == 0, "other units unaffected")

    r = rows_by_name(fleet_idle(include_header=False))
    check(all(v["in_motion_seconds"] is None and v["engine_mileage_km"] is None for v in r.values()),
          "unlocated columns (no report header): driving time and engine mileage unknown for every unit")

    tables = default_tables()
    rows = tables[TEMPLATE_GROUP_ENGINE]["rows"]
    tables[TEMPLATE_GROUP_ENGINE]["rows"] = [rows[0], rows[0], rows[2]]  # UNIT-A twice, UNIT-B missing
    r = rows_by_name(fleet_idle(tables))
    check(r["UNIT-A"]["in_motion_seconds"] is None and r["UNIT-A"]["engine_mileage_km"] is None,
          "unattributed row (name on two report rows): driving time unknown")
    check(r["UNIT-B"]["in_motion_seconds"] is None and r["UNIT-B"]["engine_mileage_km"] is None,
          "unit missing from the report: driving time unknown, not 0")

    rows = [group_engine_row("UNIT-A", "24:00:00", "1:00:00", "0:00:00", "1:00:00", 12.5),
            group_engine_row("UNIT-B", "24:00:00", "0:00:00", "0:00:00", "0:00:00", 0.0),
            group_engine_row("UNIT-C", "24:00:00", "0:00:00", "0:00:00", "0:00:00", 0.0)]
    r = rows_by_name(fleet_idle(engine_tables(rows)))
    check(r["UNIT-A"]["in_motion_seconds"] is None and r["UNIT-A"]["engine_mileage_km"] == 12.5,
          "In motion 0 with 12.5 km mileage: driving time unknown, not 0")
    check(r["UNIT-B"]["in_motion_seconds"] == 0, "In motion 0 with 0 km: legitimate 0")


def test_cache():
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"][0]["c"][7] = ""
    fleet_idle(tables, use_cache=True)
    check(main.cached(CACHE_KEY, ttl_seconds=180) is None,
          "unreadable In motion cell: result not cached, so the unknown is re-read rather than kept")

    rows = [group_engine_row("UNIT-A", "24:00:00", "1:00:00", "0:00:00", "1:00:00", 12.5),
            group_engine_row("UNIT-B", "24:00:00", "9:15:30", "8:00:00", "1:15:30", 640.0),
            group_engine_row("UNIT-C", "24:00:00", "0:00:00", "0:00:00", "0:00:00", 0.0)]
    fleet_idle(engine_tables(rows), use_cache=True)
    hit = main.cached(CACHE_KEY, ttl_seconds=180)
    check(hit is not None and rows_by_name(hit)["UNIT-A"]["in_motion_seconds"] is None
          and rows_by_name(hit)["UNIT-B"]["in_motion_seconds"] == 8 * 3600,
          "complete report cached with the unreliable unit as None (never 0) under the current idle cache key")
    main._cache.clear()


def test_group_filter():
    res = fleet_idle(group_id=8)
    check([v["vehicle_name"] for v in res["by_vehicle"]] == ["UNIT-C"]
          and res["by_vehicle"][0]["in_motion_seconds"] == 0,
          "group filter (Contracts): only that group's unit, with its own In motion value")
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"][0]["c"][7] = ""
    res = fleet_idle(tables, group_id=7)
    r = rows_by_name(res)
    check(set(r) == {"UNIT-A", "UNIT-B"} and r["UNIT-A"]["in_motion_seconds"] is None
          and r["UNIT-B"]["in_motion_seconds"] == 8 * 3600,
          "group filter (Logistics): the unknown unit stays listed (counted as Partial), not dropped")
    check(res["kpis"]["total_idle_seconds"] == 5400 + 4530 and res["kpis"]["total_idle_partial"] is False,
          "group filter: idle totals unaffected by an unreadable In motion cell")


def main_entry():
    guard = OfflineGuard().start()
    try:
        test_attributed_and_zero()
        test_inspected_discrepancies_pass_through()
        test_unknown_cases()
        test_cache()
        test_group_filter()
    finally:
        guard.stop()
    check(not guard.attempts, "no live network calls were attempted")
    print("\nAll driving-time (In motion) checks passed.")


if __name__ == "__main__":
    main_entry()
