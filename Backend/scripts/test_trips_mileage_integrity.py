"""
Offline tests: trip mileage comes only from Wialon's Trips Report, and anything it cannot attribute or read is
unknown, never a substitute or a double count.

  - /api/fleet/trips: group and unit Trips Reports both failing -> mileage and trip count unknown, no
    raw-message fallback, re-read after the short transient TTL
  - unit Trips Report: a malformed (dropped) trip row marks distance_km incomplete as well as row_shape, and
    /api/vehicles/{id}/trips and /api/fleet/trips report the distance as unknown / partial
  - /api/dashboard/mileage: a unit on several group-report rows is unknown, not summed twice; distinct trips
    with equal distances in a unit report are all counted
  - /api/dashboard/mileage: a row naming no fleet unit is left out of the total, listed, cached as a permanent gap
  - /api/fleet/trips (group report): a missing member (transient, re-read after 30 s) or ambiguous name (permanent,
    cached) -> unknown, known values kept
  - /api/dashboard/mileage: no known mileage -> total_km / average_km null; partial and complete unchanged
  - parse_mileage_km: ambiguous separators ('1,234') are unknown; parse_float_safe is unchanged
  - AI deep analytics: raw-message distance is relabelled and the Trips Report distance is added

Wialon is replaced by FakeReportEngine; every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_trips_mileage_integrity.py
"""
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    age_report_caches, BASE_TS, CALCULATED, GROUPS, TEMPLATE_GROUP_TRIPS, TEMPLATE_UNIT_TRIPS, UNITS, FakeReportEngine,
    default_tables, group_trip_row, live_records, unit_trip_row,
)

import app.ai.tools as ai_tools  # noqa: E402
import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402
import app.wialon_reports as wr  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 86400 - 1
FLEET = [dict(r, speed=0, ignition=r["status"] != "STOPPED") for r in live_records()]
NO_HISTORY = AssertionError("mileage must never fall back to raw message history")


def _patches(stack, engine):
    main._cache.clear()
    fa._analytics_cache.clear()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS))
    for target in ("app.main", "app.fleet_analytics"):
        stack.enter_context(patch(f"{target}.login", return_value="sid"))
        stack.enter_context(patch(f"{target}.get_vehicle_groups", return_value=GROUPS))
        stack.enter_context(patch(f"{target}.get_all_vehicles", return_value=UNITS))
        stack.enter_context(patch(f"{target}.get_calculated_data", return_value=CALCULATED))
        stack.enter_context(patch(f"{target}.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in FLEET]))
    stack.enter_context(patch("app.fleet_analytics.get_vehicle_history", side_effect=NO_HISTORY))
    stack.enter_context(patch("app.fleet_analytics.segment_vehicle_activity", side_effect=NO_HISTORY))


def _dashboard(group_id=None):
    return main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                      to_date=None, group_id=group_id)


def test_both_trips_reports_fail_unknown_not_cached():
    engine = FakeReportEngine(fail_exec={TEMPLATE_GROUP_TRIPS, TEMPLATE_UNIT_TRIPS})
    with ExitStack() as stack:
        _patches(stack, engine)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert all(v["gps_mileage_km"] is None and v["trip_count"] is None for v in res["vehicles"]), res["vehicles"]
        assert all(v["trips"] == [] for v in res["vehicles"]), res["vehicles"]
        summary = res["summary"]
        assert summary["total_trips"] is None and summary["total_distance_partial"] is True, summary
        assert summary["distance_unknown_vehicles"] == ["UNIT-A", "UNIT-B", "UNIT-C"], summary
        assert summary["average_trip_distance_km"] is None and summary["longest_trip_km"] is None, summary
        # No vehicle distance known at all: the fleet total is unknown, not 0 km
        assert summary["total_distance_km"] is None, summary
        # Odometer readings are a separate source and stay available
        by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
        assert by_id[101]["cumulative_odometer_km"] == 120345.6, by_id[101]
        before = engine.count("report/exec_report")
        fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") == before, "served from the short transient cache"
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") > before, "a failed report is re-read after the short transient TTL"
    print("[PASS] Fleet trips: group and unit Trips Reports both failing -> mileage and trip count unknown, "
          "no raw-history fallback, re-read after the short transient TTL.")


def test_unit_report_failure_only_affects_mileage_source():
    # Group report fails but unit reports succeed: Trips Report mileage is still used, never raw history
    engine = FakeReportEngine(fail_exec={TEMPLATE_GROUP_TRIPS})
    with ExitStack() as stack:
        _patches(stack, engine)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    assert all(v["gps_mileage_km"] == 63.0 and v["trip_count"] == 3 for v in res["vehicles"]), res["vehicles"]
    assert res["summary"]["total_distance_partial"] is False, res["summary"]
    print("[PASS] Fleet trips: group report failing -> unit Trips Reports used (63.0 km each), no raw history.")


def _tables_with_malformed_trip():
    t = default_tables()
    t[TEMPLATE_UNIT_TRIPS]["rows"].append(dict(unit_trip_row(4, BASE_TS + 40000, 20, "0:20:00", 7.0)))
    t[TEMPLATE_UNIT_TRIPS]["rows"][-1]["c"] = t[TEMPLATE_UNIT_TRIPS]["rows"][-1]["c"][:5]  # malformed: dropped
    return t


def test_malformed_trip_row_marks_distance_incomplete():
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=_tables_with_malformed_trip())):
        rep = wr.execute_wialon_unit_trips_report("sid", 101, FROM_TS, TO_TS)
    assert len(rep["trips"]) == 3, rep["trips"]
    assert {"row_shape", "distance_km"} <= set(rep["incomplete_fields"]), rep["incomplete_fields"]

    engine = FakeReportEngine(tables=_tables_with_malformed_trip())
    with ExitStack() as stack:
        _patches(stack, engine)
        res = main.get_vehicle_trips(vehicle_id=101, hours=24, from_time=FROM_TS, to_time=TO_TS)
        assert "distance_km" in res["incomplete_fields"] and "row_shape" in res["incomplete_fields"], res
        main.get_vehicle_trips(vehicle_id=101, hours=24, from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") == 2, "an incomplete trip list must not be served from cache"

    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=_tables_with_malformed_trip(), fail_exec={TEMPLATE_GROUP_TRIPS}))
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    assert all(v["gps_mileage_km"] is None for v in res["vehicles"]), "a partial sum is not a vehicle's mileage"
    assert res["summary"]["total_distance_partial"] is True, res["summary"]
    assert res["summary"]["total_distance_km"] is None, "no usable mileage for any vehicle -> unknown total, not 0"
    print("[PASS] Malformed trip row -> distance_km incomplete (with row_shape) in the unit report, the vehicle "
          "trips route (not cached) and fleet trips (unknown, partial).")


