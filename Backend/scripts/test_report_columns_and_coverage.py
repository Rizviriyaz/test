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
  - /api/dashboard/mileage: units sharing a name are listed by id with mileage unknown and not cached
  - /api/dashboard/mileage: a name shared only with a unit outside the requested group is not ambiguous
  - /api/dashboard/mileage fleet-wide: a non-member sharing a member's name, or any uncovered unit when the
    report group's membership is unknown, is listed as unknown (outside_report_group), never left out
  - /api/idle/fleet-analytics and /api/fleet/utilization: a report row naming no fleet unit makes the fleet
    total partial and is not cached (response keys unchanged)
  - /api/idle/fleet-analytics: a dash-only engine cell ("-----", Wialon's no-value placeholder) stays unknown and in
    incomplete_fields; it is a permanent gap (cached for the normal TTL, still partial). Units outside the report
    group, ambiguous names and unmatched rows are permanent gaps too; guessed columns, unreadable duration cells and
    members missing from a group report are transient (re-read after TRANSIENT_PARTIAL_TTL_SECONDS)
  - 'today' / 'yesterday' caches (/api/dashboard/mileage, /api/fleet/trips) are never served after UAE
    midnight; rolling windows keep their cache
  - /api/fleet/trips cache key: explicit bounds > from_date > timeframe, as the resolver

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
    TEMPLATE_UNIT_TRIPS, UNITS, FakeReportEngine, age_report_caches, default_tables, group_engine_row, live_records,
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


# Idle-event position keys: read only from a header-located location cell, so they legitimately differ without a header
_EVENT_LOCATION_KEYS = {"location", "address", "location_verified", "event_location_unverified"}


def _strip_unlocated(obj, drop=frozenset({"unlocated_fields"})):
    if isinstance(obj, dict):
        return {k: _strip_unlocated(v, drop) for k, v in obj.items() if k not in drop}
    if isinstance(obj, list):
        return [_strip_unlocated(x, drop) for x in obj]
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
    # Every duration, mileage, count and interval time is identical (interval times come from Wialon's t1/t2)...
    drop = frozenset({"unlocated_fields"}) | _EVENT_LOCATION_KEYS
    assert _strip_unlocated(no_header, drop) == _strip_unlocated(with_header, drop)
    # ...but an idle event's position is never taken from a guessed column: without a header it is unknown
    events = no_header["group_engine"]["events"] + no_header["unit_engine"]["idle_events"]
    assert events and all(not e["location_verified"] and e["address"] == "Unknown Location" for e in events), events
    assert no_header["group_engine"]["event_location_unverified"] is True
    print("[PASS] Parser: no header -> guessed fields listed in unlocated_fields; values unchanged; event positions unknown.")


def test_parser_missing_header_name():
    out = _run_parsers(FakeReportEngine(tables=_tables(rename=(TEMPLATE_GROUP_TRIPS, "Mileage", "Distance"))))
    rows = out["group_trips"]
    assert all(r["unlocated_fields"] == ["mileage_km"] and r["column_warnings"] for r in rows), rows
    print("[PASS] Parser: header without 'Mileage' -> mileage_km listed as unlocated (and warned).")


def _main_patches(stack, engine, units=UNITS, groups=GROUPS):
    main._cache.clear()
    fa._analytics_cache.clear()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.wialon_reports.get_vehicle_groups", return_value=groups))
    for target in ("app.main", "app.fleet_analytics"):
        stack.enter_context(patch(f"{target}.login", return_value="sid"))
        stack.enter_context(patch(f"{target}.get_vehicle_groups", return_value=groups))
        stack.enter_context(patch(f"{target}.get_all_vehicles", return_value=units))
        stack.enter_context(patch(f"{target}.get_calculated_data", return_value=CALCULATED))
        stack.enter_context(patch(f"{target}.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in FLEET]))


def _assert_transient_cache(stack, engine_factory, call, what):
    """A transient-partial result is served from cache only for TRANSIENT_PARTIAL_TTL_SECONDS, then re-read."""
    engine = engine_factory()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    call()
    assert engine.count("report/exec_report") == 0, f"{what}: served from the short transient cache"
    age_report_caches(main.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
    call()
    assert engine.count("report/exec_report") == 1, f"{what}: re-read after TRANSIENT_PARTIAL_TTL_SECONDS"


def _assert_permanent_cache(stack, engine_factory, call, what, still_partial):
    """A permanently partial result is cached past the transient TTL and keeps its partial flags."""
    engine = engine_factory()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    age_report_caches(main.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
    res = call()
    assert engine.count("report/exec_report") == 0, f"{what}: permanent gap cached like a complete result"
    assert still_partial(res), f"{what}: the cached result is still flagged partial"


def test_dashboard_mileage_guessed_column_and_missing_member():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(include_header=False))
        res = main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                         to_date=None, group_id=None)
        assert all(u["mileage"] is None for u in res["all_units"]) and res["total_km"] is None, res["all_units"]
        assert res["average_km"] is None, res  # no known mileage: unknown, not 0 km
        assert sorted(res["incomplete_units"]) == ["UNIT-A", "UNIT-B", "UNIT-C"], res
        _assert_transient_cache(stack, lambda: FakeReportEngine(include_header=False), lambda: main.get_dashboard_mileage(
            timeframe="today", from_time=None, to_time=None, from_date=None, to_date=None, group_id=None),
            "a guessed-column result")
    # Group 7 (Logistics) = units 101 and 102; the report has no row for UNIT-B
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-B",))))
        res = main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                         to_date=None, group_id=7)
        units = {u["vehicle_name"]: u for u in res["all_units"]}
        assert units["UNIT-B"]["mileage"] is None and units["UNIT-B"]["source"] == "missing_from_report", units
        assert units["UNIT-A"]["mileage"] == 250.75, units
        assert "UNIT-B" in res["incomplete_units"] and res["total_km"] == 250.75 + 0.0, res
        _assert_transient_cache(stack, lambda: FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-B",))),
                                lambda: main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None,
                                                                   from_date=None, to_date=None, group_id=7),
                                "a result with a missing member")
    print("[PASS] Dashboard mileage: guessed Mileage column -> unknown; group member missing from the report -> "
          "unknown, listed in incomplete_units; both re-read after the short transient TTL.")


def test_dashboard_mileage_unit_outside_all_unit_group():
    """H1: fleet-wide, a fleet unit that is not a member of the All Unit report group is unknown, not left out."""
    groups = [dict(g, u=[101, 102]) if g["nm"] == "All Unit" else g for g in GROUPS]
    tables = _tables(drop_group_rows=("UNIT-C",))
    call = lambda gid: main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                                  to_date=None, group_id=gid)
    with ExitStack() as stack:
        stack.enter_context(patch("app.wialon_reports.GROUP_ID_ALL_UNITS", 0))
        _main_patches(stack, FakeReportEngine(tables=tables), groups=groups)
        res = call(None)
        units = {u["vehicle_name"]: u for u in res["all_units"]}
        assert units["UNIT-C"]["mileage"] is None and units["UNIT-C"]["source"] == "outside_report_group", units
        assert res["incomplete_units"] == ["UNIT-C"], res
        assert res["total_km"] == round(units["UNIT-A"]["mileage"] + units["UNIT-B"]["mileage"], 2), res
        _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=tables), lambda: call(None),
                                "a unit outside the report group", lambda r: r["incomplete_units"] == ["UNIT-C"])
    # An explicitly requested group keeps non-members out of scope (unchanged)
    with ExitStack() as stack:
        stack.enter_context(patch("app.wialon_reports.GROUP_ID_ALL_UNITS", 0))
        _main_patches(stack, FakeReportEngine(tables=tables), groups=groups)
        res = call(7)
        assert {u["vehicle_name"] for u in res["all_units"]} == {"UNIT-A", "UNIT-B"}, res["all_units"]
        assert res["incomplete_units"] == [], res
    print("[PASS] H1: fleet-wide mileage lists a unit outside the All Unit group as unknown (outside_report_group, "
          "cached as a permanent gap, still partial); an explicit group still leaves non-members out.")


