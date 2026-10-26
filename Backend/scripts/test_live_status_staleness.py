"""
Offline regression tests for audit finding H2: stale Moving/Idling telemetry is DELAYED, never live Moving/Idling.

  - age <= CONN_STABLE_SECONDS: status from the last message (MOVING / IDLING / STOPPED)
  - CONN_STABLE_SECONDS < age <= CONN_DELAYED_SECONDS, last seen MOVING or IDLING: status DELAYED; last_known_status
    keeps the last state, speed/ignition/last_message_timestamp keep the last reported values, no live idle timer,
    no history reads
  - same age, last seen STOPPED (a parked tracker reports less often): status stays STOPPED (connection DELAYED)
  - older (or no message): OFFLINE
  - every count endpoint partitions the fleet: moving + idling + stopped + delayed + offline == total
  - idle analytics does not count a stale idler as currently idling
  - live and health views use the same CONN_STABLE_SECONDS threshold
  - D3: Vehicle Health / Alerts do not treat a stale parked (last seen STOPPED) unit as delayed (no warning, alert or
    penalty); stale Moving/Idling units still are; past CONN_DELAYED_SECONDS every unit is OFFLINE
  - R2: last seen MOVING with a GPS position (core/search_items pos.t) older than CONN_STABLE_SECONDS is DELAYED even
    with recent messages; a missing position time is unknown; IDLING/STOPPED and the OFFLINE rule are unchanged

Wialon is replaced by mocks; every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_live_status_staleness.py
"""
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402

import app.ai.tools as ai_tools  # noqa: E402
import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402
import app.vehicles as vehicles  # noqa: E402
from app.utils.status_config import CONN_DELAYED_SECONDS, CONN_STABLE_SECONDS  # noqa: E402

NOW = 1_800_000_000
IGNITION = {"1": {"n": "Ignition", "t": "engine operation"}}


def _live(age, speed, ignition_on, vid=7, pos_age=None):
    """
    One unit's live record whose last message is `age` seconds old (None: no message at all). `pos_age` sets the
    core/search_items GPS position time (`pos.t`); None leaves it absent, like calc_last's `pos`, which has no time.
    """
    t = None if age is None else NOW - age
    vehicle = {"id": vid, "nm": f"T{vid}", "sens": IGNITION, "lmsg": {"t": t} if t else None}
    if pos_age is not None:
        vehicle["pos"] = {"t": NOW - pos_age, "s": speed, "y": 25.2, "x": 55.3}
    calc = [{"i": vid, "pos": {"s": speed, "t": t, "y": 25.2, "x": 55.3},
             "sensors": {"1": {"value": 1 if ignition_on else 0}}}]
    history = MagicMock(return_value=[])
    vehicles.idle_start_times.pop(vid, None)
    with patch("app.vehicles.now_timestamp", return_value=NOW), patch("app.vehicles.get_vehicle_history", history):
        rec = vehicles.get_vehicle_live_data("sid", [vehicle], calc, groups=[])[0]
    vehicles.idle_start_times.pop(vid, None)
    return rec, history


def test_live_and_stale_states():
    assert CONN_STABLE_SECONDS == 600 and CONN_DELAYED_SECONDS == 43200, (CONN_STABLE_SECONDS, CONN_DELAYED_SECONDS)
    rec, _ = _live(300, 60, True)
    assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("MOVING", "MOVING", "STABLE"), rec
    rec, _ = _live(11 * 3600, 60, True)  # silent for 11 h while last seen at 60 km/h
    assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("DELAYED", "MOVING", "DELAYED"), rec
    assert rec["speed"] == 60 and rec["ignition"] is True and rec["is_offline"] is False, rec
    assert rec["last_message_timestamp"] == NOW - 11 * 3600 and rec["seconds_since_update"] == 11 * 3600, rec
    rec, _ = _live(13 * 3600, 60, True)
    assert (rec["status"], rec["last_known_status"], rec["is_offline"]) == ("OFFLINE", "MOVING", True), rec
    rec, _ = _live(None, 0, False)
    assert (rec["status"], rec["last_known_status"]) == ("OFFLINE", None), rec
    rec, _ = _live(600, 60, True)
    assert rec["status"] == "MOVING", rec
    rec, _ = _live(601, 60, True)
    assert rec["status"] == "DELAYED", rec
    print("[PASS] H2: <=600 s live status; 601 s..12 h DELAYED with last_known_status and last reported speed; "
          "older or no message OFFLINE.")


