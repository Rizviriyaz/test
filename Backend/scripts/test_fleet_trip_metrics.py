"""
Offline tests for fleet trip metrics (audit item H2): trip counts are never inferred from mileage or
driving time.

  - group trips report path: rows without a readable trip count give None trip metrics (distance unchanged);
    template 5's sub-row counts give real per-unit counts, totals and group sums
  - group trips parser: trip count from each unit row's sub-row count ("-----" placeholder = 0)
  - unit trips report path: real counts for several trips per vehicle and for zero trips
  - zero trips fleet-wide: total 0, no average or longest trip (None, not 0)
  - utilization (group engine report): trip_count None
  - group analytics trips_24h: sum when every count is known, None when any is unknown or missing
  - API: /api/fleet/trips and /api/dashboard/summary return null, not a vehicle count

Wialon functions are mocked in app.fleet_analytics; auth is mocked. Every outbound HTTP call is blocked
by OfflineGuard. Run from Backend/:  python scripts/test_fleet_trip_metrics.py
"""
import os
import sys
import traceback
from contextlib import ExitStack
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

import app.fleet_analytics as fa  # noqa: E402
from app.main import app, _cache  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

FLEET = [
    {"vehicle_id": 101, "vehicle_name": "UNIT-A", "group": "Logistics", "status": "MOVING", "speed": 40,
     "ignition": True, "latitude": 25.2, "longitude": 55.3, "connection_status": "STABLE", "is_offline": False},
    {"vehicle_id": 102, "vehicle_name": "UNIT-B", "group": "Logistics", "status": "STOPPED", "speed": 0,
     "ignition": False, "latitude": 25.1, "longitude": 55.2, "connection_status": "STABLE", "is_offline": False},
    {"vehicle_id": 103, "vehicle_name": "UNIT-C", "group": "Contracts", "status": "STOPPED", "speed": 0,
     "ignition": False, "latitude": 25.0, "longitude": 55.1, "connection_status": "STABLE", "is_offline": False},
]
RAW_UNITS = [{"id": v["vehicle_id"], "nm": v["vehicle_name"], "sens": {}} for v in FLEET]
CALC = [{"i": v["vehicle_id"], "mileage": {"value": 1000.0 * v["vehicle_id"]}} for v in FLEET]
GROUPS = [
    {"id": 7, "nm": "Logistics", "u": [101, 102]},
    {"id": 8, "nm": "Contracts", "u": [103]},
    {"id": 9, "nm": "Empty Group", "u": []},
]
GROUP_TRIPS_ROWS = [
    {"vehicle_name": "UNIT-A", "mileage_km": 250.75, "avg_speed": "45 km/h", "max_speed": "110 km/h"},
    {"vehicle_name": "UNIT-B", "mileage_km": 1234.5, "avg_speed": "50 km/h", "max_speed": "95 km/h"},
    {"vehicle_name": "UNIT-C", "mileage_km": 0.0, "avg_speed": "0 km/h", "max_speed": "0 km/h"},
]


def _trip(km, minutes, start):
    return {"start_time": start, "end_time": start + minutes * 60, "distance_km": km, "duration_minutes": float(minutes),
            "moving_duration": "0:30:00", "moving_duration_str": "0:30:00", "max_speed": "80 km/h",
            "average_speed": "40 km/h", "start_location": "From", "end_location": "To"}


# UNIT-A: 3 trips, UNIT-B: 1 trip, UNIT-C: 0 trips
UNIT_TRIPS = {
    101: [_trip(10.5, 35, 1000), _trip(21.0, 40, 9000), _trip(4.5, 15, 20000)],
    102: [_trip(60.0, 50, 5000)],
    103: [],
}


def _base_patches(stack, group_trips=GROUP_TRIPS_ROWS, unit_trips=UNIT_TRIPS):
    fa._analytics_cache.clear()
    _cache.clear()
    stack.enter_context(patch.object(fa, "login", return_value="sid"))
    stack.enter_context(patch.object(fa, "get_vehicle_groups", return_value=GROUPS))
    stack.enter_context(patch.object(fa, "get_all_vehicles", return_value=RAW_UNITS))
    stack.enter_context(patch.object(fa, "get_calculated_data", return_value=CALC))
    stack.enter_context(patch.object(fa, "get_vehicle_live_data", return_value=[dict(v) for v in FLEET]))
    if group_trips is None:
        gt = patch.object(fa, "execute_wialon_group_trips_report", side_effect=WialonAPIError("group report failed"))
    else:
        gt = patch.object(fa, "execute_wialon_group_trips_report", return_value=[dict(r) for r in group_trips])
    stack.enter_context(gt)
    stack.enter_context(patch.object(
        fa, "execute_wialon_unit_trips_report",
        side_effect=lambda sid, vid, f, t: {"trips": [dict(x) for x in unit_trips[vid]], "stats": {}},
    ))


