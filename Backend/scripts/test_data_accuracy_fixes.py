"""
Offline regression tests for the approved data-accuracy fixes (audit F1-F17, MOCKED / offline).

  - zero vs missing: a 0 km odometer is kept, an unknown maximum speed stays None (F15)
  - unknown ignition: a missing or invalid ignition reading is None (Unknown), never Off; live status unchanged (F6)
  - alerts: totals and severity counts cover every harsh-driving event, not only the listed ones (F4)
  - idle without event sub-rows: the duration is kept, the unit's events are unknown, no event is invented (F7)
  - idle events are Wialon engine-on periods (live shape, 2026-10-27): duration is the Idling cell, not the span
  - Idle Analytics groups: a filter never shows a group it did not fully include; General Fleet counts the
    ungrouped vehicles (F2)
  - vehicle names are trimmed before report matching (F11)
  - cache policy: complete / permanently partial results keep the normal TTL; a Wialon failure serves the last good
    result marked stale with its retrieval time, backs off, and recovers; concurrent identical requests run once

Related coverage elsewhere: In motion accepted despite differing Trips mileage (test_driving_time_in_motion.py),
stale vs Offline and idle-timer capping (test_live_status_staleness.py), partial trip / group totals
(test_fleet_trip_metrics.py, test_report_unknown_values.py), transient vs permanent cache classes
(test_report_columns_and_coverage.py, test_trips_mileage_integrity.py), pagination and de-duplication
(test_subrow_pagination.py), reporting-period boundaries (test_idle_cache_key_midnight.py, test_idle_trend_buckets.py).

Wialon is replaced by FakeReportEngine and mocks; every outbound HTTP call is blocked by OfflineGuard. Run from
Backend/:
    python scripts/test_data_accuracy_fixes.py
"""
import os
import sys
import threading
import time
import traceback
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, TEMPLATE_GROUP_ENGINE, TEMPLATE_GROUP_TRIPS, UNITS, FakeReportEngine,
    age_report_caches, default_tables, group_engine_row, group_trip_row, live_records,
)

from fastapi import HTTPException  # noqa: E402

import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402
import app.vehicles as vehicles  # noqa: E402
from app.normalize import INVALID_WIALON_VALUE  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 86400 - 1
NOW = 1_800_000_000


def _patches(stack, engine, units=UNITS, groups=GROUPS, calculated=CALCULATED, live=None):
    main._cache.clear()
    fa._analytics_cache.clear()
    main._failure_backoff.clear()
    live = live or live_records()
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.wialon_reports.get_vehicle_groups", return_value=groups))
    for target in ("app.main", "app.fleet_analytics"):
        # The fixture window is fixed in the past; these tests cover the cache rules of an open (today's) window,
        # so it is treated as open. The closed-window TTL is covered by test_cache_coalescing.py.
        stack.enter_context(patch(f"{target}.closed_window_ttl", side_effect=lambda _eff_to, open_ttl: open_ttl))
        stack.enter_context(patch(f"{target}.login", return_value="sid"))
        stack.enter_context(patch(f"{target}.get_vehicle_groups", return_value=groups))
        stack.enter_context(patch(f"{target}.get_all_vehicles", return_value=units))
        stack.enter_context(patch(f"{target}.get_calculated_data", return_value=calculated))
        stack.enter_context(patch(f"{target}.get_vehicle_live_data", side_effect=lambda *a, **k: [dict(v) for v in live]))


def _idle(**kw):
    params = dict(from_time=FROM_TS, to_time=TO_TS, include_events=True, skip_cache=False)
    params.update(kw)
    return main.get_fleet_idle_analytics(**params)


def _mileage():
    return main.get_dashboard_mileage(timeframe="today", from_time=None, to_time=None, from_date=None, to_date=None,
                                      group_id=None)


