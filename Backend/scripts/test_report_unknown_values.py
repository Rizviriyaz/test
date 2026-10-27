"""
Offline tests: unreadable Wialon report cells, unattributable rows and unread idle events are unknown (None),
never 0, in the non-fuel fleet analytics.

  - /api/fleet/trips (group report path): an unreadable mileage cell or a shared unit name gives
    gps_mileage_km None; the fleet and group totals cover known vehicles and count the unknown ones; the
    incomplete result is re-read after the short transient TTL
  - /api/fleet/utilization (group engine report path): an unreadable duration cell makes only the values that
    depend on it None (per field); fleet totals cover known values only and are flagged partial; not cached
  - group analytics: known vehicle distances are summed and the unknown ones counted; all unknown -> None
  - /api/idle/fleet-analytics: an unreadable idle/engine/total cell is None; without (complete) discrete events
    the event count, average and longest idle (fleet and per vehicle) are None; incomplete results are not cached

Wialon report functions are mocked; every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_report_unknown_values.py
"""
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import BASE_TS, CALCULATED, GROUPS, UNITS, age_report_caches, live_records  # noqa: E402

import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402

FLEET = [dict(r, speed=0, ignition=r["status"] != "STOPPED") for r in live_records()]


def _fleet_patches(stack, fleet=None, units=None):
    fa._analytics_cache.clear()
    main._cache.clear()
    stack.enter_context(patch.object(fa, "login", return_value="sid"))
    stack.enter_context(patch.object(fa, "get_vehicle_groups", return_value=GROUPS))
    stack.enter_context(patch.object(fa, "get_all_vehicles", return_value=units or UNITS))
    stack.enter_context(patch.object(fa, "get_calculated_data", return_value=CALCULATED))
    stack.enter_context(patch.object(fa, "get_vehicle_live_data", return_value=[dict(v) for v in (fleet or FLEET)]))


def _trip_row(name, km, unreadable=False):
    row = {"vehicle_name": name, "mileage_km": km, "avg_speed": "40 km/h", "max_speed": "90 km/h"}
    if unreadable:
        row["incomplete_fields"] = ["mileage_km"]
    return row