def test_dashboard_mileage_outside_unit_sharing_in_group_name():
    """H1 (D1a): unit 103 is outside All Unit (101, 102) but shares the name UNIT-A with member 101."""
    units = [{"id": 101, "nm": "UNIT-A", "sens": {}}, {"id": 102, "nm": "UNIT-B", "sens": {}},
             {"id": 103, "nm": "UNIT-A", "sens": {}}]
    groups = [dict(g, u=[101, 102]) if g["nm"] == "All Unit" else g for g in GROUPS]
    tables = _tables(drop_group_rows=("UNIT-C",))
    call = lambda: main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                              to_date=None, group_id=None)
    with ExitStack() as stack:
        stack.enter_context(patch("app.wialon_reports.GROUP_ID_ALL_UNITS", 0))
        _main_patches(stack, FakeReportEngine(tables=tables), units=units, groups=groups)
        res = call()
        by_id = {u["vehicle_id"]: u for u in res["all_units"]}
        assert set(by_id) == {101, 102, 103} and len(res["all_units"]) == 3, res["all_units"]
        # The row belongs to the member; the same-named non-member is unknown, never dropped or given that row
        assert by_id[101]["mileage"] == 250.75 and by_id[101]["source"] == "wialon_native_report", by_id[101]
        assert by_id[103]["mileage"] is None and by_id[103]["source"] == "outside_report_group", by_id[103]
        assert res["incomplete_units"] == ["UNIT-A"] and res["ambiguous_vehicle_names"] == [], res
        assert res["total_km"] == 250.75 + 1234.5, res
        _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=tables), call,
                                "a non-member sharing a member's name", lambda r: r["incomplete_units"] == ["UNIT-A"])
    print("[PASS] H1: fleet-wide, a non-member sharing a member's name is listed as unknown (outside_report_group); "
          "the member keeps its row's mileage; cached as a permanent gap, still partial.")