def test_zero_odometer_kept_and_unknown_max_speed_none():
    tables = default_tables()
    tables[TEMPLATE_GROUP_TRIPS]["rows"] = [group_trip_row("UNIT-A", 250.75, mx="-----"),
                                            group_trip_row("UNIT-B", 1234.5, mx="95 km/h")]  # no UNIT-C row
    calculated = [{"i": 101, "mileage": {"value": 0.0}}, {"i": 102, "mileage": {"value": 88000.0}}, {"i": 103}]
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=tables), calculated=calculated)
        res = fa.get_trips_mileage_analytics(from_time=FROM_TS, to_time=TO_TS)
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["cumulative_odometer_km"] == 0.0, "a genuine 0 km odometer reading is kept"
    assert by_id[103]["cumulative_odometer_km"] is None, "a missing odometer reading is unknown"
    assert by_id[101]["max_speed_kmh"] is None, "an unreadable maximum speed is unknown, not 0"
    assert by_id[103]["max_speed_kmh"] is None, "no report row: maximum speed unknown, not 0"
    assert by_id[102]["max_speed_kmh"] == 95.0 and res["summary"]["fleet_max_speed_kmh"] == 95.0, res["summary"]
    print("[PASS] F15: a 0 km odometer stays 0.0; a missing odometer and an unreadable or absent maximum speed stay "
          "None; the fleet maximum covers the known values.")


def _live_unit(ignition_value, speed):
    unit = {"id": 7, "nm": "T7", "sens": {"1": {"n": "Ignition", "t": "engine operation"}}, "lmsg": {"t": NOW - 30}}
    sensors = {} if ignition_value is ... else {"1": {"value": ignition_value}}
    calc = [{"i": 7, "pos": {"s": speed, "t": NOW - 30, "y": 25.2, "x": 55.3}, "sensors": sensors}]
    vehicles.idle_start_times.pop(7, None)
    with patch("app.vehicles.now_timestamp", return_value=NOW), \
            patch("app.vehicles.get_vehicle_history", MagicMock(return_value=[])):
        rec = vehicles.get_vehicle_live_data("sid", [unit], calc, groups=[])[0]
    vehicles.idle_start_times.pop(7, None)
    return rec


def test_unknown_ignition_is_unknown_not_off():
    for value in (..., None, INVALID_WIALON_VALUE, -200000):
        rec = _live_unit(value, 0)
        assert rec["ignition"] is None and rec["sensors"]["ignition"] is None, (value, rec["ignition"])
        # Live status is unchanged: without a known ignition-on reading a stationary unit is STOPPED (io_* unused)
        assert rec["status"] == "STOPPED", (value, rec["status"])
    rec = _live_unit(0, 0)
    assert rec["ignition"] is False and rec["status"] == "STOPPED", rec
    rec = _live_unit(1, 0)
    assert rec["ignition"] is True and rec["status"] == "IDLING", rec
    rec = _live_unit(..., 40)
    assert rec["ignition"] is None and rec["status"] == "MOVING", rec
    print("[PASS] F6: a missing or invalid ignition reading is None (Unknown), never False (Off); a real 0/1 is kept; "
          "live status rules unchanged.")


def _harsh_event(i, vid, g_force):
    return {"id": f"{vid}-{1000 + i}-Hars", "vehicle_id": vid, "vehicle_name": f"U{vid}", "group": "Logistics",
            "event_type": "Harsh Braking", "timestamp": 1000 + i, "speed_kmh": 50, "g_force": g_force,
            "location": {"latitude": 25.0, "longitude": 55.0}}


def test_safety_severity_counts_cover_every_event():
    # 120 rising edges for one unit: the event list is capped at 100, the counts are not
    msgs = []
    for i in range(240):
        p = {"io_253": 2, "io_254": 80 if i % 4 == 0 else 40} if i % 2 == 0 else {}
        msgs.append({"t": 1000 + i, "p": p, "pos": {"s": 50, "y": 25.0, "x": 55.0}})
    fleet = [{"vehicle_id": 1, "vehicle_name": "U1", "group": "Logistics"}]
    with ExitStack() as stack:
        fa._analytics_cache.clear()
        stack.enter_context(patch.object(fa, "login", return_value="sid"))
        stack.enter_context(patch.object(fa, "get_vehicle_groups", return_value=[]))
        stack.enter_context(patch.object(fa, "get_all_vehicles", return_value=[{"id": 1, "nm": "U1", "sens": {}}]))
        stack.enter_context(patch.object(fa, "get_calculated_data", return_value=[]))
        stack.enter_context(patch.object(fa, "get_vehicle_live_data", return_value=fleet))
        stack.enter_context(patch.object(fa, "get_vehicle_history", return_value=msgs))
        res = fa.get_safety_analytics(hours=24, use_cache=False)
    fa._analytics_cache.clear()
    assert res["summary"]["total_events"] == 120 and len(res["events"]) == 100, (res["summary"], len(res["events"]))
    assert res["event_severity_counts"] == {"CRITICAL": 60, "WARNING": 60}, res["event_severity_counts"]
    print("[PASS] F4: safety severity counts cover all 120 events although the event list is capped at 100.")


