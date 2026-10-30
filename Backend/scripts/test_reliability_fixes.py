"""
Offline regression tests for the dashboard reliability and performance fixes (MOCKED / offline).

  - H3: overlapping vehicle-trips requests share one report, run on the vehicle session (never the shared report
    lock); a vehicle's idle events reuse a fleet-wide events report of the same window instead of running the report
  - M1: /api/vehicles shares a read for LIVE_FLEET_TTL_SECONDS (under the 10 s poll) and states its retrieval time
  - M2: pages after the first are read in parallel, every page counts rows, rows keep their order; rows added or
    removed during the read are re-read once (never lost or duplicated), still changing or a failed page fails
  - M4: the idle summary reuses a fleet-wide events report of the same window: identical payload, one report run,
    and it expires with that report; a forced refresh still re-runs the report
  - L1: a full batched sub-row page is not read again by the per-row fallback; a short one is
  - Health: a satellite count alone is never EXCELLENT, 255 is not a count, an offline unit's old voltage is not its
    current battery state (still alerted as last reported), a delayed unit keeps its classification

Run from Backend/:  python scripts/test_reliability_fixes.py
"""
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import offline_guard  # noqa: E402,F401

import app.fleet_analytics as fa  # noqa: E402
import app.fuel.db as fuel_db  # noqa: E402
import app.main as main  # noqa: E402
import app.wialon_reports as wr  # noqa: E402
from app.fuel.db import FuelDataFetchError  # noqa: E402
from app.wialon import VEHICLE_SESSION  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, TEMPLATE_GROUP_ENGINE, UNITS, FakeReportEngine, default_tables, group_engine_row,
    group_engine_subrow, live_records,
)

FROM_TS, TO_TS = BASE_TS, BASE_TS + 3 * 86400 - 1
_real_sleep = time.sleep  # _read patches time.sleep (process-wide) to skip backoff; the fake table still waits


def _run_concurrently(fn, n=6):
    results, barrier = [None] * n, threading.Barrier(n)

    def call(i):
        barrier.wait()
        results[i] = fn()

    threads = [threading.Thread(target=call, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ── H3: vehicle detail drawer ──────────────────────────────────────────────────

def test_vehicle_trips_shared_and_isolated():
    main._cache.clear()
    runs, sessions, shared_lock_held = [], [], []

    def report(sid, vehicle_id, eff_from, eff_to):
        runs.append(sid)
        shared_lock_held.append(wr._report_lock.locked())
        time.sleep(0.2)
        return {"trips": [{"distance_km": 0.0}], "incomplete_fields": [], "column_warnings": []}

    def login(force=False, session=None):
        sessions.append(session)
        return f"sid-{session or 'shared'}"

    with patch.object(main, "login", side_effect=login), \
         patch.object(main, "execute_wialon_unit_trips_report", side_effect=report):
        results = _run_concurrently(lambda: main.get_vehicle_trips(101, from_time=FROM_TS, to_time=FROM_TS + 3600))
    assert len(runs) == 1, f"6 overlapping identical requests ran {len(runs)} reports"
    assert all(r == results[0] for r in results) and results[0]["trip_count"] == 1, results
    # A recorded 0 km trip stays 0, not unknown
    assert results[0]["trips"][0]["distance_km"] == 0.0
    assert sessions == [VEHICLE_SESSION] and runs == ["sid-vehicle"], (sessions, runs)
    assert shared_lock_held == [False]
    print("[PASS] H3: 6 overlapping vehicle-trips requests -> 1 report, on the vehicle session (shared report lock free).")


def _idle(engine, **params):
    with patch("app.wialon_reports.wialon_request", engine), \
         patch("app.main.login", return_value="sid"), \
         patch("app.main.get_vehicle_groups", return_value=GROUPS), \
         patch("app.main.get_all_vehicles", return_value=UNITS), \
         patch("app.main.get_calculated_data", return_value=CALCULATED), \
         patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()):
        return main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, **params)


