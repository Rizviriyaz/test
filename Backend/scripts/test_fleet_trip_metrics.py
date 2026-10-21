"""
Offline tests for fleet trip metrics (audit item H2): trip counts are never inferred from mileage or
driving time.

  - group trips report path (no trip count in template 5): trip_count, total_trips, average trip
    distance/duration, longest trip and per-group trips are None; distance is unchanged
  - unit trips report path: real counts for several trips per vehicle and for zero trips
  - zero trips fleet-wide: total 0, no average or longest trip (None, not 0)
  - utilization (group engine report): trip_count None
  - group analytics trips_24h: sum when every count is known, None when any is unknown or missing
  - API: /api/fleet/trips and /api/dashboard/summary return null, not a vehicle count

Wialon functions are mocked in app.fleet_analytics; auth is mocked. Every outbound HTTP call is blocked
by OfflineGuard. Run from Backend/:  python scripts/test_fleet_trip_metrics.py
"""
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.fleet_analytics as fa  # noqa: E402
from app.main import app, _cache  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

FLEET = [
    {"vehicle_id": 101, "vehicle_name": "UNIT-A", "group": "Logistics", "status": "MOVING", "speed": 40,
     "ignition": True, "latitude": 25.2, "longitude": 55.3, "connection_status": "STABLE", "is_offline": False},
    {"vehicle_id": 102, "vehicle_name": "UNIT-B", "group": "Logistics", "status": "STOPPED", "speed": 0,
     "ignition": False, "latitude": 25.1, "longitude": 55.2, "connection_status": "STABLE", "is_offline": False},
    {"vehicle_id": 103, "vehicle_name": "UNIT-C", "group": "Contracts", "status": "STOPPED", "speed": 0,
     "ignition": False, "latitude": 25.0, "longitude": 55.1, "connection_status": "STABLE", "is_offline": False},
]
RAW_UNITS = [{"id": v["vehicle_id"], "nm": v["vehicle_name"], "sens": {}} for v in FLEET]
CALC = [{"i": v["vehicle_id"], "mileage": {"value": 1000.0 * v["vehicle_id"]}} for v in FLEET]
GROUPS = [
    {"id": 7, "nm": "Logistics", "u": [101, 102]},
    {"id": 8, "nm": "Contracts", "u": [103]},
    {"id": 9, "nm": "Empty Group", "u": []},
]
GROUP_TRIPS_ROWS = [
    {"vehicle_name": "UNIT-A", "mileage_km": 250.75, "avg_speed": "45 km/h", "max_speed": "110 km/h"},
    {"vehicle_name": "UNIT-B", "mileage_km": 1234.5, "avg_speed": "50 km/h", "max_speed": "95 km/h"},
    {"vehicle_name": "UNIT-C", "mileage_km": 0.0, "avg_speed": "0 km/h", "max_speed": "0 km/h"},
]


def _trip(km, minutes, start):
    return {"start_time": start, "end_time": start + minutes * 60, "distance_km": km, "duration_minutes": float(minutes),
            "moving_duration": "0:30:00", "moving_duration_str": "0:30:00", "max_speed": "80 km/h",
            "average_speed": "40 km/h", "start_location": "From", "end_location": "To"}


# UNIT-A: 3 trips, UNIT-B: 1 trip, UNIT-C: 0 trips
UNIT_TRIPS = {
    101: [_trip(10.5, 35, 1000), _trip(21.0, 40, 9000), _trip(4.5, 15, 20000)],
    102: [_trip(60.0, 50, 5000)],
    103: [],
}


def _base_patches(stack, group_trips=GROUP_TRIPS_ROWS, unit_trips=UNIT_TRIPS):
    fa._analytics_cache.clear()
    _cache.clear()
    stack.enter_context(patch.object(fa, "login", return_value="sid"))
    stack.enter_context(patch.object(fa, "get_vehicle_groups", return_value=GROUPS))
    stack.enter_context(patch.object(fa, "get_all_vehicles", return_value=RAW_UNITS))
    stack.enter_context(patch.object(fa, "get_calculated_data", return_value=CALC))
    stack.enter_context(patch.object(fa, "get_vehicle_live_data", return_value=[dict(v) for v in FLEET]))
    if group_trips is None:
        gt = patch.object(fa, "execute_wialon_group_trips_report", side_effect=WialonAPIError("group report failed"))
    else:
        gt = patch.object(fa, "execute_wialon_group_trips_report", return_value=[dict(r) for r in group_trips])
    stack.enter_context(gt)
    stack.enter_context(patch.object(
        fa, "execute_wialon_unit_trips_report",
        side_effect=lambda sid, vid, f, t: {"trips": [dict(x) for x in unit_trips[vid]], "stats": {}},
    ))


def test_group_report_path_trip_metrics_unavailable():
    with ExitStack() as stack:
        _base_patches(stack)
        res = fa.get_trips_mileage_analytics(hours=24)
    s = res["summary"]
    for key in ("total_trips", "average_trip_distance_km", "average_trip_duration_minutes", "longest_trip_km"):
        assert s[key] is None, (key, s[key])
    assert all(v["trip_count"] is None for v in res["vehicles"]), res["vehicles"]
    assert all(g["trips"] is None for g in res["by_group"]), res["by_group"]
    # Distance is real report data and must be unchanged; longest trip must not be a vehicle's daily mileage
    assert s["total_distance_km"] == 1485.25, s
    assert s["longest_trip_km"] != 1234.5
    assert {g["group"]: g["distance_km"] for g in res["by_group"]} == {"Logistics": 1485.25, "Contracts": 0.0}
    print("[PASS] Group report path: trip count, averages, longest trip and group trips are None; distance unchanged.")


