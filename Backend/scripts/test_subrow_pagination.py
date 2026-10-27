"""
Regression test for Wialon idle sub-row pagination (MOCKED / offline).

Live Wialon was seen ignoring indexFrom/indexTo on report/get_result_subrows: every page returned the row's
whole interval list, so the old pager read the same intervals 500 times (unit 54836 on 2026-10-20: 313
intervals -> 156,500 sub-rows, its 54 idle events returned 27,000 times). Wialon is replaced by fakes here
and every outbound HTTP call is blocked by OfflineGuard.
Covers:
  - identical repeated pages (range ignored): read once, no duplicate events, totals unchanged
  - legitimate distinct pages (range honoured): every page read, all distinct events kept
  - empty pages, error pages, count mismatches and the page cap: never reported as complete
  - unit/group engine reports and /api/idle/fleet-analytics (hotspots, trend, KPIs) without inflation

  python scripts/test_subrow_pagination.py
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, CALCULATED, GROUPS, TEMPLATE_GROUP_ENGINE, TEMPLATE_UNIT_ENGINE, TEMPLATE_UNIT_TRIPS, UNITS, FakeReportEngine,
    default_tables, group_engine_row, group_engine_subrow, live_records, unit_engine_row, unit_engine_subrow,
)

SUBROWS = "report/get_result_subrows"
FROM_TS, TO_TS = BASE_TS, BASE_TS + 86400 - 1

# Shape of the live 54836 row on 2026-10-20: 313 intervals, 54 with idling > 0, 8,726 s idle in total
INTERVALS, IDLE_EVENTS, IDLE_TOTAL = 313, 54, 8726
IDLE_DURATIONS = [160] * (IDLE_EVENTS - 1) + [IDLE_TOTAL - 160 * (IDLE_EVENTS - 1)]
IDLE_STR = "2:25:26"


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"[PASS] {msg}")


def interval_durations():
    """Idle seconds per interval: every 5th of the first 270 intervals idles, the rest are 0 (moving/short)."""
    idle_at = {j * 5 for j in range(IDLE_EVENTS)}
    it = iter(IDLE_DURATIONS)
    return [next(it) if j in idle_at else 0 for j in range(INTERVALS)]


class RangeIgnoringEngine(FakeReportEngine):
    """Serves every report/get_result_subrows call like live Wialon: the whole list (or a fixed prefix), any range."""

    def __init__(self, *args, prefix=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.prefix = prefix

    def __call__(self, svc, params, sid=None, timeout=None, **kwargs):
        if svc != SUBROWS:
            return super().__call__(svc, params, sid=sid, timeout=timeout, **kwargs)
        self.calls.append((svc, dict(params)))
        subs = self.current["subrows"].get(params["rowIndex"], [])
        return json.loads(json.dumps(subs if self.prefix is None else subs[: self.prefix]))


def sequence(pages):
    """wialon_request stand-in returning `pages` in order (lists, or error dicts)."""
    calls = []

    def fake(svc, params, sid=None, **kwargs):
        assert svc == SUBROWS, svc
        calls.append((params["indexFrom"], params["indexTo"]))
        return pages[len(calls) - 1]
    return fake, calls


def honoured(rows):
    """wialon_request stand-in that honours indexFrom/indexTo over `rows`."""
    calls = []

    def fake(svc, params, sid=None, **kwargs):
        calls.append((params["indexFrom"], params["indexTo"]))
        return json.loads(json.dumps(rows[params["indexFrom"]:params["indexTo"] + 1]))
    return fake, calls


def distinct(rows):
    return len({json.dumps(r, sort_keys=True) for r in rows}) == len(rows)


def fetch(fake, expected=None):
    from app import wialon_reports as wr
    with patch("app.wialon_reports.wialon_request", fake):
        return wr._fetch_all_subrows("sid", 0, "test report", expected_count=expected)


def subrows(n, start=BASE_TS, step=240):
    return [group_engine_subrow(j, start + j * step, 60) for j in range(n)]


# ─── _fetch_all_subrows ──────────────────────────────────────────────────────

def test_repeated_pages():
    rows = subrows(INTERVALS)
    fake, calls = sequence([rows] * 500)
    got, complete = fetch(fake, expected=INTERVALS)
    check(len(calls) == 1, "range ignored (whole list per page): one sub-row call instead of 500")
    check(len(got) == INTERVALS and distinct(got) and complete, "range ignored: 313 distinct intervals, complete")

    fake, calls = sequence([rows] * 500)
    got, complete = fetch(fake)
    check(len(calls) == 1 and len(got) == INTERVALS and not complete,
          "range ignored without a sub-row count: read once but not claimed complete (end unverifiable)")

    # Wialon repeating one 200-row page: no duplicates, stops at the first page with nothing new, incomplete
    fake, calls = sequence([rows[:200]] * 500)
    got, complete = fetch(fake, expected=INTERVALS)
    check(len(calls) == 2 and len(got) == 200 and distinct(got) and not complete,
          "same 200-row page repeated: stops after the repeat, 200 distinct rows, flagged incomplete")


def test_distinct_pages():
    rows = subrows(450)
    fake, calls = honoured(rows)
    got, complete = fetch(fake, expected=450)
    check(calls == [(0, 199), (200, 399), (400, 599)] and got == rows and complete,
          "range honoured: 450 rows over 3 pages, all kept in order, complete")

    rows = subrows(400)
    fake, calls = honoured(rows)
    got, complete = fetch(fake, expected=400)
    check(len(calls) == 2 and got == rows and complete, "exact multiple of the page size: stops at the count, complete")

    fake, calls = honoured(rows)
    got, complete = fetch(fake)
    check(len(calls) == 3 and got == rows and complete, "no count: an empty third page ends the list, complete")


def test_empty_pages():
    fake, _ = sequence([[]])
    got, complete = fetch(fake, expected=3)
    check(got == [] and not complete, "empty page when 3 sub-rows are expected: incomplete")

    fake, _ = sequence([[]])
    got, complete = fetch(fake)
    check(got == [] and complete, "empty page with no expected count: complete (nothing to read)")


def test_incomplete_results():
    rows = subrows(450)
    fake, _ = sequence([rows[:200], {"error": 5}])
    got, complete = fetch(fake, expected=450)
    check(got == rows[:200] and not complete, "error on the second page: 200 rows kept, incomplete")

    fake, _ = honoured(subrows(3))
    got, complete = fetch(fake, expected=5)
    check(len(got) == 3 and not complete, "fewer distinct rows than the row's count: incomplete")

    fake, _ = honoured(subrows(6))
    got, complete = fetch(fake, expected=5)
    check(len(got) == 6 and not complete, "more distinct rows than the row's count: incomplete")

    fake, calls = honoured(subrows(450))
    with patch("app.wialon_reports.REPORT_SUBROWS_MAX_PAGES", 2):
        got, complete = fetch(fake)
    check(len(calls) == 2 and len(got) == 400 and not complete, "page cap reached: truncated list flagged incomplete")


def test_identical_events():
    """
    Sub-rows are de-duplicated by content. Two sub-rows of one row with identical content (same t1/t2 and cells)
    cannot be told apart, so they must never be silently merged into a list reported as complete: the row's
    sub-row count ("d") exposes the shortfall and the event list is flagged incomplete instead.
    """
    from app import wialon_reports as wr
    from app.wialon import WialonAPIError

    same = group_engine_subrow(0, BASE_TS, 600)
    fake, _ = honoured([same, json.loads(json.dumps(same))])
    got, complete = fetch(fake, expected=2)
    check(len(got) == 1 and not complete, "two identical sub-rows for a count of 2: merged but flagged incomplete")

    twin = dict(json.loads(json.dumps(same)), n=1)
    fake, _ = honoured([dict(same, n=0), twin])
    got, complete = fetch(fake, expected=2)
    check(len(got) == 2 and complete, "identical cells but distinct row index 'n': both events kept, complete")

    a, b = group_engine_subrow(0, BASE_TS, 600), group_engine_subrow(0, BASE_TS + 600, 600)
    a["c"][1] = b["c"][1] = {"t": "same text", "v": None}
    a["c"][3] = b["c"][3] = {"t": "same text", "v": None}
    fake, _ = honoured([a, b])
    got, complete = fetch(fake, expected=2)
    check(len(got) == 2 and complete, "identical cell text but distinct t1/t2: both events kept, complete")

    engine = FakeReportEngine()
    engine.current = {"subrows": {0: [same, json.loads(json.dumps(same))]}}
    with patch("app.wialon_reports.wialon_request", engine):
        out = wr._fetch_rows_subrows("s", [{"d": 2}], "test report")
    check(len(out[0][0]) == 1 and out[0][1] is False, "batched first page with identical sub-rows: flagged incomplete")

    tables = default_tables()
    tables[TEMPLATE_UNIT_ENGINE]["subrows"][0] = [unit_engine_subrow(0, BASE_TS + 600, 1800)] * 2
    tables[TEMPLATE_GROUP_ENGINE]["subrows"][0] = [group_engine_subrow(0, BASE_TS + 3600, 1800)] * 3
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)):
        u = wr.execute_wialon_unit_engine_report("s", 101, FROM_TS, TO_TS)
        g = wr.execute_wialon_group_engine_idle_report("s", FROM_TS, TO_TS, group_id=1)
    check("idle_events" in u["incomplete_fields"] and u["total_idle_seconds"] == 5400,
          "unit report, identical sub-rows: idle_events flagged incomplete, row idle total (5,400 s) kept")
    a_row = next(v for v in g["vehicles"] if v["vehicle_name"] == "UNIT-A")
    check(g["events_incomplete"] == ["UNIT-A"] and a_row["idle_events"] is None and a_row["total_idle_seconds"] == 5400,
          "group report, identical sub-rows: events unknown (None), row idle total (5,400 s) kept")

    tables = default_tables()
    day = dict(tables[TEMPLATE_UNIT_TRIPS]["rows"][0], d=2)
    tables[TEMPLATE_UNIT_TRIPS]["rows"] = [day]
    tables[TEMPLATE_UNIT_TRIPS]["subrows"] = {0: [dict(day, d=0)] * 2}
    try:
        with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)):
            wr.execute_wialon_unit_trips_report("s", 101, FROM_TS, TO_TS)
        raised = False
    except WialonAPIError:
        raised = True
    check(raised, "trips report, identical trip sub-rows: raises instead of undercounting trips and distance")


# ─── Reports and endpoint ────────────────────────────────────────────────────

def tables_54836_like():
    tables = default_tables()
    durs = interval_durations()
    tables[TEMPLATE_GROUP_ENGINE]["rows"][0] = group_engine_row(
        "UNIT-A", "24:00:00", "6:00:00", "3:00:00", IDLE_STR, 50.0, subrows=INTERVALS)
    tables[TEMPLATE_GROUP_ENGINE]["subrows"] = {0: [group_engine_subrow(j, BASE_TS + j * 240, d) for j, d in enumerate(durs)],
                                                1: tables[TEMPLATE_GROUP_ENGINE]["subrows"][1]}  # UNIT-B's one interval
    tables[TEMPLATE_UNIT_ENGINE]["rows"] = [
        unit_engine_row(1, BASE_TS, "24:00:00", "6:00:00", "3:00:00", IDLE_STR, 50.0, subrows=INTERVALS)]
    tables[TEMPLATE_UNIT_ENGINE]["subrows"] = {0: [unit_engine_subrow(j, BASE_TS + j * 240, d) for j, d in enumerate(durs)]}
    return tables


def check_events(events, label):
    keys = {(e["start_time"], e["end_time"]) for e in events}
    check(len(events) == IDLE_EVENTS == len(keys), f"{label}: 54 idle events, no duplicates")
    check(sum(e["duration"] for e in events) == IDLE_TOTAL, f"{label}: event durations sum to 8,726 s")


def test_reports():
    from app import wialon_reports as wr
    engine = RangeIgnoringEngine(tables=tables_54836_like())
    with patch("app.wialon_reports.wialon_request", engine):
        g = wr.execute_wialon_group_engine_idle_report("s", FROM_TS, TO_TS, group_id=1)
    a = next(v for v in g["vehicles"] if v["vehicle_name"] == "UNIT-A")
    check([p["rowIndex"] for s, p in engine.calls if s == SUBROWS].count(0) == 1,
          "group report, range ignored: one sub-row call for the 313-interval unit")
    check(a["total_idle_seconds"] == IDLE_TOTAL and a["idle_events"] == IDLE_EVENTS and not a["incomplete_fields"]
          and g["events_incomplete"] == [], "group report: 8,726 s idle, 54 events, complete")
    check_events(a["events"], "group report")

    engine = RangeIgnoringEngine(tables=tables_54836_like())
    with patch("app.wialon_reports.wialon_request", engine):
        u = wr.execute_wialon_unit_engine_report("s", 101, FROM_TS, TO_TS)
    check(engine.count(SUBROWS) == 1 and u["total_idle_seconds"] == IDLE_TOTAL and "idle_events" not in u["incomplete_fields"],
          "unit report, range ignored: one sub-row call, 8,726 s idle, complete")
    check_events(u["idle_events"], "unit report")

    # Wialon returning only the first 200 intervals for every page: incomplete, never duplicated
    engine = RangeIgnoringEngine(tables=tables_54836_like(), prefix=200)
    with patch("app.wialon_reports.wialon_request", engine):
        g = wr.execute_wialon_group_engine_idle_report("s", FROM_TS, TO_TS, group_id=1)
        u = wr.execute_wialon_unit_engine_report("s", 101, FROM_TS, TO_TS)
    a = next(v for v in g["vehicles"] if v["vehicle_name"] == "UNIT-A")
    keys = {(e["start_time"], e["end_time"]) for e in a["events"]}
    check(g["events_incomplete"] == ["UNIT-A"] and a["idle_events"] is None and a["total_idle_seconds"] == IDLE_TOTAL
          and len(keys) == len(a["events"]) < IDLE_EVENTS,
          "group report, truncated sub-rows: events unknown (None), total kept, no duplicates")
    check("idle_events" in u["incomplete_fields"] and u["total_idle_seconds"] == IDLE_TOTAL,
          "unit report, truncated sub-rows: idle_events flagged incomplete, total kept")


def test_endpoint():
    import app.main as main
    engine = RangeIgnoringEngine(tables=tables_54836_like())
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
        res = main.get_fleet_idle_analytics(from_time=FROM_TS, to_time=TO_TS, skip_cache=True)
    finally:
        for p in reversed(patches):
            p.stop()
    a_events = [e for e in res["events"] if e["vehicle_name"] == "UNIT-A"]
    check_events(a_events, "/api/idle/fleet-analytics UNIT-A")
    n, secs = len(res["events"]), sum(e["duration"] for e in res["events"])
    check(res["kpis"]["idle_event_count"] == n == IDLE_EVENTS + 1,
          "endpoint KPI idle_event_count = 54 + UNIT-B's single interval")
    check(sum(t["event_count"] for t in res["trend"]) == n and sum(t["idle_seconds"] for t in res["trend"]) == secs,
          "endpoint trend: event counts and idle seconds match the de-duplicated events")
    hot = [h for h in res["by_location"] if "UNIT-A" in h["vehicles"]]
    check(sum(h["event_count"] for h in hot) == IDLE_EVENTS and sum(h["total_idle_seconds"] for h in hot) == IDLE_TOTAL,
          "endpoint hotspots: UNIT-A clusters hold 54 events / 8,726 s")


def main():
    guard = OfflineGuard().start()
    try:
        test_repeated_pages()
        test_distinct_pages()
        test_empty_pages()
        test_incomplete_results()
        test_identical_events()
        test_reports()
        test_endpoint()
    finally:
        guard.stop()
    check(not guard.attempts, "no live network calls were attempted")
    print("\nAll sub-row pagination checks passed.")


if __name__ == "__main__":
    main()