def test_group_report_path_trip_metrics_unavailable():
    with ExitStack() as stack:
        _base_patches(stack)
        res = fa.get_trips_mileage_analytics(hours=24)
    s = res["summary"]
    for key in ("total_trips", "average_trip_distance_km", "average_trip_duration_minutes", "longest_trip_km"):
        assert s[key] is None, (key, s[key])
    assert all(v["trip_count"] is None for v in res["vehicles"]), res["vehicles"]
    assert all(g["trips"] is None for g in res["by_group"]), res["by_group"]
    # Distance is real report data and must be unchanged; longest trip must not be a vehicle's daily mileage
    assert s["total_distance_km"] == 1485.25, s
    assert s["longest_trip_km"] != 1234.5
    assert {g["group"]: g["distance_km"] for g in res["by_group"]} == {"Logistics": 1485.25, "Contracts": 0.0}
    print("[PASS] Group report path: trip count, averages, longest trip and group trips are None; distance unchanged.")


def test_group_trips_parser_reads_wialon_trip_counts():
    # Shapes seen live (template 5, detailed): a unit's sub-row count "d" is its trip count; a unit without
    # trips has one "-----" placeholder sub-row (d=1); a trip start with d=0 means detail is off (unknown)
    import app.wialon_reports as wr
    from wialon_report_fixtures import GROUP_TRIPS_HEADER, GROUPS as REPORT_GROUPS, FakeReportEngine, TEMPLATE_GROUP_TRIPS, group_trip_row

    def row(name, km, d, beginning=None):
        r = group_trip_row(name, km)
        r["d"] = d
        if beginning is not None:
            r["c"][1] = beginning
        return r

    rows = [row("UNIT-A", 35.51, 2), row("UNIT-B", 0.0, 1, beginning="-----"), row("UNIT-C", 12.0, 0)]
    engine = FakeReportEngine(tables={TEMPLATE_GROUP_TRIPS: {"header": GROUP_TRIPS_HEADER, "rows": rows, "subrows": {}}})
    with patch.object(wr, "wialon_request", engine), patch.object(wr, "get_vehicle_groups", return_value=REPORT_GROUPS):
        res = {r["vehicle_name"]: r["trip_count"] for r in wr.execute_wialon_group_trips_report("sid", 0, 1)}
        assert res == {"UNIT-A": 2, "UNIT-B": 0, "UNIT-C": None}, res
        assert engine.count("report/get_result_subrows") == 0, "counts come from the rows, no sub-row calls"

        headerless = FakeReportEngine(include_header=False,
                                      tables={TEMPLATE_GROUP_TRIPS: {"header": GROUP_TRIPS_HEADER, "rows": rows, "subrows": {}}})
        with patch.object(wr, "wialon_request", headerless):
            res = wr.execute_wialon_group_trips_report("sid", 0, 1)
        assert all(r["trip_count"] is None for r in res), "a guessed Beginning column gives no count"
    print("[PASS] Group trips parser: Wialon trip count from sub-row count; placeholder 0; detail off/no header None.")