def _without_time(payload):
    return {k: v for k, v in payload.items() if k != "retrieved_at"}


def test_idle_summary_and_vehicle_reuse_fleet_events_report():
    # Reference payloads, each from its own report run
    main._cache.clear()
    ref_summary = _idle(FakeReportEngine(), include_events=False)
    main._cache.clear()
    ref_vehicle = _idle(FakeReportEngine(), include_events=True, vehicle_id=101)

    main._cache.clear()
    engine = FakeReportEngine()
    full = _idle(engine, include_events=True)
    assert engine.count("report/exec_report") == 1
    summary = _idle(engine, include_events=False)
    vehicle = _idle(engine, include_events=True, vehicle_id=101)
    assert engine.count("report/exec_report") == 1, "summary and one vehicle's events reuse the fleet events report"
    assert _without_time(summary) == _without_time(ref_summary), "reused summary equals a summary run of its own"
    assert _without_time(vehicle) == _without_time(ref_vehicle), "reused vehicle payload equals its own run"
    assert summary["retrieved_at"] == full["retrieved_at"] == vehicle["retrieved_at"], "states the report's read time"
    assert summary["events"] == [] and summary["events_complete"] is False
    assert all(v["idle_events"] is None for v in summary["by_vehicle"]), summary["by_vehicle"]

    # The reused summary expires with the report it came from, never later
    report_entry = main._cache.get(main._engine_report_key(f"exact-{FROM_TS}-{TO_TS}", 1))
    summary_key = f"fleet-idle-v10-exact-{FROM_TS}-{TO_TS}-grp-None-veh-None-summary-uae"
    summary_entry = main._cache.get(summary_key)
    assert summary_entry["time"] + summary_entry["ttl"] <= report_entry["time"] + report_entry["ttl"] + 0.01

    # A forced refresh re-runs the report
    _idle(engine, include_events=False, refresh=True)
    assert engine.count("report/exec_report") == 2
    print("[PASS] M4/H3: summary and a vehicle's events reuse the fleet events report (1 run, identical payloads, "
          "same read time, expiring with it); refresh re-runs.")


def test_idle_summary_does_not_satisfy_events_request():
    main._cache.clear()
    engine = FakeReportEngine()
    _idle(engine, include_events=False)
    _idle(engine, include_events=True)
    assert engine.count("report/exec_report") == 2, "a summary report has no events to reuse"
    # The wider events report replaced the summary entry, so a later summary reuses it
    _idle(engine, include_events=False, vehicle_id=102)
    assert engine.count("report/exec_report") == 2
    print("[PASS] M4: a summary report never answers an events request; the later events report is then shared.")


def test_narrowed_report_equals_direct_runs():
    engine = FakeReportEngine()
    with patch("app.wialon_reports.wialon_request", engine):
        full = wr.execute_wialon_group_engine_idle_report("s", FROM_TS, TO_TS, group_id=1)
        summary = wr.execute_wialon_group_engine_idle_report("s", FROM_TS, TO_TS, group_id=1, include_events=False)
        scoped = wr.execute_wialon_group_engine_idle_report("s", FROM_TS, TO_TS, group_id=1,
                                                           event_vehicle_names={"UNIT-B"})
    assert wr.narrow_engine_idle_result(full, False) == summary
    assert wr.narrow_engine_idle_result(full, True, {"UNIT-B"}) == scoped
    assert wr.narrow_engine_idle_result(full, True) == full
    print("[PASS] M4: a fleet events report narrowed to summary / one unit equals running that report directly.")


# ── M1: live vehicles ─────────────────────────────────────────────────────────

