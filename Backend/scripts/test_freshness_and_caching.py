"""
Offline tests for data freshness (Phase 2) and cache behaviour (Phase 3):

  - freshness block: fresh / cached / stale / unavailable from the backend read time (never invented); a closed report
    window has no target age; built at response time without mutating a cached result
  - /api/vehicles and /api/vehicles/{id}: one shared live fleet (single read), freshness with the newest Wialon message
    time as source_updated_at; a stale fleet reports refresh_status=stale with a client-safe refresh_error
  - live fleet reads share the Wialon groups for 60 s (2 Wialon calls per read instead of 3)
  - report routes: mileage freshness from its snapshot time, target None for a closed window
  - Fuel: freshness from the telematics bundle's own fetched_at_utc; unavailable when Wialon failed
  - AI tools: every result carries data_freshness (read time from the result, never invented; reporting period)

Wialon and Supabase are mocked; OfflineGuard blocks every outbound HTTP call.
Run from Backend/:  python scripts/test_freshness_and_caching.py
"""
import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import AUTH, BUNDLE, FAKE_USER, QM, SUMMARY_ROWS, UNITS, Api, run  # noqa: E402

import app.ai.tools as ai_tools  # noqa: E402
import app.main as main  # noqa: E402
from app.utils import freshness as fr  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

FLEET = [{"vehicle_id": 1, "vehicle_name": "V-1", "status": "MOVING", "last_message_timestamp": 1793380000},
         {"vehicle_id": 2, "vehicle_name": "V-2", "status": "STOPPED", "last_message_timestamp": 1793370000},
         {"vehicle_id": 3, "vehicle_name": "V-3", "status": "OFFLINE", "last_message_timestamp": None}]


def test_freshness_block_states():
    now = 1_800_000_000
    assert fr.freshness_block(now - 1, target_age_seconds=30, now=now)["refresh_status"] == "fresh"
    cached = fr.freshness_block(now - 20, target_age_seconds=30, now=now)
    assert (cached["refresh_status"], cached["data_age_seconds"], cached["fetched_at"]) == ("cached", 20, now - 20)
    stale = fr.freshness_block(now - 300, target_age_seconds=30, stale=True, now=now)
    assert stale["refresh_status"] == "stale" and stale["is_stale"] and stale["refresh_error"] == fr.STALE_ERROR
    none = fr.freshness_block(None, target_age_seconds=30, now=now)
    assert (none["refresh_status"], none["data_age_seconds"], none["fetched_at"]) == ("unavailable", None, None)
    # Source times are only what the caller passes; invalid values are dropped, never invented
    assert fr.freshness_block(now, target_age_seconds=30, now=now)["source_updated_at"] is None
    assert fr.freshness_block(now, target_age_seconds=30, source_updated_at="x", now=now)["source_updated_at"] is None
    # A closed window (ended before today's UAE midnight) has no target age; an open one keeps it
    assert fr.window_target(1_000_000_000, 120) is None and fr.window_target(int(time.time()), 120) == 120
    # Built from a copy: the cached result is never changed
    result = {"retrieved_at": now - 5, "stale": False, "x": 1}
    out = fr.with_freshness(result, target_age_seconds=30, now=now)
    assert "freshness" not in result and out["freshness"]["data_age_seconds"] == 5 and out["x"] == 1
    print("[PASS] Freshness block: fresh/cached/stale/unavailable from the read time; no invented source time; closed "
          "windows have no target; cached results never mutated.")


def _live(stack_fleet=FLEET):
    main._cache.clear()
    main._failure_backoff.clear()
    groups = MagicMock(return_value=[{"id": 7, "nm": "Logistics", "u": [1, 2]}])
    live = MagicMock(side_effect=lambda *a, **k: [dict(v) for v in stack_fleet])
    search = MagicMock(return_value=[])
    patches = [patch("app.main.authenticate_access_token", return_value=FAKE_USER),
               patch("app.main.login", return_value="sid"), patch("app.main.get_all_vehicles", search),
               patch("app.main.get_calculated_data", return_value=[]), patch("app.main.get_vehicle_live_data", live),
               patch("app.main.get_vehicle_groups", groups)]
    for p in patches:
        p.start()
    return patches, groups, live, search