def test_stale_idler_has_no_live_idle_timer():
    rec, history = _live(300, 0, True)
    assert rec["status"] == "IDLING" and history.call_count >= 1, (rec["status"], history.call_count)
    rec, history = _live(2 * 3600, 0, True)
    assert (rec["status"], rec["last_known_status"]) == ("DELAYED", "IDLING"), rec
    assert rec["idle_duration"] == 0 and rec["idle_start"] is None and rec["idle"]["is_idling"] is False, rec
    assert history.call_count == 0, "a stale unit's idle timer is not seeded against wall-clock time"
    assert 7 not in vehicles.idle_start_times
    print("[PASS] H2: a unit last seen idling 2 h ago is DELAYED with no running idle timer and no history reads.")


def test_stale_parked_unit_stays_stopped():
    for age in (CONN_STABLE_SECONDS, CONN_STABLE_SECONDS + 1, 3600, CONN_DELAYED_SECONDS):
        rec, history = _live(age, 0, False)
        assert (rec["status"], rec["last_known_status"]) == ("STOPPED", "STOPPED"), (age, rec)
        expected_conn = "STABLE" if age <= CONN_STABLE_SECONDS else "DELAYED"
        assert rec["connection_status"] == expected_conn and rec["is_offline"] is False, (age, rec)
        assert rec["ignition"] is False and rec["speed"] == 0 and rec["seconds_since_update"] == age, (age, rec)
        assert rec["idle_duration"] == 0 and history.call_count == 0, (age, rec)
    rec, _ = _live(CONN_DELAYED_SECONDS + 1, 0, False)
    assert (rec["status"], rec["last_known_status"], rec["is_offline"]) == ("OFFLINE", "STOPPED", True), rec
    print("[PASS] H2: a unit last seen parked stays STOPPED (connection DELAYED, ignition off kept) up to 12 h, "
          "then OFFLINE.")


def test_moving_and_idling_boundaries():
    for speed, ignition_on, live in ((60, True, "MOVING"), (0, True, "IDLING")):
        rec, _ = _live(CONN_STABLE_SECONDS, speed, ignition_on)
        assert (rec["status"], rec["connection_status"]) == (live, "STABLE"), (live, rec)
        for age in (CONN_STABLE_SECONDS + 1, CONN_DELAYED_SECONDS):
            rec, _ = _live(age, speed, ignition_on)
            assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("DELAYED", live, "DELAYED"), (age, rec)
            assert rec["ignition"] is ignition_on and rec["speed"] == speed, (age, rec)
        rec, _ = _live(CONN_DELAYED_SECONDS + 1, speed, ignition_on)
        assert (rec["status"], rec["last_known_status"]) == ("OFFLINE", live), rec
    # Moving with ignition off (e.g. towed) is still a last-seen MOVING state: hidden as DELAYED when stale
    rec, _ = _live(CONN_STABLE_SECONDS + 1, 40, False)
    assert (rec["status"], rec["last_known_status"]) == ("DELAYED", "MOVING"), rec
    statuses = set()
    for age in (None, 0, CONN_STABLE_SECONDS, CONN_STABLE_SECONDS + 1, CONN_DELAYED_SECONDS, CONN_DELAYED_SECONDS + 1):
        for speed, ignition_on in ((60, True), (40, False), (0, True), (0, False)):
            rec, _ = _live(age, speed, ignition_on)
            statuses.add(rec["status"])
            stale_active = rec["connection_status"] == "DELAYED" and rec["last_known_status"] in ("MOVING", "IDLING")
            assert (rec["status"] == "DELAYED") == stale_active, rec
    assert statuses == {"MOVING", "IDLING", "STOPPED", "DELAYED", "OFFLINE"}, statuses
    print("[PASS] H2: Moving/Idling are live up to 600 s, DELAYED from 601 s to 12 h, then OFFLINE; every record has "
          "exactly one of the five statuses, and DELAYED only for a stale Moving/Idling state.")