def test_dashboard_duplicate_rows_not_double_counted():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"].append(group_trip_row("UNIT-A", 250.75, driver="Ali"))
    engine = FakeReportEngine(tables=t)
    with ExitStack() as stack:
        _patches(stack, engine)
        res = _dashboard()
        units = [u for u in res["all_units"] if u["vehicle_name"] == "UNIT-A"]
        assert len(units) == 1 and units[0]["mileage"] is None, units
        assert units[0]["source"] == "duplicate_report_rows" and units[0]["vehicle_id"] == 101, units
        assert res["total_km"] == 1234.5, res["total_km"]  # UNIT-B 1234.5 + UNIT-C 0.0; UNIT-A unknown, not 501.5
        assert "UNIT-A" in res["incomplete_units"] and "UNIT-A" in res["ambiguous_vehicle_names"], res
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        cached = _dashboard()
        assert engine.count("report/exec_report") == 1, "an ambiguous name is a permanent gap: cached"
        assert "UNIT-A" in cached["incomplete_units"], "the cached result is still partial"
    print("[PASS] Dashboard mileage: a unit on two group-report rows -> unknown, not summed twice; cached as a "
          "permanent gap, still partial.")


def test_distinct_equal_distance_trips_are_all_counted():
    t = default_tables()
    # Two separate trips with the same distance (different times) are both real
    t[TEMPLATE_UNIT_TRIPS]["rows"] = [unit_trip_row(1, BASE_TS, 30, "0:30:00", 10.5),
                                      unit_trip_row(2, BASE_TS + 7200, 30, "0:30:00", 10.5)]
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=t))
        res = main.get_vehicle_trips(vehicle_id=101, hours=24, from_time=FROM_TS, to_time=TO_TS)
    assert res["trip_count"] == 2 and res["incomplete_fields"] == [], res
    assert sum(trip["distance_km"] for trip in res["trips"]) == 21.0, res["trips"]
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=t, fail_exec={TEMPLATE_GROUP_TRIPS}))
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    assert all(v["gps_mileage_km"] == 21.0 and v["trip_count"] == 2 for v in res["vehicles"]), res["vehicles"]
    print("[PASS] Distinct trips with equal distances are all counted (21.0 km over 2 trips).")