def _stop(patches):
    for p in reversed(patches):
        p.stop()
    main._cache.clear()
    main._failure_backoff.clear()


def test_live_fleet_freshness_shared_read_and_groups_sharing():
    patches, groups, live, search = _live()
    try:
        client = TestClient(main.app)
        body = client.get("/api/vehicles", headers=AUTH).json()
        f = body["freshness"]
        assert f["fetched_at"] == body["retrieved_at"] and f["refresh_status"] == "fresh" and not f["is_stale"], f
        assert f["source_updated_at"] == 1793380000, "the newest Wialon message of the fleet"
        assert f["target_age_seconds"] == fr.TARGET_LIVE_FLEET_S
        assert "freshness" not in main.cached("fleet-vehicles-live", ttl_seconds=60), "the cached fleet is not mutated"
        # The details route answers from the same shared fleet: no second Wialon read
        detail = client.get("/api/vehicles/2", headers=AUTH).json()
        assert detail["found"] and detail["vehicle"]["vehicle_name"] == "V-2" and search.call_count == 1
        assert detail["freshness"]["source_updated_at"] == 1793370000 and detail["stale"] is False
        assert client.get("/api/vehicles/99", headers=AUTH).json()["found"] is False
        # A later read (the 8 s live cache expired) reuses the groups (60 s), so groups are read once
        for entry in main._cache._data.values():
            if isinstance(entry[1], dict) and entry[1].get("data", {}) and isinstance(entry[1]["data"], dict) \
                    and "vehicles" in entry[1]["data"]:
                entry[1]["time"] -= 20
        client.get("/api/vehicles", headers=AUTH)
        assert search.call_count == 2 and groups.call_count == 1, (search.call_count, groups.call_count)
        # Wialon failing: the stale fleet says so in its freshness block
        for entry in main._cache._data.values():
            if isinstance(entry[1], dict) and isinstance(entry[1].get("data"), dict) and "vehicles" in entry[1]["data"]:
                entry[1]["time"] -= 20
        live.side_effect = WialonAPIError("down")
        stale = client.get("/api/vehicles", headers=AUTH).json()
        assert stale["freshness"]["refresh_status"] == "stale" and stale["freshness"]["refresh_error"] == fr.STALE_ERROR
        assert stale["freshness"]["fetched_at"] == stale["retrieved_at"] < int(time.time()) + 1
        assert client.get("/api/vehicles/1", headers=AUTH).json()["freshness"]["is_stale"] is True
    finally:
        _stop(patches)
    print("[PASS] /api/vehicles and /api/vehicles/{id}: one shared fleet read, freshness with the newest message time, "
          "groups read once per 60 s, stale fleet flagged with a client-safe error.")


def test_mileage_freshness_from_snapshot_and_closed_window():
    snapshot_rows = [{"vehicle_name": "V-1", "mileage_km": 12.5}]
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.main.login", return_value="sid"), patch("app.main.get_vehicle_groups", return_value=[]), \
         patch("app.main.get_all_vehicles", return_value=[{"id": 1, "nm": "V-1"}]), \
         patch("app.main.get_calculated_data", return_value=[]), \
         patch("app.main.resolve_all_units_group_id", return_value=1), \
         patch("app.main.execute_wialon_group_trips_report", return_value=snapshot_rows):
        main._cache.clear()
        import app.fleet_analytics as fa
        fa._analytics_cache.clear()
        client = TestClient(main.app)
        today = client.get("/api/dashboard/mileage?timeframe=today", headers=AUTH).json()
        closed = client.get("/api/dashboard/mileage?from_date=2026-08-01&to_date=2026-08-31", headers=AUTH).json()
        main._cache.clear()
        fa._analytics_cache.clear()
    assert today["freshness"]["fetched_at"] == today["retrieved_at"] and today["freshness"]["target_age_seconds"] == fr.TARGET_TODAY_REPORT_S
    assert closed["freshness"]["target_age_seconds"] is None, "a closed window does not go out of date"
    assert today["total_km"] == 12.5 and closed["total_km"] == 12.5, "values unchanged"
    print("[PASS] Mileage: freshness from the Template 5 snapshot's read time; today's target 120 s, closed window none; "
          "values unchanged.")