def test_alert_totals_beyond_display_limit():
    events = [_harsh_event(i, 1 + i % 2, 0.8 if i < 8 else 0.3) for i in range(20)]
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    safety = {"summary": {"total_events": 20}, "events": events,
              "event_severity_counts": {"CRITICAL": 8, "WARNING": 12},
              "by_vehicle": [{"vehicle_id": 1, "total": 10}, {"vehicle_id": 2, "total": 10}],
              "by_group": [{"group": "Logistics", "total": 20}], "partial": False}
    health = {"vehicles": [{
        "vehicle_id": 3, "vehicle_name": "U3", "group": "Contracts",
        "battery": {"status": "OK", "voltage": 24.1},
        "connectivity": {"status": "OFFLINE", "seconds_since_update": 50000, "last_message_ts": 900},
        "gps": {"status": "GOOD", "satellites": 12, "latitude": 25.0, "longitude": 55.0}}]}
    fa._analytics_cache.clear()
    with patch.object(fa, "get_fleet_health_analytics", return_value=health), \
            patch.object(fa, "get_safety_analytics", return_value=safety):
        res = fa.get_fleet_alerts_analytics()
    fa._analytics_cache.clear()
    harsh = [a for a in res["alerts"] if a["alert_type"] == "HARSH_DRIVING"]
    assert len(harsh) == fa.HARSH_ALERTS_DISPLAY_LIMIT == 15, len(harsh)
    assert res["total_alerts"] == 21 and res["displayed_alerts"] == 16 and res["alerts_capped"] is True, res
    assert (res["harsh_driving_total"], res["harsh_driving_displayed"]) == (20, 15), res
    assert (res["critical_count"], res["warning_count"]) == (1 + 8, 12), res
    assert res["by_vehicle"] == {"3": 1, "1": 10, "2": 10} and res["by_group"] == {"Contracts": 1, "Logistics": 20}, res
    print("[PASS] F4: 20 harsh-driving events -> 15 listed, totals and severity counts over all 20 (21 alerts, "
          "9 critical), alerts_capped with displayed 16 of 21; per-vehicle/group counts complete.")


def _tables_unit_b_without_subrows():
    t = default_tables()
    t[TEMPLATE_GROUP_ENGINE]["rows"][1]["d"] = 0
    t[TEMPLATE_GROUP_ENGINE]["subrows"].pop(1)
    return t


def test_idle_duration_kept_without_event_subrows():
    from app import wialon_reports as wr
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=_tables_unit_b_without_subrows())):
        g = wr.execute_wialon_group_engine_idle_report("sid", FROM_TS, TO_TS, group_id=1)
    b = next(v for v in g["vehicles"] if v["vehicle_name"] == "UNIT-B")
    assert b["total_idle_seconds"] == 4530 and b["idle_events"] is None and b["events"] == [], b
    assert g["events_without_subrows"] == ["UNIT-B"] and "UNIT-B" in g["events_incomplete"], g
    assert all(e["vehicle_name"] != "UNIT-B" for e in g["events"]), "no event invented from the row total"
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=_tables_unit_b_without_subrows()))
        res = _idle()
        v = {x["vehicle_name"]: x for x in res["by_vehicle"]}
        assert v["UNIT-B"]["total_idle_seconds"] == 4530 and v["UNIT-B"]["idle_events"] is None, v["UNIT-B"]
        assert v["UNIT-B"]["longest_idle_seconds"] is None, "longest idle unknown without the unit's events"
        assert res["kpis"]["total_idle_seconds"] == 5400 + 4530 and res["kpis"]["total_idle_partial"] is False, res["kpis"]
        assert res["kpis"]["idle_event_count"] is None and res["events_complete"] is False, res["kpis"]
        assert res["events_without_subrows"] == ["UNIT-B"], res
        # A stable report gap: cached for the normal TTL
        entry = main._cache.get(next(k for k in main._cache._data if str(k).startswith("fleet-idle-")))
        assert entry["ttl"] == main.IDLE_ANALYTICS_TTL_SECONDS, entry["ttl"]
    print("[PASS] F7: idling without event sub-rows keeps its 4,530 s duration (total not partial), its event count, "
          "details and longest idle unknown; no event invented; cached as a permanent gap.")


