"""
Regression test for /api/idle/fleet-analytics report work (MOCKED / offline).

Wialon is replaced by FakeReportEngine and every outbound HTTP call is blocked by OfflineGuard.
Covers:
  - include_events=false never pages idle sub-rows and keeps the authoritative idle totals
  - vehicle_id / group_id requests page sub-rows only for the targeted units, and the response is
    identical to the previous behaviour (every unit's sub-rows paged, then filtered)
  - execute_wialon_group_engine_idle_report(event_vehicle_names=...) keeps all totals and marks
    non-targeted units' event counts unknown (None)
  - report lock wait/hold time is logged as PERF evidence

  python scripts/test_idle_event_scoping.py
"""

import json
import logging
import os
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, UNITS, FakeReportEngine, live_records, normalize_for_compare,
)

FROM_TS, TO_TS = BASE_TS, BASE_TS + 3 * 86400 - 1
SUBROWS = "report/get_result_subrows"


def subrow_rows(engine):
    return sorted({p["rowIndex"] for s, p in engine.calls if s == SUBROWS})


def run_endpoint(engine, unscoped=False, **params):
    """Call the endpoint against `engine`; unscoped=True reproduces the previous behaviour (no event scoping)."""
    import app.main as main
    from app import wialon_reports as wr

    def unscoped_report(*args, **kwargs):
        kwargs.pop("event_vehicle_names", None)
        return wr.execute_wialon_group_engine_idle_report(*args, **kwargs)

    patches = [
        patch("app.wialon_reports.wialon_request", engine),
        patch("app.main.login", return_value="sid"),
        patch("app.main.get_vehicle_groups", return_value=GROUPS),
        patch("app.main.get_all_vehicles", return_value=UNITS),
        patch("app.main.get_calculated_data", return_value=CALCULATED),
        patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()),
    ]
    if unscoped:
        patches.append(patch("app.main.execute_wialon_group_engine_idle_report", side_effect=unscoped_report))
    for p in patches:
        p.start()
    try:
        main._cache.clear()
        res = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, skip_cache=True, **params)
    finally:
        for p in reversed(patches):
            p.stop()
    return normalize_for_compare(json.loads(json.dumps(res, default=str)))


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def test_summary_skips_subrows():
    engine = FakeReportEngine()
    summary = run_endpoint(engine, include_events=False)
    check(engine.count(SUBROWS) == 0, "include_events=false pages no idle sub-rows")
    full = run_endpoint(FakeReportEngine(), include_events=True)
    check(summary["kpis"]["total_idle_seconds"] == full["kpis"]["total_idle_seconds"] == 5400 + 4530,
          "summary and full idle totals match the report (9930 s)")
    check(summary["events"] == [] and all(v["idle_events"] is None for v in summary["by_vehicle"]),
          "summary leaves events empty and event counts unknown (unchanged contract)")


def test_scoped_requests_match_previous_behaviour():
    cases = [
        ("vehicle_id=101 (sub-row unit)", {"vehicle_id": 101}, [0]),
        ("vehicle_id=102 (single-interval unit, one sub-row)", {"vehicle_id": 102}, [1]),
        ("vehicle_id=103 (no idle)", {"vehicle_id": 103}, []),
        ("group_id=7", {"group_id": 7}, [0, 1]),
        ("group_id=8", {"group_id": 8}, []),
        ("unknown group_id falls back to the fleet", {"group_id": 999}, [0, 1]),
        ("fleet-wide", {}, [0, 1]),
    ]
    for label, params, expected_rows in cases:
        scoped_engine, old_engine = FakeReportEngine(), FakeReportEngine()
        scoped = run_endpoint(scoped_engine, include_events=True, **params)
        previous = run_endpoint(old_engine, unscoped=True, include_events=True, **params)
        check(scoped == previous, f"{label}: response identical to previous unscoped behaviour")
        check(subrow_rows(scoped_engine) == expected_rows,
              f"{label}: sub-rows paged only for rows {expected_rows} (previously {subrow_rows(old_engine)})")


