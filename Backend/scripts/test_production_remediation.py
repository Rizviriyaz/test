"""
Offline regression tests for the production-readiness remediation (audit after b45a915).

  A1  battery 15-18 V is WARNING with an unknown system type (never "Optimal"); a delayed/offline unit's battery is
      marked last-reported; a non-numeric satellite count or HDOP is unknown, never a crash; deep-analytics counters
      are unknown (None), not 0, when calc_last has none
  A2  one unit's failure never fails the fleet: idle-start history (duration status "unknown", contract int kept,
      retried), safety (partial, failed_vehicles, not cached), trends, utilization fallback; a run that fills the
      searched history is a lower bound; truncated history is flagged
  A3  an unhandled exception is a generic 500 that still carries CORS headers and no exception text
  A4  /api/wialon/audit is admin-only; /api/fleet/safety refuses > 168 h with a clear 400; the summary does not load
      safety for longer windows; per-user (per-process) rate limits return 429 with Retry-After
  A5  the utilization fallback is labelled raw_message_fallback, partial and uncached
  A6  idle by_group counts every group membership and by_vehicle lists every group; the fuel vehicle list has an
      exact vehicle_filter (KPI scope) separate from its substring search; alerts get stable, unique ids
  A7  engine-report mileage is parsed strictly; idle sub-rows take times from header-located time cells or t1/t2 and
      positions only from a verified location cell; the fuel bundle treats guessed columns as unknown; summary-row
      detection no longer drops "TotalEnergies"/"Total Al Quoz" rows; day/month order is decided per column

Wialon, Supabase and Groq are replaced by mocks; every outbound HTTP call is blocked by OfflineGuard.
Run from Backend/:
    python scripts/test_production_remediation.py
"""
import os
import sys
import time
import traceback
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.ai.tools as ai_tools  # noqa: E402
import app.fleet_analytics as fa  # noqa: E402
import app.fuel.importer as importer  # noqa: E402
import app.fuel.wialon_distance as wd  # noqa: E402
import app.main as main  # noqa: E402
import app.vehicles as vehicles  # noqa: E402
import app.wialon as wialon  # noqa: E402
import app.wialon_reports as wr  # noqa: E402
from app.utils.rate_limit import SlidingWindowLimiter, reset_rate_limits  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402
from wialon_report_fixtures import BASE_TS, TEMPLATE_GROUP_ENGINE, FakeReportEngine, default_tables  # noqa: E402

NOW = 1_800_000_000
AUTH = {"Authorization": "Bearer test-token"}
VIEWER = {"id": "00000000-0000-0000-0000-0000000000a1", "email": "v@example.com", "role": "viewer"}
ADMIN = {"id": "00000000-0000-0000-0000-0000000000a2", "email": "a@example.com", "role": "admin"}
ORIGIN = "http://localhost:5173"
IGNITION = {"1": {"n": "Ignition", "t": "engine operation"}}


def _client():
    return TestClient(main.app, raise_server_exceptions=False)


# ─── A1 health ────────────────────────────────────────────────────────────────

def _health(voltage, age=30, sats=10, hdop=None, last_known="MOVING"):
    vehicle = {"vehicle_id": 1, "vehicle_name": "A", "sensors": {"battery_voltage": voltage},
               "location": {"satellites": sats}, "last_known_status": last_known}
    raw = {"lmsg": {"t": int(time.time()) - age, "p": {"hdop": hdop} if hdop is not None else {}}}
    return fa.evaluate_vehicle_health(vehicle, raw_unit=raw, calc_data={})


def test_battery_classification_is_conservative():
    for volts in (15.0, 16.4, 17.0, 18.0):
        h = _health(volts)
        assert h["battery"]["status"] == "WARNING" and h["battery"]["system_type"] == "Unknown", (volts, h["battery"])
        assert "Unclassified" in h["battery"]["label"], h["battery"]
    assert _health(25.6)["battery"]["status"] == "HEALTHY" and _health(25.6)["battery"]["system_type"] == "24V Commercial"
    assert _health(19.0)["battery"]["status"] == "CRITICAL"
    assert _health(12.7)["battery"]["status"] == "HEALTHY" and _health(12.7)["battery"]["system_type"] == "12V Standard"
    assert _health(11.0)["battery"]["status"] == "CRITICAL"
    # 15-18 V was "Optimal" before: now WARNING (-20) and never counted healthy
    assert _health(17.0)["health_score"] <= 80
    print("[PASS] A1: 15-18 V is WARNING / system Unknown (was Optimal); 12V and 24V bands unchanged.")