def test_trips_unreadable_mileage_is_unknown_and_not_cached():
    rows = [_trip_row("UNIT-A", 0.0, unreadable=True), _trip_row("UNIT-B", 120.5), _trip_row("UNIT-C", 30.0)]
    with ExitStack() as stack:
        _fleet_patches(stack)
        report = stack.enter_context(patch.object(fa, "execute_wialon_group_trips_report",
                                                  side_effect=lambda **k: [dict(r) for r in rows]))
        res = fa.get_trips_mileage_analytics(hours=24)
        fa.get_trips_mileage_analytics(hours=24)
        assert report.call_count == 1, "served from the short transient cache"
        age_report_caches(fa.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
        fa.get_trips_mileage_analytics(hours=24)
        assert report.call_count == 2, "a result with an unreadable cell is re-read after the short transient TTL"
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["gps_mileage_km"] is None, by_id[101]
    s = res["summary"]
    assert s["total_distance_km"] == 150.5 and s["total_distance_partial"] is True, s
    assert s["distance_unknown_vehicles"] == ["UNIT-A"], s
    groups = {g["group"]: (g["distance_km"], g["distance_unknown_vehicles"], g["distance_partial"]) for g in res["by_group"]}
    assert groups == {"Logistics": (120.5, 1, True), "Contracts": (30.0, 0, False)}, groups
    assert res["vehicles"][-1]["vehicle_id"] == 101, "unknown distance sorts last"
    print("[PASS] Trips: unreadable mileage -> None (not 0 km); fleet total 150.5 and group total 120.5 over known "
          "vehicles, flagged partial with the unknown count; re-read after the short transient TTL.")


def test_trips_complete_report_is_cached_and_unchanged():
    rows = [_trip_row("UNIT-A", 10.0), _trip_row("UNIT-B", 120.5), _trip_row("UNIT-C", 0.0)]
    with ExitStack() as stack:
        _fleet_patches(stack)
        report = stack.enter_context(patch.object(fa, "execute_wialon_group_trips_report",
                                                  side_effect=lambda **k: [dict(r) for r in rows]))
        res = fa.get_trips_mileage_analytics(hours=24)
        fa.get_trips_mileage_analytics(hours=24)
        assert report.call_count == 1, "a complete report is cached"
    s = res["summary"]
    assert s["total_distance_km"] == 130.5 and s["total_distance_partial"] is False and s["distance_unknown_vehicles"] == [], s
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[103]["gps_mileage_km"] == 0.0, "a recorded 0 km stays 0"
    print("[PASS] Trips: complete report keeps recorded values (including 0 km) and is cached.")


def test_trips_shared_name_is_unknown():
    twin_fleet = [dict(v, vehicle_name="UNIT-A") if v["vehicle_id"] == 102 else v for v in FLEET]
    rows = [_trip_row("UNIT-A", 55.0), _trip_row("UNIT-C", 30.0)]
    with ExitStack() as stack:
        _fleet_patches(stack, fleet=twin_fleet)
        stack.enter_context(patch.object(fa, "execute_wialon_group_trips_report", return_value=rows))
        res = fa.get_trips_mileage_analytics(hours=24)
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["gps_mileage_km"] is None and by_id[102]["gps_mileage_km"] is None, by_id
    assert res["summary"]["total_distance_km"] == 30.0, res["summary"]
    print("[PASS] Trips: a name shared by two units is attributed to neither (None), not 55 km to each.")


def test_utilization_unreadable_cell_is_unknown():
    engine = {"vehicles": [
        {"vehicle_name": "UNIT-A", "in_motion_seconds": 0, "total_idle_seconds": 0, "engine_hours_seconds": 0,
         "incomplete_fields": ["total_idle_seconds"]},
        {"vehicle_name": "UNIT-B", "in_motion_seconds": 3600, "total_idle_seconds": 1800, "engine_hours_seconds": 5400,
         "incomplete_fields": []},
        {"vehicle_name": "UNIT-C", "in_motion_seconds": 0, "total_idle_seconds": 0, "engine_hours_seconds": 0,
         "incomplete_fields": []},
    ], "column_warnings": []}
    with ExitStack() as stack:
        _fleet_patches(stack)
        report = stack.enter_context(patch.object(fa, "execute_wialon_group_engine_idle_report", return_value=engine))
        res = fa.get_fleet_utilization_analytics(hours=24)
        fa.get_fleet_utilization_analytics(hours=24)
        assert report.call_count == 2, "a result with an unreadable cell must not be served from cache"
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    a = by_id[101]
    # Per field: only the idle cell is unreadable, so idle and the values needing it are None; driving (a readable
    # 0 s), engine hours and utilization are kept
    assert (a["driving_seconds"], a["idle_seconds"], a["utilization_percentage"]) == (0, None, 0.0), a
    assert (a["stopped_seconds"], a["driving_to_idle_ratio"], a["engine_on_seconds"]) == (None, None, 0), a
    assert res["partial"] is True and res["unknown_vehicles"] == ["UNIT-A"], res
    assert res["idle_unknown_vehicles"] == ["UNIT-A"] and res["driving_unknown_vehicles"] == [], res
    assert res["total_idle_hours"] == 0.5 and res["total_driving_hours"] == 1.0, res
    # 3600 s driving over three vehicles with known driving time x 24 h
    assert res["fleet_utilization_pct"] == round(3600 / (3 * 24 * 3600) * 100, 1), res
    print("[PASS] Utilization: unreadable idle cell -> that vehicle's idle-dependent values None, its driving time "
          "and utilization kept; totals per field over known values, flagged partial; not cached.")


def test_group_analytics_unknown_distance():
    trips = {"vehicles": [{"vehicle_id": 101, "trip_count": None, "gps_mileage_km": None},
                          {"vehicle_id": 102, "trip_count": None, "gps_mileage_km": 40.0},
                          {"vehicle_id": 103, "trip_count": None, "gps_mileage_km": 12.0}]}
    with ExitStack() as stack:
        _fleet_patches(stack)
        stack.enter_context(patch.object(fa, "get_trips_mileage_analytics", return_value=trips))
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value={"by_group": []}))
        groups = {g["group_name"]: g for g in fa.get_fleet_groups_analytics()["groups"]}
    lg = groups["Logistics"]
    assert lg["distance_24h_km"] == 40.0 and lg["distance_unknown_vehicles"] == 1, lg
    assert groups["Contracts"]["distance_24h_km"] == 12.0 and groups["Contracts"]["distance_unknown_vehicles"] == 0, groups
    # Every member's trip count unknown: the group total is unknown, never 0
    assert lg["trips_24h"] is None and lg["trips_unknown_vehicles"] == 2, lg
    print("[PASS] Group analytics: known distances summed (40 km) with the unknown member counted; all trip counts "
          "unknown -> None.")


