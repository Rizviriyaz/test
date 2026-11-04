"""
Offline tests for the shared fleet discovery, stale-while-revalidate and report session changes (performance pass):

  - main.py routes share one fleet discovery (groups, units, calc_last): a cold Fleet Overview load (live fleet,
    today's mileage, the idle tile) reads each once instead of once per route; concurrent cold routes share one read
  - the live fleet always reads positions now (fresh) and shares that read with the report routes
  - an expired, retained idle result is answered at once marked `refreshing` (its own retrieved_at, not stale) while
    ONE background refresh recomputes it, however many requests arrive; the next request gets the refreshed result
  - a result kept only TRANSIENT_PARTIAL_TTL_SECONDS is re-read in the foreground (never revalidated)
  - a manual refresh (skip_cache) never takes the revalidation path
  - a failed background refresh keeps the earlier result and records the failure backoff (then the last good result)
  - the Fleet Overview idle tile (include_events=false) runs its Engine Hours report on ENGINE_SESSION; the single
    vehicle idle route on VEHICLE_SESSION; overlapping vehicle idle requests run one report
  - the freshness block reports `refreshing`

Wialon is mocked. Every outbound HTTP call is blocked by OfflineGuard.
Run from Backend/:  python scripts/test_shared_discovery_and_swr.py
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
    BASE_TS, CALCULATED, GROUPS, TEMPLATE_GROUP_ENGINE, UNITS, FakeReportEngine, age_report_caches, live_records,
)

import app.fleet_analytics as fa  # noqa: E402
import app.main as main  # noqa: E402
from app.wialon import ENGINE_SESSION, EVENTS_SESSION, HISTORY_SESSION, VEHICLE_SESSION  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 86400 - 1


def _wait_background_refreshes(timeout=5.0):
    deadline = time.time() + timeout
    while main._swr_running and time.time() < deadline:
        time.sleep(0.01)
    assert not main._swr_running, "background refresh did not finish"


def _setup(stack, engine, login=None):
    """Mocked Wialon reads; returns the discovery call counters."""
    main._cache.clear()
    fa._analytics_cache.clear()
    main._failure_backoff.clear()
    counters = {
        "get_all_vehicles": MagicMock(side_effect=lambda sid: [dict(u) for u in UNITS]),
        "get_calculated_data": MagicMock(side_effect=lambda sid, v: [dict(c) for c in CALCULATED]),
        "get_vehicle_groups": MagicMock(side_effect=lambda sid: [dict(g) for g in GROUPS]),
    }
    stack.enter_context(patch("app.wialon_reports.wialon_request", engine))
    stack.enter_context(patch("app.main.login", login or MagicMock(return_value="sid")))
    for name, mock in counters.items():
        stack.enter_context(patch(f"app.main.{name}", mock))
    stack.enter_context(patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()))
    # The fixture window is in the past; these tests cover an open (today's) window's cache rules
    stack.enter_context(patch("app.main.closed_window_ttl", side_effect=lambda _eff_to, open_ttl: open_ttl))
    return counters


def _mileage():
    return main.get_dashboard_mileage(timeframe="today", from_time=FROM_TS, to_time=TO_TS, from_date=None,
                                      to_date=None, group_id=None)


def _idle(**kw):
    params = dict(from_time=FROM_TS, to_time=TO_TS, include_events=False, skip_cache=False)
    params.update(kw)
    return main.get_fleet_idle_analytics(**params)


def test_cold_dashboard_shares_one_discovery():
    with ExitStack() as stack:
        counters = _setup(stack, FakeReportEngine())
        main.get_vehicles()
        _mileage()
        _idle()
        counts = {n: m.call_count for n, m in counters.items()}
    # Before this change each of the three routes read all three (3 x 3 = 9 reads)
    assert counts == {"get_all_vehicles": 1, "get_calculated_data": 1, "get_vehicle_groups": 1}, counts
    print("[PASS] Cold Fleet Overview load: one groups/units/calc_last read shared by the live fleet, mileage and idle.")


def test_concurrent_cold_routes_share_one_read():
    with ExitStack() as stack:
        counters = _setup(stack, FakeReportEngine())
        slow_units = counters["get_all_vehicles"]
        slow_units.side_effect = lambda sid: (time.sleep(0.2), [dict(u) for u in UNITS])[1]
        threads = [threading.Thread(target=fn) for fn in (_mileage, _idle, _mileage, _idle)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert slow_units.call_count == 1, f"expected one shared discovery read, got {slow_units.call_count}"
    print("[PASS] Concurrent cold report routes wait for one discovery read instead of each reading the fleet.")


def test_routes_and_analytics_share_one_discovery():
    with ExitStack() as stack:
        counters = _setup(stack, FakeReportEngine())
        fa_units = MagicMock(side_effect=lambda sid: [dict(u) for u in UNITS])
        stack.enter_context(patch("app.fleet_analytics.get_all_vehicles", fa_units))
        stack.enter_context(patch("app.fleet_analytics.get_vehicle_groups", side_effect=lambda sid: [dict(g) for g in GROUPS]))
        stack.enter_context(patch("app.fleet_analytics.get_calculated_data", side_effect=lambda sid, v: [dict(c) for c in CALCULATED]))
        main.get_vehicles()  # the live fleet's fresh read
        groups, units, calc = fa._fleet_discovery("sid")
        assert fa_units.call_count == 0, "the analytics builders reuse the routes' current read"
        assert [u["id"] for u in units] == [u["id"] for u in UNITS] and calc
        fa._analytics_cache.clear()
        fa._fleet_discovery("sid")
        _mileage()
        assert fa_units.call_count == 1 and counters["get_all_vehicles"].call_count == 1, \
            "and the routes reuse the builders' read"
    print("[PASS] main.py routes and fleet analytics builders share one fleet discovery read (either side reads it).")


def test_live_fleet_reads_positions_now():
    with ExitStack() as stack:
        counters = _setup(stack, FakeReportEngine())
        _mileage()  # fills the shared discovery
        main.get_vehicles()
        assert counters["get_all_vehicles"].call_count == 2, "the live fleet never reuses an older discovery read"
        _idle()
        assert counters["get_all_vehicles"].call_count == 2, "the report route reuses the live fleet's fresh read"
        age_report_caches(main.LIVE_FLEET_TTL_SECONDS + 1)
        main.get_vehicles()
        assert counters["get_all_vehicles"].call_count == 3, "an expired live fleet is read again"
    print("[PASS] The live fleet always reads positions now and shares that read with the report routes.")


def test_expired_idle_result_is_revalidated_once():
    with ExitStack() as stack:
        engine = FakeReportEngine()
        _setup(stack, engine)
        first = _idle()
        assert engine.count("report/exec_report") == 1
        age_report_caches(main.IDLE_ANALYTICS_TTL_SECONDS + 1)
        gate = threading.Event()
        original = engine.__call__

        def slow(svc, params, sid=None, timeout=None, **kwargs):
            if svc == "report/exec_report":
                gate.wait(5)
            return original(svc, params, sid=sid, timeout=timeout, **kwargs)

        stack.enter_context(patch("app.wialon_reports.wialon_request", slow))
        answers = [_idle() for _ in range(5)]
        assert all(a.get("refreshing") is True and a["stale"] is False for a in answers), answers[0]
        assert all(a["retrieved_at"] == first["retrieved_at"] for a in answers), "the earlier result's own read time"
        assert all(a["kpis"] == first["kpis"] for a in answers), "the earlier result, unchanged"
        gate.set()
        _wait_background_refreshes()
        assert engine.count("report/exec_report") == 2, f"one background refresh, got {engine.count('report/exec_report') - 1}"
        fresh = _idle()
        assert fresh.get("refreshing") is not True and engine.count("report/exec_report") == 2, fresh
        assert fresh["kpis"]["total_idle_seconds"] == first["kpis"]["total_idle_seconds"], "same report, same totals"
    print("[PASS] Expired idle result: answered at once (refreshing, own read time) while one background refresh "
          "runs; the next request gets the refreshed result.")


def test_manual_refresh_and_transient_results_are_read_now():
    with ExitStack() as stack:
        engine = FakeReportEngine()
        _setup(stack, engine)
        _idle()
        age_report_caches(main.IDLE_ANALYTICS_TTL_SECONDS + 1)
        res = _idle(skip_cache=True)
        assert res.get("refreshing") is not True and engine.count("report/exec_report") == 2, "manual refresh reads now"
        # A transiently partial entry (kept TRANSIENT_PARTIAL_TTL_SECONDS) is never revalidated
        key = next(k for k in main._cache._data if str(k).startswith("fleet-idle-"))
        entry = main._cache.get(key)
        main._cache.set(key, {**entry, "ttl": main.TRANSIENT_PARTIAL_TTL_SECONDS,
                              "time": time.time() - main.TRANSIENT_PARTIAL_TTL_SECONDS - 1}, 600)
        assert main._serve_while_revalidating(key, lambda: None, "test") is None
        assert not main._swr_running
    print("[PASS] Manual refresh and transiently partial results are read in the foreground, never revalidated.")


def test_failed_background_refresh_keeps_result_and_backs_off():
    with ExitStack() as stack:
        _setup(stack, FakeReportEngine())
        good = _idle()
        age_report_caches(main.IDLE_ANALYTICS_TTL_SECONDS + 1)
        failing = FakeReportEngine(fail_exec={TEMPLATE_GROUP_ENGINE})
        stack.enter_context(patch("app.wialon_reports.wialon_request", failing))
        assert _idle()["refreshing"] is True
        _wait_background_refreshes()
        assert failing.count("report/exec_report") >= 1
        stale = _idle()
        assert stale["stale"] is True and stale["kpis"] == good["kpis"], "the last good result, marked stale"
        calls = failing.count("report/exec_report")
        _idle()
        assert failing.count("report/exec_report") == calls, "within the backoff Wialon is not asked again"
    print("[PASS] A failed background refresh keeps the earlier result, backs off, then serves it marked stale.")


def test_report_sessions():
    sessions = []

    def login(force=False, session=None):
        sessions.append(session)
        return f"sid-{session or 'shared'}"

    with ExitStack() as stack:
        _setup(stack, FakeReportEngine(), login=MagicMock(side_effect=login))
        with patch("app.main.window_closed", return_value=False):  # today's tile (the fixture window is in the past)
            _idle()
        assert ENGINE_SESSION in sessions and EVENTS_SESSION not in sessions, sessions
        sessions.clear()
        _idle(skip_cache=True)  # a closed window: yesterday's tile
        assert HISTORY_SESSION in sessions and ENGINE_SESSION not in sessions, sessions
        sessions.clear()
        with patch("app.main.window_closed", return_value=False):
            _mileage()
        assert HISTORY_SESSION not in sessions, sessions
        sessions.clear()
        main._cache.clear()
        fa._analytics_cache.clear()
        _mileage()  # a closed window's Group Trips run on the history session
        assert HISTORY_SESSION in sessions, sessions
        sessions.clear()
        _idle(include_events=True, skip_cache=True)
        assert EVENTS_SESSION in sessions, sessions
        sessions.clear()
        report = MagicMock(side_effect=lambda *a, **k: (time.sleep(0.1), {
            "total_idle_seconds": 60, "total_engine_seconds": 120, "idle_events": [], "incomplete_fields": [],
            "unlocated_fields": [], "column_warnings": []})[1])
        stack.enter_context(patch("app.main.execute_wialon_unit_engine_report", report))
        threads = [threading.Thread(target=main.get_vehicle_idle, args=(101,),
                                    kwargs=dict(hours=24, timeframe=None, from_date=None, to_date=None,
                                                from_time=FROM_TS, to_time=TO_TS)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert report.call_count == 1, f"overlapping vehicle idle requests run one report, got {report.call_count}"
        assert sessions == [VEHICLE_SESSION], sessions
    print("[PASS] Idle tile on ENGINE_SESSION (today) or HISTORY_SESSION (closed windows, also Group Trips), idle "
          "events on EVENTS_SESSION, vehicle idle on VEHICLE_SESSION (one report for overlapping requests).")


def test_idle_event_workers_keep_request_context():
    import app.idle_events as idle_events
    from app.performance_diagnostics import count_wialon_call, request_trace_id, request_wialon_stats

    seen = []

    def report(sid, unit_id, from_time, to_time):
        seen.append(request_trace_id.get())
        count_wialon_call()  # as wialon_request does for each HTTP attempt
        return {"events": [], "complete": True, "read_at": to_time}

    stats = {"calls": 0}
    trace_token, stats_token = request_trace_id.set("req-abc123"), request_wialon_stats.set(stats)
    try:
        with patch.object(idle_events, "execute_wialon_unit_idle_events_report", side_effect=report), \
                patch.object(idle_events, "login", return_value="sid"):
            idle_events.clear_unit_results()
            results, errors = idle_events.read_units_idle_events(
                [101, 102, 103, 104, 105, 106], FROM_TS, TO_TS, "test-context-window", 60, reuse=False)
    finally:
        request_wialon_stats.reset(stats_token)
        request_trace_id.reset(trace_token)
        idle_events.clear_unit_results()
    assert not errors and len(results) == 6, (results, errors)
    assert seen == ["req-abc123"] * 6, f"every worker report logs the originating request id, got {seen}"
    assert stats["calls"] == 6, f"worker report calls count toward the originating request, got {stats}"
    print("[PASS] Idle-event worker reports run in the request's context: logged with its request id and counted "
          "toward its Wialon calls.")


def test_named_session_logins_run_in_parallel():
    import app.wialon as gateway
    counter = {"n": 0}
    guard = threading.Lock()

    def fake_request(svc, params, sid=None, is_retry=False, **kwargs):
        assert svc == "token/login"
        time.sleep(0.2)
        with guard:
            counter["n"] += 1
            return {"eid": f"sid-{counter['n']}"}

    with patch.object(gateway, "wialon_request", side_effect=fake_request), \
            patch.dict(gateway._named_sids, {}, clear=True), patch.dict(gateway._named_login_locks, {}, clear=True):
        started = time.perf_counter()
        results = _run_concurrently_named(gateway, [f"perf_s{i}" for i in range(6)] * 2)
        elapsed = time.perf_counter() - started
        assert counter["n"] == 6, f"each named session logs in once, got {counter['n']} logins"
        assert elapsed < 0.8, f"different sessions log in at the same time (6 x 0.2 s), took {elapsed:.2f}s"
        assert len(set(results)) == 6 and len({gateway.session_name(s) for s in results}) == 6, results
        # A stale id reported by several callers is refreshed once; the others get the new id
        stale = gateway._named_sids["perf_s0"]
        refreshed = _run_concurrently(lambda: gateway._login_named("perf_s0", force=True, stale_sid=stale), n=4)
        assert counter["n"] == 7 and len(set(refreshed)) == 1 and refreshed[0] != stale, (counter, refreshed)
    print("[PASS] Named Wialon sessions log in at the same time; each logs in once; a stale id is refreshed once.")


def _run_concurrently(fn, n=5):
    results = [None] * n
    start = threading.Barrier(n)

    def worker(i):
        start.wait()
        results[i] = fn()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return results


def _run_concurrently_named(gateway, sessions):
    names = list(sessions)
    return _run_concurrently(lambda: gateway.login(session=names.pop()), n=len(sessions))


def test_idle_seed_reads_history_once_for_concurrent_fleet_reads():
    import app.vehicles as vehicles
    now = 1_800_000_000
    unit = {"id": 77, "nm": "T77", "sens": {"1": {"n": "Ignition", "t": "engine operation"}}, "lmsg": {"t": now - 60}}
    calc = [{"i": 77, "pos": {"s": 0, "t": now - 60, "y": 25.2, "x": 55.3}, "sensors": {"1": {"value": 1}}}]
    # Idling since 20 min ago: a data message before (moving), then idle messages
    history_msgs = [{"tp": "ud", "t": now - 3000, "pos": {"s": 40}, "sensors": {"1": {"value": 1}}},
                    {"tp": "ud", "t": now - 1200, "pos": {"s": 0}, "sensors": {"1": {"value": 1}}},
                    {"tp": "ud", "t": now - 60, "pos": {"s": 0}, "sensors": {"1": {"value": 1}}}]
    history = MagicMock(side_effect=lambda *a, **k: (time.sleep(0.2), list(history_msgs))[1])
    vehicles.idle_start_times.pop(77, None)
    try:
        with patch("app.vehicles.now_timestamp", return_value=now), patch("app.vehicles.get_vehicle_history", history):
            records = _run_concurrently(lambda: vehicles.get_vehicle_live_data("sid", [dict(unit)], calc, groups=[])[0], n=4)
            single = vehicles.get_vehicle_live_data("sid", [dict(unit)], calc, groups=[])[0]
    finally:
        vehicles.idle_start_times.pop(77, None)
        vehicles.idle_start_lower_bound.discard(77)
    assert all(r["status"] == "IDLING" for r in records), [r["status"] for r in records]
    assert history.call_count == 1, f"one history read for concurrent fleet reads, got {history.call_count}"
    idle = {(r["idle"]["duration"], r["idle"]["duration_status"], r["idle"]["start_time"]) for r in records + [single]}
    assert len(idle) == 1, f"every reader reports the same idle start and duration, got {idle}"
    print("[PASS] Idle-start seeding: concurrent fleet reads load a vehicle's history once and report the same idle "
          "start, duration and status.")


def test_freshness_reports_refreshing():
    from app.utils.freshness import with_freshness
    now = time.time()
    block = with_freshness({"retrieved_at": int(now) - 200, "refreshing": True}, target_age_seconds=240)["freshness"]
    assert block["refreshing"] is True and block["refresh_status"] == "cached" and block["data_age_seconds"] >= 200
    assert with_freshness({"retrieved_at": int(now)}, target_age_seconds=240)["freshness"]["refreshing"] is False
    print("[PASS] Freshness block: `refreshing` set while an expired result is revalidated; its age is unchanged.")


if __name__ == "__main__":
    guard = OfflineGuard().start()
    failed = 0
    try:
        for test in (test_cold_dashboard_shares_one_discovery, test_concurrent_cold_routes_share_one_read,
                     test_routes_and_analytics_share_one_discovery,
                     test_live_fleet_reads_positions_now, test_expired_idle_result_is_revalidated_once,
                     test_manual_refresh_and_transient_results_are_read_now,
                     test_failed_background_refresh_keeps_result_and_backs_off, test_report_sessions,
                     test_idle_event_workers_keep_request_context, test_named_session_logins_run_in_parallel,
                     test_idle_seed_reads_history_once_for_concurrent_fleet_reads,
                     test_freshness_reports_refreshing):
            try:
                test()
            except Exception:
                failed += 1
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    try:
        guard.assert_no_live_calls()
    except AssertionError as exc:
        failed += 1
        print(f"[FAIL] Network guard: {exc}")
    sys.exit(1 if failed else 0)