def test_dashboard_mileage_report_group_membership_unknown():
    """H1 (D1b): WIALON_GROUP_ALL_UNITS names a group absent from the listed groups, so membership is unknown."""
    tables = _tables(drop_group_rows=("UNIT-C",))
    call = lambda: main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                              to_date=None, group_id=None)
    with ExitStack() as stack:
        stack.enter_context(patch("app.wialon_reports.GROUP_ID_ALL_UNITS", 42))
        _main_patches(stack, FakeReportEngine(tables=tables))
        res = call()
        units = {u["vehicle_name"]: u for u in res["all_units"]}
        assert set(units) == {"UNIT-A", "UNIT-B", "UNIT-C"}, res["all_units"]
        assert units["UNIT-C"]["mileage"] is None and units["UNIT-C"]["source"] == "outside_report_group", units
        assert units["UNIT-A"]["mileage"] == 250.75 and units["UNIT-B"]["mileage"] == 1234.5, units
        assert res["incomplete_units"] == ["UNIT-C"] and res["total_km"] == 250.75 + 1234.5, res
        _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=tables), call,
                                "a fleet unit no row names", lambda r: r["incomplete_units"] == ["UNIT-C"])
    print("[PASS] H1: with unknown report-group membership, a fleet unit no row names is unknown "
          "(outside_report_group), never silently left out; cached as a permanent gap, still partial.")


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


def test_fleet_trips_group_failure_has_no_unit_report_fallback():
    # The guessed-column rules of the unit report are covered by test_unit_routes_guessed_columns; fleet trips never
    # reach that report: a failed group report is a 502 (no earlier result), not one unit report per vehicle
    engine = FakeReportEngine(include_header=False, fail_exec={TEMPLATE_GROUP_TRIPS})
    with ExitStack() as stack:
        _main_patches(stack, engine)
        fa._TRIPS_BACKOFF._state.clear()
        try:
            fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
            raise AssertionError("expected WialonAPIError")
        except wr.WialonAPIError:
            pass
    unit_execs = [p for svc, p in engine.calls if svc == "report/exec_report" and p.get("reportTemplateId") == TEMPLATE_UNIT_TRIPS]
    assert unit_execs == [], unit_execs
    print("[PASS] Fleet trips: group report failing -> 502, no unit-report fallback.")


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
        _assert_transient_cache(stack, lambda: FakeReportEngine(include_header=False), lambda: main.get_fleet_idle_analytics(
            from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False), "a guessed-column result")
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",))))
        res = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False)
        v = {x["vehicle_id"]: x for x in res["by_vehicle"]}
        assert v[103]["total_idle_seconds"] is None and v[103]["idle_events"] is None, v[103]
        assert v[101]["total_idle_seconds"] == 5400 and res["kpis"]["total_idle_partial"] is True, res["kpis"]
        # UNIT-C is a member of the report group (All Unit), so its missing row is a transient report gap
        _assert_transient_cache(stack, lambda: FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",))),
                                lambda: main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS,
                                                                      include_events=True, skip_cache=False),
                                "a report-group member with no report row")
    # A unit outside the report group (H1) is a permanent gap: cached for the normal TTL, still partial
    groups = [dict(g, u=[101, 102]) if g["nm"] == "All Unit" else g for g in GROUPS]
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",))), groups=groups)
        idle = lambda: main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True,
                                                     skip_cache=False)
        res = idle()
        assert {x["vehicle_id"]: x for x in res["by_vehicle"]}[103]["total_idle_seconds"] is None, res["by_vehicle"]
        _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=_tables(drop_group_rows=("UNIT-C",))), idle,
                                "a unit outside the report group", lambda r: r["kpis"]["total_idle_partial"] is True)
    print("[PASS] Idle analytics: guessed columns or a report-group member with no row -> unknown, re-read after the "
          "short transient TTL; a unit outside the report group -> unknown (not 0 s), cached as a permanent gap, "
          "still partial.")