def test_dashboard_unmatched_row_excluded_and_flagged():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"].append(group_trip_row("GHOST", 99.0))
    engine = FakeReportEngine(tables=t)
    with ExitStack() as stack:
        _patches(stack, engine)
        res = _dashboard()
        assert res["total_km"] == 250.75 + 1234.5 + 0.0, res["total_km"]
        assert res["unmatched_report_rows"] == ["GHOST"], res
        ghost = [u for u in res["all_units"] if u["vehicle_name"] == "GHOST"]
        assert len(ghost) == 1 and ghost[0]["mileage"] is None and ghost[0]["vehicle_id"] is None, ghost
        assert ghost[0]["source"] == "unmatched_report_row" and "GHOST" in res["incomplete_units"], ghost
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        cached = _dashboard()
        assert engine.count("report/exec_report") == 1, "an unmatched row is a permanent gap: cached"
        assert "GHOST" in cached["incomplete_units"], "the cached result is still partial"
        # Same report through /api/fleet/trips: the unmatched row is not in the fleet total either
        fa._analytics_cache.clear()
        trips = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    assert trips["summary"]["total_distance_km"] == res["total_km"], (trips["summary"], res["total_km"])
    print("[PASS] Dashboard mileage: a row naming no fleet unit -> left out of the total (as /api/fleet/trips), "
          "listed as unmatched/incomplete, cached as a permanent gap.")


def test_fleet_trips_unmatched_row_flagged_not_cached():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"].append(group_trip_row("GHOST", 99.0))
    engine = FakeReportEngine(tables=t)
    with ExitStack() as stack:
        _patches(stack, engine)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        summary = res["summary"]
        assert summary["unmatched_report_rows"] == ["GHOST"], summary
        assert summary["total_distance_partial"] is True, summary
        assert summary["total_distance_km"] == 250.75 + 1234.5 + 0.0, summary  # GHOST's 99 km left out
        assert summary["distance_unknown_vehicles"] == [], summary
        assert "GHOST" not in {v["vehicle_name"] for v in res["vehicles"]}, res["vehicles"]
        before = engine.count("report/exec_report")
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        cached = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") == before, "an unmatched row is a permanent gap: cached"
        assert cached["summary"]["total_distance_partial"] is True, "the cached result is still partial"
    print("[PASS] Fleet trips: a group-report row naming no fleet unit -> listed in unmatched_report_rows, left "
          "out of the total, total_distance_partial=True, cached as a permanent gap.")


def test_fleet_trips_matched_rows_complete_and_cached():
    engine = FakeReportEngine()
    with ExitStack() as stack:
        _patches(stack, engine)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        summary = res["summary"]
        assert summary["unmatched_report_rows"] == [] and summary["total_distance_partial"] is False, summary
        assert summary["total_distance_km"] == 250.75 + 1234.5 + 0.0, summary  # a recorded 0 km stays 0
        before = engine.count("report/exec_report")
        fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") == before, "a complete result is still cached"
    print("[PASS] Fleet trips: all rows matched -> unmatched_report_rows=[], known total (with 0 km kept), cached.")


def test_fleet_trips_missing_member_not_cached():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"] = [r for r in t[TEMPLATE_GROUP_TRIPS]["rows"] if r["c"][0] != "UNIT-B"]
    engine = FakeReportEngine(tables=t)
    with ExitStack() as stack:
        _patches(stack, engine)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        by_name = {v["vehicle_name"]: v for v in res["vehicles"]}
        assert by_name["UNIT-B"]["gps_mileage_km"] is None and by_name["UNIT-B"]["trip_count"] is None, by_name["UNIT-B"]
        assert by_name["UNIT-A"]["gps_mileage_km"] == 250.75 and by_name["UNIT-C"]["gps_mileage_km"] == 0.0, by_name
        summary = res["summary"]
        assert summary["total_distance_km"] == 250.75 and summary["total_distance_partial"] is True, summary
        assert summary["distance_unknown_vehicles"] == ["UNIT-B"], summary
        before = engine.count("report/exec_report")
        fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") == before, "served from the short transient cache"
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") > before, "a missing member is re-read after the short transient TTL"
    print("[PASS] Fleet trips (group report): a member with no report row -> unknown, known values kept, re-read "
          "after the short transient TTL.")