def test_live_vehicles_cache_follows_poll_interval():
    main._cache.clear()
    reads = []
    clock = [1_000_000.0]

    def search(sid):
        reads.append(clock[0])
        return UNITS

    with patch.object(main, "login", return_value="sid"), \
         patch.object(main, "get_all_vehicles", side_effect=search), \
         patch.object(main, "get_calculated_data", return_value=[]), \
         patch.object(main, "get_vehicle_live_data", return_value=[{"vehicle_id": 1}]), \
         patch("app.main.time.time", side_effect=lambda: clock[0]), \
         patch("app.main.now_timestamp", side_effect=lambda: int(clock[0])):
        first = main.get_vehicles()
        clock[0] += 6  # another tab or user polling in between
        second = main.get_vehicles()
        clock[0] += 4  # the same viewer's next poll, 10 s after its first
        third = main.get_vehicles()
    assert main.LIVE_FLEET_TTL_SECONDS == 8
    assert len(reads) == 2, f"a read is shared for 8 s, and a poll 10 s later reads again: {reads}"
    assert second == first and second["retrieved_at"] == 1_000_000, "a shared read keeps its own retrieval time"
    assert third["retrieved_at"] == 1_000_010 and third["count"] == 1
    print("[PASS] M1: /api/vehicles reuses a read for 8 s (other viewers), re-reads for the 10 s poll, states retrieved_at.")


def test_live_vehicles_failure_is_502_not_cached_data():
    main._cache.clear()
    from fastapi import HTTPException
    from app.wialon import WialonAPIError
    with patch.object(main, "login", return_value="sid"), \
         patch.object(main, "get_all_vehicles", side_effect=WialonAPIError("down")):
        try:
            main.get_vehicles()
            raise AssertionError("expected 502")
        except HTTPException as exc:
            assert exc.status_code == 502
    print("[PASS] M1: a failed live read is a 502, never an empty or reused fleet.")


# ── M2: Supabase pages ────────────────────────────────────────────────────────

class Resp:
    def __init__(self, payload=None, status=200, headers=None):
        self._payload = payload if payload is not None else []
        self.status_code, self.ok, self.headers = status, 200 <= status < 300, headers or {}

    def json(self):
        return self._payload


class Table:
    def __init__(self, rows, cap=None, delay=0.0, mutate=None, fail_offset=None):
        self.rows, self.cap, self.delay, self.mutate, self.fail_offset = list(rows), cap, delay, mutate, fail_offset
        self.calls, self.active, self.peak = [], 0, 0
        self._lock = threading.Lock()

    def get(self, url, params=None, headers=None, timeout=None):
        with self._lock:
            self.calls.append((dict(params), dict(headers or {})))
            self.active += 1
            self.peak = max(self.peak, self.active)
            n = len(self.calls)
        try:
            if self.delay:
                _real_sleep(self.delay)
            off, lim = int(params["offset"]), int(params["limit"])
            if self.fail_offset == off:
                return Resp(status=500)
            if self.mutate and n == 2:
                self.mutate(self.rows)
            if self.cap:
                lim = min(lim, self.cap)
            page = self.rows[off:off + lim]
            headers_out = {}
            if "count=exact" in (headers or {}).get("Prefer", ""):
                headers_out["content-range"] = f"{off}-{off + len(page) - 1}/{len(self.rows)}"
            return Resp(page, status=206, headers=headers_out)
        finally:
            with self._lock:
                self.active -= 1


ROWS = [{"id": i + 1, "transaction_date": f"2026-08-{1 + i % 28:02d}T10:00:00", "litres": 1} for i in range(4321)]


def _read(table):
    with patch.object(fuel_db._session, "get", side_effect=table.get), patch("app.fuel.db.time.sleep"):
        try:
            return fuel_db._fetch_all_pages("https://x/rest/v1/fuel_transactions",
                                            {"select": "*", "order": "transaction_date.asc,id.asc", "limit": "1000"},
                                            {"apikey": "k"}, "fuel transactions")
        except FuelDataFetchError as exc:
            return exc