def _tables_with_ghost_engine_row():
    """default_tables() plus a group engine row naming no fleet unit."""
    t = default_tables()
    t[TEMPLATE_GROUP_ENGINE]["rows"].append(group_engine_row("GHOST", "24:00:00", "5:00:00", "3:00:00", "2:00:00", 99.0))
    return t


def test_idle_unmatched_report_row_flagged_not_cached():
    """D4: a fleet-wide idle total that leaves out a row naming no fleet unit is partial and not cached."""
    idle = lambda **kw: main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True,
                                                      skip_cache=False, **kw)
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        baseline = idle()
    assert baseline["kpis"]["total_idle_partial"] is False, baseline["kpis"]
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables_with_ghost_engine_row()))
        res = idle()
        assert res["kpis"]["total_idle_partial"] is True, res["kpis"]
        assert res["kpis"]["total_idle_seconds"] == baseline["kpis"]["total_idle_seconds"], res["kpis"]
        assert "GHOST" not in {v["vehicle_name"] for v in res["by_vehicle"]}, res["by_vehicle"]
        assert set(res) == set(baseline) and set(res["kpis"]) == set(baseline["kpis"]), "response shape unchanged"
        _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=_tables_with_ghost_engine_row()), idle,
                                "a result with an unmatched row", lambda r: r["kpis"]["total_idle_partial"] is True)
    # One vehicle's view is unaffected by another unit's unmatched row
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables_with_ghost_engine_row()))
        res = idle(vehicle_id=101)
    assert res["kpis"]["total_idle_partial"] is False and res["kpis"]["total_idle_seconds"] == 5400, res["kpis"]
    print("[PASS] D4: idle report row naming no fleet unit -> fleet total partial (value unchanged), cached as a "
          "permanent gap and still partial; a single-vehicle view is unaffected; response keys unchanged.")


def test_utilization_unmatched_report_row_flagged_not_cached():
    """D4: utilization totals that leave out a row naming no fleet unit are partial and not cached."""
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        baseline = fa.get_fleet_utilization_analytics(hours=24)
    assert baseline["partial"] is False, baseline
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=_tables_with_ghost_engine_row()))
        res = fa.get_fleet_utilization_analytics(hours=24)
        assert res["partial"] is True and res["unknown_vehicles"] == [], res
        for key in ("fleet_utilization_pct", "total_driving_hours", "total_idle_hours", "total_engine_on_hours"):
            assert res[key] == baseline[key], (key, res[key], baseline[key])
        assert "GHOST" not in {v["vehicle_name"] for v in res["vehicles"]} and set(res) == set(baseline), res
        engine = FakeReportEngine(tables=_tables_with_ghost_engine_row())
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        cached = fa.get_fleet_utilization_analytics(hours=24)
        # An unmatched row is a permanent gap: cached for the normal TTL, still flagged partial
        assert engine.count("report/exec_report") == 0 and cached["partial"] is True, "served from cache, still partial"
        assert fa._analytics_cache.get("fleet_utilization_24")["ttl"] is None, "normal TTL"
    print("[PASS] D4: utilization with a report row naming no fleet unit -> partial (totals unchanged), cached as a "
          "permanent gap; response keys unchanged.")


def _engine_tables_with_cells(**cells):
    """default_tables() with group engine cells replaced: name=(column index, raw cell text)."""
    t = default_tables()
    for row in t[TEMPLATE_GROUP_ENGINE]["rows"]:
        if row["c"][0] in cells:
            idx, text = cells[row["c"][0]]
            row["c"][idx] = text
    return t


MILEAGE_COL, IDLING_COL = 9, 8  # group engine report columns (GROUP_ENGINE_HEADER)


def _idle_closed(**kw):
    return main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False, **kw)