def test_unit_trips_parser_expands_day_rows():
    # Template 4 (seen live) groups trips by day: a day row with sub-rows ("d") summarises its trips, which are
    # the sub-rows. A trip start without a position is plain text; the sub-row's t1/t2 carry the times.
    import app.wialon_reports as wr
    from wialon_report_fixtures import UNIT_TRIPS_HEADER, FakeReportEngine, TEMPLATE_UNIT_TRIPS, unit_trip_row

    t1, t2, t3 = (unit_trip_row(i, 1000 + i * 7200, 30, "0:25:00", 10.0 * i) for i in (1, 2, 3))
    t1["c"][1] = "00:16:40"  # no start position: plain text cell
    day1 = dict(unit_trip_row(9, 1000, 300, "1:00:00", 30.0), d=2)
    day2 = dict(unit_trip_row(9, 90000, 300, "0:30:00", 30.0), d=1)
    tables = {TEMPLATE_UNIT_TRIPS: {"header": UNIT_TRIPS_HEADER, "rows": [day1, day2], "subrows": {0: [t1, t2], 1: [t3]}}}
    engine = FakeReportEngine(tables=tables)
    with patch.object(wr, "wialon_request", engine):
        rep = wr.execute_wialon_unit_trips_report("sid", 101, 0, 200000)
    assert [t["distance_km"] for t in rep["trips"]] == [10.0, 20.0, 30.0], rep["trips"]
    assert rep["trips"][0]["start_time"] == t1["t1"] and rep["trips"][0]["start_time_str"] == "00:16:40", rep["trips"][0]
    assert rep["trips"][2]["end_time"] == t3["t2"], rep["trips"][2]
    # Every day's trips come from one core/batch call, not one get_result_subrows round trip per day
    assert engine.count("core/batch") == 1 and engine.count("report/get_result_subrows") == 2, engine.calls
    batch_engine_trips = rep["trips"]

    # A failed batch, or a batched page short of the day's "d" trips, is re-read per row with the same result
    class BatchFailingEngine(FakeReportEngine):
        def __call__(self, svc, params, sid=None, timeout=None, **kwargs):
            if svc == "core/batch":
                self.calls.append((svc, params))
                return {"error": 4}
            return super().__call__(svc, params, sid=sid, timeout=timeout, **kwargs)

    class ShortBatchEngine(FakeReportEngine):
        def __call__(self, svc, params, sid=None, timeout=None, **kwargs):
            if svc == "core/batch":
                self.calls.append((svc, params))
                return [[], []]
            return super().__call__(svc, params, sid=sid, timeout=timeout, **kwargs)

    for engine_cls in (BatchFailingEngine, ShortBatchEngine):
        engine = engine_cls(tables=tables)
        with patch.object(wr, "wialon_request", engine):
            rep = wr.execute_wialon_unit_trips_report("sid", 101, 0, 200000)
        assert rep["trips"] == batch_engine_trips, (engine_cls.__name__, rep["trips"])
        assert engine.count("report/get_result_subrows") == 2, (engine_cls.__name__, engine.calls)

    engine = FakeReportEngine(tables=tables, subrow_error_rows={1})
    with patch.object(wr, "wialon_request", engine):
        try:
            wr.execute_wialon_unit_trips_report("sid", 101, 0, 200000)
            raise AssertionError("an unreadable day of trips must fail the report, not undercount")
        except WialonAPIError:
            pass
    print("[PASS] Unit trips parser: day rows expand to their trip sub-rows (2 days -> 3 trips) via one batch call; "
          "failed or short batches fall back per row; unreadable sub-rows fail.")


def test_ai_vehicle_trips_tool_uses_wialon_report():
    import app.ai.tools as tools
    rep = {"trips": [dict(_trip(10.5, 35, 1000), max_speed="114 km/h"), _trip(21.0, 40, 9000)],
           "incomplete_fields": [], "unlocated_fields": []}
    with patch.object(tools, "login", return_value="sid"), \
            patch.object(tools, "get_all_vehicles", return_value=RAW_UNITS), \
            patch.object(tools, "execute_wialon_unit_trips_report", return_value=rep) as report, \
            patch.object(tools, "get_vehicle_history", side_effect=AssertionError("raw history must not be segmented")):
        by_name = tools.tool_get_vehicle_trips("unit-b", hours=24)  # fleet names stand in for IDs
        assert report.call_args.args[1] == 102 and by_name["vehicle_name"] == "UNIT-B", (report.call_args, by_name)
        assert tools.tool_get_vehicle_trips(999, hours=24) == {"found": False, "vehicle_id": 999}
        out = tools.tool_get_vehicle_trips(101, hours=24)
    sid, vid, frm, to = report.call_args.args
    assert (sid, vid, to - frm) == ("sid", 101, 24 * 3600), report.call_args
    assert out["trip_count"] == 2 and out["vehicle_name"] == "UNIT-A", out
    assert out["trips"][0]["distance_km"] == 10.5 and out["trips"][0]["max_speed_kmh"] == 114.0, out["trips"][0]
    assert out["trips"][0]["duration"] == "35m 0s", out["trips"][0]
    print("[PASS] AI get_vehicle_trips: Wialon trips report (same source as the dashboard), not raw-message segmentation.")


