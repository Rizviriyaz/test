"""
Regression test for idle-event completeness downstream of the engine reports (MOCKED / offline).

When Wialon cannot deliver a unit's idle intervals (sub-row error, truncated list, unreadable idle cell), the
event list is partial. Event-derived values must then be unknown (None) or flagged, never shown as complete:
  - /api/vehicles/{id}/idle: idle_periods / longest / average None, events_complete false, periods kept
  - /api/idle/fleet-analytics: events_complete false whenever trend / hotspots / events are partial
  - fuel get_vehicle_telematics_detail: idle_events_count None, idle_events_complete false, a warning;
    the "no transactions" placeholder reports the count as unknown, not 0
Complete lists keep their previous values (plus the additive completeness flags).
Wialon is replaced by FakeReportEngine and every outbound HTTP call is blocked by OfflineGuard.

  python scripts/test_idle_completeness.py
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, TEMPLATE_UNIT_ENGINE, UNITS, FakeReportEngine, default_tables, live_records,
)
from test_fuel_telematics_phase2 import CLOSED_FROM, CLOSED_TO, Env  # noqa: E402

import app.fuel.wialon_distance as wd  # noqa: E402
import app.main as main  # noqa: E402
from app.fuel.routes import _unavailable_activity  # noqa: E402

FROM_TS, TO_TS = BASE_TS, BASE_TS + 3 * 86400 - 1
VEHICLE_SUMMARY_KEYS = {"idle_periods", "total_idle_seconds", "total_idle_minutes", "total_engine_seconds",
                        "total_engine_hours", "longest_idle_seconds", "average_idle_seconds"}
FLEET_KEYS = {"timeframe", "kpis", "by_group", "by_vehicle", "by_location", "trend", "events", "groups",
              "ambiguous_vehicle_names"}


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def unreadable_idle_tables():
    tables = default_tables()
    tables[TEMPLATE_UNIT_ENGINE]["rows"][0]["c"][8] = ""  # first day's Idling cell unreadable
    return tables


# ─── /api/vehicles/{id}/idle ─────────────────────────────────────────────────

def vehicle_idle(engine):
    main._cache.clear()
    with patch("app.wialon_reports.wialon_request", engine), patch("app.main.login", return_value="sid"):
        return main.get_vehicle_idle(101, from_time=FROM_TS, to_time=TO_TS)


def test_vehicle_idle():
    res = vehicle_idle(FakeReportEngine())
    s = res["summary"]
    check(set(s) == VEHICLE_SUMMARY_KEYS and res["events_complete"] is True,
          "vehicle idle, complete list: summary keys unchanged, events_complete true")
    check(s["idle_periods"] == 3 and s["longest_idle_seconds"] == 1800 and s["average_idle_seconds"] == 1800.0
          and s["total_idle_seconds"] == 5400 and len(res["periods"]) == 3,
          "vehicle idle, complete list: 3 periods, longest 1,800 s, average 1,800 s, total 5,400 s (unchanged)")

    res = vehicle_idle(FakeReportEngine(subrow_error_rows={0}))
    s = res["summary"]
    check(res["events_complete"] is False and "idle_events" in res["incomplete_fields"],
          "vehicle idle, sub-row error: events_complete false, idle_events flagged")
    check(s["idle_periods"] is None and s["longest_idle_seconds"] is None and s["average_idle_seconds"] is None,
          "vehicle idle, sub-row error: period count, longest and average unknown (None), not 1 / 1,800 / 5,400")
    check(s["total_idle_seconds"] == 5400 and len(res["periods"]) == 1,
          "vehicle idle, sub-row error: report idle total kept, the one period read is still listed")
    check(main.cached("vehicle-idle-v6-101-exact-%s-%s-uae" % (FROM_TS, TO_TS), ttl_seconds=60) is None,
          "vehicle idle, sub-row error: result not cached")

    res = vehicle_idle(FakeReportEngine(tables=unreadable_idle_tables()))
    s = res["summary"]
    check(res["events_complete"] is False and s["total_idle_seconds"] is None and s["idle_periods"] is None
          and s["longest_idle_seconds"] is None and s["average_idle_seconds"] is None,
          "vehicle idle, unreadable idle cell: total and every period-derived value unknown")


# ─── /api/idle/fleet-analytics ───────────────────────────────────────────────

def fleet_idle(engine, include_events=True):
    patches = [
        patch("app.wialon_reports.wialon_request", engine),
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
        return main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, include_events=include_events,
                                             skip_cache=True)
    finally:
        for p in reversed(patches):
            p.stop()


def test_fleet_idle():
    res = fleet_idle(FakeReportEngine())
    check(FLEET_KEYS <= set(res) and res["events_complete"] is True and res["kpis"]["idle_event_count"] == 4,
          "fleet idle, complete lists: existing keys kept, events_complete true, 4 events")
    check(sum(t["event_count"] for t in res["trend"]) == 4 and sum(h["event_count"] for h in res["by_location"]) == 4,
          "fleet idle, complete lists: trend and hotspots cover all 4 events")

    res = fleet_idle(FakeReportEngine(subrow_error_rows={0}))
    a = next(v for v in res["by_vehicle"] if v["vehicle_name"] == "UNIT-A")
    check(res["events_complete"] is False and a["idle_events"] is None and res["kpis"]["idle_event_count"] is None,
          "fleet idle, UNIT-A sub-rows failed: events_complete false, UNIT-A and KPI event counts unknown")
    check(isinstance(res["trend"], list) and isinstance(res["by_location"], list)
          and all(e["vehicle_name"] != "UNIT-A" for e in res["events"]),
          "fleet idle, partial: trend / hotspots / events keep their shape and contain only events actually read")
    check(a["total_idle_seconds"] == 5400 and res["kpis"]["total_idle_seconds"] == 5400 + 4530,
          "fleet idle, partial: authoritative idle totals unchanged")

    res = fleet_idle(FakeReportEngine(), include_events=False)
    check(res["events_complete"] is False and res["events"] == [],
          "fleet idle, events not requested: events_complete false (trend/hotspots carry no events)")


# ─── Fuel vehicle activity ───────────────────────────────────────────────────

def test_fuel_activity():
    with Env():
        d = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
    check(d["idle_events_count"] == 3 and d["idle_events_complete"] is True and len(d["idle_events"]) == 3
          and d["status"] == "complete" and not any("idle events" in w for w in d["warnings"]),
          "fuel activity, complete list: 3 idle sessions, complete, no warning")

    with Env(engine=FakeReportEngine(subrow_error_rows={0})):
        d = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
    check(d["idle_events_count"] is None and d["idle_events_complete"] is False and len(d["idle_events"]) == 1,
          "fuel activity, sub-row error: idle session count unknown (None), not 1; flag false")
    check(any("idle events could not be read" in w for w in d["warnings"]),
          "fuel activity, sub-row error: warning explains the missing count")
    check(d["status"] == "complete" and d["idle_duration_hours"] == 1.5 and d["engine_hours"] == 8.5,
          "fuel activity, sub-row error: durations and status (used by efficiency) unchanged")

    with Env(engine=FakeReportEngine(tables=unreadable_idle_tables())):
        d = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
    check(d["idle_events_count"] is None and d["idle_events_complete"] is False and d["idle_duration_hours"] is None
          and not any("idle events could not be read" in w for w in d["warnings"]),
          "fuel activity, unreadable idle cell: count unknown; the existing duration warning covers it")

    p = _unavailable_activity("no transactions")
    check(p["idle_events_count"] is None and p["idle_events_complete"] is False and p["status"] == "unavailable",
          "fuel activity placeholder (no transactions): idle session count unknown, not 0")


def main_entry():
    guard = OfflineGuard().start()
    try:
        test_vehicle_idle()
        test_fleet_idle()
        test_fuel_activity()
    finally:
        guard.stop()
    check(not guard.attempts, "no live network calls were attempted")
    print("\nAll idle completeness checks passed.")


if __name__ == "__main__":
    main_entry()
