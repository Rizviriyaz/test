"""
Offline regression tests: a hung or failing Wialon report must not block the Fuel Performance dashboard.

Reproduces the failure seen locally (report/exec_report timing out after ~120 s with transport_error while the
fuel endpoints wait on the fleet telematics bundle): the bundle is made to hang, or to fail, and the fuel routes must
answer within FUEL_TELEMATICS_WAIT_S with their recorded fuel figures, telematics values null (never 0) and
telematics_status "unavailable". The hung report is computed once (single flight) and, once it finishes, serves
the next request from cache. Recorded zero hours stay 0.

Wialon is replaced by fixtures; OfflineGuard blocks every outbound HTTP call.

Run from Backend/:  python scripts/test_fuel_telematics_deadline.py
"""

import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import UNITS  # noqa: E402

from app.main import app  # noqa: E402
import app.fuel.routes as fuel_routes  # noqa: E402
import app.fuel.wialon_distance as wd  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

AUTH = {"Authorization": "Bearer offline-test-token"}
FAKE_USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "t@ats.ae", "role": "admin"}
PERIOD = "date_from=2026-08-01&date_to=2026-08-31"
WAIT_S = 1
MAX_RESPONSE_S = 6  # the wait plus generous headroom; the unbounded path took minutes
ROWS = [
    {"id": 1, "import_id": 1, "transaction_date": "2026-08-03T10:00:00", "vehicle_identifier": "UNIT-A",
     "litres": 60.0, "total_cost": 180.0, "fuel_type": "Diesel"},
    {"id": 2, "import_id": 1, "transaction_date": "2026-08-12T10:00:00", "vehicle_identifier": "UNIT-B",
     "litres": 40.0, "total_cost": 120.0, "fuel_type": "Diesel"},
]


def fake_bundle(idle_seconds=7200):
    by_name = {}
    for name, km in (("UNIT-A", 500.0), ("UNIT-B", 300.0)):
        e = wd._empty_entry(name)
        e.update(mileage_km=km, distance_source="wialon_group_trips_report", in_trips_report=True, in_engine_report=True,
                 driving_seconds=36000, idle_seconds=idle_seconds, engine_hours_seconds=36000 + idle_seconds,
                 total_time_seconds=36000 + idle_seconds)
        e.update(driving_hours=wd._hours(e["driving_seconds"]), idle_hours=wd._hours(e["idle_seconds"]),
                 engine_hours=wd._hours(e["engine_hours_seconds"]), total_time_hours=wd._hours(e["total_time_seconds"]),
                 has_telemetry=True)
        by_name[name] = e
    return {"by_name": by_name, "duplicate_names": [], "status": "complete", "errors": [], "warnings": [],
            "column_fallbacks": [], "date_from": "2026-08-01", "date_to": "2026-08-31", "fetched_at_utc": 0}


class HungReport:
    """Stands in for _build_fleet_telematics_bundle: blocks until released, then returns a real bundle."""

    def __init__(self, result=None):
        self.release = threading.Event()
        self.calls = 0
        self.result = result or fake_bundle()

    def __call__(self, date_from, date_to, sid):
        self.calls += 1
        self.release.wait(30)
        return self.result


def patched(**extra):
    env = patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": str(WAIT_S)})
    ctx = [
        env,
        patch("app.main.authenticate_access_token", return_value=FAKE_USER),
        patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=[dict(r) for r in ROWS]),
        patch("app.fuel.routes.login", return_value="s"),
        patch("app.fuel.routes.get_all_vehicles", return_value=UNITS),
    ]
    ctx += [patch(target, value) for target, value in extra.items()]
    return ctx


class Patches:
    def __init__(self, **extra):
        self.ctx = patched(**{k.replace("__", "."): v for k, v in extra.items()})

    def __enter__(self):
        wd.clear_distance_cache()
        for c in self.ctx:
            c.start()
        return self

    def __exit__(self, *exc):
        for c in reversed(self.ctx):
            c.stop()
        wd.clear_distance_cache()


def settle(release, cache, key):
    """Release a hung report and wait until its background result is cached, so it cannot leak into the next test."""
    release.set()
    deadline = time.time() + 5
    while cache.get(key) is None and time.time() < deadline:
        time.sleep(0.05)


MONTH_KEY = ("2026-08-01", "2026-08-31")


def timed_get(client, url):
    started = time.perf_counter()
    r = client.get(url, headers=AUTH)
    return r, time.perf_counter() - started