def test_ai_tool_results_fit_model_budget():
    import json
    import app.ai.tools as tools
    small = {"total_alerts": 2, "alerts": [{"id": 1}, {"id": 2}]}
    assert tools._fit_for_model(small) == json.dumps(small), "small results pass through unchanged"

    alerts = [{"id": i, "severity": "CRITICAL" if i < 11 else "INFO", "message": "x" * 300} for i in range(79)]
    groups = [{"group": f"G{g}", "trips_24h": g, "vehicles": [{"vehicle_id": v, "pad": "y" * 200} for v in range(40)]} for g in range(5)]
    big = {"total_alerts": 79, "critical_count": 11, "alerts": alerts, "groups": groups}
    out = json.loads(tools._fit_for_model(big))
    assert len(json.dumps(out)) <= tools.MAX_TOOL_RESULT_CHARS + 600, len(json.dumps(out))
    assert out["total_alerts"] == 79 and out["critical_count"] == 11, "counts stay exact"
    assert out["alerts"] == alerts[:len(out["alerts"])] and out["alerts"], "kept items are the leading ones, unchanged"
    assert out["truncated_lists"]["alerts"] == f"showing first {len(out['alerts'])} of 79", out["truncated_lists"]
    assert [g["trips_24h"] for g in out["groups"]] == [0, 1, 2, 3, 4], "short lists are kept whole"
    assert all(v.startswith("showing first ") and not v.startswith("showing first 0") for v in out["truncated_lists"].values())
    assert len(big["alerts"]) == 79, "the (possibly cached) input is not mutated"
    print(f"[PASS] AI tool results: oversized lists halved to fit ({len(json.dumps(out))} chars); counts exact; truncation listed.")


def test_group_report_path_real_trip_counts():
    rows = [dict(GROUP_TRIPS_ROWS[0], trip_count=3), dict(GROUP_TRIPS_ROWS[1], trip_count=1),
            dict(GROUP_TRIPS_ROWS[2], trip_count=0)]
    with ExitStack() as stack:
        _base_patches(stack, group_trips=rows)
        res = fa.get_trips_mileage_analytics(hours=24)
    s = res["summary"]
    assert {v["vehicle_id"]: v["trip_count"] for v in res["vehicles"]} == {101: 3, 102: 1, 103: 0}, res["vehicles"]
    assert s["total_trips"] == 4 and s["average_trip_distance_km"] == round(1485.25 / 4, 2), s
    # No per-trip rows on this path: per-trip duration and longest trip stay unknown
    assert s["average_trip_duration_minutes"] is None and s["longest_trip_km"] is None, s
    assert {g["group"]: g["trips"] for g in res["by_group"]} == {"Logistics": 4, "Contracts": 0}, res["by_group"]

    rows[0] = dict(rows[0], trip_count=None)
    with ExitStack() as stack:
        _base_patches(stack, group_trips=rows)
        res = fa.get_trips_mileage_analytics(hours=24)
    assert res["summary"]["total_trips"] is None, "one unknown count makes the fleet total unknown"
    print("[PASS] Group report path: Wialon trip counts summed (3/1/0 -> 4); one unknown count -> total None.")


def test_unit_report_path_multiple_and_zero_trips():
    with ExitStack() as stack:
        _base_patches(stack, group_trips=None)
        res = fa.get_trips_mileage_analytics(hours=24)
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["trip_count"] == 3 and by_id[102]["trip_count"] == 1 and by_id[103]["trip_count"] == 0, by_id
    s = res["summary"]
    assert s["total_trips"] == 4, s
    assert s["total_distance_km"] == 96.0, s                      # 10.5 + 21 + 4.5 + 60
    assert s["average_trip_distance_km"] == 24.0, s               # 96 / 4 trips
    assert s["average_trip_duration_minutes"] == 35.0, s          # (35 + 40 + 15 + 50) / 4
    assert s["longest_trip_km"] == 60.0, s                        # longest single trip
    groups = {g["group"]: g for g in res["by_group"]}
    assert groups["Logistics"]["trips"] == 4 and groups["Contracts"]["trips"] == 0, groups
    print("[PASS] Unit report path: 3/1/0 trips per vehicle, total 4, avg 24.0 km, avg 35.0 min, longest 60.0 km, group sums.")


def test_zero_trips_fleet_wide():
    with ExitStack() as stack:
        _base_patches(stack, group_trips=None, unit_trips={101: [], 102: [], 103: []})
        res = fa.get_trips_mileage_analytics(hours=24)
    s = res["summary"]
    assert s["total_trips"] == 0, s
    assert s["average_trip_distance_km"] is None and s["average_trip_duration_minutes"] is None, s
    assert s["longest_trip_km"] is None, s
    assert all(g["trips"] == 0 for g in res["by_group"]), res["by_group"]
    print("[PASS] Zero trips: total 0, groups 0, average and longest trip None (not 0).")