def _fleet():
    rows = [("MOVING", "STABLE"), ("IDLING", "STABLE"), ("STOPPED", "STABLE"), ("DELAYED", "DELAYED"),
            ("DELAYED", "DELAYED"), ("OFFLINE", "OFFLINE"), ("STOPPED", "DELAYED")]
    return [{"vehicle_id": 100 + i, "vehicle_name": f"U{i}", "group": "Logistics", "groups": ["Logistics"],
             "status": st, "last_known_status": "IDLING" if st == "DELAYED" else st, "connection_status": conn,
             "is_offline": conn == "OFFLINE", "speed": 0, "ignition": True, "latitude": 25.0, "longitude": 55.0,
             "idle_duration": 0, "idle_start": None, "sensors": {}, "driver": {}, "mileage": None, "engine_hours": None}
            for i, (st, conn) in enumerate(rows)]


def _assert_partition(block, where):
    parts = block["moving"] + block["idling"] + block["stopped"] + block["delayed"] + block["offline"]
    total = block.get("total_vehicles", block.get("total"))
    # The stale parked unit (STOPPED, connection DELAYED) counts as stopped, never as delayed or offline
    assert (block["moving"], block["idling"], block["stopped"], block["delayed"], block["offline"]) == (1, 1, 2, 2, 1), (where, block)
    assert parts == total == 7, (where, block)


def test_counts_partition_the_fleet():
    groups = [{"id": 7, "nm": "Logistics", "u": [100 + i for i in range(7)]}]
    units = [{"id": 100 + i, "nm": f"U{i}"} for i in range(7)]
    with ExitStack() as stack:
        main._cache.clear()
        fa._analytics_cache.clear()
        for target in ("app.main", "app.fleet_analytics", "app.ai.tools"):
            stack.enter_context(patch(f"{target}.login", return_value="sid"))
            stack.enter_context(patch(f"{target}.get_all_vehicles", return_value=units))
            stack.enter_context(patch(f"{target}.get_calculated_data", return_value=[]))
            stack.enter_context(patch(f"{target}.get_vehicle_live_data", side_effect=lambda *a, **k: _fleet()))
        for target in ("app.main", "app.fleet_analytics"):
            stack.enter_context(patch(f"{target}.get_vehicle_groups", return_value=groups))
        stack.enter_context(patch("app.fleet_analytics.get_trips_mileage_analytics",
                                  return_value={"summary": {"total_distance_km": 0.0, "total_trips": 0}, "vehicles": []}))
        stack.enter_context(patch("app.fleet_analytics.get_safety_analytics",
                                  return_value={"summary": {"total_events": 0}, "by_group": []}))
        stack.enter_context(patch("app.fleet_analytics.get_fleet_utilization_analytics",
                                  return_value={"total_idle_hours": 0.0, "partial": False}))
        _assert_partition(main.get_dashboard_summary(hours=24, timeframe=None)["summary"], "/api/dashboard/summary")
        _assert_partition(main.get_fleet_health()["fleet"], "/api/dashboard/fleet-health")
        _assert_partition(main.get_groups()["groups"][0], "/api/groups")
        _assert_partition(fa.get_fleet_groups_analytics()["groups"][0], "/api/fleet/groups")
        _assert_partition(ai_tools.tool_get_fleet_summary(), "AI get_fleet_summary")
        delayed = ai_tools.tool_get_vehicles_by_status("DELAYED")
        assert delayed["count"] == 2 and all(v["last_known_status"] == "IDLING" for v in delayed["vehicles"]), delayed
        assert ai_tools.tool_get_vehicles_by_status("IDLING")["count"] == 1
    print("[PASS] H2: summary, fleet-health, groups, fleet groups and AI counts partition the fleet "
          "(moving + idling + stopped + delayed + offline == total); AI can list DELAYED units.")