def test_hung_report_still_returns_recorded_fuel():
    hung = HungReport()
    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung):
        try:
            r, took = timed_get(client, f"/api/fuel/analytics/efficiency?{PERIOD}")
        finally:
            settle(hung.release, wd._TELEMATICS_CACHE, MONTH_KEY)
    assert r.status_code == 200, r.text
    assert took < MAX_RESPONSE_S, f"waited {took:.1f}s"
    body = r.json()
    assert body["total_litres"] == 100.0 and body["total_cost"] == 300.0 and body["transaction_count"] == 2, body
    for key in ("total_distance_km", "total_driving_hours", "total_idle_hours", "estimated_idle_fuel_litres",
                "fuel_efficiency_l_per_100km"):
        assert body[key] is None, (key, body[key])
    assert body["telematics_status"] == "unavailable", body["telematics_status"]
    assert body["telematics_pending"] is True, "timed out, report still running: the client may ask again"
    assert any("did not respond in time" in w for w in body["data_warnings"]), body["data_warnings"]
    print(f"[PASS] Hung Wialon report: /efficiency answers in {took:.1f}s with recorded fuel, telematics null + 'unavailable'.")


def test_failed_report_is_not_a_502_for_fuel_figures():
    def failing(date_from, date_to, sid):
        raise WialonAPIError("Wialon transport failure for report/exec_report")

    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=failing):
        r = client.get(f"/api/fuel/analytics/efficiency?{PERIOD}", headers=AUTH)
        w = client.get(f"/api/fuel/analytics/waste?{PERIOD}", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_litres"] == 100.0 and body["total_distance_km"] is None, body
    assert body["telematics_status"] == "unavailable" and any("failed" in x for x in body["data_warnings"]), body
    assert body["telematics_pending"] is False, "a failure is not pending: never retried silently"
    assert w.status_code == 200, w.text
    assert w.json()["telematics_status"] == "unavailable" and w.json()["telematics_pending"] is False, w.json()
    print("[PASS] Failed Wialon report: /efficiency and /waste keep fuel data (200) and mark telematics unavailable.")


def test_dashboard_requests_share_one_report_and_reuse_it_once_finished():
    hung = HungReport()
    client = TestClient(app)
    urls = [
        f"/api/fuel/analytics/efficiency?{PERIOD}",
        f"/api/fuel/analytics/efficiency/vehicles?{PERIOD}&page=1&page_size=20&sort_by=efficiency&sort_order=desc",
        f"/api/fuel/analytics/efficiency/vehicles?{PERIOD}&page=1&page_size=5&sort_by=estimated_idle_fuel&sort_order=desc&status=matched",
        f"/api/fuel/analytics/waste?{PERIOD}",
    ]
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung):
        try:
            for url in urls:
                r, took = timed_get(client, url)
                assert r.status_code == 200, (url, r.text)
                assert took < MAX_RESPONSE_S, (url, took)
                assert r.json()["telematics_status"] == "unavailable", (url, r.json().get("telematics_status"))
                if "/vehicles" in url:
                    assert all(v["estimated_idle_fuel_litres"] is None for v in r.json()["data"]), r.json()["data"]
        finally:
            settle(hung.release, wd._TELEMATICS_CACHE, MONTH_KEY)
        assert hung.calls == 1, f"the month's reports ran {hung.calls} times"
        r = client.get(urls[0], headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["telematics_status"] == "complete" and body["total_distance_km"] == 800.0, body
    assert hung.calls == 1, "the finished report is reused from cache, not run again"
    print("[PASS] Initial-load requests join one in-flight report; once it finishes the next load uses the cache.")


def test_trend_does_not_wait_for_a_hung_fleet_trips_report():
    release = threading.Event()

    def hung_fleet_trips(date_from, date_to):
        release.wait(30)
        return {"by_name": {}, "duplicate_names": []}

    per_unit = MagicMock(side_effect=AssertionError("per-unit reports must not queue behind the fleet report"))
    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=lambda f, t, s: fake_bundle(),
                 app__fuel__wialon_distance___fetch_fleet_trips=hung_fleet_trips,
                 app__fuel__routes__get_units_trips_for_range=per_unit):
        try:
            r, took = timed_get(client, "/api/fuel/analytics/efficiency/trend?group_by=month&date_from=2026-03-01&date_to=2026-08-31")
        finally:
            settle(release, wd._FLEET_TRIPS_CACHE, ("2026-03-01", "2026-08-31"))
    assert r.status_code == 200, r.text
    assert took < MAX_RESPONSE_S, took
    body = r.json()
    aug = [p for p in body["data"] if p["period"] == "2026-08"]
    assert aug and aug[0]["litres"] == 100.0 and aug[0]["distance_km"] is None, body["data"]
    assert any("withheld" in w for w in body["data_warnings"]), body["data_warnings"]
    per_unit.assert_not_called()
    print(f"[PASS] 6-month trend answers in {took:.1f}s with fuel per month and distance withheld while fleet trips hang.")