def test_engine_placeholder_stays_unknown_and_visible():
    """A dash-only Mileage cell ("-----") is still unknown (None) and listed in incomplete_fields."""
    tables = _engine_tables_with_cells(**{"UNIT-C": (MILEAGE_COL, "-----")})
    with ExitStack() as stack:
        stack.enter_context(patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)))
        stack.enter_context(patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS))
        rows = {r["vehicle_name"]: r for r in wr.execute_wialon_group_engine_idle_report(
            "sid", FROM_TS, TO_TS, include_events=False)["vehicles"]}
    c = rows["UNIT-C"]
    assert c["mileage_km"] is None and c["incomplete_fields"] == ["mileage_km"] and c["mileage_str"] == "-----", c
    assert wr.engine_row_unstable_incomplete(c) == [], "a dash-only placeholder is not an unstable incompleteness"
    assert all(wr.engine_row_unstable_incomplete(r) == [] == r["incomplete_fields"] for n, r in rows.items() if n != "UNIT-C")
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=tables))
        res = _idle_closed()
    v = {x["vehicle_name"]: x for x in res["by_vehicle"]}
    # Driving time is not tied to the mileage cell: the readable In motion value (0 s here) is kept as reported
    assert v["UNIT-C"]["engine_mileage_km"] is None and v["UNIT-C"]["in_motion_seconds"] == 0, v["UNIT-C"]
    assert v["UNIT-A"]["engine_mileage_km"] is not None, "other units unaffected"
    print("[PASS] 69597 case: a dash-only engine Mileage cell stays unknown (None) and in incomplete_fields; the "
          "unit's readable In motion value is still used.")


def test_engine_placeholder_only_closed_window_is_cached():
    tables = _engine_tables_with_cells(**{"UNIT-C": (MILEAGE_COL, "-----")})
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=tables))
        first = _idle_closed()
        engine = FakeReportEngine(tables=tables)
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        second = _idle_closed()
        assert engine.count("report/exec_report") == 0, "a closed window with only a stable placeholder is cached"
    assert second == first
    print("[PASS] 69597 case: a closed window whose only incompleteness is a dash-only placeholder is cached.")


def test_engine_placeholder_open_window_cached_as_permanent_gap():
    tables = _engine_tables_with_cells(**{"UNIT-C": (MILEAGE_COL, "-----")})
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=tables))
        stack.enter_context(patch("app.main.now_timestamp", return_value=TO_TS))  # the window ends now: still open
        _idle_closed()
        _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=tables), _idle_closed,
                                "a placeholder in a window still open",
                                lambda r: {x["vehicle_name"]: x for x in r["by_vehicle"]}["UNIT-C"]["engine_mileage_km"] is None)
    print("[PASS] 69597 case: the same placeholder in a window that has not closed is a permanent gap: cached for the "
          "normal TTL, the value still unknown.")


def test_engine_other_incomplete_cases_cache_policy():
    # An unreadable duration cell is transient: re-read after the short TTL
    transient = {
        "placeholder + unreadable Idling": {"UNIT-C": (MILEAGE_COL, "-----"), "UNIT-B": (IDLING_COL, "abc")},
        "unreadable Idling only": {"UNIT-B": (IDLING_COL, "??")},
    }
    for label, cells in transient.items():
        tables = _engine_tables_with_cells(**cells)
        with ExitStack() as stack:
            _main_patches(stack, FakeReportEngine(tables=tables))
            res = _idle_closed()
            assert {x["vehicle_name"]: x for x in res["by_vehicle"]}["UNIT-B"]["total_idle_seconds"] is None, label
            _assert_transient_cache(stack, lambda: FakeReportEngine(tables=tables), _idle_closed, label)
    # F10: the Engine Hours mileage is not displayed, so an unreadable mileage cell never limits caching; the value
    # itself stays unknown
    for label, cells in {"unreadable Mileage": {"UNIT-C": (MILEAGE_COL, "n/a")},
                         "single dash Mileage": {"UNIT-C": (MILEAGE_COL, "-")}}.items():
        tables = _engine_tables_with_cells(**cells)
        with ExitStack() as stack:
            _main_patches(stack, FakeReportEngine(tables=tables))
            _idle_closed()
            _assert_permanent_cache(
                stack, lambda: FakeReportEngine(tables=tables), _idle_closed, label,
                lambda r: {x["vehicle_name"]: x for x in r["by_vehicle"]}["UNIT-C"]["engine_mileage_km"] is None)
    # Utilization (always a window ending now): a row with an unreadable cell is treated as transient, kept only for
    # the short transient TTL and then re-read
    tables = _engine_tables_with_cells(**{"UNIT-C": (MILEAGE_COL, "-----")})
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=tables))
        fa.get_fleet_utilization_analytics(hours=24)
        engine = FakeReportEngine(tables=tables)
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        fa.get_fleet_utilization_analytics(hours=24)
        assert engine.count("report/exec_report") == 0, "kept for the transient TTL"
        assert fa._analytics_cache.get("fleet_utilization_24")["ttl"] == fa.TRANSIENT_PARTIAL_TTL_SECONDS
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        fa.get_fleet_utilization_analytics(hours=24)
        assert engine.count("report/exec_report") == 1, "re-read after the transient TTL"
    print("[PASS] 69597 case: unreadable duration cells (with or without a placeholder) are re-read after the short "
          "transient TTL; an unreadable Engine Hours mileage cell no longer limits idle caching (value stays unknown); "
          "utilization with a placeholder is kept only for the transient TTL.")


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


