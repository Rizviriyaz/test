"""
Regression tests for the Idle Dashboard idle events (Wialon Idle Report-Unit template, /api/idle/events) (MOCKED / offline).

  - the reader takes start/end from the time cells' epochs (`v`), Wialon's own duration, a validated location, the
    driver (empty -> None) and the department, and identifies an event by unit and start
  - an event still in progress is ongoing only while the unit's Idling sensor is on and its telemetry is fresh; a
    later read updates the same event (same id, later end, longer duration), never duplicates it
  - once the sensor is off the event is completed with Wialon's final end and duration
  - stale telemetry or an event that ended long before the read is never "currently idling"
  - a unit whose report fails is unknown (None), not zero; if every unit fails the last good result is served stale
  - windows are limited to the last 7 UAE calendar days
  - units without messages in the window are not reported on (no report run, zero events)
  - /api/idle/fleet-analytics (Fleet Overview's Engine Hours KPIs) never uses the idle events template

The row shapes are the ones Wialon returned live for template 28 (2026-11-02). Every outbound HTTP call is blocked by
OfflineGuard. Run from Backend/:
    python scripts/test_idle_events_template.py
"""
import os
import sys
import time
import traceback
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402

from fastapi import HTTPException  # noqa: E402

import app.idle_events as ie  # noqa: E402
import app.main as main  # noqa: E402
from app.utils.datetime_helpers import UAE_TZ  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402
from app.wialon_reports import IDLE_REPORT_RESOURCE_ID, TEMPLATE_ID_UNIT_IDLE_EVENTS  # noqa: E402

UNIT_A, UNIT_B, UNIT_C = 600446509, 600446322, 600446312  # C: no message today
HEADER = ["Grouping", "Department", "Sensor", "Activated", "Deactivated", "Idling Duration", "Location", "Driver"]
HEADER_TYPE = ["", "user_column", "sensor_name", "time_begin", "time_end", "duration", "location", "driver"]
LAT, LNG, ADDR = 24.9629933, 55.2063866, "74 Street, Dubai Investments Park 2, Dubai, UAE"