def test_trend_runs_fleet_trips_after_the_bundle_never_alongside():
    events = []

    def slow_bundle(date_from, date_to, sid):
        events.append("bundle_start")
        time.sleep(0.2)
        events.append("bundle_end")
        return fake_bundle()

    def fleet_trips(date_from, date_to):
        events.append("fleet_start")
        return {"by_name": {}, "duplicate_names": []}

    def unit_trips(unit_ids, date_from, date_to, sid=None, units=None):
        return {int(u): {"status": "complete", "trips": [], "error": None} for u in unit_ids}

    url = f"/api/fuel/analytics/efficiency/trend?group_by=month&{PERIOD}"
    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=slow_bundle,
                 app__fuel__wialon_distance___fetch_fleet_trips=fleet_trips,
                 app__fuel__routes__get_units_trips_for_range=unit_trips):
        r = client.get(url, headers=AUTH)
    assert r.status_code == 200, r.text
    assert events == ["bundle_start", "bundle_end", "fleet_start"], events

    def limited_bundle(date_from, date_to, sid):
        raise WialonAPIError("Wialon group trips report failed for group 1", code=1004)

    fleet = MagicMock(side_effect=AssertionError("no fleet trips report after the bundle failed"))
    per_unit = MagicMock(side_effect=AssertionError("no per-unit reports after the bundle failed"))
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=limited_bundle,
                 app__fuel__wialon_distance___fetch_fleet_trips=fleet,
                 app__fuel__routes__get_units_trips_for_range=per_unit):
        r = client.get(url, headers=AUTH)
    assert r.status_code == 200, r.text
    aug = [p for p in r.json()["data"] if p["period"] == "2026-08"]
    assert aug and aug[0]["litres"] == 100.0 and aug[0]["distance_km"] is None, r.json()["data"]
    fleet.assert_not_called()
    per_unit.assert_not_called()
    print("[PASS] Trend runs the fleet trips report only after the bundle; a failed bundle (1004) starts no further reports.")