def _engine_on_period(j, start, span, motion, idling):
    """A group engine sub-row as Wialon sends it (seen live 2026-10-27): one engine-on period whose Idling cell is
    the idle time inside it; the period can include driving, so its span is not an idle duration."""
    def hms(sec):
        return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"
    return {"c": [str(j), {"t": f"s-{j}", "v": start}, {"t": f"Yard {j}", "x": 55.3, "y": 25.1},
                  {"t": f"e-{j}", "v": start + span}, {"t": f"Site {j}", "x": 55.4, "y": 25.2},
                  hms(span), hms(span), hms(motion), hms(idling), "12.00 km", "30 km/h", "90 km/h"],
            "t1": start, "t2": start + span}


def test_idle_events_are_engine_on_periods():
    tables = default_tables()
    engine = tables[TEMPLATE_GROUP_ENGINE]
    # UNIT-A: a 14,152 s period with 4,126 s driving and 10,026 s idling (the live maximum on 2026-10-26), a driving-only
    # period and a 1,800 s idle-only period; the row totals are the sums of its sub-rows, as verified live
    engine["rows"][0] = group_engine_row("UNIT-A", "24:00:00", "4:35:52", "1:18:46", "3:17:06", 180.25, subrows=3)
    engine["subrows"][0] = [_engine_on_period(0, FROM_TS + 3600, 14152, 4126, 10026),
                            _engine_on_period(1, FROM_TS + 20000, 600, 600, 0),
                            _engine_on_period(2, FROM_TS + 30000, 1800, 0, 1800)]
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=tables))
        res = _idle()
    a_events = sorted((e for e in res["events"] if e["vehicle_name"] == "UNIT-A"), key=lambda e: e["start_time"])
    # One event per period with idling; its duration is the Idling cell, never the period span or engine time
    assert [e["duration"] for e in a_events] == [10026, 1800], a_events
    assert (a_events[0]["start_time"], a_events[0]["end_time"]) == (FROM_TS + 3600, FROM_TS + 3600 + 14152), a_events[0]
    v = {x["vehicle_name"]: x for x in res["by_vehicle"]}
    assert v["UNIT-A"]["total_idle_seconds"] == 11826 and v["UNIT-A"]["idle_events"] == 2, v["UNIT-A"]
    assert v["UNIT-A"]["in_motion_seconds"] == 4726 and v["UNIT-A"]["longest_idle_seconds"] == 10026, v["UNIT-A"]
    k = res["kpis"]
    # UNIT-B keeps its single 4,530 s interval: 3 events, total = sum of rows = sum of events
    assert k["idle_event_count"] == 3 and k["total_idle_seconds"] == 11826 + 4530, k
    assert k["longest_idle_seconds"] == 10026 and k["longest_idle_vehicle_name"] == "UNIT-A", k
    assert k["average_idle_seconds"] == round((11826 + 4530) / 3, 2), k
    assert sum(e["duration"] for e in res["events"]) == k["total_idle_seconds"], res["events"]
    assert sum(p["idle_seconds"] for p in res["trend"]) == k["total_idle_seconds"], res["trend"]
    print("[PASS] Idle events are Wialon engine-on periods: duration = Idling cell (10,026 s, not the 14,152 s span), "
          "a driving-only period is no event, events and trend sum to the row totals.")