def _engine_row(name, idle, events=None, unreadable=()):
    return {"vehicle_name": name, "total_time_seconds": 7200, "engine_hours_seconds": 5000, "in_motion_seconds": 1000,
            "total_idle_seconds": idle, "idle_minutes": round(idle / 60, 2), "longest_idle_seconds": idle,
            "idle_events": None if events is None else len(events), "incomplete_fields": list(unreadable),
            "events": events or []}


def _event(name, start, dur):
    return {"vehicle_name": name, "start_time": start, "end_time": start + dur, "duration": dur,
            "duration_minutes": round(dur / 60, 2), "location": {"latitude": 25.2, "longitude": 55.3}, "address": "X"}


def _idle(rep, include_events, skip_cache=True):
    with patch("app.main.login", return_value="sid"), \
            patch("app.main.get_vehicle_groups", return_value=GROUPS), \
            patch("app.main.get_all_vehicles", return_value=UNITS), \
            patch("app.main.get_calculated_data", return_value=CALCULATED), \
            patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()), \
            patch("app.main.execute_wialon_group_engine_idle_report", return_value=rep) as report:
        res = main.get_fleet_idle_analytics(from_time=BASE_TS, to_time=BASE_TS + 86400 - 1,
                                            include_events=include_events, skip_cache=skip_cache)
        return res, report


def test_idle_unreadable_cells_are_unknown():
    main._cache.clear()
    ev_b = [_event("UNIT-B", BASE_TS + 100, 900)]
    rep = {"vehicles": [_engine_row("UNIT-A", 0, events=[], unreadable=("total_idle_seconds", "engine_hours_seconds")),
                        _engine_row("UNIT-B", 900, events=ev_b), _engine_row("UNIT-C", 0, events=[])],
           "events": ev_b, "events_incomplete": [], "column_warnings": []}
    res, _ = _idle(rep, include_events=True)
    v = {x["vehicle_id"]: x for x in res["by_vehicle"]}
    assert v[101]["total_idle_seconds"] is None and v[101]["engine_hours_seconds"] is None, v[101]
    assert v[101]["total_time_seconds"] == 7200, "a readable cell of the same row is kept"
    assert res["kpis"]["total_idle_seconds"] == 900 and res["kpis"]["total_idle_partial"] is True, res["kpis"]
    # The unreadable idle cell parsed as 0, so the report never fetched UNIT-A's events: they are unknown too
    assert v[101]["idle_events"] is None and v[101]["longest_idle_seconds"] is None, v[101]
    assert v[102]["idle_events"] == 1 and v[102]["longest_idle_seconds"] == 900, v[102]
    for key in ("idle_event_count", "average_idle_seconds", "longest_idle_seconds"):
        assert res["kpis"][key] is None, (key, res["kpis"][key])
    print("[PASS] Idle: unreadable idle/engine cells -> None (readable cells kept), incl. that unit's events and "
          "the fleet event KPIs; total 900 s flagged partial.")


def test_utilization_all_unknown_and_summary_flags():
    engine = {"vehicles": [
        {"vehicle_name": n, "in_motion_seconds": 0, "total_idle_seconds": 0, "engine_hours_seconds": 0,
         "incomplete_fields": ["in_motion_seconds"]} for n in ("UNIT-A", "UNIT-B", "UNIT-C")
    ], "column_warnings": []}
    with ExitStack() as stack:
        _fleet_patches(stack)
        stack.enter_context(patch.object(fa, "execute_wialon_group_engine_idle_report", return_value=engine))
        res = fa.get_fleet_utilization_analytics(hours=24)
    # Only In motion is unreadable: the driving-based values are unknown, the readable idle and engine totals kept
    for key in ("fleet_utilization_pct", "total_driving_hours", "driving_to_idle_ratio"):
        assert res[key] is None, (key, res[key])
    assert res["total_idle_hours"] == 0.0 and res["total_engine_on_hours"] == 0.0, res

    from fastapi.testclient import TestClient
    rows = [_trip_row("UNIT-A", 0.0, unreadable=True), _trip_row("UNIT-B", 20.0), _trip_row("UNIT-C", 5.0)]
    with ExitStack() as stack:
        _fleet_patches(stack)
        stack.enter_context(patch("app.main.authenticate_access_token", return_value={"id": "u", "role": "viewer"}))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=UNITS))
        stack.enter_context(patch("app.main.get_calculated_data", return_value=CALCULATED))
        stack.enter_context(patch("app.main.get_vehicle_live_data", return_value=[dict(v) for v in FLEET]))
        stack.enter_context(patch.object(fa, "execute_wialon_group_trips_report", side_effect=lambda **k: [dict(r) for r in rows]))
        stack.enter_context(patch.object(fa, "execute_wialon_group_engine_idle_report", return_value=engine))
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value={"summary": {"total_events": 0}}))
        client = TestClient(main.app)
        body = client.get("/api/dashboard/summary?hours=24", headers={"Authorization": "Bearer t"}).json()["summary"]
        assert main.cached("summary-24", ttl_seconds=60) is None, "a partial summary must not be cached"
    assert body["total_distance_km"] == 25.0 and body["total_distance_partial"] is True, body
    assert body["total_idle_minutes"] == 0.0 and body["total_idle_partial"] is True, body
    print("[PASS] Utilization with every vehicle's In motion unknown -> driving totals None (not 0%), readable idle "
          "kept; dashboard summary flags partial distance/idle and is not cached.")