def test_trend_chain_finishes_after_timeout_and_next_request_reuses_it():
    events, lock = [], threading.Lock()

    def note(e):
        with lock:
            events.append(e)

    def slow_bundle(date_from, date_to, sid):
        note("bundle_start")
        time.sleep(WAIT_S + 0.5)  # outlasts the request's single wait
        note("bundle_end")
        return fake_bundle()

    def fleet_trips(date_from, date_to):
        note("fleet_start")
        time.sleep(0.2)
        note("fleet_end")
        return {"by_name": {}, "duplicate_names": []}

    aug10 = int(datetime(2026, 8, 10, 8, tzinfo=timezone(timedelta(hours=4))).timestamp())

    def unit_trips(unit_ids, date_from, date_to, sid=None, units=None):
        return {int(u): {"status": "complete", "trips": [{"start_time": aug10, "end_time": aug10 + 3600,
                                                           "distance_km": 250.0}], "error": None} for u in unit_ids}

    chains = []
    real_chain = fuel_routes._trend_telematics_chain

    def counted_chain(*args):
        chains.append(args)
        return real_chain(*args)

    url = f"/api/fuel/analytics/efficiency/trend?group_by=month&{PERIOD}"
    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=slow_bundle,
                 app__fuel__wialon_distance___fetch_fleet_trips=fleet_trips,
                 app__fuel__routes__get_units_trips_for_range=unit_trips,
                 app__fuel__routes___trend_telematics_chain=counted_chain):
        # Two requests at once: both time out on the single wait and share one chain
        results = []
        threads = [threading.Thread(target=lambda: results.append(timed_get(client, url))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for r, took in results:
            assert r.status_code == 200, r.text
            assert took < MAX_RESPONSE_S, took
            aug = [p for p in r.json()["data"] if p["period"] == "2026-08"]
            assert aug and aug[0]["litres"] == 100.0 and aug[0]["distance_km"] is None, aug
        assert len(chains) == 1, f"one chain for concurrent requests of one range, got {len(chains)}"
        # The chain keeps running after the waits ended: bundle, then fleet trips (never at once)
        deadline = time.time() + 5
        while "fleet_end" not in events and time.time() < deadline:
            time.sleep(0.05)
        assert events == ["bundle_start", "bundle_end", "fleet_start", "fleet_end"], events
        time.sleep(0.1)  # let the finished chain be forgotten
        # The next request reads both caches: no report runs again and distance is shown
        r, took = timed_get(client, url)
    assert r.status_code == 200, r.text
    assert events == ["bundle_start", "bundle_end", "fleet_start", "fleet_end"], f"reports re-ran: {events}"
    aug = [p for p in r.json()["data"] if p["period"] == "2026-08"]
    assert aug and aug[0]["distance_km"] == 500.0, aug
    print(f"[PASS] Trend chain (bundle -> fleet trips) outlives a timed-out wait, is shared by concurrent requests, "
          f"and the next request reuses its caches ({took:.1f}s, distance shown).")


def test_pending_then_finished_report_is_served_from_cache():
    """The timed-out month keeps running once; asking again after it finished returns real telematics."""
    hung = HungReport()
    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung):
        try:
            first, _ = timed_get(client, f"/api/fuel/analytics/efficiency?{PERIOD}")
            rows, _ = timed_get(client, f"/api/fuel/analytics/efficiency/vehicles?{PERIOD}")
            waste, _ = timed_get(client, f"/api/fuel/analytics/waste?{PERIOD}")
        finally:
            settle(hung.release, wd._TELEMATICS_CACHE, MONTH_KEY)
        again, took = timed_get(client, f"/api/fuel/analytics/efficiency?{PERIOD}")
    for r in (first, rows, waste):
        assert r.status_code == 200 and r.json()["telematics_pending"] is True, r.json().get("telematics_pending")
    assert hung.calls == 1, f"one report for the month, shared by every endpoint and the retry (ran {hung.calls})"
    body = again.json()
    assert body["telematics_pending"] is False and body["telematics_status"] == "complete", body["telematics_status"]
    assert body["total_distance_km"] == 800.0 and body["total_idle_hours"] == 4.0, body
    print(f"[PASS] Pending month: every endpoint says pending, one report runs; the retry gets real telematics ({took:.2f}s).")


def test_recorded_zero_idle_stays_zero():
    client = TestClient(app)
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=lambda f, t, s: fake_bundle(idle_seconds=0)):
        r = client.get(f"/api/fuel/analytics/efficiency?{PERIOD}", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_idle_hours"] == 0.0 and body["total_driving_hours"] == 20.0, body
    assert body["telematics_status"] == "complete", body["telematics_status"]
    print("[PASS] A fast report is unaffected: recorded zero idle hours stay 0, status complete.")


class CountingSubmit:
    """Counts jobs submitted to the fuel telematics executor (the real executor still runs them)."""

    def __init__(self):
        self.calls = 0
        self._real = fuel_routes._TELEMATICS_EXECUTOR.submit

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._real(*args, **kwargs)


def _gather(*coros):
    import asyncio

    async def run():
        return await asyncio.gather(*coros)

    return asyncio.run(run())


def test_concurrent_requests_share_one_bundle_job():
    hung, submit = HungReport(), CountingSubmit()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung), \
            patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": "10"}), \
            patch.object(fuel_routes._TELEMATICS_EXECUTOR, "submit", side_effect=submit):
        threading.Timer(0.3, hung.release.set).start()
        results = _gather(*(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s") for _ in range(6)))
        assert all(bundle is not None and warnings == [] for bundle, warnings in results), results
        assert hung.calls == 1 and submit.calls == 1, (hung.calls, submit.calls)
        # Once built, the range is answered from the bundle cache without any executor job
        bundle, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s"))[0]
        assert bundle is results[0][0] and warnings == [] and submit.calls == 1, submit.calls
    assert fuel_routes._BUNDLE_BUILDS == {}, "a finished build is forgotten"
    print("[PASS] 6 concurrent requests for one range share 1 executor job and 1 build; a cached range submits none.")


def test_timed_out_or_cancelled_waiter_never_cancels_the_shared_build():
    import asyncio

    hung, submit = HungReport(), CountingSubmit()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung), \
            patch.object(fuel_routes._TELEMATICS_EXECUTOR, "submit", side_effect=submit):
        try:
            started = time.perf_counter()
            bundle, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s"))[0]
            took = time.perf_counter() - started
            assert bundle is None and warnings == [fuel_routes.TELEMATICS_TIMEOUT_WARNING], (bundle, warnings)
            assert WAIT_S - 0.1 <= took < WAIT_S + 2, f"the bounded wait still applies ({took:.2f}s)"
            build = fuel_routes._BUNDLE_BUILDS[MONTH_KEY]
            assert not build.done() and not build.cancelled(), "the timed-out waiter left the build running"

            async def cancelled_request():
                task = asyncio.ensure_future(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s"))
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return build.cancelled()

            assert asyncio.run(cancelled_request()) is False, "a cancelled request must not cancel the shared build"
            assert submit.calls == 1, "later waiters joined the running build instead of submitting another"
            with patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": "10"}):
                threading.Timer(0.3, hung.release.set).start()
                bundle, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s"))[0]
            assert bundle is not None and warnings == [] and hung.calls == 1 and submit.calls == 1, (hung.calls,
                                                                                                   submit.calls)
        finally:
            settle(hung.release, wd._TELEMATICS_CACHE, MONTH_KEY)
    print("[PASS] A timed-out or cancelled waiter leaves the shared build running; the next request joins it (1 build).")


def test_cached_range_answers_while_every_executor_worker_is_busy():
    workers = fuel_routes._TELEMATICS_EXECUTOR._max_workers
    blocker = threading.Event()
    occupied = [fuel_routes._TELEMATICS_EXECUTOR.submit(blocker.wait, 30) for _ in range(workers)]
    hung = HungReport()
    other_key = ("2026-07-01", "2026-07-31")
    client = TestClient(app)
    try:
        with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung):
            wd._TELEMATICS_CACHE.set(MONTH_KEY, fake_bundle(), 60)
            started = time.perf_counter()
            bundle, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s"))[0]
            assert bundle is not None and warnings == [] and time.perf_counter() - started < 0.5
            r, took = timed_get(client, f"/api/fuel/analytics/efficiency?{PERIOD}")
            assert r.status_code == 200 and took < 3, (r.status_code, took)
            body = r.json()
            assert body["telematics_status"] == "complete" and body["total_distance_km"] == 800.0, body
            # An uncached range still waits for a worker and answers "unavailable" after the bounded wait, as before
            bundle, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(other_key, "s"))[0]
            assert bundle is None and warnings == [fuel_routes.TELEMATICS_TIMEOUT_WARNING], warnings
            assert hung.calls == 0, "no worker was free, so the uncached build has not started yet"
            # Let the queued build run and finish while the fixtures are still patched in
            blocker.set()
            hung.release.set()
            deadline = time.time() + 5
            while fuel_routes._BUNDLE_BUILDS and time.time() < deadline:
                time.sleep(0.05)
            assert hung.calls == 1 and not fuel_routes._BUNDLE_BUILDS, "the queued build ran once workers freed"
    finally:
        blocker.set()
        hung.release.set()
        for f in occupied:
            f.result(5)
        wd.clear_distance_cache()
    print(f"[PASS] With all {workers} executor workers busy, a cached range answers at once (endpoint 200, complete "
          "telematics); an uncached range still times out to 'unavailable' after FUEL_TELEMATICS_WAIT_S.")