def test_battery_stale_and_malformed_gps():
    assert _health(24.8, age=30)["battery"]["stale"] is False
    assert _health(24.8, age=13 * 3600)["battery"]["stale"] is True, "offline unit's battery is last-reported"
    assert _health(24.8, age=1200)["battery"]["stale"] is True, "delayed unit's battery is last-reported"
    h = _health(24.8, sats="abc", hdop="n/a")
    assert h["gps"]["satellites"] is None and h["gps"]["hdop"] is None and h["gps"]["status"] == "UNKNOWN", h["gps"]
    h = _health(24.8, sats="12", hdop="0.9")
    assert h["gps"]["status"] == "EXCELLENT", h["gps"]
    assert fa._rounded_counter([{"engine_hours": {"value": None}}], "engine_hours") is None
    assert fa._rounded_counter([{}], "mileage") is None and fa._rounded_counter([], "mileage") is None
    assert fa._rounded_counter([{"mileage": {"value": 1234.56}}], "mileage") == 1234.6
    print("[PASS] A1: delayed/offline battery flagged stale; bad satellites/HDOP unknown (no crash); null counters stay null.")


# ─── A2 per-vehicle isolation ─────────────────────────────────────────────────

def _idling_unit(vid):
    vehicle = {"id": vid, "nm": f"T{vid}", "sens": IGNITION, "lmsg": {"t": NOW - 30}}
    calc = {"i": vid, "pos": {"s": 0, "t": NOW - 30, "y": 25.2, "x": 55.3}, "sensors": {"1": {"value": 1}}}
    return vehicle, calc


def _msg(t, speed, ign):
    return {"tp": "ud", "t": t, "pos": {"s": speed}, "sensors": {"1": {"value": ign}}}


def _live(units, history):
    for v, _ in units:
        vehicles.idle_start_times.pop(v["id"], None)
        vehicles.idle_start_lower_bound.discard(v["id"])
    with patch("app.vehicles.now_timestamp", return_value=NOW), patch("app.vehicles.get_vehicle_history", history):
        return vehicles.get_vehicle_live_data("sid", [v for v, _ in units], [c for _, c in units], groups=[])


def test_idle_seed_failure_is_isolated_and_unknown():
    units = [_idling_unit(1), _idling_unit(2)]

    def history(sid, vid, from_time=None, to_time=None, hours=None):
        if vid == 2:
            raise WialonAPIError("history failed")
        return [_msg(NOW - 1000, 20, 1), _msg(NOW - 600, 0, 1), _msg(NOW - 30, 0, 1)]

    recs = _live(units, history)
    a, b = recs
    assert a["status"] == b["status"] == "IDLING"
    # Idling since NOW - 600, last message at NOW - 30: the timer is capped at the last message (570 s)
    assert a["idle_duration"] == 570 and a["idle_duration_status"] == "known", a
    assert b["idle_duration"] == 0 and isinstance(b["idle_duration"], int), "integer contract kept"
    assert b["idle_duration_status"] == "unknown" and b["idle"]["duration_status"] == "unknown" and b["idle_start"] is None, b
    assert 2 not in vehicles.idle_start_times, "a failed seed is not stored: retried on the next request"
    print("[PASS] A2: a failing idle-start history read leaves that unit 'unknown' (int 0 kept), other units unaffected.")


def test_idle_seed_lower_bound_when_run_fills_history():
    units = [_idling_unit(3)]

    def history(sid, vid, from_time=None, to_time=None, hours=None):
        # idling from the first message of every window searched (2 h, then 24 h)
        return [_msg(from_time + 60, 0, 1), _msg(NOW - 30, 0, 1)]

    rec = _live(units, history)[0]
    assert rec["idle_duration_status"] == "lower_bound" and rec["idle_duration"] > 0, rec
    # the lower-bound flag survives later polls of the same idle run
    with patch("app.vehicles.now_timestamp", return_value=NOW + 60), patch("app.vehicles.get_vehicle_history", history):
        again = vehicles.get_vehicle_live_data("sid", [units[0][0]], [units[0][1]], groups=[])[0]
    assert again["idle_duration_status"] == "lower_bound", again
    vehicles.idle_start_times.pop(3, None)
    vehicles.idle_start_lower_bound.discard(3)
    assert ai_tools._idle_fields(again)["idle_duration"].startswith("at least")
    assert ai_tools._idle_fields({"idle_duration": 0, "idle_duration_status": "unknown"}) == {
        "idle_duration": "unknown", "idle_seconds": None, "idle_duration_status": "unknown"}
    print("[PASS] A2: an idle run filling the searched history is a lower bound (kept across polls); AI tools say so.")