def test_utilization_trip_count_unavailable():
    engine = {"vehicles": [
        {"vehicle_name": "UNIT-A", "in_motion_seconds": 3600, "total_idle_seconds": 600, "engine_hours_seconds": 4200},
        {"vehicle_name": "UNIT-B", "in_motion_seconds": 0, "total_idle_seconds": 0, "engine_hours_seconds": 0},
    ]}
    with ExitStack() as stack:
        _base_patches(stack)
        stack.enter_context(patch.object(fa, "execute_wialon_group_engine_idle_report", return_value=engine))
        res = fa.get_fleet_utilization_analytics(hours=24)
    assert all(v["trip_count"] is None for v in res["vehicles"]), res["vehicles"]
    by_id = {v["vehicle_id"]: v for v in res["vehicles"]}
    assert by_id[101]["driving_seconds"] == 3600 and by_id[101]["idle_seconds"] == 600, by_id[101]
    print("[PASS] Utilization: trip_count None (not 1 per driving vehicle); driving/idle unchanged.")


def _groups_with(trips_vehicles):
    with ExitStack() as stack:
        _base_patches(stack)
        stack.enter_context(patch.object(fa, "get_trips_mileage_analytics",
                                         return_value={"vehicles": trips_vehicles}))
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value={"by_group": []}))
        return {g["group_name"]: g for g in fa.get_fleet_groups_analytics()["groups"]}


def test_group_analytics_trips():
    known = [{"vehicle_id": 101, "trip_count": 3, "gps_mileage_km": 36.0},
             {"vehicle_id": 102, "trip_count": 1, "gps_mileage_km": 60.0},
             {"vehicle_id": 103, "trip_count": 0, "gps_mileage_km": 0.0}]
    g = _groups_with(known)
    assert g["Logistics"]["trips_24h"] == 4 and g["Contracts"]["trips_24h"] == 0, g
    assert g["Empty Group"]["trips_24h"] == 0, g

    one_unknown = [dict(known[0], trip_count=None), known[1], known[2]]
    g = _groups_with(one_unknown)
    assert g["Logistics"]["trips_24h"] is None and g["Contracts"]["trips_24h"] == 0, g
    assert g["Logistics"]["distance_24h_km"] == 96.0, g

    missing = known[1:]  # UNIT-A absent from the trips data
    g = _groups_with(missing)
    assert g["Logistics"]["trips_24h"] is None, g
    print("[PASS] Group analytics: trips_24h sums known counts; None when any count is unknown or missing.")


def test_api_responses_null():
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    user = {"id": "u1", "email": "x@example.com", "role": "viewer"}
    with ExitStack() as stack:
        _base_patches(stack)
        stack.enter_context(patch("app.main.authenticate_access_token", return_value=user))
        res = client.get("/api/fleet/trips?hours=24", headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]["total_trips"] is None and body["summary"]["longest_trip_km"] is None, body["summary"]
        assert body["summary"]["average_trip_distance_km"] is None, body["summary"]
        assert all(v["trip_count"] is None for v in body["vehicles"])

        fa._analytics_cache.clear()
        _cache.clear()
        stack.enter_context(patch.object(fa, "get_safety_analytics", return_value={"summary": {"total_events": 0}}))
        stack.enter_context(patch.object(fa, "get_fleet_utilization_analytics", return_value={"total_idle_hours": 0.0}))
        stack.enter_context(patch("app.main.login", return_value="sid"))
        stack.enter_context(patch("app.main.get_all_vehicles", return_value=RAW_UNITS))
        stack.enter_context(patch("app.main.get_calculated_data", return_value=CALC))
        stack.enter_context(patch("app.main.get_vehicle_live_data", return_value=[dict(v) for v in FLEET]))
        res = client.get("/api/dashboard/summary?hours=24", headers=headers)
        assert res.status_code == 200, res.text
        summary = res.json()["summary"]
        assert summary["total_trips"] is None, summary
        assert summary["total_vehicles"] == 3 and summary["total_distance_km"] == 1485.25, summary
    print("[PASS] API: /api/fleet/trips and /api/dashboard/summary return null trip metrics (JSON null).")


def main() -> int:
    guard = OfflineGuard().start()
    tests = [
        test_group_report_path_trip_metrics_unavailable,
        test_group_trips_parser_reads_wialon_trip_counts,
        test_group_report_path_real_trip_counts,
        test_unit_trips_parser_expands_day_rows,
        test_ai_vehicle_trips_tool_uses_wialon_report,
        test_ai_tool_results_fit_model_budget,
        test_unit_report_path_multiple_and_zero_trips,
        test_zero_trips_fleet_wide,
        test_utilization_trip_count_unavailable,
        test_group_analytics_trips,
        test_api_responses_null,
    ]
    passed = 0
    try:
        for test in tests:
            try:
                test()
                passed += 1
            except Exception:
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
        fa._analytics_cache.clear()
        _cache.clear()
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