def test_idle_analytics_excludes_stale_idler():
    from wialon_report_fixtures import CALCULATED, GROUPS, UNITS, FakeReportEngine
    fleet = [dict(v, vehicle_id=u["id"], vehicle_name=u["nm"]) for v, u in zip(_fleet(), UNITS)]
    fleet[0]["status"] = "DELAYED"  # last known IDLING, stale
    fleet[1]["status"] = "IDLING"
    with ExitStack() as stack:
        main._cache.clear()
        stack.enter_context(patch("app.wialon_reports.wialon_request", FakeReportEngine()))
        stack.enter_context(patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS))
        stack.enter_context(patch("app.wialon_reports.GROUP_ID_ALL_UNITS", 0))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_vehicle_groups", return_value=GROUPS))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=UNITS))
        stack.enter_context(patch("app.main.get_calculated_data", return_value=CALCULATED))
        stack.enter_context(patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in fleet]))
        res = main.get_fleet_idle_analytics(hours=24, timeframe=None, from_date=None, to_date=None, from_time=None,
                                            to_time=None, group_id=None, vehicle_id=None, include_events=False,
                                            refresh=True, skip_cache=True)
    assert res["kpis"]["currently_idling_vehicles"] == 1, res["kpis"]
    print("[PASS] H2: idle analytics counts only a live idler as currently idling, not a DELAYED one.")


def test_health_and_live_share_threshold():
    assert fa.HEALTH_THRESHOLDS["CONN_STABLE_SECONDS"] == CONN_STABLE_SECONDS
    raw = {"id": 7, "nm": "T7", "lmsg": {"t": NOW - (CONN_STABLE_SECONDS + 1)}}
    with patch("app.fleet_analytics.time.time", return_value=NOW):
        health = fa.evaluate_vehicle_health({"vehicle_id": 7, "vehicle_name": "T7"}, raw_unit=raw, calc_data={})
    rec, _ = _live(CONN_STABLE_SECONDS + 1, 60, True)
    assert health["connectivity"]["status"] == rec["connection_status"] == "DELAYED", (health["connectivity"], rec)
    print("[PASS] H2: live status and vehicle health use the same CONN_STABLE_SECONDS threshold.")


def _health(age, speed, ignition_on, vid=7):
    """(live record, health evaluation) for one unit whose last message is `age` seconds old."""
    rec, _ = _live(age, speed, ignition_on, vid=vid)
    raw = {"id": vid, "nm": f"T{vid}", "lmsg": {"t": NOW - age} if age is not None else None}
    with patch("app.fleet_analytics.time.time", return_value=NOW):
        return rec, fa.evaluate_vehicle_health(rec, raw_unit=raw, calc_data={})


def test_health_stale_parked_unit_not_delayed():
    """D3: a parked unit's older message is no delay warning or penalty; Moving/Idling still are; OFFLINE unchanged."""
    _, fresh = _health(60, 0, False)
    base_score = fresh["health_score"]
    assert fresh["connectivity"]["status"] == "STABLE", fresh["connectivity"]
    for age in (CONN_STABLE_SECONDS + 1, 3600, CONN_DELAYED_SECONDS):
        rec, h = _health(age, 0, False)
        assert (rec["status"], rec["connection_status"]) == ("STOPPED", "DELAYED"), "live status unchanged"
        assert h["connectivity"]["status"] == "STABLE" and h["health_score"] == base_score, (age, h)
        assert h["connectivity"]["seconds_since_update"] == age, "the real message age is still reported"
    for speed, ignition_on, live in ((60, True, "MOVING"), (0, True, "IDLING")):
        for age in (CONN_STABLE_SECONDS + 1, CONN_DELAYED_SECONDS):
            rec, h = _health(age, speed, ignition_on)
            assert rec["last_known_status"] == live and h["connectivity"]["status"] == "DELAYED", (live, age, h)
            assert h["health_score"] == base_score - 15, (live, age, h["health_score"])
    for speed, ignition_on in ((0, False), (60, True), (0, True)):
        rec, h = _health(CONN_DELAYED_SECONDS + 1, speed, ignition_on)
        assert rec["status"] == "OFFLINE" and h["connectivity"]["status"] == "OFFLINE", h["connectivity"]
        assert h["health_score"] == base_score - 35, h["health_score"]
    rec, h = _health(None, 0, False)
    assert h["connectivity"]["status"] == "OFFLINE" and h["connectivity"]["seconds_since_update"] is None, h
    print("[PASS] D3: stale parked (STOPPED) unit -> connectivity STABLE, no penalty, real age kept; stale "
          "Moving/Idling -> DELAYED (-15); past 12 h or no message -> OFFLINE (-35) for every last state.")