def test_ai_tool_path_shares_the_build_and_uses_the_cache():
    import concurrent.futures

    hung, submit = HungReport(), CountingSubmit()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung), \
            patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": "10"}), \
            patch.object(fuel_routes._TELEMATICS_EXECUTOR, "submit", side_effect=submit):
        build = wd.get_fleet_telematics_bundle
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as callers:
            ai_calls = [callers.submit(fuel_routes.telematics_bundle_bounded_sync, MONTH_KEY, "s", build) for _ in range(3)]
            time.sleep(0.2)
            threading.Timer(0.3, hung.release.set).start()  # released while the 2 route requests also wait
            route_call = _gather(*(fuel_routes._telematics_bundle_or_unavailable(MONTH_KEY, "s") for _ in range(2)))
            results = [f.result(10) for f in ai_calls]
        assert all(r is not None for r in results) and all(b is not None for b, _ in route_call)
        assert hung.calls == 1 and submit.calls == 1, ("3 AI tool calls + 2 route requests: one build", hung.calls,
                                                       submit.calls)
        assert fuel_routes.telematics_bundle_bounded_sync(MONTH_KEY, "s", build) is results[0] and submit.calls == 1

    # Cached: answered at once while every executor worker is busy; uncached: the bounded wait still applies
    workers = fuel_routes._TELEMATICS_EXECUTOR._max_workers
    blocker, hung = threading.Event(), HungReport()
    occupied = [fuel_routes._TELEMATICS_EXECUTOR.submit(blocker.wait, 30) for _ in range(workers)]
    try:
        with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=hung):
            wd._TELEMATICS_CACHE.set(MONTH_KEY, fake_bundle(), 60)
            started = time.perf_counter()
            assert fuel_routes.telematics_bundle_bounded_sync(MONTH_KEY, "s", wd.get_fleet_telematics_bundle) is not None
            assert time.perf_counter() - started < 0.5
            try:
                fuel_routes.telematics_bundle_bounded_sync(("2026-07-01", "2026-07-31"), "s", wd.get_fleet_telematics_bundle)
            except concurrent.futures.TimeoutError:
                pass
            else:
                raise AssertionError("an uncached range with no free worker must time out")
            blocker.set()
            hung.release.set()
            deadline = time.time() + 5
            while fuel_routes._BUNDLE_BUILDS and time.time() < deadline:
                time.sleep(0.05)
    finally:
        blocker.set()
        hung.release.set()
        for f in occupied:
            f.result(5)
        wd.clear_distance_cache()
    print("[PASS] AI fuel tool path: 3 AI calls + 2 route requests share 1 build; a cached range answers with every "
          "worker busy; an uncached one still times out after FUEL_TELEMATICS_WAIT_S.")