def _duplicate_name_setup():
    """Units 101 and 102 are both named UNIT-A; the report has two UNIT-A rows (250.75 km and 1234.5 km)."""
    units = [{"id": 101, "nm": "UNIT-A", "sens": {}}, {"id": 102, "nm": "UNIT-A", "sens": {}},
             {"id": 103, "nm": "UNIT-C", "sens": {}}]
    tables = _tables()
    for row in tables[TEMPLATE_GROUP_TRIPS]["rows"]:
        if row["c"][0] == "UNIT-B":
            row["c"][0] = "UNIT-A"
    return units, tables


def test_dashboard_mileage_duplicate_names_unknown():
    units, tables = _duplicate_name_setup()
    for group_id in (None, 7):
        with ExitStack() as stack:
            _main_patches(stack, FakeReportEngine(tables=tables), units=units)
            res = main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                             to_date=None, group_id=group_id)
            by_id = {u["vehicle_id"]: u for u in res["all_units"]}
            # No row is emitted without a unit id, and neither same-named unit gets a row's mileage
            # (the fake engine returns every fixture row whatever the group, so UNIT-C's row is always there)
            assert None not in by_id and len(res["all_units"]) == 3, res["all_units"]
            for vid in (101, 102):
                assert by_id[vid]["mileage"] is None and by_id[vid]["source"] == "ambiguous_vehicle_name", by_id[vid]
            assert res["incomplete_units"] == ["UNIT-A", "UNIT-A"], res["incomplete_units"]
            assert res["ambiguous_vehicle_names"] == ["UNIT-A"], res
            assert res["total_km"] == 0.0, "neither 250.75 nor 1234.5 km may be counted for an unattributable row"
            assert by_id[103]["mileage"] == 0.0 and by_id[103]["source"] == "wialon_native_report", by_id[103]
            _assert_permanent_cache(stack, lambda: FakeReportEngine(tables=tables),
                                    lambda: main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None,
                                                                       from_date=None, to_date=None, group_id=group_id),
                                    "a result with ambiguous units",
                                    lambda r: r["incomplete_units"] == ["UNIT-A", "UNIT-A"])
    print("[PASS] Dashboard mileage: two units sharing a name -> each listed by id with mileage unknown, in "
          "incomplete_units, not counted in total_km; cached as a permanent gap, still partial.")


def test_dashboard_mileage_duplicate_name_outside_group():
    """UNIT-A names unit 101 (in group 7) and unit 103 (outside it); group 7 = 101, 102."""
    units = [{"id": 101, "nm": "UNIT-A", "sens": {}}, {"id": 102, "nm": "UNIT-B", "sens": {}},
             {"id": 103, "nm": "UNIT-A", "sens": {}}]
    tables = _tables(drop_group_rows=("UNIT-C",))
    call = lambda gid: main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                                  to_date=None, group_id=gid)
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine(tables=tables), units=units)
        res = call(7)
        by_id = {u["vehicle_id"]: u for u in res["all_units"]}
        assert set(by_id) == {101, 102}, res["all_units"]
        assert by_id[101]["mileage"] == 250.75 and by_id[101]["source"] == "wialon_native_report", by_id[101]
        assert by_id[102]["mileage"] == 1234.5, by_id[102]
        assert res["ambiguous_vehicle_names"] == [] and res["incomplete_units"] == [], res
        engine = FakeReportEngine(tables=tables)
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        call(7)
        assert engine.count("report/exec_report") == 0, "a complete in-group result is cached as before"
    # Fleet-wide (All Unit holds both) and unknown group membership (fallback to whole-fleet matching): ambiguous
    for gid in (None, 99):
        with ExitStack() as stack:
            _main_patches(stack, FakeReportEngine(tables=tables), units=units)
            res = call(gid)
            by_id = {u["vehicle_id"]: u for u in res["all_units"]}
            assert set(by_id) == {101, 102, 103}, (gid, res["all_units"])
            for vid in (101, 103):
                assert by_id[vid]["mileage"] is None and by_id[vid]["source"] == "ambiguous_vehicle_name", (gid, by_id[vid])
            assert by_id[102]["mileage"] == 1234.5, (gid, by_id[102])
            assert res["ambiguous_vehicle_names"] == ["UNIT-A"] and res["total_km"] == 1234.5, (gid, res)
    print("[PASS] Dashboard mileage: a name shared with a unit outside the requested group keeps the in-group "
          "unit's mileage; fleet-wide or with unknown membership it stays ambiguous.")