def today_start():
    return int(datetime.now(UAE_TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def fmt(sec):
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def subrow(uid, start, end, duration=None, driver="", lat=LAT, lng=LNG, addr=ADDR):
    duration = end - start if duration is None else duration
    tcell = lambda ts: {"t": datetime.fromtimestamp(ts, timezone.utc).strftime("%H:%M:%S"), "v": ts, "y": lat, "x": lng, "u": uid}
    return {"t1": start, "t2": end, "d": 0, "mrk": 0,
            "c": ["day", "Transport Dept", "Idling", tcell(start), tcell(end), fmt(duration),
                  {"t": addr, "y": lat, "x": lng, "u": uid}, driver]}


class FakeIdleReport:
    """Wialon stand-in for the Idle Report-Unit template: one day row per unit holding its event sub-rows."""

    def __init__(self, events_by_unit, failing=(), sensor=None):
        self.events_by_unit = events_by_unit
        self.failing = set(failing)
        self.sensor = sensor if sensor is not None else {}
        self.current = None
        self.exec_calls = []

    def __call__(self, svc, params, sid=None, **kwargs):
        if svc == "report/exec_report":
            assert params["reportResourceId"] == IDLE_REPORT_RESOURCE_ID
            assert params["reportTemplateId"] == TEMPLATE_ID_UNIT_IDLE_EVENTS
            uid = params["reportObjectId"]
            self.exec_calls.append(uid)
            if uid in self.failing:
                return {"error": 5}
            self.current = self.events_by_unit.get(uid, [])
            if not self.current:
                return {"reportResult": {"tables": []}}
            return {"reportResult": {"tables": [{"name": "unit_digital_sensors", "rows": 1, "level": 2,
                                                 "header": HEADER, "header_type": HEADER_TYPE}]}}
        if svc == "report/get_result_rows":
            subs = self.current
            return [{"n": 0, "t1": subs[0]["t1"], "t2": subs[-1]["t2"], "d": len(subs), "c": ["day"]}]
        if svc == "report/get_result_subrows":
            return list(self.current)
        if svc == "core/batch":
            return [list(self.current) for _ in params["params"]]
        if svc == "report/cleanup_result":
            return {}
        if svc == "unit/calc_last":
            return [{"i": uid, "sensors": {"15": {"value": self.sensor.get(uid, 0)}}} for uid in params["itemIds"]]
        raise AssertionError(f"unexpected Wialon call {svc}")


UNITS = [{"id": uid, "nm": name, "sens": {"15": {"n": "Idling"}}}
         for uid, name in ((UNIT_A, "58523"), (UNIT_B, "11111"), (UNIT_C, "22222"))]


def live(age_a=20):
    now = int(time.time())
    return [
        {"vehicle_id": UNIT_A, "vehicle_name": "58523", "status": "IDLING", "seconds_since_update": age_a,
         "last_message_timestamp": now - age_a, "idle_start": now - 600, "idle_duration": 600,
         "idle_duration_status": "known"},
        {"vehicle_id": UNIT_B, "vehicle_name": "11111", "status": "STOPPED", "seconds_since_update": 30,
         "last_message_timestamp": now - 30},
        {"vehicle_id": UNIT_C, "vehicle_name": "22222", "status": "OFFLINE", "seconds_since_update": 10 * 86400,
         "last_message_timestamp": now - 10 * 86400},
    ]


GROUPS = [{"id": 1, "nm": "Transport", "u": [UNIT_A, UNIT_B]}, {"id": 600456397, "nm": "All Unit", "u": [UNIT_A, UNIT_B, UNIT_C]}]


def run(stack, engine, age_a=20, **kwargs):
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.idle_events.wialon_request", engine))
    stack.enter_context(patch("app.idle_events.login", side_effect=lambda session=None: f"sid-{session}"))
    stack.enter_context(patch("app.main.login", return_value="sid"))
    stack.enter_context(patch("app.main.get_vehicle_groups", return_value=GROUPS))
    stack.enter_context(patch("app.main.get_all_vehicles", return_value=UNITS))
    stack.enter_context(patch("app.main.get_calculated_data", return_value=[]))
    stack.enter_context(patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live(age_a)))
    return main.get_idle_events_analytics(**{"timeframe": "today", **kwargs})


def reset():
    main._cache.clear()
    main._failure_backoff.clear()
    ie.clear_unit_results()


def test_reader_fields_and_identity():
    reset()
    now = int(time.time())
    start = now - 1260
    engine = FakeIdleReport({UNIT_A: [subrow(UNIT_A, start - 3000, start - 2900, driver=""),
                                      subrow(UNIT_A, start, now - 30, driver="Ahmed K")]})
    with ExitStack() as stack:
        res = run(stack, engine)
    evs = sorted((e for e in res["events"] if e["vehicle_id"] == UNIT_A), key=lambda e: e["start_time"])
    check(len(evs) == 2, "every Wialon sub-row is one idle event")
    ev = evs[1]
    check(ev["id"] == f"{UNIT_A}-{start}", "event identity is unit id + start epoch")
    check(ev["start_time"] == start and ev["end_time"] == now - 30, "start/end are the time cells' epochs (v), not their UTC text")
    check(ev["duration"] == now - 30 - start, "duration is Wialon's Idling Duration cell")
    check(ev["location"] == {"latitude": LAT, "longitude": LNG} and ev["address"] == ADDR and ev["location_verified"],
          "location and address come from the location cell")
    check(ev["driver"] == "Ahmed K" and evs[0]["driver"] is None, "driver text is kept; an empty driver cell is None")
    check(ev["department"] == "Transport Dept" and ev["sensor_name"] == "Idling", "department and sensor are kept")


def test_ongoing_updates_then_closes():
    reset()
    now = int(time.time())
    start = now - 1260
    events = {UNIT_A: [subrow(UNIT_A, start, now - 60)]}
    engine = FakeIdleReport(events, sensor={UNIT_A: 1})
    with ExitStack() as stack:
        first = run(stack, engine)
    a1 = [e for e in first["events"] if e["vehicle_id"] == UNIT_A]
    row1 = next(v for v in first["by_vehicle"] if v["vehicle_id"] == UNIT_A)
    check(len(a1) == 1 and a1[0]["ongoing"], "an event ending at the latest message with the Idling sensor on is ongoing")
    check(first["kpis"]["currently_idling_vehicles"] == 1 and row1["currently_idling"],
          "currently idling comes from the ongoing template event")
    check(row1["idle_start"] == start and row1["idle_duration"] == a1[0]["duration"],
          "the vehicle's idle start/duration are the ongoing event's latest reading")
    grp = next(g for g in first["by_group"] if g["group_id"] == 1)
    check(grp["currently_idling"] == 1, "group currently idling counts the ongoing event")

    # Next poll: Wialon returns the same event with a later end and longer duration
    events[UNIT_A] = [subrow(UNIT_A, start, now)]
    with ExitStack() as stack:
        second = run(stack, engine, refresh=True)
    a2 = [e for e in second["events"] if e["vehicle_id"] == UNIT_A]
    check(len(a2) == 1 and a2[0]["id"] == a1[0]["id"], "a repeated read updates the same event, no duplicate")
    check(a2[0]["start_time"] == start and a2[0]["end_time"] > a1[0]["end_time"] and a2[0]["duration"] > a1[0]["duration"],
          "start unchanged, end advances and duration grows while idling continues")

    # Vehicle moved off: sensor off, Wialon closes the event at its final end
    engine.sensor[UNIT_A] = 0
    final_end = now
    with ExitStack() as stack:
        third = run(stack, engine, refresh=True)
    a3 = [e for e in third["events"] if e["vehicle_id"] == UNIT_A]
    check(len(a3) == 1 and not a3[0]["ongoing"], "with the Idling sensor off the event is completed")
    check(a3[0]["end_time"] == final_end and a3[0]["duration"] == final_end - start,
          "a completed event keeps Wialon's final end and duration")
    check(third["kpis"]["currently_idling_vehicles"] == 0, "a completed event no longer counts as currently idling")


def test_stale_never_currently_idling():
    reset()
    now = int(time.time())
    engine = FakeIdleReport({UNIT_A: [subrow(UNIT_A, now - 900, now - 60)]}, sensor={UNIT_A: 1})
    with ExitStack() as stack:
        res = run(stack, engine, age_a=main.CONN_DELAYED_SECONDS // 2)
    check(res["kpis"]["currently_idling_vehicles"] == 0, "stale telemetry is never currently idling")
    reset()
    engine = FakeIdleReport({UNIT_A: [subrow(UNIT_A, now - 4000, now - 3000)]}, sensor={UNIT_A: 1})
    with ExitStack() as stack:
        res = run(stack, engine)
    check(res["kpis"]["currently_idling_vehicles"] == 0 and not res["events"][0]["ongoing"],
          "an event that ended long before the read is not ongoing even with the sensor on")


def test_unit_failure_is_unknown_not_zero():
    reset()
    now = int(time.time())
    engine = FakeIdleReport({UNIT_A: [subrow(UNIT_A, now - 900, now - 600)]}, failing={UNIT_B})
    with ExitStack() as stack:
        res = run(stack, engine)
    b = next(v for v in res["by_vehicle"] if v["vehicle_id"] == UNIT_B)
    check(b["total_idle_seconds"] is None and b["idle_events"] is None and b["events_error"],
          "a failed unit report leaves that vehicle unknown (None), never zero")
    check(res["events_complete"] is False and res["kpis"]["idle_event_count"] is None and res["kpis"]["total_idle_partial"],
          "fleet event KPIs are unknown / partial when a vehicle's events are missing")
    check(res["events_unavailable_vehicle_ids"] == [UNIT_B], "the failed vehicle is listed")


def test_all_fail_serves_last_good():
    reset()
    now = int(time.time())
    good = FakeIdleReport({UNIT_A: [subrow(UNIT_A, now - 900, now - 600)]})
    with ExitStack() as stack:
        first = run(stack, good)
    check(first["stale"] is False and len(first["events"]) == 1, "first read is live")
    bad = FakeIdleReport({}, failing={UNIT_A, UNIT_B})
    with ExitStack() as stack:
        second = run(stack, bad, refresh=True)
    check(second["stale"] is True and second["events"] == first["events"] and second["retrieved_at"] == first["retrieved_at"],
          "when every report fails the last good result is served, marked stale, with its retrieval time")
    reset()
    with ExitStack() as stack:
        try:
            run(stack, FakeIdleReport({}, failing={UNIT_A, UNIT_B}))
            raised = None
        except (WialonAPIError, HTTPException) as exc:
            raised = exc
    check(raised is not None, "with no earlier result a total failure is an error, never zero idle events")


def test_seven_day_limit_and_no_message_units():
    reset()
    engine = FakeIdleReport({})
    oldest = today_start() - 6 * 86400
    with ExitStack() as stack:
        try:
            run(stack, engine, timeframe=None, from_time=oldest - 1, to_time=int(time.time()))
            status = None
        except HTTPException as exc:
            status = exc
    check(status is not None and status.status_code == 400 and "7 days" in status.detail,
          "a window starting before the last 7 UAE days is rejected with a clear message")
    with ExitStack() as stack:
        res = run(stack, engine, timeframe=None, from_time=oldest, to_time=int(time.time()))
    check(res["timeframe"]["from_time"] == oldest, "a window starting exactly 7 UAE days back is served")
    check(UNIT_C not in engine.exec_calls, "a unit without messages in the window is not reported on")
    c = next(v for v in res["by_vehicle"] if v["vehicle_id"] == UNIT_C)
    check(c["idle_events"] == 0 and c["total_idle_seconds"] == 0, "it has zero events (known), not unknown")


def test_vehicle_scope_reuses_fleet_read():
    reset()
    now = int(time.time())
    engine = FakeIdleReport({UNIT_A: [subrow(UNIT_A, now - 900, now - 600)]})
    with ExitStack() as stack:
        run(stack, engine)
        calls = len(engine.exec_calls)
        res = run(stack, engine, vehicle_id=UNIT_A)
    check(len(engine.exec_calls) == calls, "the drawer's vehicle request reuses the unit's fleet read (no new report)")
    check([e["vehicle_id"] for e in res["events"]] == [UNIT_A] and res["kpis"]["total_vehicles"] == 1,
          "a vehicle-scoped result holds only that vehicle")


def test_fleet_overview_endpoint_untouched():
    reset()
    with patch("app.main.execute_wialon_group_engine_idle_report", side_effect=WialonAPIError("engine")) as engine_rep, \
            patch("app.idle_events.execute_wialon_unit_idle_events_report") as idle_rep, \
            patch("app.main.login", return_value="sid"), \
            patch("app.main.get_vehicle_groups", return_value=GROUPS), \
            patch("app.main.get_all_vehicles", return_value=UNITS), \
            patch("app.main.get_calculated_data", return_value=[]), \
            patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live()):
        try:
            main.get_fleet_idle_analytics(timeframe="today", include_events=False)
        except (WialonAPIError, HTTPException):
            pass
    check(engine_rep.called and not idle_rep.called,
          "/api/idle/fleet-analytics (Fleet Overview) still runs the Engine Hours report, never the idle events template")


TESTS = [
    test_reader_fields_and_identity,
    test_ongoing_updates_then_closes,
    test_stale_never_currently_idling,
    test_unit_failure_is_unknown_not_zero,
    test_all_fail_serves_last_good,
    test_seven_day_limit_and_no_message_units,
    test_vehicle_scope_reuses_fleet_read,
    test_fleet_overview_endpoint_untouched,
]

if __name__ == "__main__":
    guard = OfflineGuard().start()
    failed = 0
    try:
        for test in TESTS:
            try:
                test()
            except Exception:
                failed += 1
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    check(not guard.attempts, "no live network call was attempted")
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} test groups passed")
    sys.exit(1 if failed else 0)