def test_parallel_pages_complete_and_ordered():
    table = Table(ROWS, delay=0.05)
    started = time.perf_counter()
    rows = _read(table)
    elapsed = time.perf_counter() - started
    assert rows == ROWS, "every row, once, in the query's order"
    offsets = [c[0]["offset"] for c in table.calls]
    assert offsets[0] == "0" and sorted(offsets[1:], key=int) == ["1000", "2000", "3000", "4000"], offsets
    uncounted = [c[0]["offset"] for c in table.calls if c[1].get("Prefer") != "count=exact"]
    assert uncounted == [], f"every page, parallel ones included, counts rows: {uncounted}"
    assert table.peak > 1, "later pages overlap"
    print(f"[PASS] M2: 4,321 rows in 5 pages, pages 2-5 in parallel (peak {table.peak} at once, {elapsed:.2f}s with "
          f"50 ms/page vs >=0.25 s sequential); every page counts; order kept.")


def test_server_cap_sets_the_page_stride():
    table = Table(ROWS[:1234], cap=500)
    rows = _read(table)
    assert rows == ROWS[:1234] and sorted(int(c[0]["offset"]) for c in table.calls) == [0, 500, 1000]
    print("[PASS] M2: a server max-rows cap below the page size sets the stride (0/500/1000); nothing is skipped.")


def _mutating_table(rows, mutate, every_attempt=False):
    """A table that changes once (or on every read attempt) right after page 1 was served: a concurrent import."""
    table = Table(rows)
    state = {"pending": False, "done": False}
    real_get = table.get

    def get(url, params=None, headers=None, timeout=None):
        with table._lock:
            if params["offset"] == "0":
                state["pending"] = every_attempt or not state["done"]
            elif state["pending"]:
                mutate(table.rows)
                state["pending"], state["done"] = False, True
        return real_get(url, params=params, headers=headers, timeout=timeout)

    table.get = get
    return table


def _insert_at(position, row_id=99999):
    def mutate(rows):
        rows.insert(position, {"id": row_id + len(rows), "transaction_date": "2026-08-15T10:00:00", "litres": 1})
    return mutate


def test_insert_during_read_is_reread_never_lost():
    # (original total, insert position): first-page region; later page with the total an exact multiple of the page
    # size (the case that silently lost a row); later page, other total; the very end
    for total, position in ((2500, 10), (2000, 1500), (2335, 1500), (2000, 2000), (3000, 2999)):
        original = ROWS[:total]
        table = _mutating_table(original, _insert_at(position))
        rows = _read(table)
        assert not isinstance(rows, Exception), (total, position, rows)
        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids)) == total + 1, (total, position, len(ids), len(set(ids)))
        assert {r["id"] for r in original} <= set(ids), f"a transaction present before the insert was lost ({total}, {position})"
        assert rows == table.rows, "the result is the table as it stands after the insert, in order"
        page_reads = sum(1 for c in table.calls if c[0]["offset"] == "0")
        assert page_reads == 2, f"the inconsistent read was repeated once ({total}, {position}): {page_reads}"
    print("[PASS] M2: an insert during the read (page-1 region, later page with an exact-multiple total, other later "
          "page, end) is detected and re-read: every transaction once, none lost.")


def test_continuous_change_fails_after_one_reread():
    table = _mutating_table(ROWS[:2000], _insert_at(1500), every_attempt=True)
    res = _read(table)
    assert isinstance(res, FuelDataFetchError) and "row count changed" in str(res), res
    assert sum(1 for c in table.calls if c[0]["offset"] == "0") == 2, "one re-read, then fail"
    print("[PASS] M2: rows still changing on the re-read -> FuelDataFetchError (503), never a partial result.")