def _today_key():
    today = fuel_routes.get_uae_now().date().isoformat()
    return (today[:8] + "01", today)


def _aged_bundle(age_s, **extra):
    b = fake_bundle()
    b.update(fetched_at_utc=fuel_routes.now_timestamp() - age_s, **extra)
    return b


def _wait_builds(deadline_s=5):
    deadline = time.time() + deadline_s
    while fuel_routes._BUNDLE_BUILDS and time.time() < deadline:
        time.sleep(0.02)
    assert not fuel_routes._BUNDLE_BUILDS, "background rebuild still running"


def test_live_range_is_refreshed_ahead_once_and_served_meanwhile():
    key = _today_key()
    fresh = fake_bundle()
    builds = []
    gate = threading.Event()

    def build(date_from, date_to, sid):
        builds.append((date_from, date_to))
        gate.wait(5)
        return dict(fresh, fetched_at_utc=fuel_routes.now_timestamp())

    fuel_routes._REFRESH_FAILED_AT.clear()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=build), \
            patch("app.fuel.routes.seconds_since_limit_hit", return_value=None):
        live_ttl = wd.TELEMATICS_TTL_LIVE_S
        old = _aged_bundle(int(live_ttl * fuel_routes.TELEMATICS_REFRESH_AHEAD) + 1)
        wd._TELEMATICS_CACHE.set(key, old, 60)
        started = time.perf_counter()
        results = _gather(*(fuel_routes._telematics_bundle_or_unavailable(key, "s") for _ in range(5)))
        assert time.perf_counter() - started < 0.5, "viewers never wait for the refresh"
        assert all(b is old and w == [] for b, w in results), "the current bundle is served while it refreshes"
        assert fuel_routes.telematics_bundle_bounded_sync(key, "s", wd.get_fleet_telematics_bundle) is old
        assert len(builds) == 1, ("one refresh per range, however many requests", builds)
        gate.set()
        _wait_builds()
        refreshed = wd._TELEMATICS_CACHE.get(key)
        assert refreshed is not old and refreshed["by_name"] == fresh["by_name"], "the rebuild replaced the entry"
        # The refreshed entry keeps the live TTL computed by the bundle's own rule
        expires_at = wd._TELEMATICS_CACHE._data[key][0]
        assert abs((expires_at - wd._TELEMATICS_CACHE._now()) - wd._ttl_for_bundle(key[1], refreshed)) < 2
        # A young bundle is not refreshed again
        _gather(fuel_routes._telematics_bundle_or_unavailable(key, "s"))
        assert len(builds) == 1, builds
    print("[PASS] Refresh-ahead: an aging live bundle is served at once to every request (routes and AI) while ONE "
          "background rebuild replaces it with the bundle's own live TTL; a young bundle is not rebuilt.")


def test_refresh_ahead_skips_closed_partial_young_and_limited_ranges():
    key = _today_key()
    builds = []

    def build(date_from, date_to, sid):
        builds.append((date_from, date_to))
        return fake_bundle()

    due_age = int(wd.TELEMATICS_TTL_LIVE_S * fuel_routes.TELEMATICS_REFRESH_AHEAD) + 1
    fuel_routes._REFRESH_FAILED_AT.clear()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=build):
        cases = [
            ("closed range", MONTH_KEY, _aged_bundle(10 ** 6), None),
            ("partial bundle", key, _aged_bundle(due_age, errors=["Group Trips report empty"]), None),
            ("young bundle", key, _aged_bundle(5), None),
            ("Wialon 1004 seen 60 s ago", key, _aged_bundle(due_age), 60.0),
        ]
        for what, k, bundle, since_limit in cases:
            wd.clear_distance_cache()
            wd._TELEMATICS_CACHE.set(k, bundle, 60)
            with patch("app.fuel.routes.seconds_since_limit_hit", return_value=since_limit):
                got, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(k, "s"))[0]
            assert got is bundle and warnings == [], what
            _wait_builds()
            assert builds == [], (what, builds)
    print("[PASS] No refresh-ahead for a closed range, a partial bundle, a young bundle, or within 300 s of a Wialon 1004.")