def test_history_truncation_flagged():
    page = [{"t": NOW - 1000 + i} for i in range(100)]
    with patch.dict(os.environ, {"WIALON_HISTORY_MAX_REQUESTS": "1", "WIALON_HISTORY_PAGE_LIMIT": "100"}), \
            patch("app.wialon._load_history_page", return_value=page):
        msgs = wialon.get_vehicle_history("sid", 1, from_time=NOW - 1000, to_time=NOW)
    assert wialon.history_truncated(msgs) and len(msgs) == 100 and isinstance(msgs, list)
    with patch("app.wialon._load_history_page", return_value=page[:5]):
        msgs = wialon.get_vehicle_history("sid", 1, from_time=NOW - 1000, to_time=NOW)
    assert not wialon.history_truncated(msgs) and not wialon.history_truncated([])
    print("[PASS] A2: a history load stopped at the request cap is flagged truncated (still a list for callers).")


FLEET = [
    {"vehicle_id": 1, "vehicle_name": "A", "group": "North", "groups": ["North"], "status": "MOVING",
     "connection_status": "STABLE", "is_offline": False, "speed": 40, "ignition": True, "latitude": 25.0, "longitude": 55.0},
    {"vehicle_id": 2, "vehicle_name": "B", "group": "North", "groups": ["North"], "status": "STOPPED",
     "connection_status": "STABLE", "is_offline": False, "speed": 0, "ignition": False, "latitude": 25.1, "longitude": 55.1},
]
UNITS = [{"id": 1, "nm": "A", "sens": {}}, {"id": 2, "nm": "B", "sens": {}}]


def _fa_patches(stack, history):
    fa._analytics_cache.clear()
    stack.enter_context(patch.object(fa, "login", return_value="sid"))
    stack.enter_context(patch.object(fa, "get_vehicle_groups", return_value=[]))
    stack.enter_context(patch.object(fa, "get_all_vehicles", return_value=UNITS))
    stack.enter_context(patch.object(fa, "get_calculated_data", return_value=[]))
    stack.enter_context(patch.object(fa, "get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in FLEET]))
    stack.enter_context(patch.object(fa, "get_vehicle_history", side_effect=history))


def _history_failing_b(sid, vid, hours=None, from_time=None, to_time=None):
    if vid == 2:
        raise WialonAPIError("boom")
    return [{"t": NOW - 100, "p": {"io_253": 2}, "pos": {"s": 40, "y": 25.0, "x": 55.0}}]


def test_safety_partial_not_cached_and_alerts_still_load():
    with ExitStack() as stack:
        _fa_patches(stack, _history_failing_b)
        res = fa.get_safety_analytics(hours=24, use_cache=False)
        assert res["partial"] is True and res["failed_vehicles"] == ["B"], res
        assert res["summary"]["total_events"] == 1 and res["events"][0]["event_type"] == "Harsh Braking"
        assert res["vehicles_without_safety_source"] == 0, "A reports io_253"
        assert fa.get_cached("safety_analytics_24", ttl_seconds=180) is None, "a partial result is not cached"
    print("[PASS] A2: one unit's history failure makes safety partial (listed, uncached) instead of failing it.")

    health = {"generated_at": NOW, "vehicles": []}
    with patch.object(fa, "get_fleet_health_analytics", return_value=health), \
            patch.object(fa, "get_safety_analytics", return_value=dict(res, generated_at=NOW)):
        fa._analytics_cache.clear()
        alerts = fa.get_fleet_alerts_analytics()
    assert alerts["safety_partial"] is True and alerts["safety_unavailable_vehicles"] == ["B"], alerts
    assert alerts["generated_at"] == NOW and alerts["total_alerts"] == 1
    entry = fa._analytics_cache.get(fa._ALERTS_CACHE_KEY)
    age = time.time() - entry["time"]
    assert age >= fa._ALERTS_TTL_SECONDS - fa._ALERTS_PARTIAL_TTL_SECONDS - 1, "partial alerts are served briefly only"
    print("[PASS] A2: alerts still load with safety_partial + the affected units; kept only briefly in the cache.")


def test_group_safety_unknown_when_member_failed():
    groups = [{"id": 5, "nm": "North", "u": [1, 2]}, {"id": 6, "nm": "Solo", "u": [1]}]
    safety = {"by_vehicle": [{"vehicle_id": 1, "vehicle_name": "A", "total": 3}], "failed_vehicles": ["B"],
              "truncated_vehicles": [], "partial": True}
    trips = {"summary": {}, "vehicles": [{"vehicle_id": 1, "gps_mileage_km": 1.0, "trip_count": 1},
                                         {"vehicle_id": 2, "gps_mileage_km": 2.0, "trip_count": 1}]}
    with ExitStack() as stack:
        _fa_patches(stack, _history_failing_b)
        stack.enter_context(patch.object(fa, "get_vehicle_groups", return_value=groups))
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value=safety))
        stack.enter_context(patch.object(fa, "get_trips_mileage_analytics", return_value=trips))
        res = fa.get_fleet_groups_analytics()
    by = {g["group_name"]: g for g in res["groups"]}
    # Known members summed, the unreadable member counted as unknown (never as 0 events)
    assert by["North"]["safety_events_24h"] == 3 and by["North"]["safety_unknown_vehicles"] == 1, by["North"]
    assert by["Solo"]["safety_events_24h"] == 3 and by["Solo"]["safety_unknown_vehicles"] == 0,         "membership totals: A counts in each of its groups"
    assert res["totals_basis"] == "group_membership"
    assert fa.get_cached("fleet_groups_analytics", ttl_seconds=180) is None
    print("[PASS] A6: group safety is a membership total over the readable members, with the unreadable ones counted "
          "(partial, not cached).")