def test_health_alerts_and_filters_consistent_for_parked_units():
    """D3: fleet health counts, alerts and the Vehicle Health page's filter inputs agree for each kind of unit."""
    cases = {  # vid: (age, speed, ignition, expected connectivity)
        1: (60, 0, False, "STABLE"), 2: (2 * 3600, 0, False, "STABLE"), 3: (2 * 3600, 60, True, "DELAYED"),
        4: (2 * 3600, 0, True, "DELAYED"), 5: (CONN_DELAYED_SECONDS + 1, 0, False, "OFFLINE"),
    }
    fleet, raws = [], []
    for vid, (age, speed, ign, _) in cases.items():
        rec, _ = _live(age, speed, ign, vid=vid)
        fleet.append(rec)
        raws.append({"id": vid, "nm": f"T{vid}", "lmsg": {"t": NOW - age}})
    with ExitStack() as stack:
        fa._analytics_cache.clear()
        stack.enter_context(patch("app.fleet_analytics.time.time", return_value=NOW))
        stack.enter_context(patch("app.fleet_analytics.login", return_value="sid"))
        stack.enter_context(patch("app.fleet_analytics.get_vehicle_groups", return_value=[]))
        stack.enter_context(patch("app.fleet_analytics.get_all_vehicles", return_value=raws))
        stack.enter_context(patch("app.fleet_analytics.get_calculated_data", return_value=[]))
        stack.enter_context(patch("app.fleet_analytics.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in fleet]))
        stack.enter_context(patch("app.fleet_analytics.get_safety_analytics",
                                  return_value={"summary": {"total_events": 0}, "vehicles": [], "events": []}))
        health = fa.get_fleet_health_analytics(use_cache=False)
        alerts = fa._build_fleet_alerts(use_cache=False)
    fa._analytics_cache.clear()
    by_id = {v["vehicle_id"]: v for v in health["vehicles"]}
    for vid, (_, _, _, conn) in cases.items():
        assert by_id[vid]["connectivity"]["status"] == conn, (vid, by_id[vid]["connectivity"])
    assert by_id[1]["health_score"] == by_id[2]["health_score"], "a stale parked unit scores as a fresh one"
    assert health["connectivity_warnings"] == 3, health  # units 3, 4 (DELAYED) and 5 (OFFLINE)
    conn_alerts = {(a["vehicle_id"], a["alert_type"]) for a in alerts["alerts"]
                   if a["alert_type"] in ("TELEMETRY_DELAYED", "DEVICE_OFFLINE")}
    assert conn_alerts == {(3, "TELEMETRY_DELAYED"), (4, "TELEMETRY_DELAYED"), (5, "DEVICE_OFFLINE")}, conn_alerts
    # Vehicle Health filters (Frontend VehicleHealth.jsx) read connectivity.status: same split as the alerts
    warning = {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "DELAYED"}
    critical = {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "OFFLINE"}
    assert warning == {3, 4} and critical == {5}, (warning, critical)
    assert {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "STABLE"} == {1, 2}
    print("[PASS] D3: health counts, TELEMETRY_DELAYED/DEVICE_OFFLINE alerts and Vehicle Health filter inputs agree: "
          "stale parked units are not delayed, stale Moving/Idling are, past 12 h is OFFLINE.")


def test_stale_gps_position_moving_is_delayed():
    """R2: recent messages without a new GPS fix keep the old position's speed: MOVING from it is DELAYED."""
    for pos_age in (CONN_STABLE_SECONDS + 1, 3600, 21738, 3 * 86400):
        for ignition_on in (False, True):
            rec, _ = _live(60, 14, ignition_on, pos_age=pos_age)
            assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("DELAYED", "MOVING", "STABLE"), (pos_age, rec)
            assert rec["speed"] == 14 and rec["ignition"] is ignition_on and rec["is_offline"] is False, rec
            assert rec["seconds_since_update"] == 60 and rec["last_message_timestamp"] == NOW - 60, rec
            assert (rec["latitude"], rec["longitude"]) == (25.2, 55.3), rec
            assert rec["idle_duration"] == 0 and rec["idle_start"] is None, rec
            assert "seconds_since_position" not in rec, "no new response field"
    print("[PASS] R2: last seen MOVING with a GPS position older than 10 min (fresh messages) -> DELAYED; "
          "last_known_status, speed, coordinates and message age kept; no new response field.")


def test_stale_gps_position_boundary_and_missing():
    assert _live(60, 40, True, pos_age=CONN_STABLE_SECONDS)[0]["status"] == "MOVING"
    assert _live(60, 40, True, pos_age=CONN_STABLE_SECONDS + 1)[0]["status"] == "DELAYED"
    assert _live(60, 40, True, pos_age=0)[0]["status"] == "MOVING"
    rec, _ = _live(60, 40, True)  # no position time at all: unknown, never stale
    assert (rec["status"], rec["connection_status"]) == ("MOVING", "STABLE"), rec
    print("[PASS] R2: position age 600 s -> MOVING, 601 s -> DELAYED; a missing position time keeps MOVING (unknown).")


def test_stale_gps_position_does_not_change_idling_or_stopped():
    rec, history = _live(60, 0, True, pos_age=3 * 3600)
    assert (rec["status"], rec["last_known_status"]) == ("IDLING", "IDLING") and history.call_count >= 1, rec
    assert _live(60, 0, False, pos_age=3 * 3600)[0]["status"] == "STOPPED"
    rec, _ = _live(2 * 3600, 0, False, pos_age=3 * 3600)  # parked, sparse messages (D3 case)
    assert (rec["status"], rec["connection_status"]) == ("STOPPED", "DELAYED"), rec
    print("[PASS] R2: an old GPS position alone never changes IDLING or STOPPED.")


def test_fresh_gps_position_recovers_moving():
    rec, _ = _live(30, 50, True, vid=9, pos_age=4000)
    assert rec["status"] == "DELAYED", rec
    rec, _ = _live(3, 56, True, vid=9, pos_age=3)  # the next message carries a fresh fix
    assert (rec["status"], rec["last_known_status"], rec["speed"]) == ("MOVING", "MOVING", 56), rec
    print("[PASS] R2: a fresh GPS fix returns the unit to MOVING.")


def test_stale_gps_position_offline_rule_unchanged():
    for pos_age in (60, 2 * 86400):
        rec, _ = _live(CONN_DELAYED_SECONDS + 1, 40, True, pos_age=pos_age)
        assert (rec["status"], rec["is_offline"]) == ("OFFLINE", True), rec
    rec, _ = _live(60, 40, True, pos_age=2 * 86400)  # recent messages, 2-day-old fix: not OFFLINE
    assert (rec["status"], rec["connection_status"], rec["is_offline"]) == ("DELAYED", "STABLE", False), rec
    rec, _ = _live(CONN_DELAYED_SECONDS, 40, True, pos_age=60)  # message-age rule unchanged up to 12 h
    assert (rec["status"], rec["connection_status"]) == ("DELAYED", "DELAYED"), rec
    print("[PASS] R2: OFFLINE is still decided by message age only (> 12 h); a stale fix with recent messages is "
          "DELAYED, never OFFLINE.")


def test_stale_gps_position_counts_health_alerts():
    """R2 records through fleet counts, Vehicle Health and Alerts."""
    cases = {  # vid: (message age, speed, ignition, position age) -> expected live status
        1: ((30, 40, True, 30), "MOVING"), 2: ((60, 14, False, 21738), "DELAYED"),
        3: ((60, 0, True, 3 * 3600), "IDLING"), 4: ((2 * 3600, 0, False, 3 * 3600), "STOPPED"),
        5: ((CONN_DELAYED_SECONDS + 1, 0, False, None), "OFFLINE"),
    }
    fleet, raws = [], []
    for vid, ((age, speed, ign, pos_age), expected) in cases.items():
        rec, _ = _live(age, speed, ign, vid=vid, pos_age=pos_age)
        assert rec["status"] == expected, (vid, rec)
        fleet.append(dict(rec, group="Logistics", groups=["Logistics"]))
        raws.append({"id": vid, "nm": f"T{vid}", "lmsg": {"t": NOW - age}})
    groups = [{"id": 7, "nm": "Logistics", "u": list(cases)}]
    expected_counts = {"moving": 1, "idling": 1, "stopped": 1, "delayed": 1, "offline": 1}
    with ExitStack() as stack:
        main._cache.clear()
        fa._analytics_cache.clear()
        stack.enter_context(patch("app.fleet_analytics.time.time", return_value=NOW))
        for target in ("app.main", "app.fleet_analytics", "app.ai.tools"):
            stack.enter_context(patch(f"{target}.login", return_value="sid"))
            stack.enter_context(patch(f"{target}.get_all_vehicles", return_value=raws))
            stack.enter_context(patch(f"{target}.get_calculated_data", return_value=[]))
            stack.enter_context(patch(f"{target}.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in fleet]))
        for target in ("app.main", "app.fleet_analytics"):
            stack.enter_context(patch(f"{target}.get_vehicle_groups", return_value=groups))
        stack.enter_context(patch("app.fleet_analytics.get_safety_analytics",
                                  return_value={"summary": {"total_events": 0}, "vehicles": [], "events": []}))
        blocks = {"/api/dashboard/fleet-health": main.get_fleet_health()["fleet"],
                  "/api/groups": main.get_groups()["groups"][0], "AI get_fleet_summary": ai_tools.tool_get_fleet_summary()}
        health = fa.get_fleet_health_analytics(use_cache=False)
        alerts = fa._build_fleet_alerts(use_cache=False)
    main._cache.clear()
    fa._analytics_cache.clear()
    for where, block in blocks.items():
        assert {k: block[k] for k in expected_counts} == expected_counts, (where, block)
        assert sum(block[k] for k in expected_counts) == block.get("total_vehicles", block.get("total")) == 5, (where, block)
    by_id = {v["vehicle_id"]: v for v in health["vehicles"]}
    # Messages are recent, so the stale-fix unit has no connectivity penalty: it scores as the fresh moving unit
    assert by_id[2]["connectivity"]["status"] == "STABLE" and by_id[2]["health_score"] == by_id[1]["health_score"], by_id[2]
    assert by_id[2]["status"] == "DELAYED", "Health shows the unit's live status"
    conn_alerts = {(a["vehicle_id"], a["alert_type"]) for a in alerts["alerts"]
                   if a["alert_type"] in ("TELEMETRY_DELAYED", "DEVICE_OFFLINE")}
    assert conn_alerts == {(5, "DEVICE_OFFLINE")}, conn_alerts
    assert health["connectivity_warnings"] == 1, health
    warning = {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "DELAYED"}
    critical = {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "OFFLINE"}
    assert warning == set() and critical == {5}, (warning, critical)
    print("[PASS] R2: fleet-health, groups and AI counts partition the fleet with the stale-fix unit as delayed; "
          "Health/Alerts unchanged for it (recent messages: no connectivity penalty or alert); filters agree.")


TESTS = [
    test_live_and_stale_states,
    test_stale_idler_has_no_live_idle_timer,
    test_stale_parked_unit_stays_stopped,
    test_moving_and_idling_boundaries,
    test_counts_partition_the_fleet,
    test_idle_analytics_excludes_stale_idler,
    test_health_and_live_share_threshold,
    test_health_stale_parked_unit_not_delayed,
    test_health_alerts_and_filters_consistent_for_parked_units,
    test_stale_gps_position_moving_is_delayed,
    test_stale_gps_position_boundary_and_missing,
    test_stale_gps_position_does_not_change_idling_or_stopped,
    test_fresh_gps_position_recovers_moving,
    test_stale_gps_position_offline_rule_unchanged,
    test_stale_gps_position_counts_health_alerts,
]


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in TESTS:
            try:
                t()
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
    print(f"{len(TESTS) - failures}/{len(TESTS)} test functions passed")
    sys.exit(1 if failures else 0)
