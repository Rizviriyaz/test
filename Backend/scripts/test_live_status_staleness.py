"""
Offline regression tests for live status and connection health (audit H2, revised by F1: movement status is kept
separate from connection health).

  - age <= CONN_STABLE_SECONDS: status from the last message (MOVING / IDLING / STOPPED), telemetry_stale false
  - CONN_STABLE_SECONDS < age <= CONN_DELAYED_SECONDS, last seen MOVING or IDLING: status keeps that last known state
    with telemetry_stale true (connection DELAYED); speed/ignition/last_message_timestamp keep the last reported
    values; the idle timer stops at the last message
  - same age, last seen STOPPED (a parked tracker reports less often): STOPPED, telemetry_stale false (D3)
  - older (or no message): OFFLINE (12 h threshold unchanged); a DELAYED vehicle is never counted as OFFLINE
  - every count endpoint partitions the fleet: moving + idling + stopped + offline == total; `delayed` counts the
    telemetry-stale vehicles among them
  - idle analytics counts a stale idler under its last known status (currently idling)
  - live and health views use the same CONN_STABLE_SECONDS threshold
  - D3: Vehicle Health / Alerts do not treat a stale parked (last seen STOPPED) unit as delayed (no warning, alert or
    penalty); stale Moving/Idling units still are; past CONN_DELAYED_SECONDS every unit is OFFLINE
  - R2: last seen MOVING with a GPS position (core/search_items pos.t) older than CONN_STABLE_SECONDS stays MOVING
    with position_stale and telemetry_stale true even with recent messages; a missing position time is unknown;
    IDLING/STOPPED and the OFFLINE rule are unchanged
  - Phase 3: the fix time/age (position_timestamp, seconds_since_position) and position_stale (exactly the R2 case)
    are exposed; Health marks such a unit's GPS STALE (-15, GPS warning, GPS_POSITION_STALE alert) only while its
    connection is STABLE (no double penalty); AI vehicle tools report message age, fix age and last-reported speed

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


def _live(age, speed, ignition_on, vid=7, pos_age=None, sats=None, history_msgs=None):
    """
    One unit's live record whose last message is `age` seconds old (None: no message at all). `pos_age` sets the
    core/search_items GPS position time (`pos.t`); None leaves it absent, like calc_last's `pos`, which has no time.
    `sats` sets calc_last's satellite count (`pos.sc`); None leaves it absent (GPS quality UNKNOWN).
    """
    t = None if age is None else NOW - age
    vehicle = {"id": vid, "nm": f"T{vid}", "sens": IGNITION, "lmsg": {"t": t} if t else None}
    if pos_age is not None:
        vehicle["pos"] = {"t": NOW - pos_age, "s": speed, "y": 25.2, "x": 55.3}
    calc_pos = {"s": speed, "t": t, "y": 25.2, "x": 55.3}
    if sats is not None:
        calc_pos["sc"] = sats
    calc = [{"i": vid, "pos": calc_pos, "sensors": {"1": {"value": 1 if ignition_on else 0}}}]
    history = MagicMock(return_value=list(history_msgs or []))
    vehicles.idle_start_times.pop(vid, None)
    with patch("app.vehicles.now_timestamp", return_value=NOW), patch("app.vehicles.get_vehicle_history", history):
        rec = vehicles.get_vehicle_live_data("sid", [vehicle], calc, groups=[])[0]
    vehicles.idle_start_times.pop(vid, None)
    return rec, history


def test_live_and_stale_states():
    assert CONN_STABLE_SECONDS == 600 and CONN_DELAYED_SECONDS == 43200, (CONN_STABLE_SECONDS, CONN_DELAYED_SECONDS)
    rec, _ = _live(300, 60, True)
    assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("MOVING", "MOVING", "STABLE"), rec
    assert rec["telemetry_stale"] is False, rec
    rec, _ = _live(11 * 3600, 60, True)  # silent for 11 h while last seen at 60 km/h
    assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("MOVING", "MOVING", "DELAYED"), rec
    assert rec["telemetry_stale"] is True, "the status is the last known one, flagged stale (never Offline)"
    assert rec["speed"] == 60 and rec["ignition"] is True and rec["is_offline"] is False, rec
    assert rec["last_message_timestamp"] == NOW - 11 * 3600 and rec["seconds_since_update"] == 11 * 3600, rec
    rec, _ = _live(13 * 3600, 60, True)
    assert (rec["status"], rec["last_known_status"], rec["is_offline"]) == ("OFFLINE", "MOVING", True), rec
    assert rec["telemetry_stale"] is False, "OFFLINE is its own state, not a stale one"
    rec, _ = _live(None, 0, False)
    assert (rec["status"], rec["last_known_status"]) == ("OFFLINE", None), rec
    rec, _ = _live(600, 60, True)
    assert (rec["status"], rec["telemetry_stale"]) == ("MOVING", False), rec
    rec, _ = _live(601, 60, True)
    assert (rec["status"], rec["telemetry_stale"]) == ("MOVING", True), rec
    print("[PASS] F1: <=600 s live status; 601 s..12 h keeps the last known status (telemetry_stale, last reported "
          "speed); older or no message OFFLINE.")


def _msg(ts, speed, ignition_on):
    return {"tp": "ud", "t": ts, "pos": {"s": speed}, "sensors": {"1": {"value": 1 if ignition_on else 0}}}


def test_stale_idler_keeps_idling_with_capped_timer():
    rec, history = _live(300, 0, True)
    assert rec["status"] == "IDLING" and rec["telemetry_stale"] is False and history.call_count >= 1, rec
    # Moving until 3 h ago, then idling; the last message was 2 h ago
    msgs = [_msg(NOW - 3 * 3600 - 60, 30, True), _msg(NOW - 3 * 3600, 0, True), _msg(NOW - 2 * 3600, 0, True)]
    rec, history = _live(2 * 3600, 0, True, history_msgs=msgs)
    assert (rec["status"], rec["last_known_status"], rec["telemetry_stale"]) == ("IDLING", "IDLING", True), rec
    assert rec["idle_start"] == NOW - 3 * 3600 and rec["idle_duration_status"] == "known", rec
    # Idling is only known up to the last message: 1 h, not the 3 h a wall-clock timer would show
    assert rec["idle_duration"] == 3600 and rec["idle"]["duration"] == 3600, rec
    # A live idler's timer is capped the same way (its last message is recent)
    msgs = [_msg(NOW - 1800 - 60, 30, True), _msg(NOW - 1800, 0, True), _msg(NOW - 30, 0, True)]
    rec, _ = _live(30, 0, True, history_msgs=msgs)
    assert rec["idle_duration"] == 1800 - 30 and rec["telemetry_stale"] is False, rec
    print("[PASS] F1: a unit last seen idling 2 h ago stays IDLING (telemetry_stale) and its idle timer stops at the "
          "last message (1 h, not 3 h); a live idler's timer is capped at its last message too.")


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
            assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == (live, live, "DELAYED"), (age, rec)
            assert rec["telemetry_stale"] is True, (age, rec)
            assert rec["ignition"] is ignition_on and rec["speed"] == speed, (age, rec)
        rec, _ = _live(CONN_DELAYED_SECONDS + 1, speed, ignition_on)
        assert (rec["status"], rec["last_known_status"]) == ("OFFLINE", live), rec
    # Moving with ignition off (e.g. towed) is still a last-seen MOVING state: kept as MOVING, flagged stale
    rec, _ = _live(CONN_STABLE_SECONDS + 1, 40, False)
    assert (rec["status"], rec["last_known_status"], rec["telemetry_stale"]) == ("MOVING", "MOVING", True), rec
    statuses = set()
    for age in (None, 0, CONN_STABLE_SECONDS, CONN_STABLE_SECONDS + 1, CONN_DELAYED_SECONDS, CONN_DELAYED_SECONDS + 1):
        for speed, ignition_on in ((60, True), (40, False), (0, True), (0, False)):
            rec, _ = _live(age, speed, ignition_on)
            statuses.add(rec["status"])
            stale_active = rec["connection_status"] == "DELAYED" and rec["last_known_status"] in ("MOVING", "IDLING")
            assert rec["telemetry_stale"] is stale_active, rec
            if rec["status"] != "OFFLINE":
                assert rec["status"] == rec["last_known_status"], rec
    assert statuses == {"MOVING", "IDLING", "STOPPED", "OFFLINE"}, statuses
    print("[PASS] F1: Moving/Idling are live up to 600 s, kept as last known (telemetry_stale) from 601 s to 12 h, then "
          "OFFLINE; every record has exactly one of the four statuses; DELAYED is never a status.")


def _fleet():
    # (status, connection, telemetry_stale): two stale idlers keep IDLING, a stale parked unit stays STOPPED
    rows = [("MOVING", "STABLE", False), ("IDLING", "STABLE", False), ("STOPPED", "STABLE", False),
            ("IDLING", "DELAYED", True), ("IDLING", "DELAYED", True), ("OFFLINE", "OFFLINE", False),
            ("STOPPED", "DELAYED", False)]
    return [{"vehicle_id": 100 + i, "vehicle_name": f"U{i}", "group": "Logistics", "groups": ["Logistics"],
             "status": st, "last_known_status": st if st != "OFFLINE" else "IDLING", "connection_status": conn,
             "telemetry_stale": stale,
             "is_offline": conn == "OFFLINE", "speed": 0, "ignition": True, "latitude": 25.0, "longitude": 55.0,
             "idle_duration": 0, "idle_start": None, "sensors": {}, "driver": {}, "mileage": None, "engine_hours": None}
            for i, (st, conn, stale) in enumerate(rows)]


def _assert_partition(block, where):
    parts = block["moving"] + block["idling"] + block["stopped"] + block["offline"]
    total = block.get("total_vehicles", block.get("total"))
    # The stale idlers count as idling (last known status) and in `delayed` (stale telemetry), never as offline; the
    # stale parked unit (STOPPED, connection DELAYED) counts as stopped only
    assert (block["moving"], block["idling"], block["stopped"], block["delayed"], block["offline"]) == (1, 3, 2, 2, 1), (where, block)
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
        stale = ai_tools.tool_get_vehicles_by_status("STALE")
        assert stale["count"] == 2 and all(v["status"] == "IDLING" and v["telemetry_stale"] for v in stale["vehicles"]), stale
        assert all(v["speed_is_last_reported"] for v in stale["vehicles"]), stale
        assert ai_tools.tool_get_vehicles_by_status("IDLING")["count"] == 3
        assert ai_tools.tool_get_vehicles_by_status("OFFLINE")["count"] == 1, "stale vehicles are never OFFLINE"
    print("[PASS] F1: summary, fleet-health, groups, fleet groups and AI counts partition the fleet "
          "(moving + idling + stopped + offline == total, stale idlers as idling); `delayed` counts stale telemetry; "
          "AI lists stale units with STALE.")


def test_idle_analytics_excludes_stale_idler():
    from wialon_report_fixtures import CALCULATED, GROUPS, UNITS, FakeReportEngine
    fleet = [dict(v, vehicle_id=u["id"], vehicle_name=u["nm"]) for v, u in zip(_fleet(), UNITS)]
    fleet[0].update(status="IDLING", telemetry_stale=True)  # last known IDLING, stale
    fleet[1].update(status="IDLING", telemetry_stale=False)
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
    assert res["kpis"]["currently_idling_vehicles"] == 2, res["kpis"]
    print("[PASS] F1: idle analytics counts a stale idler under its last known status (currently idling).")


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
            assert (rec["status"], rec["last_known_status"], rec["connection_status"]) == ("MOVING", "MOVING", "STABLE"), (pos_age, rec)
            assert rec["telemetry_stale"] is True, (pos_age, rec)
            assert rec["speed"] == 14 and rec["ignition"] is ignition_on and rec["is_offline"] is False, rec
            assert rec["seconds_since_update"] == 60 and rec["last_message_timestamp"] == NOW - 60, rec
            assert (rec["latitude"], rec["longitude"]) == (25.2, 55.3), rec
            assert rec["idle_duration"] == 0 and rec["idle_start"] is None, rec
            # Phase 3 P1: the GPS fix age is exposed separately from the message age
            assert rec["position_timestamp"] == NOW - pos_age and rec["seconds_since_position"] == pos_age, rec
            assert rec["position_stale"] is True, rec
    print("[PASS] R2/F1: last seen MOVING with a GPS position older than 10 min (fresh messages) -> MOVING with "
          "telemetry_stale; speed, coordinates and message age kept; fix time/age exposed, position_stale true.")


def test_stale_gps_position_boundary_and_missing():
    for pos_age, stale in ((CONN_STABLE_SECONDS, False), (CONN_STABLE_SECONDS + 1, True), (0, False)):
        rec = _live(60, 40, True, pos_age=pos_age)[0]
        assert (rec["status"], rec["position_stale"], rec["seconds_since_position"]) == ("MOVING", stale, pos_age), rec
        assert rec["telemetry_stale"] is stale, rec
    rec, _ = _live(60, 40, True)  # no position time at all: unknown, never stale
    assert (rec["status"], rec["connection_status"]) == ("MOVING", "STABLE"), rec
    assert (rec["position_timestamp"], rec["seconds_since_position"], rec["position_stale"]) == (None, None, False), rec
    print("[PASS] R2/F1: position age 600 s -> live MOVING, 601 s -> MOVING with position_stale/telemetry_stale; a "
          "missing position time keeps MOVING with null fix time/age (unknown).")


def test_stale_gps_position_does_not_change_idling_or_stopped():
    rec, history = _live(60, 0, True, pos_age=3 * 3600)
    assert (rec["status"], rec["last_known_status"]) == ("IDLING", "IDLING") and history.call_count >= 1, rec
    assert _live(60, 0, False, pos_age=3 * 3600)[0]["status"] == "STOPPED"
    rec, _ = _live(2 * 3600, 0, False, pos_age=3 * 3600)  # parked, sparse messages (D3 case)
    assert (rec["status"], rec["connection_status"]) == ("STOPPED", "DELAYED"), rec
    for speed, ign, age in ((0, True, 60), (0, False, 60), (0, False, 2 * 3600)):
        rec, _ = _live(age, speed, ign, pos_age=3 * 3600)
        assert rec["position_stale"] is False and rec["seconds_since_position"] == 3 * 3600, rec
    print("[PASS] R2: an old GPS position alone never changes IDLING or STOPPED (position_stale false, age exposed).")


def test_fresh_gps_position_recovers_moving():
    rec, _ = _live(30, 50, True, vid=9, pos_age=4000)
    assert (rec["status"], rec["telemetry_stale"]) == ("MOVING", True), rec
    rec, _ = _live(3, 56, True, vid=9, pos_age=3)  # the next message carries a fresh fix
    assert (rec["status"], rec["last_known_status"], rec["speed"], rec["telemetry_stale"]) == ("MOVING", "MOVING", 56, False), rec
    print("[PASS] R2: a fresh GPS fix clears the stale flag.")


def test_stale_gps_position_offline_rule_unchanged():
    for pos_age in (60, 2 * 86400):
        rec, _ = _live(CONN_DELAYED_SECONDS + 1, 40, True, pos_age=pos_age)
        assert (rec["status"], rec["is_offline"], rec["position_stale"]) == ("OFFLINE", True, False), rec
    rec, _ = _live(60, 40, True, pos_age=2 * 86400)  # recent messages, 2-day-old fix: not OFFLINE
    assert (rec["status"], rec["connection_status"], rec["is_offline"], rec["telemetry_stale"]) == ("MOVING", "STABLE", False, True), rec
    rec, _ = _live(CONN_DELAYED_SECONDS, 40, True, pos_age=60)  # message-age rule unchanged up to 12 h
    assert (rec["status"], rec["connection_status"], rec["position_stale"], rec["telemetry_stale"]) == ("MOVING", "DELAYED", False, True), rec
    rec, _ = _live(CONN_STABLE_SECONDS + 1, 40, True, pos_age=3600)  # message-delayed too: not position_stale
    assert (rec["status"], rec["connection_status"], rec["position_stale"], rec["telemetry_stale"]) == ("MOVING", "DELAYED", False, True), rec
    print("[PASS] R2: OFFLINE is still decided by message age only (> 12 h); a stale fix with recent messages is "
          "MOVING with telemetry_stale, never OFFLINE.")


def test_stale_gps_position_counts_health_alerts():
    """R2 records through fleet counts, Vehicle Health and Alerts."""
    cases = {  # vid: (message age, speed, ignition, position age) -> expected live status
        1: ((30, 40, True, 30), "MOVING"), 2: ((60, 14, False, 21738), "MOVING"),  # stale fix: MOVING, telemetry_stale
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
    expected_counts = {"moving": 2, "idling": 1, "stopped": 1, "delayed": 1, "offline": 1}
    partition = ("moving", "idling", "stopped", "offline")
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
        assert sum(block[k] for k in partition) == block.get("total_vehicles", block.get("total")) == 5, (where, block)
    by_id = {v["vehicle_id"]: v for v in health["vehicles"]}
    # Phase 3 P3: messages are recent, so connectivity stays STABLE, but the stale fix makes GPS quality STALE with -15
    assert by_id[2]["connectivity"]["status"] == "STABLE" and by_id[2]["gps"]["status"] == "STALE", by_id[2]
    assert by_id[2]["health_score"] == by_id[1]["health_score"] - 15, (by_id[1]["health_score"], by_id[2]["health_score"])
    assert by_id[2]["gps"]["seconds_since_position"] == 21738, by_id[2]["gps"]
    assert by_id[2]["status"] == "MOVING", "Health shows the unit's last known status"
    # D3: the idler and the parked unit with old fixes are not GPS-stale
    assert by_id[3]["gps"]["status"] != "STALE" and by_id[4]["gps"]["status"] != "STALE", (by_id[3], by_id[4])
    conn_alerts = {(a["vehicle_id"], a["alert_type"]) for a in alerts["alerts"]
                   if a["alert_type"] in ("TELEMETRY_DELAYED", "DEVICE_OFFLINE")}
    assert conn_alerts == {(5, "DEVICE_OFFLINE")}, conn_alerts
    stale_alerts = [a for a in alerts["alerts"] if a["alert_type"] == "GPS_POSITION_STALE"]
    assert [(a["vehicle_id"], a["severity"]) for a in stale_alerts] == [(2, "WARNING")], stale_alerts
    assert stale_alerts[0]["timestamp"] == NOW - 21738 and "362m" in stale_alerts[0]["title"], stale_alerts[0]
    assert health["connectivity_warnings"] == 1 and health["gps_warnings"] == 1, health
    warning = {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "DELAYED"}
    critical = {v["vehicle_id"] for v in health["vehicles"] if v["connectivity"]["status"] == "OFFLINE"}
    assert warning == set() and critical == {5}, (warning, critical)
    print("[PASS] R2/P3: fleet-health, groups and AI counts partition the fleet with the stale-fix unit as moving "
          "(and counted in delayed); "
          "Health marks its GPS STALE (-15, one GPS warning) with one GPS_POSITION_STALE alert at the fix time; "
          "connectivity unchanged; stale-fix idler/parked unit not flagged (D3).")


def test_health_stale_position_no_double_penalty():
    """P3: GPS STALE only adds -15 when connectivity is STABLE; a delayed/offline unit keeps its single penalty."""
    def health(age, pos_age, speed=40, ign=True):
        rec, _ = _live(age, speed, ign, pos_age=pos_age)
        raw = {"id": 7, "nm": "T7", "lmsg": {"t": NOW - age}}
        with patch("app.fleet_analytics.time.time", return_value=NOW):
            return rec, fa.evaluate_vehicle_health(rec, raw_unit=raw, calc_data={})
    _, fresh = health(30, 30)
    base = fresh["health_score"]
    rec, h = health(60, 3600)
    assert (h["gps"]["status"], h["connectivity"]["status"], h["health_score"]) == ("STALE", "STABLE", base - 15), h
    rec, h = health(CONN_STABLE_SECONDS + 1, 3600)  # connection already DELAYED
    assert h["gps"]["status"] != "STALE" and h["connectivity"]["status"] == "DELAYED" and h["health_score"] == base - 15, h
    rec, h = health(CONN_DELAYED_SECONDS + 1, 3600)  # OFFLINE
    assert h["gps"]["status"] != "STALE" and h["connectivity"]["status"] == "OFFLINE" and h["health_score"] == base - 35, h
    for speed, ign in ((0, True), (0, False)):  # D3: stationary units are never position-stale
        rec, h = health(60, 3 * 3600, speed, ign)
        assert h["gps"]["status"] != "STALE" and h["health_score"] == base, (speed, ign, h)
    rec, h = health(60, CONN_STABLE_SECONDS)  # boundary: exactly 10 min is not stale
    assert h["gps"]["status"] != "STALE" and h["health_score"] == base, h
    print("[PASS] P3: STALE GPS (-15) only with a STABLE connection; DELAYED (-15) / OFFLINE (-35) not doubled; "
          "stationary units and the 600 s boundary not flagged.")


_NO_POS = object()


def test_position_timestamp_malformed_and_valid():
    """
    P1 hardening: every pos.t shape in one fleet call. Missing or malformed values (None, bool, non-numeric, NaN/inf,
    <= 0 incl. Wialon's 0 for "no time", a non-dict pos) give null time/age and no staleness, and never fail the other
    units; valid numbers (int, float, numeric string) are exposed as int epoch seconds and keep the R2 rule.
    """
    cases = [  # (pos value, expected position_timestamp, expected status for a fresh message moving at 40 km/h)
        (_NO_POS, None, "MOVING"), (None, None, "MOVING"), ({}, None, "MOVING"), ("garbage", None, "MOVING"),
        ({"t": None}, None, "MOVING"), ({"t": 0}, None, "MOVING"), ({"t": -5}, None, "MOVING"),
        ({"t": "abc"}, None, "MOVING"), ({"t": ""}, None, "MOVING"), ({"t": True}, None, "MOVING"),
        ({"t": float("nan")}, None, "MOVING"), ({"t": float("inf")}, None, "MOVING"), ({"t": [1]}, None, "MOVING"),
        ({"t": {"v": 1}}, None, "MOVING"),
        ({"t": NOW - 30}, NOW - 30, "MOVING"),
        ({"t": NOW - CONN_STABLE_SECONDS}, NOW - CONN_STABLE_SECONDS, "MOVING"),
        ({"t": NOW - CONN_STABLE_SECONDS - 1}, NOW - CONN_STABLE_SECONDS - 1, "STALE"),
        ({"t": float(NOW - 3600) + 0.7}, NOW - 3600, "STALE"),
        ({"t": str(NOW - 3600)}, NOW - 3600, "STALE"),
    ]  # "STALE": MOVING with position_stale / telemetry_stale
    units, calc = [], []
    for vid, (pos, _, _) in enumerate(cases, start=100):
        unit = {"id": vid, "nm": f"T{vid}", "sens": IGNITION, "lmsg": {"t": NOW - 30}}
        if pos is not _NO_POS:
            unit["pos"] = pos
        units.append(unit)
        calc.append({"i": vid, "pos": {"s": 40, "y": 25.2, "x": 55.3}, "sensors": {"1": {"value": 1}}})
    with patch("app.vehicles.now_timestamp", return_value=NOW), \
            patch("app.vehicles.get_vehicle_history", MagicMock(return_value=[])):
        fleet = vehicles.get_vehicle_live_data("sid", units, calc, groups=[])
    assert len(fleet) == len(cases), "one malformed record never drops or fails the fleet"
    for rec, (pos, want_ts, want_status) in zip(fleet, cases):
        want_age = None if want_ts is None else NOW - want_ts
        got = (rec["position_timestamp"], rec["seconds_since_position"], rec["status"], rec["position_stale"],
               rec["telemetry_stale"])
        stale = want_status == "STALE"
        assert got == (want_ts, want_age, "MOVING", stale, stale), (pos, got)
        assert rec["position_timestamp"] is None or type(rec["position_timestamp"]) is int, (pos, rec["position_timestamp"])
        assert (rec["seconds_since_update"], rec["connection_status"]) == (30, "STABLE"), (pos, rec)
    # D3 unchanged with a malformed or valid old time: stationary units are never position-stale
    for pos in ({"t": "abc"}, {"t": NOW - 3 * 3600}):
        for speed, ign, live in ((0, True, "IDLING"), (0, False, "STOPPED")):
            unit = {"id": 9, "nm": "T9", "sens": IGNITION, "lmsg": {"t": NOW - 30}, "pos": pos}
            c = [{"i": 9, "pos": {"s": speed, "y": 25.2, "x": 55.3}, "sensors": {"1": {"value": 1 if ign else 0}}}]
            vehicles.idle_start_times.pop(9, None)
            with patch("app.vehicles.now_timestamp", return_value=NOW), \
                    patch("app.vehicles.get_vehicle_history", MagicMock(return_value=[])):
                rec = vehicles.get_vehicle_live_data("sid", [unit], c, groups=[])[0]
            vehicles.idle_start_times.pop(9, None)
            assert (rec["status"], rec["position_stale"]) == (live, False), (pos, live, rec["status"])
    for vid in range(100, 100 + len(cases)):
        vehicles.idle_start_times.pop(vid, None)
    print(f"[PASS] P1: {len(cases)} pos.t shapes in one fleet call: missing/malformed/<=0 -> null time and age, not "
          "stale, other units unaffected; int/float/numeric-string -> int epoch, R2 boundary 600/601 s kept; D3 kept.")


def test_last_message_timestamp_malformed_and_valid():
    """
    Every lmsg.t shape in one /api/vehicles call. Missing or malformed values (no lmsg, None, non-dict lmsg, None, bool,
    non-numeric, NaN/inf, <= 0) fall back to calc_last's position time as before; with none either, the unit is OFFLINE
    with a null message time and age (like a unit that never reported) and the other units are unaffected. Valid
    numbers (int, float, numeric string) keep the existing STABLE/DELAYED/OFFLINE and status rules.
    """
    cases = [  # (lmsg value, calc_last pos.t, expected last_message_timestamp, connection, status)
        (_NO_POS, None, None, "OFFLINE", "OFFLINE"), (None, None, None, "OFFLINE", "OFFLINE"),
        ("garbage", None, None, "OFFLINE", "OFFLINE"), ({}, None, None, "OFFLINE", "OFFLINE"),
        ({"t": None}, None, None, "OFFLINE", "OFFLINE"), ({"t": 0}, None, None, "OFFLINE", "OFFLINE"),
        ({"t": -5}, None, None, "OFFLINE", "OFFLINE"), ({"t": "abc"}, None, None, "OFFLINE", "OFFLINE"),
        ({"t": ""}, None, None, "OFFLINE", "OFFLINE"), ({"t": True}, None, None, "OFFLINE", "OFFLINE"),
        ({"t": float("nan")}, None, None, "OFFLINE", "OFFLINE"), ({"t": float("inf")}, None, None, "OFFLINE", "OFFLINE"),
        ({"t": [1]}, None, None, "OFFLINE", "OFFLINE"),
        # Fallback to calc_last's position time, as before, when lmsg.t is missing or malformed
        ({"t": "abc"}, NOW - 30, NOW - 30, "STABLE", "MOVING"), (None, NOW - 30, NOW - 30, "STABLE", "MOVING"),
        ({"t": "abc"}, "bad", None, "OFFLINE", "OFFLINE"),
        # Valid times: existing thresholds unchanged
        ({"t": NOW - 30}, None, NOW - 30, "STABLE", "MOVING"),
        ({"t": str(NOW - 30)}, None, NOW - 30, "STABLE", "MOVING"),
        ({"t": float(NOW - 30) + 0.9}, None, NOW - 30, "STABLE", "MOVING"),
        ({"t": NOW - CONN_STABLE_SECONDS}, None, NOW - CONN_STABLE_SECONDS, "STABLE", "MOVING"),
        ({"t": NOW - CONN_STABLE_SECONDS - 1}, None, NOW - CONN_STABLE_SECONDS - 1, "DELAYED", "MOVING"),
        ({"t": NOW - CONN_DELAYED_SECONDS - 1}, None, NOW - CONN_DELAYED_SECONDS - 1, "OFFLINE", "OFFLINE"),
    ]
    units, calc = [], []
    for vid, (lmsg, calc_t, _, _, _) in enumerate(cases, start=200):
        unit = {"id": vid, "nm": f"T{vid}", "sens": IGNITION}
        if lmsg is not _NO_POS:
            unit["lmsg"] = lmsg
        units.append(unit)
        calc_pos = {"s": 40, "y": 25.2, "x": 55.3}
        if calc_t is not None:
            calc_pos["t"] = calc_t
        calc.append({"i": vid, "pos": calc_pos, "sensors": {"1": {"value": 1}}})
    with ExitStack() as stack:
        main._cache.clear()
        stack.enter_context(patch("app.vehicles.now_timestamp", return_value=NOW))
        stack.enter_context(patch("app.vehicles.get_vehicle_history", MagicMock(return_value=[])))
        stack.enter_context(patch("app.vehicles.get_vehicle_groups", return_value=[]))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=units))
        stack.enter_context(patch("app.main.get_calculated_data", return_value=calc))
        body = main.get_vehicles()  # the /api/vehicles handler
    main._cache.clear()
    assert body["count"] == len(cases) == len(body["vehicles"]), "one malformed record never drops or fails the fleet"
    for rec, (lmsg, calc_t, want_ts, want_conn, want_status) in zip(body["vehicles"], cases):
        want_age = None if want_ts is None else NOW - want_ts
        got = (rec["last_message_timestamp"], rec["seconds_since_update"], rec["connection_status"], rec["status"])
        assert got == (want_ts, want_age, want_conn, want_status), (lmsg, calc_t, got)
        assert rec["is_offline"] is (want_conn == "OFFLINE"), (lmsg, rec)
        assert rec["last_known_status"] == (None if want_ts is None else "MOVING"), (lmsg, rec["last_known_status"])
        assert rec["last_message_timestamp"] is None or type(rec["last_message_timestamp"]) is int, (lmsg, rec)
    for vid in range(200, 200 + len(cases)):
        vehicles.idle_start_times.pop(vid, None)
    print(f"[PASS] lmsg.t: {len(cases)} shapes in one /api/vehicles call: missing/malformed -> calc_last fallback, else "
          "OFFLINE with null time/age, other units unaffected; int/float/numeric-string keep 600 s / 12 h thresholds.")


def test_health_alerts_deep_analytics_malformed_lmsg():
    """
    Fleet health, alerts and vehicle deep analytics parse the raw lmsg.t like the live view: a missing or malformed
    value (or a non-dict lmsg) is OFFLINE with a null last_message_ts / age, never a crash, and never a guessed time;
    valid values keep their STABLE / DELAYED / D3 / OFFLINE results.
    """
    cases = {  # vid: (lmsg, speed, ignition) -> (connectivity, last_message_ts)
        301: ({"t": "abc"}, 40, True, "OFFLINE", None), 302: ("garbage", 40, True, "OFFLINE", None),
        303: ({"t": float("nan")}, 0, False, "OFFLINE", None), 304: ({"t": True}, 0, False, "OFFLINE", None),
        305: ({"t": None}, 0, False, "OFFLINE", None), 306: (_NO_POS, 0, False, "OFFLINE", None),
        307: ({"t": NOW - 30}, 40, True, "STABLE", NOW - 30), 308: ({"t": str(NOW - 30)}, 40, True, "STABLE", NOW - 30),
        309: ({"t": NOW - CONN_STABLE_SECONDS - 1}, 40, True, "DELAYED", NOW - CONN_STABLE_SECONDS - 1),
        310: ({"t": NOW - 7200}, 0, False, "STABLE", NOW - 7200),  # D3: parked, older message, not delayed
        311: ({"t": NOW - CONN_DELAYED_SECONDS - 1}, 40, True, "OFFLINE", NOW - CONN_DELAYED_SECONDS - 1),
    }
    units, calc = [], []
    for vid, (lmsg, speed, ign, _, _) in cases.items():
        unit = {"id": vid, "nm": f"T{vid}", "sens": IGNITION}
        if lmsg is not _NO_POS:
            unit["lmsg"] = lmsg
        units.append(unit)
        calc.append({"i": vid, "pos": {"s": speed, "y": 25.2, "x": 55.3}, "sensors": {"1": {"value": 1 if ign else 0}}})
    with ExitStack() as stack:
        fa._analytics_cache.clear()
        stack.enter_context(patch("app.vehicles.now_timestamp", return_value=NOW))
        stack.enter_context(patch("app.vehicles.get_vehicle_history", MagicMock(return_value=[])))
        stack.enter_context(patch("app.vehicles.get_vehicle_groups", return_value=[]))
        stack.enter_context(patch("app.fleet_analytics.time.time", return_value=NOW))
        stack.enter_context(patch("app.fleet_analytics.login", return_value="sid"))
        stack.enter_context(patch("app.fleet_analytics.get_vehicle_groups", return_value=[]))
        stack.enter_context(patch("app.fleet_analytics.get_all_vehicles", return_value=units))
        stack.enter_context(patch("app.fleet_analytics.get_calculated_data",
                                  side_effect=lambda sid, vs: [c for c in calc if c["i"] in {v["id"] for v in vs}]))
        stack.enter_context(patch("app.fleet_analytics.get_vehicle_history", return_value=[]))
        stack.enter_context(patch("app.fleet_analytics.get_safety_analytics",
                                  return_value={"summary": {"total_events": 0}, "vehicles": [], "events": []}))
        health = fa.get_fleet_health_analytics(use_cache=False)
        alerts = fa._build_fleet_alerts(use_cache=False)
        deep = {vid: fa.get_single_vehicle_deep_analytics(vid) for vid in (301, 302, 307)}
    fa._analytics_cache.clear()
    by_id = {v["vehicle_id"]: v for v in health["vehicles"]}
    assert set(by_id) == set(cases), "every unit evaluated; a malformed one never fails the others"
    for vid, (lmsg, _, _, want_conn, want_ts) in cases.items():
        conn = by_id[vid]["connectivity"]
        want_age = None if want_ts is None else NOW - want_ts
        assert (conn["status"], conn["last_message_ts"], conn["seconds_since_update"]) == (want_conn, want_ts, want_age), (vid, lmsg, conn)
        if want_ts is None:
            assert conn["last_seen_formatted"] == "—", conn
    conn_alerts = {a["vehicle_id"]: (a["alert_type"], a["timestamp"]) for a in alerts["alerts"]
                   if a["alert_type"] in ("DEVICE_OFFLINE", "TELEMETRY_DELAYED")}
    assert conn_alerts == {**{vid: ("DEVICE_OFFLINE", None) for vid in range(301, 307)},
                           309: ("TELEMETRY_DELAYED", NOW - CONN_STABLE_SECONDS - 1),
                           311: ("DEVICE_OFFLINE", NOW - CONN_DELAYED_SECONDS - 1)}, conn_alerts
    assert all("—h" in a["title"] for a in alerts["alerts"] if a["vehicle_id"] in range(301, 307)), "unknown age, not 0"
    for vid in (301, 302):
        d = deep[vid]
        assert d["found"] and (d["status"], d["is_offline"]) == ("OFFLINE", True), (vid, d)
        assert (d["health"]["connectivity"]["status"], d["health"]["connectivity"]["last_message_ts"]) == ("OFFLINE", None), d["health"]
    assert deep[307]["health"]["connectivity"]["status"] == "STABLE" and deep[307]["status"] == "MOVING", deep[307]
    print("[PASS] lmsg.t in Health: malformed/missing/non-dict -> OFFLINE with null time/age and DEVICE_OFFLINE "
          "(unknown hours, null timestamp) in fleet health, alerts and deep analytics, no crash; valid times keep "
          "STABLE / DELAYED / D3 / OFFLINE.")


def _sats_for(rating):
    """A satellite count giving the requested GPS rating, from the configured thresholds (not a fixed number)."""
    th = fa.HEALTH_THRESHOLDS
    return th["GPS_SATELLITES_EXCELLENT"] if rating == "EXCELLENT" else th["GPS_SATELLITES_GOOD"] - 1


def test_health_stale_keeps_satellite_deduction():
    """P3: with a known satellite count, STALE keeps that count's deduction and adds exactly one -15."""
    def health(pos_age, sats):
        rec, _ = _live(30, 40, True, pos_age=pos_age, sats=sats)
        raw = {"id": 7, "nm": "T7", "lmsg": {"t": NOW - 30}}
        with patch("app.fleet_analytics.time.time", return_value=NOW):
            return fa.evaluate_vehicle_health(rec, raw_unit=raw, calc_data={})
    scores = {}
    for rating in ("EXCELLENT", "WEAK"):
        sats = _sats_for(rating)
        fresh, stale = health(30, sats), health(3600, sats)
        assert fresh["gps"]["status"] == rating and fresh["gps"]["satellites"] == sats, fresh["gps"]
        assert stale["gps"]["status"] == "STALE" and stale["gps"]["satellites"] == sats, stale["gps"]
        assert stale["health_score"] == fresh["health_score"] - 15, (rating, fresh["health_score"], stale["health_score"])
        scores[rating] = (fresh["health_score"], stale["health_score"])
    # The satellite deduction (EXCELLENT 0, WEAK -25) is kept under STALE: the gap between the ratings is unchanged
    assert scores["EXCELLENT"][0] - scores["WEAK"][0] == scores["EXCELLENT"][1] - scores["WEAK"][1] == 25, scores
    print("[PASS] P3: a stale fix with known satellites keeps the satellite deduction (EXCELLENT 0 / WEAK -25) and "
          "adds exactly one -15 STALE penalty; satellites still reported.")


def test_stale_weak_unit_gets_one_stale_alert():
    """P3: a unit that is STALE and would otherwise be GPS-WEAK gets GPS_POSITION_STALE only, never GPS_WEAK too."""
    weak = _sats_for("WEAK")
    cases = {1: (30, 30), 2: (30, 3600)}  # vid: (message age, position age); both have a weak satellite count
    fleet, raws = [], []
    for vid, (age, pos_age) in cases.items():
        rec, _ = _live(age, 40, True, vid=vid, pos_age=pos_age, sats=weak)
        fleet.append(dict(rec, group="Logistics", groups=["Logistics"]))
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
    gps_alerts = sorted((a["vehicle_id"], a["alert_type"]) for a in alerts["alerts"]
                        if a["alert_type"] in ("GPS_WEAK", "GPS_POSITION_STALE"))
    assert gps_alerts == [(1, "GPS_WEAK"), (2, "GPS_POSITION_STALE")], gps_alerts
    assert health["gps_warnings"] == 2 and alerts["by_vehicle"] == {"1": 1, "2": 1}, (health["gps_warnings"], alerts["by_vehicle"])
    assert len({a["id"] for a in alerts["alerts"]}) == len(alerts["alerts"]), "alert ids unique"
    print("[PASS] P3: a weak-satellite unit with a stale fix gets one GPS_POSITION_STALE alert (no GPS_WEAK); a fresh "
          "weak unit keeps GPS_WEAK; one GPS warning each; alert ids unique.")


def test_ai_tools_report_message_and_fix_age():
    """P4: AI vehicle tools return last_known_status, message age, GPS fix age and whether speed is last-reported."""
    cases = {1: (30, 40, True, 30), 2: (60, 14, False, 21738), 3: (CONN_STABLE_SECONDS + 60, 0, True, None),
             4: (CONN_DELAYED_SECONDS + 1, 0, False, None)}
    fleet = [_live(age, speed, ign, vid=vid, pos_age=pos_age)[0] for vid, (age, speed, ign, pos_age) in cases.items()]
    with ExitStack() as stack:
        stack.enter_context(patch("app.ai.tools.login", return_value="sid"))
        stack.enter_context(patch("app.ai.tools.get_all_vehicles", return_value=[]))
        stack.enter_context(patch("app.ai.tools.get_calculated_data", return_value=[]))
        stack.enter_context(patch("app.ai.tools.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in fleet]))
        by_status = {v["vehicle_id"]: v for v in ai_tools.tool_get_vehicles_by_status("ALL")["vehicles"]}
        single = ai_tools.tool_get_vehicle_status(2)
    expected = {  # vid: (last_known_status, message_age_minutes, gps_fix_age_minutes, gps_position_stale, last-reported)
        1: ("MOVING", 0.5, 0.5, False, False), 2: ("MOVING", 1.0, 362.3, True, True),
        3: ("IDLING", round((CONN_STABLE_SECONDS + 60) / 60, 1), None, False, True),
        4: ("STOPPED", round((CONN_DELAYED_SECONDS + 1) / 60, 1), None, False, True),
    }
    keys = ("last_known_status", "message_age_minutes", "gps_fix_age_minutes", "gps_position_stale", "speed_is_last_reported")
    for vid, want in expected.items():
        assert tuple(by_status[vid][k] for k in keys) == want, (vid, by_status[vid])
    assert single["found"] and tuple(single[k] for k in keys) == expected[2] and single["speed"] == 14, single
    descriptions = {d["function"]["name"]: d["function"]["description"] for d in ai_tools.TOOL_DEFINITIONS}
    for name in ("get_vehicle_status", "get_vehicles_by_status"):
        assert "gps_fix_age_minutes" in descriptions[name] and "last reported" in descriptions[name], name
    assert "GPS_POSITION_STALE" in descriptions["get_fleet_alerts"], descriptions["get_fleet_alerts"]
    print("[PASS] P4: AI vehicle tools return last_known_status, message/GPS fix ages, gps_position_stale and "
          "speed_is_last_reported; tool descriptions forbid presenting stale speed/position as current.")


TESTS = [
    test_live_and_stale_states,
    test_stale_idler_keeps_idling_with_capped_timer,
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
    test_health_stale_position_no_double_penalty,
    test_position_timestamp_malformed_and_valid,
    test_last_message_timestamp_malformed_and_valid,
    test_health_alerts_deep_analytics_malformed_lmsg,
    test_health_stale_keeps_satellite_deduction,
    test_stale_weak_unit_gets_one_stale_alert,
    test_ai_tools_report_message_and_fix_age,
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