def test_utilization_fallback_is_labelled_and_isolated():
    with ExitStack() as stack:
        _fa_patches(stack, lambda sid, vid, hours=None: (_ for _ in ()).throw(WialonAPIError("x")) if vid == 2 else [])
        stack.enter_context(patch.object(fa, "execute_wialon_group_engine_idle_report", side_effect=WialonAPIError("down")))
        res = fa.get_fleet_utilization_analytics(hours=1)
    assert res["source"] == "raw_message_fallback" and res["partial"] is True, res
    b = next(v for v in res["vehicles"] if v["vehicle_id"] == 2)
    assert b["driving_seconds"] is None and res["unknown_vehicles"] == ["B"], res
    assert fa.get_cached("fleet_utilization_1", ttl_seconds=180) is None, "fallback never cached"
    print("[PASS] A5: utilization fallback labelled raw_message_fallback, partial, uncached; a failing unit is unknown.")


def test_trends_isolate_failed_units():
    main._cache.clear()

    def history(sid, vid, hours=None):
        if vid == 2:
            raise WialonAPIError("x")
        return []

    with patch.object(main, "login", return_value="sid"), patch.object(main, "get_all_vehicles", return_value=UNITS), \
            patch.object(main, "get_vehicle_history", side_effect=history):
        res = main.get_dashboard_trends(hours=2)
    assert res["partial"] is True and res["failed_vehicles"] == ["B"] and len(res["points"]) == 2, res
    assert main.cached("trends-2", ttl_seconds=600) is None
    print("[PASS] A2: dashboard trends leave out (and list) a failing unit; partial result not cached.")


# ─── A3 errors / A4 authorization, bounds, rate limits ────────────────────────

def test_unhandled_error_is_generic_500_with_cors():
    main._cache.clear()
    with patch("app.main.authenticate_access_token", return_value=VIEWER), \
            patch.object(main, "login", return_value="sid"), \
            patch.object(main, "get_all_vehicles", side_effect=ValueError("secret-internal-detail token=abc")):
        res = _client().get("/api/vehicles/1", headers={**AUTH, "Origin": ORIGIN})
    assert res.status_code == 500 and res.json() == {"detail": "Internal server error"}, res.text
    assert "secret" not in res.text
    assert res.headers.get("access-control-allow-origin") == ORIGIN, dict(res.headers)
    print("[PASS] A3: an unhandled exception is a generic 500 with CORS headers (readable by the browser), no detail.")


def test_audit_is_admin_only():
    with patch("app.main.authenticate_access_token", return_value=VIEWER), \
            patch.object(main, "login", side_effect=AssertionError("Wialon must not be reached")):
        res = _client().get("/api/wialon/audit", headers=AUTH)
    assert res.status_code == 403, res.text
    reset_rate_limits()
    with patch("app.main.authenticate_access_token", return_value=ADMIN), \
            patch.object(main, "login", return_value="sid"), \
            patch.object(main, "audit_wialon_fleet", return_value={"success": True}):
        res = _client().get("/api/wialon/audit", headers=AUTH)
    assert res.status_code == 200 and res.json() == {"success": True}, res.text
    print("[PASS] A4: /api/wialon/audit -> 403 for viewer, 200 for admin.")