def test_unit_report_path_multiple_and_zero_trips():
    with ExitStack() as stack:
        _base_patches(stack, group_trips=None)
        res = fa.get_trips_mileage_analytics(hours=24)
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["trip_count"] == 3 and by_id[102]["trip_count"] == 1 and by_id[103]["trip_count"] == 0, by_id
    s = res["summary"]
    assert s["total_trips"] == 4, s
    assert s["total_distance_km"] == 96.0, s                      # 10.5 + 21 + 4.5 + 60
    assert s["average_trip_distance_km"] == 24.0, s               # 96 / 4 trips
    assert s["average_trip_duration_minutes"] == 35.0, s          # (35 + 40 + 15 + 50) / 4
    assert s["longest_trip_km"] == 60.0, s                        # longest single trip
    groups = {g["group"]: g for g in res["by_group"]}
    assert groups["Logistics"]["trips"] == 4 and groups["Contracts"]["trips"] == 0, groups
    print("[PASS] Unit report path: 3/1/0 trips per vehicle, total 4, avg 24.0 km, avg 35.0 min, longest 60.0 km, group sums.")


def test_zero_trips_fleet_wide():
    with ExitStack() as stack:
        _base_patches(stack, group_trips=None, unit_trips={101: [], 102: [], 103: []})
        res = fa.get_trips_mileage_analytics(hours=24)
    s = res["summary"]
    assert s["total_trips"] == 0, s
    assert s["average_trip_distance_km"] is None and s["average_trip_duration_minutes"] is None, s
    assert s["longest_trip_km"] is None, s
    assert all(g["trips"] == 0 for g in res["by_group"]), res["by_group"]
    print("[PASS] Zero trips: total 0, groups 0, average and longest trip None (not 0).")


def test_utilization_trip_count_unavailable():
    engine = {"vehicles": [
        {"vehicle_name": "UNIT-A", "in_motion_seconds": 3600, "total_idle_seconds": 600, "engine_hours_seconds": 4200},
        {"vehicle_name": "UNIT-B", "in_motion_seconds": 0, "total_idle_seconds": 0, "engine_hours_seconds": 0},
    ]}
    with ExitStack() as stack:
        _base_patches(stack)
        stack.enter_context(patch.object(fa, "execute_wialon_group_engine_idle_report", return_value=engine))
        res = fa.get_fleet_utilization_analytics(hours=24)
    assert all(v["trip_count"] is None for v in res["vehicles"]), res["vehicles"]
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["driving_seconds"] == 3600 and by_id[101]["idle_seconds"] == 600, by_id[101]
    print("[PASS] Utilization: trip_count None (not 1 per driving vehicle); driving/idle unchanged.")


def _groups_with(trips_vehicles):
    with ExitStack() as stack:
        _base_patches(stack)
        stack.enter_context(patch.object(fa, "get_trips_mileage_analytics",
                                         return_value={"vehicles": trips_vehicles}))
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value={"by_group": []}))
        return {g["group_name"]: g for g in fa.get_fleet_groups_analytics()["groups"]}


def test_group_analytics_trips():
    known = [{"vehicle_id": 101, "trip_count": 3, "gps_mileage_km": 36.0},
             {"vehicle_id": 102, "trip_count": 1, "gps_mileage_km": 60.0},
             {"vehicle_id": 103, "trip_count": 0, "gps_mileage_km": 0.0}]
    g = _groups_with(known)
    assert g["Logistics"]["trips_24h"] == 4 and g["Contracts"]["trips_24h"] == 0, g
    assert g["Empty Group"]["trips_24h"] == 0, g

    one_unknown = [dict(known[0], trip_count=None), known[1], known[2]]
    g = _groups_with(one_unknown)
    assert g["Logistics"]["trips_24h"] is None and g["Contracts"]["trips_24h"] == 0, g
    assert g["Logistics"]["distance_24h_km"] == 96.0, g

    missing = known[1:]  # UNIT-A absent from the trips data
    g = _groups_with(missing)
    assert g["Logistics"]["trips_24h"] is None, g
    print("[PASS] Group analytics: trips_24h sums known counts; None when any count is unknown or missing.")


def test_api_responses_null():
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    user = {"id": "u1", "email": "x@example.com", "role": "viewer"}
    with ExitStack() as stack:
        _base_patches(stack)
        stack.enter_context(patch("app.main.authenticate_access_token", return_value=user))
        res = client.get("/api/fleet/trips?hours=24", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]["total_trips"] is None and body["summary"]["longest_trip_km"] is None, body["summary"]
        assert body["summary"]["average_trip_distance_km"] is None, body["summary"]
        assert all(v["trip_count"] is None for v in body["vehicles"])

        fa._analytics_cache.clear()
        _cache.clear()
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value={"summary": {"total_events": 0}}))
        stack.enter_context(patch.object(fa, "get_fleet_utilization_analytics", return_value={"total_idle_hours": 0.0}))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=RAW_UNITS))
        stack.enter_context(patch("app.main.get_calculated_data", return_value=CALC))
        stack.enter_context(patch("app.main.get_vehicle_live_data", return_value=[dict(v) for v in FLEET]))
        res = client.get("/api/dashboard/summary?hours=24", headers=headers)
        assert res.status_code == 200, res.text
        summary = res.json()["summary"]
        assert summary["total_trips"] is None, summary
        assert summary["total_vehicles"] == 3 and summary["total_distance_km"] == 1485.25, summary
    print("[PASS] API: /api/fleet/trips and /api/dashboard/summary return null trip metrics (JSON null).")


def main() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_group_report_path_trip_metrics_unavailable,
        test_unit_report_path_multiple_and_zero_trips,
        test_zero_trips_fleet_wide,
        test_utilization_trip_count_unavailable,
        test_group_analytics_trips,
        test_api_responses_null,
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
        _cache.clear()
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