def test_idle_event_kpis_unknown_without_events():
    main._cache.clear()
    rep = {"vehicles": [_engine_row("UNIT-A", 600), _engine_row("UNIT-B", 900), _engine_row("UNIT-C", 0)],
           "events": [], "events_incomplete": [], "column_warnings": []}
    res, _ = _idle(rep, include_events=False)
    k = res["kpis"]
    assert k["total_idle_seconds"] == 1500 and k["total_idle_partial"] is False, k
    for key in ("idle_event_count", "average_idle_seconds", "longest_idle_seconds", "longest_idle_event"):
        assert k[key] is None, (key, k[key])
    assert all(x["longest_idle_seconds"] is None for x in res["by_vehicle"]), res["by_vehicle"]
    print("[PASS] Idle: without discrete events, event count/average/longest are None (not 0); idle totals unchanged.")


def test_idle_incomplete_events_unknown_and_not_cached():
    main._cache.clear()
    ev_a = [_event("UNIT-A", BASE_TS + 50, 300)]
    rep = {"vehicles": [_engine_row("UNIT-A", 600, events=ev_a, unreadable=("idle_events",)),
                        _engine_row("UNIT-B", 0, events=[]), _engine_row("UNIT-C", 0, events=[])],
           "events": ev_a, "events_incomplete": ["UNIT-A"], "column_warnings": []}
    res, report = _idle(rep, include_events=True, skip_cache=False)
    assert res["kpis"]["idle_event_count"] is None and res["kpis"]["longest_idle_seconds"] is None, res["kpis"]
    _, report2 = _idle(rep, include_events=True, skip_cache=False)
    assert report2.call_count == 0, "served from the short transient cache"
    age_report_caches(main.TRANSIENT_PARTIAL_TTL_SECONDS + 1)
    _, report3 = _idle(rep, include_events=True, skip_cache=False)
    assert report3.call_count == 1, "an incomplete idle result is re-read after the short transient TTL"
    print("[PASS] Idle: partially read events -> event KPIs None (not a lower bound); re-read after the short "
          "transient TTL.")


def test_idle_complete_report_is_cached():
    main._cache.clear()
    rep = {"vehicles": [_engine_row("UNIT-A", 0, events=[]), _engine_row("UNIT-B", 0, events=[]),
                        _engine_row("UNIT-C", 0, events=[])],
           "events": [], "events_incomplete": [], "column_warnings": []}
    res, _ = _idle(rep, include_events=True, skip_cache=False)
    assert res["kpis"]["idle_event_count"] == 0 and res["kpis"]["longest_idle_seconds"] == 0, "recorded zero stays 0"
    _, report2 = _idle(rep, include_events=True, skip_cache=False)
    assert report2.call_count == 0, "a complete idle result is served from cache"
    print("[PASS] Idle: complete report with no idling keeps recorded zeros and is cached.")


def main_() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_trips_unreadable_mileage_is_unknown_and_not_cached,
        test_trips_complete_report_is_cached_and_unchanged,
        test_trips_shared_name_is_unknown,
        test_utilization_unreadable_cell_is_unknown,
        test_group_analytics_unknown_distance,
        test_idle_unreadable_cells_are_unknown,
        test_utilization_all_unknown_and_summary_flags,
        test_idle_event_kpis_unknown_without_events,
        test_idle_incomplete_events_unknown_and_not_cached,
        test_idle_complete_report_is_cached,
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