def test_safety_window_and_summary_bound():
    reset_rate_limits()
    with patch("app.main.authenticate_access_token", return_value=VIEWER), \
            patch.object(main, "get_safety_analytics", return_value={"summary": {}}) as safety:
        for hours, code in ((168, 200), (24, 200), (169, 400), (744, 400)):
            res = _client().get(f"/api/fleet/safety?hours={hours}", headers=AUTH)
            assert res.status_code == code, (hours, res.status_code, res.text)
        assert "at most 168 hours" in res.text
        assert [c.kwargs.get("hours") for c in safety.call_args_list] == [168, 24]

    main._cache.clear()
    safety = MagicMock(side_effect=AssertionError("safety must not load weeks of raw history"))
    with patch.object(main, "login", return_value="sid"), patch.object(main, "get_all_vehicles", return_value=UNITS), \
            patch.object(main, "get_calculated_data", return_value=[]), \
            patch.object(main, "get_vehicle_live_data", return_value=[dict(v) for v in FLEET]), \
            patch.object(fa, "get_safety_analytics", safety), \
            patch.object(fa, "get_trips_mileage_analytics", return_value={"summary": {"total_distance_km": 5.0}}), \
            patch.object(fa, "get_fleet_utilization_analytics", return_value={"total_idle_hours": 1.0}):
        res = main.get_dashboard_summary(hours=200)
    s = res["summary"]
    assert s["total_safety_events"] is None and s["safety_window_supported"] is False, s
    print("[PASS] A4: safety > 168 h -> clear 400; the 200 h summary reports safety as unknown without loading it.")


def test_rate_limits_return_429_per_user():
    limiter = SlidingWindowLimiter(clock=lambda: clock[0])
    clock = [100.0]
    assert [limiter.check("u", 2, 60) for _ in range(3)][:2] == [None, None]
    assert limiter.check("u", 2, 60) is not None and limiter.check("other", 2, 60) is None
    clock[0] += 61
    assert limiter.check("u", 2, 60) is None, "the window slides"
    assert limiter.check("u", 0, 60) is None, "0 disables"

    reset_rate_limits()
    users = {"tok-a": dict(VIEWER, id="user-a"), "tok-b": dict(VIEWER, id="user-b")}
    body = {"messages": [{"role": "user", "content": "hi"}]}
    with patch.dict(os.environ, {"AI_CHAT_RATE_LIMIT_PER_MINUTE": "2"}), \
            patch("app.main.authenticate_access_token", side_effect=lambda t: users[t]), \
            patch("app.ai.assistant.run_assistant", return_value="ok") as run:
        client = _client()
        codes = [client.post("/api/ai/chat", json=body, headers={"Authorization": "Bearer tok-a"}).status_code for _ in range(3)]
        limited = client.post("/api/ai/chat", json=body, headers={"Authorization": "Bearer tok-a"})
        other = client.post("/api/ai/chat", json=body, headers={"Authorization": "Bearer tok-b"})
    assert codes == [200, 200, 429], codes
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1 and "Too many requests" in limited.text
    assert other.status_code == 200, "limits are per user"
    assert run.call_count == 3, "a limited request never reaches the assistant"
    reset_rate_limits()
    print("[PASS] A4: per-user sliding-window limit -> 429 + Retry-After; other users unaffected (per-process).")


# ─── A6 consistency ───────────────────────────────────────────────────────────

def test_idle_by_group_counts_every_membership():
    main._cache.clear()
    groups = [{"id": 1, "nm": "North", "u": [10, 11]}, {"id": 2, "nm": "Contract", "u": [10]}]
    live = [
        {"vehicle_id": 10, "vehicle_name": "V10", "status": "IDLING", "idle_duration": 0, "idle_duration_status": "unknown"},
        {"vehicle_id": 11, "vehicle_name": "V11", "status": "STOPPED", "idle_duration": 0, "idle_duration_status": "known"},
    ]

    def row(name, idle):
        return {"vehicle_name": name, "total_idle_seconds": idle, "engine_hours_seconds": idle, "total_time_seconds": 86400,
                "in_motion_seconds": 0, "mileage_km": 0.0, "longest_idle_seconds": 0, "incomplete_fields": []}

    rep = {"vehicles": [row("V10", 600), row("V11", 300)], "events": [], "events_incomplete": [], "column_warnings": [],
           "unlocated_fields": []}
    with patch.object(main, "login", return_value="sid"), patch.object(main, "get_vehicle_groups", return_value=groups), \
            patch.object(main, "get_all_vehicles", return_value=[]), patch.object(main, "get_calculated_data", return_value=[]), \
            patch.object(main, "get_vehicle_live_data", return_value=live), \
            patch.object(main, "resolve_all_units_group_id", return_value=99), \
            patch.object(main, "execute_wialon_group_engine_idle_report", return_value=rep):
        res = main.get_fleet_idle_analytics(hours=24, include_events=False)
    by_group = {g["group_name"]: g for g in res["by_group"]}
    assert by_group["North"]["total_idle_seconds"] == 900 and by_group["Contract"]["total_idle_seconds"] == 600, by_group
    assert by_group["North"]["currently_idling"] == 1 and by_group["Contract"]["currently_idling"] == 1
    assert res["by_group_basis"] == "group_membership" and res["kpis"]["total_idle_seconds"] == 900
    v10 = next(v for v in res["by_vehicle"] if v["vehicle_id"] == 10)
    assert v10["group_names"] == ["North", "Contract"] and v10["group_ids"] == [1, 2] and v10["group_name"] == "North"
    assert v10["idle_duration_status"] == "unknown"
    print("[PASS] A6: idle by_group = membership totals (fleet total unchanged); by_vehicle lists every group.")