def test_failed_refresh_keeps_the_entry_and_backs_off():
    key = _today_key()
    attempts = []

    def failing(date_from, date_to, sid):
        attempts.append(1)
        raise WialonAPIError("Wialon report failed")

    due_age = int(wd.TELEMATICS_TTL_LIVE_S * fuel_routes.TELEMATICS_REFRESH_AHEAD) + 1
    fuel_routes._REFRESH_FAILED_AT.clear()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=failing), \
            patch("app.fuel.routes.seconds_since_limit_hit", return_value=None):
        old = _aged_bundle(due_age)
        wd._TELEMATICS_CACHE.set(key, old, 60)
        assert _gather(fuel_routes._telematics_bundle_or_unavailable(key, "s"))[0] == (old, [])
        _wait_builds()
        assert len(attempts) == 1 and key in fuel_routes._REFRESH_FAILED_AT
        assert wd._TELEMATICS_CACHE.get(key) is old, "a failed refresh leaves the current entry in place"
        for _ in range(3):
            assert _gather(fuel_routes._telematics_bundle_or_unavailable(key, "s"))[0] == (old, [])
        _wait_builds()
        assert len(attempts) == 1, "no further refresh within FUEL_TELEMATICS_TTL_PARTIAL_S of a failure"
        fuel_routes._REFRESH_FAILED_AT[key] -= wd.TELEMATICS_TTL_PARTIAL_S + 1  # the back-off has passed
        _gather(fuel_routes._telematics_bundle_or_unavailable(key, "s"))
        _wait_builds()
        assert len(attempts) == 2, attempts
    fuel_routes._REFRESH_FAILED_AT.clear()
    print("[PASS] A failed refresh-ahead keeps serving the cached bundle until it expires and retries only after 30 s.")


def test_refresh_trend_chain_and_route_interleaving_runs_one_build():
    # The audited interleaving: a refresh-ahead outlives its entry's remaining life, then the trend chain (a direct
    # get_fleet_telematics_bundle caller) and a route request ask for the same live range
    key = _today_key()
    builds, active, peak, lock = [], [0], [0], threading.Lock()

    def slow_build(date_from, date_to, sid):
        with lock:
            builds.append(1)
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(1.0)
        with lock:
            active[0] -= 1
        return dict(fake_bundle(), fetched_at_utc=fuel_routes.now_timestamp())

    fuel_routes._REFRESH_FAILED_AT.clear()
    with Patches(app__fuel__wialon_distance___build_fleet_telematics_bundle=slow_build), \
            patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": "10"}), \
            patch("app.fuel.routes.seconds_since_limit_hit", return_value=None):
        aged = _aged_bundle(int(wd.TELEMATICS_TTL_LIVE_S * fuel_routes.TELEMATICS_REFRESH_AHEAD) + 10)
        wd._TELEMATICS_CACHE.set(key, aged, 0.3)  # refresh due, 0.3 s of life left
        assert fuel_routes._cached_bundle(key, "s") is aged  # starts the refresh-ahead
        time.sleep(0.5)  # the entry expires while the refresh is still running
        assert wd._TELEMATICS_CACHE.get(key) is None
        chain = {}
        trend = threading.Thread(target=lambda: chain.setdefault(
            "bundle", fuel_routes._trend_telematics_chain(key[0], key[1], "s", False)))
        trend.start()
        route_bundle, warnings = _gather(fuel_routes._telematics_bundle_or_unavailable(key, "s"))[0]
        trend.join(5)
        _wait_builds()
        cached = wd._TELEMATICS_CACHE.get(key)  # read before Patches clears the cache on exit
    assert len(builds) == 1 and peak[0] == 1, ("refresh + trend chain + route request: one build", builds, peak)
    assert warnings == [] and cached is not None and route_bundle is chain["bundle"] is cached, \
        "all three got the refreshed bundle"
    print("[PASS] Refresh-ahead outliving its entry: the trend chain and a route request join it (1 build, never 2).")