def test_idle_groups_under_filter_and_general_fleet():
    units = UNITS + [{"id": 104, "nm": "UNIT-D", "sens": {}}]
    groups = [dict(g, u=g["u"] + [104]) if g["nm"] == "All Unit" else g for g in GROUPS]
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"].append(
        group_engine_row("UNIT-D", "24:00:00", "1:00:00", "0:40:00", "0:20:00", 12.0))
    live = live_records() + [dict(live_records()[2], vehicle_id=104, vehicle_name="UNIT-D", group="General Fleet")]
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(tables=tables), units=units, groups=groups, live=live)
        fleet = {g["group_name"]: g for g in _idle()["by_group"]}
        assert fleet["General Fleet"]["vehicle_count"] == 1, "General Fleet counts the ungrouped vehicle"
        assert fleet["General Fleet"]["total_idle_seconds"] == 1200, fleet["General Fleet"]
        assert set(fleet) == {"Logistics", "Contracts", "General Fleet"}, set(fleet)
        by_group = {g["group_name"]: g for g in _idle(group_id=7)["by_group"]}
        assert set(by_group) == {"Logistics"}, "a group filter shows only the groups it fully includes"
        assert by_group["Logistics"]["total_idle_seconds"] == 5400 + 4530, by_group["Logistics"]
        assert _idle(vehicle_id=101)["by_group"] == [], "one vehicle of a two-vehicle group: no partial group total"
        assert [g["group_name"] for g in _idle(vehicle_id=103)["by_group"]] == ["Contracts"], "its only member"
    print("[PASS] F2: General Fleet vehicle_count counts ungrouped vehicles; a group or vehicle filter leaves out "
          "groups it does not fully include (no invented zeros or partial sums as group totals).")


def test_vehicle_names_trimmed_before_report_matching():
    unit = {"id": 7, "nm": "  UNIT-A ", "sens": {}, "lmsg": {"t": NOW - 30}}
    calc = [{"i": 7, "pos": {"s": 0, "t": NOW - 30, "y": 25.2, "x": 55.3}, "sensors": {}}]
    with patch("app.vehicles.now_timestamp", return_value=NOW), \
            patch("app.vehicles.get_vehicle_history", MagicMock(return_value=[])):
        rec = vehicles.get_vehicle_live_data("sid", [unit], calc, groups=[])[0]
    assert rec["vehicle_name"] == "UNIT-A", repr(rec["vehicle_name"])
    # Idle analytics trims too, even for a record carrying an untrimmed name
    live = [dict(r, vehicle_name=r["vehicle_name"] + " ") for r in live_records()]
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine(), live=live)
        res = _idle()
    v = {x["vehicle_id"]: x for x in res["by_vehicle"]}
    assert v[101]["total_idle_seconds"] == 5400 and v[102]["total_idle_seconds"] == 4530, v
    assert res["kpis"]["total_idle_partial"] is False, res["kpis"]
    print("[PASS] F11: live vehicle names are trimmed, and idle analytics matches untrimmed names to report rows.")


def test_cache_complete_and_permanent_partial_keep_normal_ttl():
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine())
        res = _idle()
        assert res["stale"] is False and isinstance(res["retrieved_at"], int), res
        key = next(k for k in main._cache._data if str(k).startswith("fleet-idle-"))
        assert main._cache.get(key)["ttl"] == main.IDLE_ANALYTICS_TTL_SECONDS == 180
        engine = FakeReportEngine()
        stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
        age_report_caches(main.IDLE_ANALYTICS_TTL_SECONDS - 5)
        _idle()
        assert engine.count("report/exec_report") == 0, "fresh within 180 s"
        age_report_caches(10)
        _idle()
        assert engine.count("report/exec_report") == 1, "re-read after 180 s"
    print("[PASS] Cache: a complete idle result is served for 180 s, then re-read; responses carry retrieved_at and "
          "stale=false.")