def test_fuel_vehicle_list_exact_filter():
    seen = []

    def fetch(filters, token=None):
        seen.append(dict(filters))
        return []

    with patch("app.main.authenticate_access_token", return_value=VIEWER), \
            patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", side_effect=fetch):
        client = _client()
        r1 = client.get("/api/fuel/analytics/efficiency/vehicles?vehicle_filter=7-AUH-55943", headers=AUTH)
        r2 = client.get("/api/fuel/analytics/efficiency/vehicles?vehicle_identifier=559", headers=AUTH)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    # vehicle_filter goes through the KPI scope's exact filter (DB pre-filter on the plate digits, exact in memory)
    assert seen[0]["vehicle_identifier"] == "55943", seen[0]
    # the search box alone is not a scope filter (substring over the aggregated records, as before)
    assert seen[1]["vehicle_identifier"] is None, seen[1]
    print("[PASS] A6: efficiency/vehicles vehicle_filter is the exact KPI-scope filter; vehicle_identifier stays search.")


def test_alert_ids_stable_and_unique():
    health = {"generated_at": NOW, "vehicles": []}
    events = [
        {"id": "1-100-Hars", "vehicle_id": 1, "vehicle_name": "A", "group": "G", "event_type": "Harsh Braking",
         "timestamp": 100, "speed_kmh": 40, "g_force": None, "location": {}},
        {"id": "1-200-Hars", "vehicle_id": 1, "vehicle_name": "A", "group": "G", "event_type": "Harsh Braking",
         "timestamp": 200, "speed_kmh": 40, "g_force": None, "location": {}},
        {"vehicle_id": 2, "vehicle_name": "B", "group": "G", "event_type": "Harsh Braking", "timestamp": 300,
         "speed_kmh": 40, "g_force": None, "location": {}},
        {"vehicle_id": 2, "vehicle_name": "B", "group": "G", "event_type": "Harsh Braking", "timestamp": 400,
         "speed_kmh": 40, "g_force": None, "location": {}},
    ]
    runs = []
    for _ in range(2):
        fa._analytics_cache.clear()
        with patch.object(fa, "get_fleet_health_analytics", return_value=health), \
                patch.object(fa, "get_safety_analytics", return_value={"events": events, "generated_at": NOW}):
            runs.append(sorted(a["id"] for a in fa.get_fleet_alerts_analytics()["alerts"]))
    ids = runs[0]
    assert len(ids) == len(set(ids)) == 4, ids
    assert ids == sorted(["HARSH_DRIVING-1-1-100-Hars", "HARSH_DRIVING-1-1-200-Hars", "HARSH_DRIVING-2", "HARSH_DRIVING-2-2"]), ids
    assert runs[0] == runs[1], "ids are stable across rebuilds"
    print("[PASS] A8: alert ids stable across rebuilds and unique for several same-type alerts on one vehicle.")


def test_unclassified_and_stale_battery_alerts():
    v = {"vehicle_id": 7, "vehicle_name": "T7", "group": "G",
         "battery": {"status": "WARNING", "voltage": 16.5, "system_type": "Unknown", "stale": True},
         "connectivity": {"status": "STABLE", "seconds_since_update": 30, "last_message_ts": NOW},
         "gps": {"status": "GOOD", "satellites": 9, "latitude": 1, "longitude": 2}}
    fa._analytics_cache.clear()
    with patch.object(fa, "get_fleet_health_analytics", return_value={"generated_at": NOW, "vehicles": [v]}), \
            patch.object(fa, "get_safety_analytics", return_value={"events": [], "generated_at": NOW}):
        alerts = fa.get_fleet_alerts_analytics()["alerts"]
    assert [a["alert_type"] for a in alerts] == ["BATTERY_UNCLASSIFIED"], alerts
    assert "(last reported)" in alerts[0]["title"] and alerts[0]["id"] == "BATTERY_UNCLASSIFIED-7"
    print("[PASS] A8: an unclassified reading gets its own alert type; last-reported values are worded as such.")