def test_fuel_freshness_from_bundle_read_time():
    bundle = dict(BUNDLE, fetched_at_utc=int(time.time()) - 42)
    with Api(rows=SUMMARY_ROWS, units=UNITS) as api:
        api.bundle.return_value = bundle
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}")
        ranked = api.get(f"/api/fuel/analytics/efficiency/vehicles?{QM}&matched_only=true")
    f = eff["freshness"]
    assert f["fetched_at"] == bundle["fetched_at_utc"] and 42 <= f["data_age_seconds"] <= 45, f
    assert f["target_age_seconds"] is None, "August 2026 is a closed month"
    assert ranked["freshness"]["fetched_at"] == bundle["fetched_at_utc"]
    with Api(rows=SUMMARY_ROWS, units=UNITS) as api:
        api.bundle.side_effect = WialonAPIError("down")
        failed = api.get(f"/api/fuel/analytics/efficiency?{QM}")
    assert failed["freshness"]["refresh_status"] == "unavailable" and failed["freshness"]["fetched_at"] is None
    assert failed["total_litres"] == eff["total_litres"], "fuel figures unchanged without telematics"
    print("[PASS] Fuel: freshness from the telematics bundle's own read time (closed month: no target); Wialon failing "
          "-> unavailable, fuel figures unchanged.")


def test_ai_tool_results_disclose_read_time_and_period():
    cached_health = {"total_vehicles": 3, "retrieved_at": int(time.time()) - 120, "stale": False}
    with patch("app.fleet_analytics.get_fleet_health_analytics", return_value=cached_health):
        out = json.loads(ai_tools.dispatch_tool("get_fleet_health", {}))
    df = out["data_freshness"]
    assert df["data_read_at"].endswith("UAE") and 1.9 <= df["data_age_minutes"] <= 2.2 and df["stale"] is False, df
    assert "data_freshness" not in cached_health, "the cached analytics result is not mutated"
    summary = {"total_vehicles": 3}
    with patch("app.ai.tools.tool_get_fleet_summary", return_value=summary):
        live = json.loads(ai_tools.dispatch_tool("get_fleet_summary", {}))
    assert live["data_freshness"]["data_read_at"] == "read from Wialon for this answer"
    with patch("app.ai.tools.tool_get_fuel_summary", return_value={"period": {"date_from": "2026-08-01", "date_to": "2026-08-31"}}):
        fuel = json.loads(ai_tools.dispatch_tool("get_fuel_summary", {}))
    assert fuel["data_freshness"]["reporting_period"] == {"date_from": "2026-08-01", "date_to": "2026-08-31"}
    with patch("app.ai.tools.tool_get_fleet_summary", side_effect=WialonAPIError("down")):
        err = json.loads(ai_tools.dispatch_tool("get_fleet_summary", {}))
    assert "data_freshness" not in err and "error" in err
    from app.ai.prompts import SYSTEM_PROMPT
    assert "DATA FRESHNESS" in SYSTEM_PROMPT and "data_read_at" in SYSTEM_PROMPT
    print("[PASS] AI tools: data_freshness with the result's own read time (cached: its age), live tools read for the "
          "answer, fuel period disclosed; errors unchanged; the prompt requires stating period and read time.")


TESTS = [
    test_freshness_block_states,
    test_live_fleet_freshness_shared_read_and_groups_sharing,
    test_mileage_freshness_from_snapshot_and_closed_window,
    test_fuel_freshness_from_bundle_read_time,
    test_ai_tool_results_disclose_read_time_and_period,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FRESHNESS AND CACHING (OFFLINE)") else 0)