def test_dashboard_mileage_unique_names_still_cached():
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        res = main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                         to_date=None, group_id=None)
        units = {u["vehicle_id"]: u["mileage"] for u in res["all_units"]}
        assert units == {101: 250.75, 102: 1234.5, 103: 0.0} and res["incomplete_units"] == [], res
        engine = FakeReportEngine()
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None,
                                   to_date=None, group_id=None)
        assert engine.count("report/exec_report") == 0, "a complete result is still served from cache"
    print("[PASS] Dashboard mileage: unique names -> unchanged values, complete result cached.")


def _uae(y, mo, d, h, mi, s):
    from app.utils.datetime_helpers import UAE_TZ
    from datetime import datetime
    return datetime(y, mo, d, h, mi, s, tzinfo=UAE_TZ)


def _call_at(stack, when, call):
    """Run `call` with the UAE clock at `when`; returns (result, exec_report count for that call)."""
    engine = FakeReportEngine()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.utils.datetime_helpers.get_uae_now", return_value=when))
    return call(), engine.count("report/exec_report")


def test_calendar_cache_rolls_over_at_uae_midnight():
    before, also_before, after = _uae(2026, 10, 24, 23, 59, 50), _uae(2026, 10, 24, 23, 59, 55), _uae(2026, 10, 25, 0, 0, 10)
    # (window before midnight, window after midnight) as UAE wall clocks; 'today' ends at the call time
    expected = {
        "today": (((2026, 10, 24, 0, 0, 0), None), ((2026, 10, 25, 0, 0, 0), None)),
        "yesterday": (((2026, 10, 23, 0, 0, 0), (2026, 10, 23, 23, 59, 59)),
                      ((2026, 10, 24, 0, 0, 0), (2026, 10, 24, 23, 59, 59))),
    }
    for timeframe, ((lo1, hi1), (lo3, hi3)) in expected.items():
        calls = {
            "/api/dashboard/mileage": lambda: main.get_dashboard_mileage(
                timeframe=timeframe, from_time=None, to_time=None, from_date=None, to_date=None, group_id=None),
            "/api/fleet/trips (analytics)": lambda: fa.get_trips_mileage_analytics(timeframe=timeframe),
            "/api/fleet/trips (route)": lambda: main.api_fleet_trips(
                hours=24, from_time=None, to_time=None, timeframe=timeframe, from_date=None, to_date=None),
        }
        for name, call in calls.items():
            lo, hi = ("effective_from", "effective_to") if name == "/api/dashboard/mileage" else ("from_time", "to_time")
            with ExitStack() as stack:
                _main_patches(stack, FakeReportEngine())
                first, n1 = _call_at(stack, before, call)
                second, n2 = _call_at(stack, also_before, call)
                third, n3 = _call_at(stack, after, call)
            label = f"{name} {timeframe}"
            assert (n1, n2, n3) == (1, 0, 1), f"{label}: exec counts {(n1, n2, n3)}"
            assert first[lo] == int(_uae(*lo1).timestamp()), (label, first[lo])
            assert first[hi] == (int(_uae(*hi1).timestamp()) if hi1 else int(before.timestamp())), (label, first[hi])
            assert second == first, f"{label}: the second call before midnight must be the cached result"
            assert third[lo] == int(_uae(*lo3).timestamp()), f"{label} served the previous day's window"
            assert third[hi] == (int(_uae(*hi3).timestamp()) if hi3 else int(after.timestamp())), (label, third[hi])
    print("[PASS] 'today' and 'yesterday' caches: reused within the UAE day with exact window bounds, never served "
          "after UAE midnight (/api/dashboard/mileage, /api/fleet/trips analytics and route).")