def test_fleet_trips_ambiguous_name_not_cached():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"].append(group_trip_row("UNIT-A", 250.75, driver="Ali"))
    engine = FakeReportEngine(tables=t)
    with ExitStack() as stack:
        _patches(stack, engine)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        by_name = {v["vehicle_name"]: v for v in res["vehicles"]}
        assert by_name["UNIT-A"]["gps_mileage_km"] is None and by_name["UNIT-A"]["trip_count"] is None, by_name["UNIT-A"]
        assert by_name["UNIT-B"]["gps_mileage_km"] == 1234.5, by_name["UNIT-B"]
        summary = res["summary"]
        assert summary["total_distance_km"] == 1234.5 and summary["total_distance_partial"] is True, summary
        before = engine.count("report/exec_report")
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        cached = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
        assert engine.count("report/exec_report") == before, "an ambiguous name is a permanent gap: cached"
        assert cached["summary"]["total_distance_partial"] is True, "the cached result is still partial"
    print("[PASS] Fleet trips (group report): a name on two report rows -> unknown, known values kept, cached as a "
          "permanent gap.")


def test_dashboard_mileage_totals_unknown_partial_complete():
    # No unit has a readable mileage: total and average are unknown (null), never 0 km
    t = default_tables()
    for row in t[TEMPLATE_GROUP_TRIPS]["rows"]:
        row["c"][5] = "1,234 km"
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=t))
        res = _dashboard()
    assert all(u["mileage"] is None for u in res["all_units"]), res["all_units"]
    assert res["total_km"] is None and res["average_km"] is None, res
    # Partially known: total and average cover the known units (UNIT-B 1234.5, UNIT-C 0.0)
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"][0]["c"][5] = "1,234 km"
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=t))
        res = _dashboard()
    assert res["total_km"] == 1234.5 and res["average_km"] == 617.25, res
    # Complete: unchanged values
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine())
        res = _dashboard()
    assert res["total_km"] == 1485.25 and res["average_km"] == 495.08, res
    print("[PASS] Dashboard mileage: no known mileage -> total_km/average_km null; partial -> known-unit total "
          "and average; complete -> unchanged.")


def test_fleet_trips_some_unknown_keeps_known_total():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"][0]["c"][5] = "1,234 km"  # UNIT-A unknown; UNIT-B and UNIT-C readable
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=t))
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    summary = res["summary"]
    assert summary["total_distance_km"] == 1234.5 and summary["total_distance_partial"] is True, summary
    assert summary["distance_unknown_vehicles"] == ["UNIT-A"], summary
    print("[PASS] Fleet trips: one vehicle unknown -> the known total (1234.5 km) is kept and flagged partial.")


def test_dashboard_summary_passes_unknown_distance_through():
    engine = FakeReportEngine(fail_exec={TEMPLATE_GROUP_TRIPS, TEMPLATE_UNIT_TRIPS})
    with ExitStack() as stack:
        _patches(stack, engine)
        stack.enter_context(patch("app.fleet_analytics.get_safety_analytics", return_value={"summary": {"total_events": 0}}))
        stack.enter_context(patch("app.fleet_analytics.get_fleet_utilization_analytics",
                                  return_value={"total_idle_hours": 1.0, "partial": False}))
        res = main.get_dashboard_summary(hours=24)
    s = res["summary"]
    assert s["total_distance_km"] is None and s["total_distance_partial"] is True, s
    print("[PASS] Dashboard summary: unknown fleet distance passes through as null (partial), not 0.")


def test_ai_vehicle_trips_note_discloses_dropped_rows():
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=_tables_with_malformed_trip()))
        stack.enter_context(patch("app.ai.tools.login", return_value="sid"))
        stack.enter_context(patch("app.ai.tools.get_all_vehicles", return_value=UNITS))
        res = ai_tools.tool_get_vehicle_trips(101)
    assert res["trip_count"] == 3, res
    assert "trip_count may be incomplete" in res["note"] and "distances" in res["note"], res["note"]

    with ExitStack() as stack:
        _patches(stack, FakeReportEngine())
        stack.enter_context(patch("app.ai.tools.login", return_value="sid"))
        stack.enter_context(patch("app.ai.tools.get_all_vehicles", return_value=UNITS))
        res = ai_tools.tool_get_vehicle_trips(101)
    assert "note" not in res, res
    print("[PASS] AI vehicle trips: a dropped malformed row -> note says trip_count may be incomplete; clean -> no note.")