def test_transient_failure_serves_last_good_with_backoff():
    with ExitStack() as stack:
        _patches(stack, FakeReportEngine())
        good = _mileage()
        retrieved = good["retrieved_at"]
        age_report_caches(main.MILEAGE_TTL_SECONDS + 1)  # expired: the next request re-reads Wialon
        failing = FakeReportEngine(fail_exec={TEMPLATE_GROUP_TRIPS})
        stack.enter_context(patch("app.wialon_reports.wialon_request", failing))
        stale = _mileage()
        assert stale["stale"] is True and failing.count("report/exec_report") == 1, stale
        assert stale["total_km"] == good["total_km"] and stale["all_units"] == good["all_units"], "last good result"
        assert stale["retrieved_at"] == retrieved, "its own (older) Wialon retrieval time, not when it was cached"
        _mileage()
        assert failing.count("report/exec_report") == 1, "within the backoff Wialon is not asked again"
        failures, retry_at = main._failure_backoff.get(next(iter(main._failure_backoff._data)))
        assert failures == 1 and 0 < retry_at - time.time() <= main._FAILURE_BACKOFF_BASE_SECONDS, (failures, retry_at)
        key = next(iter(main._failure_backoff._data))
        main._failure_backoff.set(key, (1, time.time() - 1), 600)  # backoff over
        _mileage()
        assert failing.count("report/exec_report") == 2, "retried after the backoff"
        assert main._failure_backoff.get(key)[0] == 2, "consecutive failures double the backoff"
        main._failure_backoff.set(key, (2, time.time() - 1), 600)
        healthy = FakeReportEngine()
        stack.enter_context(patch("app.wialon_reports.wialon_request", healthy))
        fresh = _mileage()
        assert fresh["stale"] is False and healthy.count("report/exec_report") == 1, fresh
        assert main._failure_backoff.get(key) is None, "a success clears the backoff"
    with ExitStack() as stack:  # no last good result: the failure is reported, never an empty or zero result
        _patches(stack, FakeReportEngine(fail_exec={TEMPLATE_GROUP_TRIPS}))
        try:
            _mileage()
            raise AssertionError("expected an error without a last good result")
        except HTTPException as exc:
            assert exc.status_code == 502, exc.status_code
    with ExitStack() as stack:  # the idle endpoint behaves the same way
        _patches(stack, FakeReportEngine())
        _idle()
        age_report_caches(main.IDLE_ANALYTICS_TTL_SECONDS + 1)
        stack.enter_context(patch("app.wialon_reports.wialon_request", FakeReportEngine(fail_exec={TEMPLATE_GROUP_ENGINE})))
        stale = _idle()
        assert stale["stale"] is True and stale["kpis"]["total_idle_seconds"] == 5400 + 4530, stale["kpis"]
    print("[PASS] Cache: a Wialon failure serves the last good result marked stale with its original retrieval "
          "time, backs off (15 s, doubling), recovers on success; without a last good result the error is reported.")


def test_concurrent_identical_requests_run_once():
    class SlowEngine(FakeReportEngine):
        def __call__(self, svc, params, sid=None, timeout=None, **kwargs):
            if svc == "report/exec_report":
                time.sleep(0.2)
            return super().__call__(svc, params, sid=sid, timeout=timeout, **kwargs)

    engine = SlowEngine()
    results, errors = [], []
    with ExitStack() as stack:
        _patches(stack, engine)

        def call():
            try:
                results.append(_mileage())
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert not errors and len(results) == 5, errors
    assert engine.count("report/exec_report") == 1, engine.count("report/exec_report")
    assert len({r["retrieved_at"] for r in results}) == 1, "every caller got the same result"
    print("[PASS] Cache: five simultaneous identical mileage requests run the Wialon report once.")


def main_():
    guard = OfflineGuard()
    guard.start()
    tests = [
        test_zero_odometer_kept_and_unknown_max_speed_none,
        test_unknown_ignition_is_unknown_not_off,
        test_safety_severity_counts_cover_every_event,
        test_alert_totals_beyond_display_limit,
        test_idle_duration_kept_without_event_subrows,
        test_idle_events_are_engine_on_periods,
        test_idle_groups_under_filter_and_general_fleet,
        test_vehicle_names_trimmed_before_report_matching,
        test_cache_complete_and_permanent_partial_keep_normal_ttl,
        test_transient_failure_serves_last_good_with_backoff,
        test_concurrent_identical_requests_run_once,
    ]
    passed = 0
    try:
        for test in tests:
            try:
                test()
                passed += 1
            except Exception:
                traceback.print_exc()
                print(f"[FAIL] {test.__name__}")
    finally:
        guard.stop()
        main._cache.clear()
        fa._analytics_cache.clear()
    guard.assert_no_live_calls()
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    sys.exit(0 if passed == len(tests) else 1)


if __name__ == "__main__":
    main_()