def test_concurrent_trend_unit_trips_share_one_job():
    import asyncio

    release, calls, submit = threading.Event(), [], CountingSubmit()

    def unit_trips(unit_ids, date_from, date_to, sid, units):
        calls.append(sorted(unit_ids))
        release.wait(10)
        return {int(u): {"status": "ok", "trips": []} for u in unit_ids}

    with patch("app.fuel.routes.get_units_trips_for_range", side_effect=unit_trips), \
            patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": "10"}), \
            patch.object(fuel_routes._TELEMATICS_EXECUTOR, "submit", side_effect=submit):
        threading.Timer(0.3, release.set).start()
        results = _gather(*(fuel_routes._shared_unit_trips(ids, MONTH_KEY, "s", UNITS)
                            for ids in ([3, 1, 2], [1, 2, 3], [2, 3, 1])))
        assert calls == [[1, 2, 3]] and submit.calls == 1, ("same range + unit set: one job", calls, submit.calls)
        assert results[0] is results[1] is results[2], "every request gets the shared result"
        _gather(fuel_routes._shared_unit_trips([1, 2], MONTH_KEY, "s", UNITS))
        assert len(calls) == 2 and submit.calls == 2, "a different unit set is its own job"

    # The bounded wait still applies; the timed-out job keeps running and is not cancelled
    release.clear()
    with patch("app.fuel.routes.get_units_trips_for_range", side_effect=unit_trips), \
            patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": str(WAIT_S)}):
        started = time.perf_counter()
        try:
            _gather(fuel_routes._shared_unit_trips([7], MONTH_KEY, "s", UNITS))
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("a hung per-unit report must time out after FUEL_TELEMATICS_WAIT_S")
        assert WAIT_S - 0.1 <= time.perf_counter() - started < WAIT_S + 2
        job = fuel_routes._UNIT_TRIPS_JOBS[(MONTH_KEY[0], MONTH_KEY[1], (7,))]
        assert not job.cancelled()
        release.set()
        job.result(5)
    assert fuel_routes._UNIT_TRIPS_JOBS == {}, "finished jobs are forgotten"
    print("[PASS] Trend per-unit trips: 3 concurrent requests for one range + unit set share 1 job; a timeout still "
          "applies after FUEL_TELEMATICS_WAIT_S and never cancels the job.")


def test_refresh_failure_map_is_pruned_but_active_backoffs_stay():
    import concurrent.futures

    retry_s = wd.TELEMATICS_TTL_PARTIAL_S
    now = time.monotonic()
    expired, active = ("2026-10-01", "2026-10-27"), ("2026-10-01", "2026-10-28")
    fuel_routes._REFRESH_FAILED_AT.clear()
    fuel_routes._REFRESH_FAILED_AT.update({expired: now - retry_s - 1, active: now - 1})
    done = concurrent.futures.Future()
    done.set_result(fake_bundle())
    fuel_routes._note_refresh(("2026-10-01", "2026-10-29"), done)
    assert expired not in fuel_routes._REFRESH_FAILED_AT, "a failure past the retry window is dropped"
    assert active in fuel_routes._REFRESH_FAILED_AT, "an active back-off is kept"
    failed = concurrent.futures.Future()
    failed.set_exception(WialonAPIError("down"))
    fuel_routes._note_refresh(("2026-10-01", "2026-10-29"), failed)
    assert set(fuel_routes._REFRESH_FAILED_AT) == {active, ("2026-10-01", "2026-10-29")}
    fuel_routes._REFRESH_FAILED_AT.clear()
    print("[PASS] Refresh failure map: entries past FUEL_TELEMATICS_TTL_PARTIAL_S are pruned; active back-offs stay.")


TESTS = [
    test_hung_report_still_returns_recorded_fuel,
    test_failed_report_is_not_a_502_for_fuel_figures,
    test_dashboard_requests_share_one_report_and_reuse_it_once_finished,
    test_trend_does_not_wait_for_a_hung_fleet_trips_report,
    test_trend_runs_fleet_trips_after_the_bundle_never_alongside,
    test_trend_chain_finishes_after_timeout_and_next_request_reuses_it,
    test_pending_then_finished_report_is_served_from_cache,
    test_recorded_zero_idle_stays_zero,
    test_concurrent_requests_share_one_bundle_job,
    test_timed_out_or_cancelled_waiter_never_cancels_the_shared_build,
    test_cached_range_answers_while_every_executor_worker_is_busy,
    test_ai_tool_path_shares_the_build_and_uses_the_cache,
    test_live_range_is_refreshed_ahead_once_and_served_meanwhile,
    test_refresh_ahead_skips_closed_partial_young_and_limited_ranges,
    test_failed_refresh_keeps_the_entry_and_backs_off,
    test_refresh_trend_chain_and_route_interleaving_runs_one_build,
    test_concurrent_trend_unit_trips_share_one_job,
    test_refresh_failure_map_is_pruned_but_active_backoffs_stay,
]


if __name__ == "__main__":
    print("=" * 60)
    print("FUEL - BOUNDED WIALON TELEMATICS WAIT (OFFLINE)")
    print("=" * 60)
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
    print("=" * 60)
    print(f"{len(TESTS) - failures}/{len(TESTS)} test functions passed")
    sys.exit(1 if failures else 0)
