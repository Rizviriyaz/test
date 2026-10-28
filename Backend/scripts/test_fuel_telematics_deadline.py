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


TESTS = [
    test_hung_report_still_returns_recorded_fuel,
    test_failed_report_is_not_a_502_for_fuel_figures,
    test_dashboard_requests_share_one_report_and_reuse_it_once_finished,
    test_trend_does_not_wait_for_a_hung_fleet_trips_report,
    test_trend_runs_fleet_trips_after_the_bundle_never_alongside,
    test_trend_chain_finishes_after_timeout_and_next_request_reuses_it,
    test_pending_then_finished_report_is_served_from_cache,
    test_recorded_zero_idle_stays_zero,
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