def test_dashboard_unique_rows_unchanged():
    engine = FakeReportEngine()
    with ExitStack() as stack:
        _patches(stack, engine)
        res = _dashboard()
        assert res["total_km"] == 250.75 + 1234.5 and res["unmatched_report_rows"] == [], res
        assert res["incomplete_units"] == [] and res["ambiguous_vehicle_names"] == [], res
        _dashboard()
        assert engine.count("report/exec_report") == 1, "a complete result is still cached"
    print("[PASS] Dashboard mileage: unique, matched rows -> unchanged totals and still cached.")


def test_parse_mileage_km():
    cases = {
        12.34: 12.34, 0: 0.0, "0.00 km": 0.0, "12.34 km": 12.34, "1234.5 km": 1234.5, "1.234 km": 1.234,
        "12,5 km": 12.5, "1,234.5 km": 1234.5, "1,234,567 km": 1234567.0, "1.234,5 km": 1234.5,
        "1.234.567 km": 1234567.0, "1 234.5 km": 1234.5,
        "-12.5": -12.5, "-12.5 km": -12.5, "0,500": 0.5, "0,500 km": 0.5, "0,5 km": 0.5,
    }
    for raw, expected in cases.items():
        assert wr.parse_mileage_km(raw) == expected, (raw, wr.parse_mileage_km(raw), expected)
    assert wr.parse_mileage_km({"v": 89.2, "t": "89.2 km"}) == 89.2
    assert wr.parse_mileage_km({"t": "1,234 km"}) is None
    for ambiguous in ("1,234", "1,234 km", "12,345 km", "1,23.4", "1.2.3", "1,2,3", "12.5.0,1", "", "---", "N/A",
                      None, True, float("nan"), float("inf"), "abc km", "12 km 5",
                      "--12", "--12 km", "-", "--", "-1,234", "0,123.5"):
        assert wr.parse_mileage_km(ambiguous) is None, (ambiguous, wr.parse_mileage_km(ambiguous))
    print("[PASS] parse_mileage_km: unambiguous formats parsed ('-12.5', '0,500' -> 0.5); '1,234', '--12' and "
          "malformed text -> unknown (None).")


def test_parse_float_safe_unchanged():
    # Characterization of the general parser (speeds, fuel and engine reports use it); deliberately not changed
    assert wr.parse_float_safe(12.34) == 12.34
    assert wr.parse_float_safe("1,234.56 km") == 1234.56
    assert wr.parse_float_safe("45,8 km/h") == 45.8
    assert wr.parse_float_safe({"v": 89.2, "t": "89.2 km"}) == 89.2
    assert wr.parse_float_safe({"t": "12.5"}) == 12.5
    assert wr.parse_float_safe("---") == 0.0 and wr.parse_float_safe(None) == 0.0
    assert wr.parse_float_safe("---", default=None) is None
    assert wr.parse_float_safe("1,234") == 1.234  # the ambiguity parse_mileage_km refuses
    assert wr.parse_float_safe("1.234,5") == 1.2345  # likewise misread here; mileage cells no longer use it
    print("[PASS] parse_float_safe: existing behaviour unchanged (mileage cells use parse_mileage_km instead).")


def test_ambiguous_mileage_cells_are_unknown():
    t = default_tables()
    t[TEMPLATE_GROUP_TRIPS]["rows"][0]["c"][5] = "1,234 km"  # UNIT-A: 1234 or 1.234?
    t[TEMPLATE_GROUP_TRIPS]["rows"][1]["c"][5] = "1.234,5 km"  # UNIT-B: only one reading
    t[TEMPLATE_UNIT_TRIPS]["rows"][0]["c"][9] = "1,234 km"
    engine = FakeReportEngine(tables=t)
    with ExitStack() as stack:
        _patches(stack, engine)
        res = _dashboard()
        units = {u["vehicle_name"]: u for u in res["all_units"]}
        assert units["UNIT-A"]["mileage"] is None and units["UNIT-B"]["mileage"] == 1234.5, units
        assert "UNIT-A" in res["incomplete_units"] and res["total_km"] == 1234.5, res
        trips = main.get_vehicle_trips(vehicle_id=101, hours=24, from_time=FROM_TS, to_time=TO_TS)
    assert "distance_km" in trips["incomplete_fields"], trips
    assert "distance_km" in trips["trips"][0]["incomplete_fields"], trips["trips"][0]
    print("[PASS] Ambiguous mileage cells ('1,234 km') -> unknown and flagged in dashboard mileage and unit trips.")