def test_delete_during_read_and_failed_page():
    def delete_tail(rows):
        del rows[1500:]

    table = _mutating_table(ROWS[:2500], delete_tail)
    rows = _read(table)
    assert rows == ROWS[:1500], "after the re-read: exactly the rows that still exist, once each"
    res = _read(Table(ROWS[:2500], fail_offset=2000))
    assert isinstance(res, FuelDataFetchError), res
    print("[PASS] M2: a delete during the read is re-read to the rows that exist; a failed page raises (no partial rows).")


# ── L1: sub-row fallback ──────────────────────────────────────────────────────

def _subrow_windows(subrow_count):
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"] = [
        group_engine_row("UNIT-A", "24:00:00", "8:00:00", "4:00:00", "4:00:00", 10.0, subrows=subrow_count)]
    tables[TEMPLATE_GROUP_ENGINE]["subrows"] = {
        0: [group_engine_subrow(j, BASE_TS + j * 60, 30) for j in range(subrow_count)]}
    engine = FakeReportEngine(tables=tables)
    with patch("app.wialon_reports.wialon_request", engine):
        g = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400, group_id=1)
    windows = [(p["indexFrom"], p["indexTo"]) for s, p in engine.calls if s == "report/get_result_subrows"]
    return g, windows


def test_full_batched_page_is_not_read_again():
    g, windows = _subrow_windows(450)
    assert len(g["events"]) == 450 and g["events_incomplete"] == []
    assert windows == [(0, 199), (200, 399), (400, 599)], windows
    print("[PASS] L1: 450 sub-rows: batched page 0 is kept; 2 more pages, not 3 (page 0 no longer re-read).")


def test_short_batched_page_is_read_again():
    class ShortBatch(FakeReportEngine):
        def __call__(self, svc, params, sid=None, **kw):
            if svc == "core/batch":
                self.calls.append((svc, params))
                return [[] for _ in params["params"]]
            return super().__call__(svc, params, sid=sid, **kw)

    tables = default_tables()
    engine = ShortBatch(tables=tables)
    with patch("app.wialon_reports.wialon_request", engine):
        g = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400, group_id=1)
    direct_engine = FakeReportEngine(tables=default_tables())
    with patch("app.wialon_reports.wialon_request", direct_engine):
        expected = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400, group_id=1)
    assert g == expected and g["events_incomplete"] == [], "an empty batched page is re-read, never trusted"
    print("[PASS] L1: an empty/short batched page is re-read from page 0, giving the same complete events.")


# ── Vehicle health ────────────────────────────────────────────────────────────

NOW = 1_800_000_000


def _health(sats=10, hdop=None, voltage=25.0, msg_age=30, last_status="MOVING"):
    vehicle = {"vehicle_id": 7, "vehicle_name": "T7", "sensors": {"battery_voltage": voltage},
               "location": {"satellites": sats, "latitude": 25.2, "longitude": 55.3},
               "last_known_status": last_status, "position_stale": False}
    lmsg = {"t": NOW - msg_age}
    if hdop is not None:
        lmsg["p"] = {"hdop": hdop}
    with patch("app.fleet_analytics.time.time", return_value=NOW):
        return fa.evaluate_vehicle_health(vehicle, raw_unit={"id": 7, "lmsg": lmsg}, calc_data={})


def test_satellites_alone_are_not_excellent():
    assert _health(sats=14)["gps"]["status"] == "GOOD", "no HDOP: at most GOOD"
    assert _health(sats=14, hdop=0.9)["gps"]["status"] == "EXCELLENT"
    assert _health(sats=14, hdop=2.0)["gps"]["status"] == "GOOD"
    assert _health(sats=9)["gps"]["status"] == "GOOD" and _health(sats=5)["gps"]["status"] == "WEAK"
    zero = _health(sats=0)
    assert zero["gps"]["status"] == "WEAK" and zero["gps"]["satellites"] == 0, "0 satellites is a real (weak) reading"
    for invalid in (255, 51, -1):
        h = _health(sats=invalid, hdop=0.9)
        assert h["gps"]["status"] == "UNKNOWN", (invalid, h["gps"])
        assert h["health_score"] == 100 - 15, (invalid, h["health_score"])
    assert _health(sats=None)["gps"]["status"] == "UNKNOWN"
    print("[PASS] Health: satellites alone are at most GOOD; EXCELLENT needs HDOP; 255/out-of-range are UNKNOWN (-15); 0 is WEAK.")