# ─── A7 strict parsing ────────────────────────────────────────────────────────

def _group_engine(tables):
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)):
        return wr.execute_wialon_group_engine_idle_report("sid", BASE_TS, BASE_TS + 86400, group_id=1, include_events=True)


def test_engine_mileage_parsed_strictly():
    tables = default_tables()
    rows = tables[TEMPLATE_GROUP_ENGINE]["rows"]
    rows[0]["c"][9] = "1,234 km"      # 1234 or 1.234? ambiguous -> unknown (lenient parsing read 1.234)
    rows[1]["c"][9] = "1,234.5 km"    # one reading -> 1234.5
    g = _group_engine(tables)
    a, b = g["vehicles"][0], g["vehicles"][1]
    assert a["mileage_km"] is None and "mileage_km" in a["incomplete_fields"], a
    assert b["mileage_km"] == 1234.5, b
    print("[PASS] A7: engine-report mileage uses the strict Trips parser (ambiguous '1,234' is unknown, not 1.234).")


def test_idle_subrows_never_guess_columns():
    g = _group_engine(default_tables())
    assert g["events"] and all(e["location_verified"] for e in g["events"]), g["events"]
    assert g["event_location_unverified"] is False and g["events"][0]["address"].startswith("Hotspot")

    with patch("app.wialon_reports.wialon_request", FakeReportEngine()):
        u = wr.execute_wialon_unit_engine_report("sid", 101, BASE_TS, BASE_TS + 3 * 86400)
    subs = default_tables()[1]["subrows"]
    expected = [(s["t1"], s["t2"]) for row in sorted(subs) for s in subs[row]]
    assert [(e["start_time"], e["end_time"]) for e in u["idle_events"]] == expected, "times from time cells / t1-t2"
    # The fixture's unit sub-rows hold a time cell under the 'Initial location' header: no position is guessed
    assert u["event_location_unverified"] is True and all(e["address"] == "Unknown Location" for e in u["idle_events"])
    assert u["total_idle_seconds"] == 5400 and len(u["idle_events"]) == 3, "durations unchanged"

    no_header = None
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(include_header=False)):
        no_header = wr.execute_wialon_group_engine_idle_report("sid", BASE_TS, BASE_TS + 86400, group_id=1, include_events=True)
    assert all(not e["location_verified"] for e in no_header["events"])
    assert [e["start_time"] for e in no_header["events"]] == [e["start_time"] for e in g["events"]]
    print("[PASS] A7: idle sub-rows use header-located time cells or t1/t2; positions only from verified location cells.")


def test_fuel_bundle_treats_guessed_columns_as_unknown():
    trips_rows = [{"vehicle_name": "A", "mileage_km": 10.0, "unlocated_fields": ["mileage_km"]},
                  {"vehicle_name": "B", "mileage_km": 20.0}]
    engine = {"vehicles": [
        {"vehicle_name": "A", "in_motion_seconds": 100, "total_idle_seconds": 50, "engine_hours_seconds": 200,
         "total_time_seconds": 300, "mileage_km": 9.0, "incomplete_fields": []},
        {"vehicle_name": "B", "in_motion_seconds": 400, "total_idle_seconds": 60, "engine_hours_seconds": 500,
         "total_time_seconds": 600, "mileage_km": 20.0, "incomplete_fields": []},
        {"vehicle_name": "C", "in_motion_seconds": 1, "total_idle_seconds": 1, "engine_hours_seconds": 1,
         "total_time_seconds": 1, "mileage_km": 3.0, "incomplete_fields": []},
    ], "unlocated_fields": ["in_motion_seconds", "mileage_km"], "column_warnings": ["Column 'In motion' not found"]}

    def run(fn, sid, *args, **kwargs):
        return (trips_rows if fn is wr.execute_wialon_group_trips_report else engine), sid

    with patch.object(wd, "login", return_value="sid"), patch.object(wd, "_run_report_with_retry", side_effect=run):
        b = wd._build_fleet_telematics_bundle("2026-10-01", "2026-10-02", None)
    a, bb, c = b["by_name"]["A"], b["by_name"]["B"], b["by_name"]["C"]
    assert a["mileage_km"] is None and a["driving_seconds"] is None and a["idle_seconds"] == 50, a
    assert bb["mileage_km"] == 20.0 and bb["driving_seconds"] is None, bb
    assert c["mileage_km"] is None, "engine-only mileage from a guessed column is unknown"
    assert b["status"] == "partial"
    print("[PASS] A7: the fuel telematics bundle never uses values read from guessed column positions.")


