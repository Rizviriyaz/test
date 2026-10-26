"""
Offline tests for /api/fleet/alerts, which now runs the health and 24h safety analytics concurrently.

  - the alert list (ids, order, severities, counts, rollups) is built exactly as before from health + safety
  - health and safety run at the same time (a sequential call would never pass the shared barrier)
  - the request trace id reaches both workers; safety is asked for a 24h window
  - an error in either part propagates and nothing is cached; a cached result skips both parts

Health and safety are mocked in app.fleet_analytics. Every outbound HTTP call is blocked by OfflineGuard.
Run from Backend/:  python scripts/test_fleet_alerts_concurrency.py
"""
import os
import sys
import threading
import traceback
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.fleet_analytics as fa  # noqa: E402
from app.performance_diagnostics import request_trace_id  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402


def _health_vehicle(vid, name, group, battery="OK", volt=24.1, conn="ONLINE", secs=30, gps="GOOD", sats=12, ts=1000):
    return {
        "vehicle_id": vid, "vehicle_name": name, "group": group,
        "battery": {"status": battery, "voltage": volt},
        "connectivity": {"status": conn, "seconds_since_update": secs, "last_message_ts": ts},
        "gps": {"status": gps, "satellites": sats, "latitude": 25.0, "longitude": 55.0},
    }


HEALTH = {"vehicles": [
    _health_vehicle(1, "UNIT-A", "Logistics", battery="CRITICAL", volt=10.2, ts=1100),
    _health_vehicle(2, "UNIT-B", "Logistics", battery="WARNING", volt=11.8, conn="DELAYED", secs=1500, ts=1200),
    _health_vehicle(3, "UNIT-C", "Contracts", conn="OFFLINE", secs=7300, gps="WEAK", sats=3, ts=900),
]}
SAFETY = {"events": [
    {"vehicle_id": 3, "vehicle_name": "UNIT-C", "group": "Contracts", "event_type": "Harsh Braking",
     "timestamp": 1300, "speed_kmh": 62, "g_force": 0.8, "location": {"latitude": 25.1, "longitude": 55.1}},
    {"vehicle_id": 1, "vehicle_name": "UNIT-A", "group": "Logistics", "event_type": "Harsh Acceleration",
     "timestamp": 1250, "speed_kmh": 30, "g_force": None, "location": {"latitude": 25.2, "longitude": 55.2}},
]}

# (id, type, severity) in final order: CRITICAL then WARNING, newest first. Ids are stable across rebuilds:
# "<alert_type>-<vehicle_id>" (plus the harsh event's id when it has one), no longer the build-order "alt-N"
EXPECTED = [
    ("HARSH_DRIVING-3", "HARSH_DRIVING", "CRITICAL"),
    ("BATTERY_CRITICAL-1", "BATTERY_CRITICAL", "CRITICAL"),
    ("DEVICE_OFFLINE-3", "DEVICE_OFFLINE", "CRITICAL"),
    ("HARSH_DRIVING-1", "HARSH_DRIVING", "WARNING"),
    ("BATTERY_LOW-2", "BATTERY_LOW", "WARNING"),
    ("TELEMETRY_DELAYED-2", "TELEMETRY_DELAYED", "WARNING"),
    ("GPS_WEAK-3", "GPS_WEAK", "WARNING"),
]


def _reset():
    fa._analytics_cache.clear()


def test_alert_list_unchanged():
    _reset()
    with patch.object(fa, "get_fleet_health_analytics", return_value=HEALTH), \
         patch.object(fa, "get_safety_analytics", return_value=SAFETY):
        res = fa.get_fleet_alerts_analytics()
    got = [(a["id"], a["alert_type"], a["severity"]) for a in res["alerts"]]
    assert got == EXPECTED, got
    assert (res["total_alerts"], res["critical_count"], res["warning_count"], res["info_count"]) == (7, 3, 4, 0)
    assert res["by_vehicle"] == {"1": 2, "2": 2, "3": 3}, res["by_vehicle"]
    assert res["by_group"] == {"Logistics": 4, "Contracts": 3}, res["by_group"]
    offline = next(a for a in res["alerts"] if a["alert_type"] == "DEVICE_OFFLINE")
    assert offline["value"] == "2h offline", offline
    print("[PASS] Alert list, ids, order and counts are built exactly as before")


def test_parts_run_concurrently_with_trace_id():
    _reset()
    barrier = threading.Barrier(2, timeout=5)
    seen = {}

    def health():
        seen["health"] = request_trace_id.get()
        barrier.wait()  # raises BrokenBarrierError if safety is not running at the same time
        return HEALTH

    def safety(hours):
        seen["safety"] = (request_trace_id.get(), hours)
        barrier.wait()
        return SAFETY

    token = request_trace_id.set("trace-abc")
    try:
        with patch.object(fa, "get_fleet_health_analytics", side_effect=health), \
             patch.object(fa, "get_safety_analytics", side_effect=safety):
            res = fa.get_fleet_alerts_analytics()
    finally:
        request_trace_id.reset(token)
    assert res["total_alerts"] == 7
    assert seen == {"health": "trace-abc", "safety": ("trace-abc", 24)}, seen
    print("[PASS] Health and safety overlap; trace id propagates; safety window is 24h")


def test_errors_propagate_and_are_not_cached():
    for failing in ("get_fleet_health_analytics", "get_safety_analytics"):
        _reset()
        ok = {"get_fleet_health_analytics": HEALTH, "get_safety_analytics": SAFETY}
        with patch.object(fa, "get_fleet_health_analytics",
                          side_effect=WialonAPIError("boom") if failing == "get_fleet_health_analytics" else None,
                          return_value=ok["get_fleet_health_analytics"]), \
             patch.object(fa, "get_safety_analytics",
                          side_effect=WialonAPIError("boom") if failing == "get_safety_analytics" else None,
                          return_value=ok["get_safety_analytics"]):
            try:
                fa.get_fleet_alerts_analytics()
            except WialonAPIError:
                pass
            else:
                raise AssertionError(f"{failing} error was swallowed")
        assert fa.get_cached("fleet_alerts_analytics", ttl_seconds=180) is None, failing
    print("[PASS] An error in health or safety propagates and nothing is cached")


def test_cache_hit_skips_parts():
    _reset()
    with patch.object(fa, "get_fleet_health_analytics", return_value=HEALTH) as h, \
         patch.object(fa, "get_safety_analytics", return_value=SAFETY) as s:
        first = fa.get_fleet_alerts_analytics()
        second = fa.get_fleet_alerts_analytics()
    assert first is second
    assert (h.call_count, s.call_count) == (1, 1), (h.call_count, s.call_count)
    print("[PASS] A cached result skips both health and safety")


def main() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_alert_list_unchanged,
        test_parts_run_concurrently_with_trace_id,
        test_errors_propagate_and_are_not_cached,
        test_cache_hit_skips_parts,
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
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
