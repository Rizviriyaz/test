"""
Offline tests: guessed report columns and units missing from a group report are unknown, never a value.

Parser (app/wialon_reports.py), real code against FakeReportEngine:
  - with the production header every column is located and `unlocated_fields` is empty
  - with no header, or a header that lacks a column name, every value read from a guessed position is listed
    in `unlocated_fields`; all pre-existing keys and values are unchanged (fuel callers are unaffected)

Non-fuel consumers:
  - /api/dashboard/mileage, /api/fleet/trips, /api/fleet/utilization, /api/idle/fleet-analytics,
    /api/vehicles/{id}/trips and /api/vehicles/{id}/idle report guessed-column values as unknown and do not
    cache them
  - a group member with no row in the group report (Wialon emits a zero row for every member without
    activity, verified live) is unknown, not 0 km / 0 s

Wialon is replaced by FakeReportEngine; every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_report_columns_and_coverage.py
"""
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, TEMPLATE_GROUP_ENGINE, TEMPLATE_GROUP_TRIPS, TEMPLATE_UNIT_ENGINE,
    TEMPLATE_UNIT_TRIPS, UNITS, FakeReportEngine, default_tables, live_records,
)

import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402
import app.wialon_reports as wr  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 86400 - 1
FLEET = [dict(r, speed=0, ignition=r["status"] != "STOPPED") for r in live_records()]


def _tables(drop_group_rows=(), rename=None):
    """default_tables() with group-report rows removed for some units, or one header name changed."""
    t = default_tables()
    for tid in (TEMPLATE_GROUP_TRIPS, TEMPLATE_GROUP_ENGINE):
        t[tid]["rows"] = [r for r in t[tid]["rows"] if r["c"][0] not in drop_group_rows]
    if rename:
        tid, old, new = rename
        t[tid]["header"] = [new if h == old else h for h in t[tid]["header"]]
    return t


def _run_parsers(engine):
    with patch("app.wialon_reports.wialon_request", engine), \
            patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS):
        return {
            "unit_trips": wr.execute_wialon_unit_trips_report("sid", 101, FROM_TS, TO_TS),
            "unit_engine": wr.execute_wialon_unit_engine_report("sid", 101, FROM_TS, TO_TS),
            "group_trips": wr.execute_wialon_group_trips_report("sid", FROM_TS, TO_TS),
            "group_engine": wr.execute_wialon_group_engine_idle_report("sid", FROM_TS, TO_TS, include_events=True),
        }


def _strip_unlocated(obj):
    if isinstance(obj, dict):
        return {k: _strip_unlocated(v) for k, v in obj.items() if k != "unlocated_fields"}
    if isinstance(obj, list):
        return [_strip_unlocated(x) for x in obj]
    return obj


def test_parser_header_located():
    out = _run_parsers(FakeReportEngine())
    assert out["unit_trips"]["unlocated_fields"] == [] and out["unit_engine"]["unlocated_fields"] == [], out
    assert out["group_engine"]["unlocated_fields"] == [], out["group_engine"]["unlocated_fields"]
    assert all("unlocated_fields" not in r for r in out["group_trips"]), out["group_trips"]
    print("[PASS] Parser: with the production header every column is located (unlocated_fields empty).")


def test_parser_no_header_lists_guessed_fields_and_keeps_existing_values():
    with_header = _run_parsers(FakeReportEngine())
    no_header = _run_parsers(FakeReportEngine(include_header=False))
    assert no_header["unit_trips"]["unlocated_fields"] == ["distance_km"], no_header["unit_trips"]
    assert no_header["unit_engine"]["unlocated_fields"] == ["engine_hours", "idle_seconds", "in_motion_seconds"]
    assert no_header["group_engine"]["unlocated_fields"] == [
        "engine_hours_seconds", "in_motion_seconds", "mileage_km", "total_idle_seconds", "total_time_seconds"]
    assert all(r["unlocated_fields"] == ["mileage_km"] for r in no_header["group_trips"]), no_header["group_trips"]
    # Additive only: every pre-existing key and value is identical, so callers that ignore the new key (fuel) see no change
    assert _strip_unlocated(no_header) == _strip_unlocated(with_header)
    print("[PASS] Parser: no header -> guessed fields listed in unlocated_fields; all existing keys/values unchanged.")


def test_parser_missing_header_name():
    out = _run_parsers(FakeReportEngine(tables=_tables(rename=(TEMPLATE_GROUP_TRIPS, "Mileage", "Distance"))))
    rows = out["group_trips"]
    assert all(r["unlocated_fields"] == ["mileage_km"] and r["column_warnings"] for r in rows), rows
    print("[PASS] Parser: header without 'Mileage' -> mileage_km listed as unlocated (and warned).")