def test_fleet_trips_cache_key_follows_resolver_precedence():
    t0, t1 = _uae(2026, 10, 24, 12, 0, 0), _uae(2026, 10, 24, 12, 0, 20)
    x, y = int(_uae(2026, 10, 20, 6, 0, 0).timestamp()), int(_uae(2026, 10, 20, 18, 0, 0).timestamp())
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        today, n_today = _call_at(stack, t0, lambda: fa.get_trips_mileage_analytics(timeframe="today"))
        # Explicit bounds win over timeframe and from_date (as in get_timeframe_timestamps): never today's entry
        exact, n_exact = _call_at(stack, t0, lambda: fa.get_trips_mileage_analytics(from_time=x, to_time=y, timeframe="today"))
        other, n_other = _call_at(stack, t0, lambda: fa.get_trips_mileage_analytics(from_time=x, to_time=y + 3600, timeframe="today"))
        with_date, n_with_date = _call_at(stack, t0, lambda: fa.get_trips_mileage_analytics(
            from_time=x, to_time=y, from_date="2026-10-21"))
        dates, n_dates = _call_at(stack, t0, lambda: fa.get_trips_mileage_analytics(from_date="2026-10-21", timeframe="today"))
        _, n_today_again = _call_at(stack, t1, lambda: fa.get_trips_mileage_analytics(timeframe="today"))
        _, n_dates_again = _call_at(stack, t1, lambda: fa.get_trips_mileage_analytics(from_date="2026-10-21"))
    assert (today["from_time"], today["to_time"]) == (int(_uae(2026, 10, 24, 0, 0, 0).timestamp()), int(t0.timestamp()))
    assert (exact["from_time"], exact["to_time"]) == (x, y), "explicit bounds must not be answered by the 'today' entry"
    assert (other["from_time"], other["to_time"]) == (x, y + 3600), "a different explicit window must not collide"
    assert (with_date["from_time"], with_date["to_time"]) == (x, y), "explicit bounds win over from_date"
    assert dates["from_time"] == int(_uae(2026, 10, 21, 0, 0, 0).timestamp()), "from_date wins over timeframe"
    assert (n_today, n_exact, n_other, n_with_date, n_dates) == (1, 1, 1, 0, 1), \
        (n_today, n_exact, n_other, n_with_date, n_dates)
    assert (n_today_again, n_dates_again) == (0, 0), "each window is still served from its own cache entry"

    # Route: rolling timeframes keep their cache (the route passes the caller's bounds, not resolved ones)
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        route = lambda **kw: main.api_fleet_trips(**{**dict(hours=24, from_time=None, to_time=None, timeframe=None,
                                                             from_date=None, to_date=None), **kw})
        _, n1 = _call_at(stack, t0, lambda: route(timeframe="24h"))
        _, n2 = _call_at(stack, t1, lambda: route(timeframe="24h"))
        res, n3 = _call_at(stack, t1, lambda: route(timeframe="24h", from_time=x, to_time=y))
    assert (n1, n2, n3) == (1, 0, 1), (n1, n2, n3)
    assert (res["from_time"], res["to_time"]) == (x, y)
    print("[PASS] /api/fleet/trips cache key: explicit bounds > from_date > timeframe, as the resolver; distinct "
          "windows never collide; rolling '24h' still cached through the route.")


def test_rolling_timeframe_cache_unchanged():
    t0, t1 = _uae(2026, 10, 24, 12, 0, 0), _uae(2026, 10, 24, 12, 0, 20)
    with ExitStack() as stack:
        _main_patches(stack, FakeReportEngine())
        call = lambda: main.get_dashboard_mileage(timeframe="24h", from_time=None, to_time=None, from_date=None,
                                                  to_date=None, group_id=None)
        _, n1 = _call_at(stack, t0, call)
        _, n2 = _call_at(stack, t1, call)
    assert (n1, n2) == (1, 0), (n1, n2)
    print("[PASS] Rolling '24h' window: still served from cache within its TTL (key not tied to a moving start).")


def main_() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_parser_header_located,
        test_parser_no_header_lists_guessed_fields_and_keeps_existing_values,
        test_parser_missing_header_name,
        test_dashboard_mileage_guessed_column_and_missing_member,
        test_dashboard_mileage_unit_outside_all_unit_group,
        test_dashboard_mileage_outside_unit_sharing_in_group_name,
        test_dashboard_mileage_report_group_membership_unknown,
        test_fleet_trips_guessed_column_and_missing_row,
        test_fleet_trips_group_failure_has_no_unit_report_fallback,
        test_utilization_guessed_column_and_missing_row,
        test_idle_guessed_column_and_missing_row,
        test_idle_unmatched_report_row_flagged_not_cached,
        test_utilization_unmatched_report_row_flagged_not_cached,
        test_engine_placeholder_stays_unknown_and_visible,
        test_engine_placeholder_only_closed_window_is_cached,
        test_engine_placeholder_open_window_cached_as_permanent_gap,
        test_engine_other_incomplete_cases_cache_policy,
        test_unit_routes_guessed_columns,
        test_dashboard_mileage_duplicate_names_unknown,
        test_dashboard_mileage_duplicate_name_outside_group,
        test_dashboard_mileage_unique_names_still_cached,
        test_calendar_cache_rolls_over_at_uae_midnight,
        test_fleet_trips_cache_key_follows_resolver_precedence,
        test_rolling_timeframe_cache_unchanged,
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