def test_offline_battery_is_not_current():
    offline = _health(voltage=22.0, msg_age=50_000)  # 24V critical, last message 13.9 h ago
    assert offline["connectivity"]["status"] == "OFFLINE"
    bat = offline["battery"]
    assert bat["status"] == "UNKNOWN" and bat["last_reported_status"] == "CRITICAL" and bat["voltage"] == 22.0, bat
    assert bat["stale"] is True and bat["label"].startswith("Last reported: "), bat
    # 100 - 10 (battery unknown) - 5 (GPS GOOD) - 35 (offline); the old reading no longer costs -40 as current
    assert offline["health_score"] == 50, offline["health_score"]

    delayed = _health(voltage=22.0, msg_age=3_600)  # 1 h: DELAYED, last seen moving
    assert delayed["connectivity"]["status"] == "DELAYED"
    assert delayed["battery"]["status"] == "CRITICAL" and delayed["battery"]["last_reported_status"] is None

    online = _health(voltage=25.0)
    assert online["battery"]["status"] == "HEALTHY" and online["battery"]["last_reported_status"] is None
    no_voltage_offline = _health(voltage=None, msg_age=50_000)
    assert no_voltage_offline["battery"]["status"] == "UNKNOWN"
    assert no_voltage_offline["battery"]["last_reported_status"] is None
    print("[PASS] Health: offline -> battery UNKNOWN (last reading kept as last_reported_status); delayed/online unchanged.")


def test_offline_last_reading_still_alerts_as_last_reported():
    offline = _health(voltage=22.0, msg_age=50_000)
    offline.update(vehicle_id=7, vehicle_name="T7", group="G")
    health = {"vehicles": [offline], "total_vehicles": 1}
    with patch.object(fa, "get_fleet_health_analytics", return_value=health), \
         patch.object(fa, "get_safety_analytics", return_value={"vehicles": [], "events": [], "by_vehicle": []}):
        try:
            alerts = fa._build_fleet_alerts(use_cache=True)
        except Exception as exc:  # the safety section may need more fields; the battery alert is what is checked
            raise AssertionError(f"alerts build failed: {exc!r}")
    battery = [a for a in alerts["alerts"] if a["alert_type"].startswith("BATTERY")]
    assert len(battery) == 1 and battery[0]["alert_type"] == "BATTERY_CRITICAL", battery
    assert "(last reported)" in battery[0]["title"], battery[0]
    print("[PASS] Health: an offline unit's last critical reading still alerts, marked '(last reported)'.")


if __name__ == "__main__":
    tests = [
        test_vehicle_trips_shared_and_isolated,
        test_idle_summary_and_vehicle_reuse_fleet_events_report,
        test_idle_summary_does_not_satisfy_events_request,
        test_narrowed_report_equals_direct_runs,
        test_live_vehicles_cache_follows_poll_interval,
        test_live_vehicles_failure_is_502_not_cached_data,
        test_parallel_pages_complete_and_ordered,
        test_server_cap_sets_the_page_stride,
        test_insert_during_read_is_reread_never_lost,
        test_continuous_change_fails_after_one_reread,
        test_delete_during_read_and_failed_page,
        test_full_batched_page_is_not_read_again,
        test_short_batched_page_is_read_again,
        test_satellites_alone_are_not_excellent,
        test_offline_battery_is_not_current,
        test_offline_last_reading_still_alerts_as_last_reported,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"[FAIL] {t.__name__}: {exc}")
    main._cache.clear()
    sys.exit(1 if failed else 0)