def test_deep_analytics_labels_raw_distance():
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine())
        stack.enter_context(patch("app.fleet_analytics.get_vehicle_history", return_value=[]))
        stack.enter_context(patch("app.fleet_analytics.segment_vehicle_activity",
                                  return_value={"trips": [{"distance": 50.0, "duration": 3600}], "idle_periods": []}))
        res = fa.get_single_vehicle_deep_analytics(vehicle_id=101, hours=24)
    assert res["metrics_24h"]["distance_km"] == 50.0, res["metrics_24h"]
    assert res["metrics_24h"]["distance_source"] == "raw_message_segmentation", res["metrics_24h"]
    print("[PASS] Deep analytics: raw-message distance is labelled distance_source=raw_message_segmentation.")


def test_ai_deep_analytics_uses_trips_report_distance():
    cached = {"found": True, "vehicle_id": 101, "metrics_24h": {
        "distance_km": 50.0, "distance_source": "raw_message_segmentation", "trips_count": 1}, "trips": []}
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine())
        stack.enter_context(patch("app.ai.tools.login", return_value="sid"))
        stack.enter_context(patch("app.ai.tools.get_all_vehicles", return_value=UNITS))
        stack.enter_context(patch("app.fleet_analytics.get_single_vehicle_deep_analytics", return_value=cached))
        res = ai_tools.tool_get_vehicle_deep_analytics("UNIT-A")
    m = res["metrics_24h"]
    assert "distance_km" not in m and "distance_source" not in m, m
    assert m["message_segmented_distance_km"] == 50.0 and m["trips_report_distance_km"] == 63.0, m
    assert "trips_report_distance_km" in res["note"], res
    assert cached["metrics_24h"]["distance_km"] == 50.0, "the shared cached analytics must not be mutated"

    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(fail_exec={TEMPLATE_UNIT_TRIPS}))
        stack.enter_context(patch("app.ai.tools.login", return_value="sid"))
        stack.enter_context(patch("app.ai.tools.get_all_vehicles", return_value=UNITS))
        stack.enter_context(patch("app.fleet_analytics.get_single_vehicle_deep_analytics", return_value=cached))
        res = ai_tools.tool_get_vehicle_deep_analytics(101)
    assert res["metrics_24h"]["trips_report_distance_km"] is None, res["metrics_24h"]
    assert res["metrics_24h"]["message_segmented_distance_km"] == 50.0, "never substituted for the report"

    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=_tables_with_malformed_trip()))
        stack.enter_context(patch("app.ai.tools.login", return_value="sid"))
        stack.enter_context(patch("app.ai.tools.get_all_vehicles", return_value=UNITS))
        stack.enter_context(patch("app.fleet_analytics.get_single_vehicle_deep_analytics", return_value=cached))
        res = ai_tools.tool_get_vehicle_deep_analytics(101)
    assert res["metrics_24h"]["trips_report_distance_km"] is None, "a partial report sum is unknown"
    print("[PASS] AI deep analytics: Trips Report distance (63.0 km) exposed; raw-message distance relabelled; "
          "failed or partial report -> null, never the raw figure.")


def main_():
    guard = OfflineGuard().start()
    tests = [
        test_both_trips_reports_fail_unknown_not_cached,
        test_unit_report_failure_only_affects_mileage_source,
        test_malformed_trip_row_marks_distance_incomplete,
        test_dashboard_duplicate_rows_not_double_counted,
        test_distinct_equal_distance_trips_are_all_counted,
        test_dashboard_unmatched_row_excluded_and_flagged,
        test_fleet_trips_unmatched_row_flagged_not_cached,
        test_fleet_trips_matched_rows_complete_and_cached,
        test_fleet_trips_missing_member_not_cached,
        test_fleet_trips_ambiguous_name_not_cached,
        test_dashboard_mileage_totals_unknown_partial_complete,
        test_fleet_trips_some_unknown_keeps_known_total,
        test_dashboard_summary_passes_unknown_distance_through,
        test_ai_vehicle_trips_note_discloses_dropped_rows,
        test_dashboard_unique_rows_unchanged,
        test_parse_mileage_km,
        test_parse_float_safe_unchanged,
        test_ambiguous_mileage_cells_are_unknown,
        test_deep_analytics_labels_raw_distance,
        test_ai_deep_analytics_uses_trips_report_distance,
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