def test_report_scoping_keeps_totals():
    from app import wialon_reports as wr

    with patch("app.wialon_reports.wialon_request", FakeReportEngine()):
        full = wr.execute_wialon_group_engine_idle_report("sid", FROM_TS, TO_TS, group_id=1)
    engine = FakeReportEngine()
    with patch("app.wialon_reports.wialon_request", engine):
        scoped = wr.execute_wialon_group_engine_idle_report(
            "sid", FROM_TS, TO_TS, group_id=1, event_vehicle_names={"UNIT-B"})
    check(subrow_rows(engine) == [1], "report: sub-rows paged only for the targeted unit (UNIT-B, row 1)")
    for a, b in zip(full["vehicles"], scoped["vehicles"]):
        for key in ("total_idle_seconds", "engine_hours_seconds", "in_motion_seconds", "total_time_seconds", "mileage_km"):
            if a[key] != b[key]:
                raise AssertionError(f"{a['vehicle_name']} {key}: {a[key]} != {b[key]}")
    check(True, "report: every unit's totals unchanged by event scoping")
    by_name = {v["vehicle_name"]: v for v in scoped["vehicles"]}
    check(by_name["UNIT-A"]["idle_events"] is None and by_name["UNIT-A"]["events"] == [],
          "report: non-targeted unit has unknown event count and no events")
    check(by_name["UNIT-B"]["idle_events"] == 1 and [e["vehicle_name"] for e in scoped["events"]] == ["UNIT-B"],
          "report: targeted single-interval unit keeps its event")


def test_lock_wait_is_logged():
    from app import wialon_reports as wr

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = Capture(level=logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        holder = threading.Thread(target=lambda: (wr._report_lock.acquire(), time.sleep(0.3), wr._report_lock.release()))
        holder.start()
        time.sleep(0.05)
        with patch("app.wialon_reports.wialon_request", FakeReportEngine()):
            wr.execute_wialon_group_engine_idle_report("sid", FROM_TS, TO_TS, group_id=1, include_events=False)
        holder.join()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    lines = [r for r in records if r.startswith("PERF report_lock") and "report=group_engine" in r]
    check(len(lines) == 1, "report lock wait/hold logged once per report")
    wait_ms = float(lines[0].split("wait_ms=")[1].split()[0])
    check(wait_ms >= 200, f"logged wait_ms reflects time queued behind another report ({wait_ms:.0f} ms)")


def test_concurrent_identical_requests_share_one_report():
    import app.main as main

    engine = FakeReportEngine()

    def slow_engine(svc, *args, **kwargs):
        if svc == "report/exec_report":
            time.sleep(0.2)  # keep the first report running while the other requests arrive
        return engine(svc, *args, **kwargs)

    patches = [
        patch("app.wialon_reports.wialon_request", slow_engine),
        patch("app.main.login", return_value="sid"),
        patch("app.main.get_vehicle_groups", return_value=GROUPS),
        patch("app.main.get_all_vehicles", return_value=UNITS),
        patch("app.main.get_calculated_data", return_value=CALCULATED),
        patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()),
    ]
    for p in patches:
        p.start()
    try:
        main._cache.clear()
        results = [None] * 3

        def call(i):
            results[i] = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True)

        threads = [threading.Thread(target=call, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check(engine.count("report/exec_report") == 1, "3 overlapping identical requests run the Wialon report once")
        check(results[0] is not None and results[0] == results[1] == results[2], "overlapping requests get the same payload")
        main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=True, refresh=True)
        check(engine.count("report/exec_report") == 2, "a refresh still recomputes instead of reusing the cached result")
        check(not main._flight_locks, "per-key request locks are released once no request is waiting")
    finally:
        for p in reversed(patches):
            p.stop()
        main._cache.clear()


if __name__ == "__main__":
    guard = OfflineGuard().start()
    try:
        test_summary_skips_subrows()
        test_scoped_requests_match_previous_behaviour()
        test_report_scoping_keeps_totals()
        test_lock_wait_is_logged()
        test_concurrent_identical_requests_share_one_report()
    finally:
        guard.stop()
    if guard.attempts:
        raise AssertionError(f"Live calls attempted: {guard.attempts}")
    print("\nAll idle event scoping tests passed.")