def test_import_summary_rows_and_date_order():
    keep = [["TotalEnergies Al Quoz", "2026-10-01", "7-AUH-1", 40], ["Total Al Quoz", "2026-10-01", "7-AUH-1", 40],
            ["Monthly total diesel", "2026-10-01", "7-AUH-1", 40]]
    drop = [["Total", None, None, 400], ["Grand Total:", None, None, 400], ["Sub-total AED", 1, 2, 3], ["SUMMARY"]]
    for row in keep:
        assert importer.is_summary_or_blank_row(row, 4) == (False, ""), row
    for row in drop:
        assert importer.is_summary_or_blank_row(row + [None] * (4 - len(row)), 4)[0] is True, row

    assert importer.infer_date_order(["03/05/2026", "25/05/2026"]) == "dmy"
    assert importer.infer_date_order(["03/05/2026", "05/25/2026"]) == "mdy"
    assert importer.infer_date_order(["13/05/2026", "05/25/2026"]) == "conflict"
    assert importer.infer_date_order(["03/05/2026", None, 45000]) is None
    assert importer.parse_date_value("03/05/2026", "mdy") == ("2026-03-05", None)
    assert importer.parse_date_value("03/05/2026", "dmy") == ("2026-05-03", None)
    dt, warn = importer.parse_date_value("03/05/2026", None)
    assert dt == "2026-05-03" and "Ambiguous" in warn, "no evidence: UAE default with a warning, as before"
    dt, warn = importer.parse_date_value("03/05/2026", "conflict")
    assert dt is None and "both DD/MM and MM/DD" in warn
    assert importer.parse_date_value("25/05/2026", "conflict") == ("2026-05-25", None), "unambiguous rows still parse"

    cols = {"transaction_date": 0, "vehicle_identifier": 1, "litres": 2}
    rows = [["05/25/2026", "7-AUH-1", 40], ["03/05/2026", "7-AUH-1", 41]]
    order = importer.statement_date_order(rows, cols)
    out = [importer.classify_statement_row(r, cols, i, 3, order) for i, r in enumerate(rows, start=2)]
    assert order == "mdy" and [o["candidate"]["fields"]["transaction_date"] for o in out] == ["2026-05-25", "2026-03-05"]
    rows.append(["13/05/2026", "7-AUH-1", 42])
    order = importer.statement_date_order(rows, cols)
    out = importer.classify_statement_row(rows[1], cols, 3, 3, order)
    assert order == "conflict" and out["kind"] == "failed" and "both DD/MM and MM/DD" in out["reason"], out
    print("[PASS] A7: summary labels only (TotalEnergies/Total Al Quoz kept); day/month order decided per column.")


TESTS = [
    test_battery_classification_is_conservative,
    test_battery_stale_and_malformed_gps,
    test_idle_seed_failure_is_isolated_and_unknown,
    test_idle_seed_lower_bound_when_run_fills_history,
    test_history_truncation_flagged,
    test_safety_partial_not_cached_and_alerts_still_load,
    test_group_safety_unknown_when_member_failed,
    test_utilization_fallback_is_labelled_and_isolated,
    test_trends_isolate_failed_units,
    test_unhandled_error_is_generic_500_with_cors,
    test_audit_is_admin_only,
    test_safety_window_and_summary_bound,
    test_rate_limits_return_429_per_user,
    test_idle_by_group_counts_every_membership,
    test_fuel_vehicle_list_exact_filter,
    test_alert_ids_stable_and_unique,
    test_unclassified_and_stale_battery_alerts,
    test_engine_mileage_parsed_strictly,
    test_idle_subrows_never_guess_columns,
    test_fuel_bundle_treats_guessed_columns_as_unknown,
    test_import_summary_rows_and_date_order,
]


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failures = 0
    try:
        for test in TESTS:
            try:
                test()
            except Exception:
                failures += 1
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
        guard.assert_no_live_calls()
        print("[PASS] Network guard: zero live network calls attempted.")
    finally:
        guard.stop()
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} remediation tests passed.")
    sys.exit(1 if failures else 0)