def _main_patches(stack, engine, units=UNITS):
    main._cache.clear()
    fa._analytics_cache.clear()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS))
    for target in ("app.main", "app.fleet_analytics"):
        stack.enter_context(patch(f"{target}.login", return_value="sid"))
        stack.enter_context(patch(f"{target}.get_vehicle_groups", return_value=GROUPS))
        stack.enter_context(patch(f"{target}.get_all_vehicles", return_value=units))
        stack.enter_context(patch(f"{target}.get_calculated_data", return_value=CALCULATED))
        stack.enter_context(patch(f"{target}.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in FLEET]))


def test_dashboard_mileage_guessed_column_and_missing_member():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False))
        res = main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                         to_date=None, group_id=None)
        assert all(u["mileage"] is None for u in res["all_units"]) and res["total_km"] == 0, res["all_units"]
        assert sorted(res["incomplete_units"]) == ["UNIT-A", "UNIT-B", "UNIT-C"], res
        assert main.cached("dashboard-mileage-v3-tf-today-None-uae", ttl_seconds=60) is None, "not cached"
    # Group 7 (Logistics) = units 101 and 102; the report has no row for UNIT-B
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-B",))))
        res = main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                         to_date=None, group_id=7)
        units = {u["vehicle_name"]: u for u in res["all_units"]}
        assert units["UNIT-B"]["mileage"] is None and units["UNIT-B"]["source"] == "missing_from_report", units
        assert units["UNIT-A"]["mileage"] == 250.75, units
        assert "UNIT-B" in res["incomplete_units"] and res["total_km"] == 250.75 + 0.0, res
        assert main.cached("dashboard-mileage-v3-tf-today-7-uae", ttl_seconds=60) is None, "not cached"
    print("[PASS] Dashboard mileage: guessed Mileage column -> unknown (not cached); group member missing "
          "from the report -> unknown, listed in incomplete_units.")


def test_fleet_trips_guessed_column_and_missing_row():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False))
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    assert all(v["gps_mileage_km"] is None for v in res["vehicles"]), res["vehicles"]
    assert res["summary"]["total_distance_partial"] is True, res["summary"]
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",))))
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[103]["gps_mileage_km"] is None, by_id[103]
    assert res["summary"]["distance_unknown_vehicles"] == ["UNIT-C"], res["summary"]
    assert res["summary"]["average_trip_distance_km"] is None and res["summary"]["longest_trip_km"] is None
    print("[PASS] Fleet trips: guessed Mileage column -> every distance unknown; unit with no report row -> "
          "unknown (not 0 km), flagged partial.")


def test_fleet_trips_unit_path_guessed_column():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False, fail_exec={TEMPLATE_GROUP_TRIPS}))
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    assert all(v["gps_mileage_km"] is None for v in res["vehicles"]), res["vehicles"]
    assert all(v["trip_count"] is not None for v in res["vehicles"]), "trip counts are still real"
    print("[PASS] Fleet trips (unit-report fallback): guessed Mileage column -> distance unknown, trip counts kept.")


def test_utilization_guessed_column_and_missing_row():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False))
        res = fa.get_fleet_utilization_analytics(hours=24)
    assert all(v["utilization_percentage"] is None for v in res["vehicles"]) and res["fleet_utilization_pct"] is None
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-B",))))
        res = fa.get_fleet_utilization_analytics(hours=24)
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[102]["driving_seconds"] is None and res["unknown_vehicles"] == ["UNIT-B"], res
    assert by_id[101]["driving_seconds"] == 4 * 3600 + 30 * 60, by_id[101]
    print("[PASS] Utilization: guessed duration columns -> unknown; unit with no report row -> unknown, not 0.")


def test_idle_guessed_column_and_missing_row():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False))
        res = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False)
        assert all(v["total_idle_seconds"] is None for v in res["by_vehicle"]), res["by_vehicle"]
        assert res["kpis"]["total_idle_partial"] is True and res["kpis"]["idle_event_count"] is None, res["kpis"]
        engine = FakeReportEngine(include_header=False)
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False)
        assert engine.count("report/exec_report") == 1, "a guessed-column result must not be served from cache"
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",))))
        res = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False)
        v = {x["vehicle_id"]: x for x in res["by_vehicle"]}
        assert v[103]["total_idle_seconds"] is None and v[103]["idle_events"] is None, v[103]
        assert v[101]["total_idle_seconds"] == 5400 and res["kpis"]["total_idle_partial"] is True, res["kpis"]
        engine = FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",)))
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False)
        assert engine.count("report/exec_report") == 1, "a result with a missing unit must not be served from cache"
    print("[PASS] Idle analytics: guessed columns -> unknown; unit with no report row -> unknown (not 0 s); "
          "neither result is cached.")


def test_unit_routes_guessed_columns():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False))
        trips = main.get_vehicle_trips(vehicle_id=101, hours=24, from_time=None, to_time=None)
        idle = main.get_vehicle_idle(vehicle_id=101, hours=24, timeframe=None, from_date=None, to_date=None,
                                     from_time=None, to_time=None)
    assert trips["trips"] and all("distance_km" in t["incomplete_fields"] for t in trips["trips"]), trips["trips"]
    assert "distance_km" in trips["incomplete_fields"], trips
    s = idle["summary"]
    assert s["total_idle_seconds"] is None and s["total_engine_seconds"] is None, s
    assert main.cached("vehicle-trips-v3-101-hours-24-uae", ttl_seconds=60) is None
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        trips = main.get_vehicle_trips(vehicle_id=101, hours=24, from_time=None, to_time=None)
    assert all("incomplete_fields" not in t for t in trips["trips"]) and trips["incomplete_fields"] == [], trips
    print("[PASS] Vehicle trips/idle: guessed columns -> trip distances and idle/engine totals unknown, not cached; "
          "located columns unchanged.")


def main_() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_parser_header_located,
        test_parser_no_header_lists_guessed_fields_and_keeps_existing_values,
        test_parser_missing_header_name,
        test_dashboard_mileage_guessed_column_and_missing_member,
        test_fleet_trips_guessed_column_and_missing_row,
        test_fleet_trips_unit_path_guessed_column,
        test_utilization_guessed_column_and_missing_row,
        test_idle_guessed_column_and_missing_row,
        test_unit_routes_guessed_columns,
    ]
    passed = 0
    try:
        for test in tests:
            try:
                test()
                passed += 1
            except Exception:
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
        fa._analytics_cache.clear()
        main._cache.clear()
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main_())
