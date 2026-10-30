"""
Offline regression tests for Fuel Phase 2: Wialon telematics freshness (TTL + single-flight),
complete report paging, header-based column lookup, consistent in-motion driving hours, and
explicit None (never 0) for missing or unreadable telematics.

Wialon is replaced by FakeReportEngine at app.wialon_reports.wialon_request, so the real report
parsers run end to end. Every outbound HTTP call is blocked by OfflineGuard.

Run from Backend/:  python scripts/test_fuel_telematics_phase2.py
"""

import copy
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
from test_fuel_scope import reset_fuel_read_caches  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    BASE_TS, GROUP_ENGINE_HEADER, GROUPS, TEMPLATE_GROUP_ENGINE, TEMPLATE_GROUP_TRIPS, TEMPLATE_UNIT_ENGINE,
    TEMPLATE_UNIT_TRIPS, UNITS, FakeReportEngine, default_tables, group_engine_row, group_engine_subrow,
    group_trip_row, unit_engine_row,
)

from app.main import app  # noqa: E402
import app.fuel.wialon_distance as wd  # noqa: E402
import app.wialon_reports as wr  # noqa: E402
from app.fuel.efficiency import calculate_fleet_efficiency, calculate_vehicle_efficiency_records  # noqa: E402
from app.utils.ttl_cache import TTLCache  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402
from app.fuel.reporting_period import DATE_BASIS_MONTH_END  # noqa: E402

UAE = timezone(timedelta(hours=4))
AUTH = {"Authorization": "Bearer offline-test-token"}
FAKE_USER = {"id": "00000000-0000-0000-0000-000000000001", "email": "t@ats.ae", "role": "admin"}
CLOSED_FROM, CLOSED_TO = "2026-08-03", "2026-08-05"
# Fuel routes report monthly summaries for whole months: route tests query the whole (closed) month
MONTH_Q = "date_from=2026-08-01&date_to=2026-08-31"
SUMMARY = {"transaction_date": "2026-08-31T00:00:00", "date_basis": DATE_BASIS_MONTH_END}


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Env:
    """Patch Wialon with a fake engine and give wialon_distance a fresh, clock-controlled cache."""

    def __init__(self, engine=None, today=datetime(2026, 10, 19, 12, 0, tzinfo=UAE)):
        self.engine = engine or FakeReportEngine()
        self.clock = FakeClock()
        self.login = MagicMock(return_value="fresh-sid")
        self._patches = [
            patch("app.wialon_reports.wialon_request", self.engine),
            patch("app.fuel.wialon_distance.login", self.login),
            patch("app.fuel.wialon_distance.get_uae_now", return_value=today),
            patch.object(wd, "_TELEMATICS_CACHE", TTLCache(clock=self.clock)),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()

    def execs(self):
        return self.engine.count("report/exec_report")


# ─── 1-5. Freshness, single-flight, failures ───────────────────────────────────

def test_closed_range_cached():
    with Env() as env:
        a = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        assert env.execs() == 2 and a is b  # one trips + one engine report, then cache
        env.clock.t += 3599
        wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        assert env.execs() == 2
        env.clock.t += 2
        wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        assert env.execs() == 4
    print("[PASS] Closed range cached for 3600 s, then refetched.")


def test_today_range_refreshes():
    with Env(today=datetime(2026, 8, 5, 12, 0, tzinfo=UAE)) as env:
        wd.get_fleet_telematics_bundle(CLOSED_FROM, "2026-08-05", sid="s")
        env.clock.t += 119
        wd.get_fleet_telematics_bundle(CLOSED_FROM, "2026-08-05", sid="s")
        assert env.execs() == 2
        env.clock.t += 2
        wd.get_fleet_telematics_bundle(CLOSED_FROM, "2026-08-05", sid="s")
        assert env.execs() == 4
    print("[PASS] Range ending today refreshes after 120 s (no more frozen 'today' data).")


def test_single_flight():
    engine = FakeReportEngine()
    real_call = engine.__call__

    def slow(svc, params, **kw):
        if svc == "report/exec_report":
            time.sleep(0.15)
        return real_call(svc, params, **kw)

    with Env(engine=MagicMock(side_effect=slow)) as env:
        results = []
        threads = [threading.Thread(target=lambda: results.append(wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        exec_calls = sum(1 for c in env.engine.call_args_list if c.args[0] == "report/exec_report")
        assert exec_calls == 2, exec_calls
        assert len(results) == 4 and all(r is results[0] for r in results)
    print("[PASS] Four concurrent cold requests share one Wialon fetch (single-flight).")


def test_whole_report_failure_retries_is_not_cached_and_route_degrades():
    with Env(engine=FakeReportEngine(fail_exec={TEMPLATE_GROUP_ENGINE})) as env:
        for _ in range(2):
            try:
                wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
                raise AssertionError("expected WialonAPIError")
            except WialonAPIError:
                pass
        # the engine report fails with Wialon error 5 (not a session error): it is retried once on its current
        # session, without forcing a new login
        forced = [c for c in env.login.call_args_list if c.kwargs.get("force")]
        assert forced == [], env.login.call_args_list
        # failure not cached: second call executed the reports again
        assert env.execs() == 2 * (1 + 2), env.execs()  # per call: trips once, engine twice (retry)

        client = TestClient(app)
        txs = [{"vehicle_identifier": "UNIT-A", "litres": 50, "total_cost": 150, **SUMMARY}]
        with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
             patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=txs), \
             patch("app.fuel.routes.login", return_value="s"), \
             patch("app.fuel.routes.get_all_vehicles", return_value=UNITS):
            r = client.get(f"/api/fuel/analytics/efficiency?{MONTH_Q}", headers=AUTH)
        # The route keeps the recorded fuel figures and marks telematics unavailable (null, never 0) instead of a 502
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_litres"] == 50.0 and body["telematics_status"] == "unavailable", body
        assert body["total_distance_km"] is None and body["total_idle_hours"] is None, body
    print("[PASS] A report failing after its one retry (no new session for a non-session error) is never cached; the route answers with fuel data and telematics unavailable.")


def test_empty_engine_report_is_partial_not_zero():
    with Env(engine=FakeReportEngine(empty_tables={TEMPLATE_GROUP_ENGINE})):
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
    assert b["status"] == "partial" and "group_engine_empty" in b["errors"]
    a = b["by_name"]["UNIT-A"]
    assert a["mileage_km"] == 250.75
    assert a["driving_seconds"] is None and a["idle_seconds"] is None and a["engine_hours_seconds"] is None
    assert any("engine report returned no rows" in w for w in b["warnings"])
    eff = calculate_fleet_efficiency(
        [{"vehicle_identifier": "UNIT-A", "litres": 50.0, "total_cost": 150.0}], b["by_name"], UNITS,
        telematics_status=b["status"], data_warnings=b["warnings"])
    assert eff.fuel_efficiency_l_per_100km == round(50 / 250.75 * 100, 2)
    assert eff.total_idle_hours is None and eff.estimated_idle_fuel_litres is None
    assert eff.telematics_status == "partial" and eff.data_warnings
    print("[PASS] Empty engine report: hours None (not 0), distance kept, status partial with warnings.")


# ─── 7-11. Paging, cell parsing, headers ───────────────────────────────────────

def test_paging_reads_past_old_caps():
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"] = [
        group_engine_row(f"U{i}", "24:00:00", "1:00:00", "0:40:00", "0:20:00", 10.0) for i in range(1203)]
    tables[TEMPLATE_GROUP_ENGINE]["subrows"] = {}
    tables[TEMPLATE_UNIT_ENGINE]["rows"] = [
        unit_engine_row(i, BASE_TS + i * 600, "1:00:00", "0:10:00", "0:08:00", "0:02:00", 1.0) for i in range(150)]
    tables[TEMPLATE_UNIT_ENGINE]["subrows"] = {}
    tables[TEMPLATE_GROUP_ENGINE]["rows"][0] = group_engine_row("U0", "24:00:00", "8:00:00", "4:00:00", "4:00:00", 10.0, subrows=450)
    tables[TEMPLATE_GROUP_ENGINE]["subrows"] = {0: [group_engine_subrow(j, BASE_TS + j * 60, 30) for j in range(450)]}
    engine = FakeReportEngine(tables=tables)
    with patch("app.wialon_reports.wialon_request", engine):
        g = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400)
        u = wr.execute_wialon_unit_engine_report("s", 101, BASE_TS, BASE_TS + 86400)
    assert len(g["vehicles"]) == 1203 and g["rows_total"] == 1203
    windows = [(p["indexFrom"], p["indexTo"]) for s, p in engine.calls if s == "report/get_result_rows"]
    assert windows[:2] == [(0, 999), (1000, 1202)], windows
    # U0: 450 sub-row events; the other 1,202 rows idle without sub-rows: their idle totals are kept but no event is
    # invented from a row total (their event counts are unknown)
    assert len(g["vehicles"][0]["events"]) == 450 and len(g["events"]) == 450
    assert len(g["events_without_subrows"]) == 1202 and all(v["total_idle_seconds"] == 1200 for v in g["vehicles"][1:])
    sub_windows = [(p["indexFrom"], p["indexTo"]) for s, p in engine.calls if s == "report/get_result_subrows"]
    # the batched first page (0-199) is full but short of the row's 450 sub-rows: paging resumes after it
    # (200-399, 400-599) without reading page 0 again
    assert sub_windows == [(0, 199), (200, 399), (400, 599)], sub_windows
    assert u["total_idle_seconds"] == 150 * 120 and u["total_engine_seconds"] == 150 * 600  # old cap: 101 rows
    assert u["total_in_motion_seconds"] == 150 * 480
    print("[PASS] 1,203 group rows, 150 unit-engine rows and 450 idle sub-rows all read (old caps 501/101/201 gone).")


def test_short_page_raises():
    tables = default_tables()
    tables[TEMPLATE_GROUP_TRIPS]["rows"] = [group_trip_row(f"U{i}", 1.0) for i in range(30)]
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables, max_rows_per_call=20)):
        try:
            wr.execute_wialon_group_trips_report("s", BASE_TS, BASE_TS + 86400)
            raise AssertionError("expected WialonAPIError")
        except WialonAPIError:
            pass
    print("[PASS] A report page shorter than requested raises instead of truncating.")


def test_dict_cells_parse_like_string_cells():
    tables_s = default_tables()
    tables_d = copy.deepcopy(tables_s)
    for row in tables_d[TEMPLATE_GROUP_ENGINE]["rows"]:
        row["c"] = [c if isinstance(c, dict) else {"t": c} for c in row["c"]]
    out = []
    for t in (tables_s, tables_d):
        with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=t)):
            out.append(wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400))
    keys = ["vehicle_name", "total_time_seconds", "engine_hours_seconds", "in_motion_seconds", "total_idle_seconds",
            "mileage_km", "idle_events", "incomplete_fields"]
    for a, b in zip(out[0]["vehicles"], out[1]["vehicles"]):
        assert {k: a[k] for k in keys} == {k: b[k] for k in keys}, (a, b)
    assert out[1]["vehicles"][0]["vehicle_name"] == "UNIT-A"
    print("[PASS] Dict-wrapped cells parse to the same names/durations as plain string cells.")


def test_columns_found_by_header_name():
    tables = default_tables()
    t = tables[TEMPLATE_GROUP_ENGINE]
    hdr = list(GROUP_ENGINE_HEADER)
    hdr[7], hdr[8] = hdr[8], hdr[7]  # 'Idling' now before 'In motion'
    t["header"] = hdr
    for row in t["rows"]:
        row["c"][7], row["c"][8] = row["c"][8], row["c"][7]
    for subs in t["subrows"].values():
        for s in subs:
            s["c"][7], s["c"][8] = s["c"][8], s["c"][7]
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)):
        g = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400, include_events=False)
    a = g["vehicles"][0]
    assert a["in_motion_seconds"] == 16200 and a["total_idle_seconds"] == 5400 and not g["column_warnings"]

    tables2 = default_tables()
    tables2[TEMPLATE_GROUP_ENGINE]["header"] = [h if h != "In motion" else "Moving" for h in GROUP_ENGINE_HEADER]
    with Env(engine=FakeReportEngine(tables=tables2)):
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
    # A value read from the guessed (historical) position is never used as authoritative: unknown, listed, warned
    unit_a = b["by_name"]["UNIT-A"]
    assert unit_a["driving_seconds"] is None and unit_a["driving_hours"] is None, unit_a
    assert "driving_seconds" in unit_a["incomplete_fields"], unit_a
    assert unit_a["idle_seconds"] == 5400, "located columns are unaffected"
    assert b["status"] == "partial"
    assert any("Column 'In motion' not found" in w for w in b["warnings"]), b["warnings"]
    print("[PASS] Columns located by header name; a renamed column is unknown (not its guessed position), with a visible warning.")


def test_unreadable_cells_are_none_not_zero():
    tables = default_tables()
    rows = tables[TEMPLATE_GROUP_ENGINE]["rows"]
    rows[0]["c"][8] = "--:--"        # UNIT-A idling unreadable
    rows[1]["c"][9] = "1,234.5 km"   # UNIT-B mileage with thousands separator
    rows[2]["c"][9] = "abc km"       # UNIT-C mileage unreadable
    tables[TEMPLATE_GROUP_TRIPS]["rows"] = [group_trip_row("UNIT-A", 250.75)]  # B and C only in engine report
    tables[TEMPLATE_GROUP_TRIPS]["rows"][0]["c"][5] = "250.75 km"
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)):
        g = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400, include_events=False)
    a, b_row, c_row = g["vehicles"]
    assert a["total_idle_seconds"] == 0 and "total_idle_seconds" in a["incomplete_fields"]  # legacy int kept for other callers
    assert b_row["mileage_km"] == 1234.5 and c_row["mileage_km"] is None
    with Env(engine=FakeReportEngine(tables=tables)):
        bundle = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
    e = bundle["by_name"]
    assert e["UNIT-A"]["idle_seconds"] is None and e["UNIT-A"]["driving_seconds"] == 16200
    assert e["UNIT-B"]["mileage_km"] == 1234.5 and e["UNIT-B"]["distance_source"] == "wialon_group_engine_report"
    assert e["UNIT-C"]["mileage_km"] is None
    assert bundle["status"] == "partial"
    print("[PASS] Unreadable Wialon cells become None in fuel telematics (legacy ints unchanged for other pages).")


# ─── 12-13. Missing units and duplicate names ──────────────────────────────────

def test_missing_unit_is_unknown_recorded_zero_stays_zero():
    units = UNITS + [{"id": 104, "nm": "UNIT-D"}]
    with Env():
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
    txs = [{"vehicle_identifier": "UNIT-C", "litres": 20.0, "total_cost": 60.0},
           {"vehicle_identifier": "UNIT-D", "litres": 30.0, "total_cost": 90.0},
           {"vehicle_identifier": "UNIT-A", "litres": 50.0, "total_cost": 150.0}]
    items = {i.vehicle_identifier: i for i in calculate_vehicle_efficiency_records(txs, b["by_name"], units)}
    assert items["UNIT-C"].distance_km == 0.0 and items["UNIT-C"].status == "no_distance_data"
    assert items["UNIT-D"].distance_km is None and items["UNIT-D"].distance_source == "unavailable"
    assert any("not present in Wialon reports" in w for w in items["UNIT-D"].quality_warnings)
    fleet = calculate_fleet_efficiency(txs, b["by_name"], units, telematics_status=b["status"])
    assert fleet.vehicles_telemetry_unknown == 1 and fleet.telematics_status == "partial"
    assert fleet.total_distance_km == 250.75 and fleet.tracked_litres == 50.0  # D excluded, C has no distance
    print("[PASS] A unit missing from Wialon reports is unknown (None, excluded); a recorded 0 km stays 0.0.")


def test_duplicate_names_withheld():
    tables = default_tables()
    tables[TEMPLATE_GROUP_TRIPS]["rows"].append(group_trip_row("UNIT-A", 999.0))
    with Env(engine=FakeReportEngine(tables=tables)):
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
    assert b["duplicate_names"] == ["UNIT-A"] and "UNIT-A" not in b["by_name"] and b["status"] == "partial"
    units = [{"id": 1, "nm": "TWIN"}, {"id": 2, "nm": "TWIN"}, {"id": 3, "nm": "SOLO"}]
    tele = {"TWIN": {"mileage_km": 100.0}, "SOLO": {"mileage_km": 200.0}}
    with patch("app.fuel.efficiency.match_fuel_vehicle_to_wialon",
               side_effect=lambda vid, us: next(u for u in us if u["id"] == int(vid))):
        items = calculate_vehicle_efficiency_records(
            [{"vehicle_identifier": "1", "litres": 10.0, "total_cost": 30.0},
             {"vehicle_identifier": "3", "litres": 10.0, "total_cost": 30.0}], tele, units)
    twin, solo = items
    assert twin.distance_km is None and any("shared by multiple units" in w for w in twin.quality_warnings)
    assert solo.distance_km == 200.0
    print("[PASS] Duplicate unit names (in reports or the unit list) withhold telemetry instead of overwriting.")


# ─── 14-16. Vehicle detail ─────────────────────────────────────────────────────

def test_detail_driving_hours_from_in_motion():
    with Env():
        d = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
    trips_total_time_h = sum(t["duration_minutes"] for t in d["trips"]) / 60.0
    assert d["driving_duration_hours"] == 7.0, d["driving_duration_hours"]  # 4:00 + 3:00 + 0:00 in motion
    assert abs(d["driving_duration_hours"] - trips_total_time_h) > 0.01
    assert d["driving_basis"] == "engine_report_in_motion" and d["status"] == "complete"
    assert d["distance_km"] == round(10.5 + 21.0 + 31.5, 2) and d["engine_hours"] == 8.5 and d["idle_duration_hours"] == 1.5
    print("[PASS] Vehicle-detail driving hours = engine-report 'In motion', never trip 'Total time'.")


def test_detail_unreadable_values_are_none_and_failure_is_502():
    tables = default_tables()
    tables[TEMPLATE_UNIT_ENGINE]["rows"][1]["c"][7] = ""          # in-motion unreadable on one row
    tables[TEMPLATE_UNIT_TRIPS]["rows"][0]["c"][9] = "--- km"     # one trip distance unreadable
    with Env(engine=FakeReportEngine(tables=tables)):
        d = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
        try:
            wd.get_vehicle_distance(101, CLOSED_FROM, CLOSED_TO, sid="s")
            raise AssertionError("expected WialonAPIError for unreadable distance")
        except WialonAPIError:
            pass
    assert d["driving_duration_hours"] is None and d["driving_basis"] == "unavailable"
    assert d["distance_km"] is None and d["distance_source"] == "unavailable" and d["status"] == "partial"
    assert d["idle_duration_hours"] == 1.5  # still known
    with Env(engine=FakeReportEngine(fail_exec={TEMPLATE_UNIT_ENGINE})):
        try:
            wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
            raise AssertionError("expected WialonAPIError")
        except WialonAPIError:
            pass
    print("[PASS] Detail: unreadable in-motion/distance -> None + partial; whole-report failure -> error (502).")


def test_detail_route_exposes_basis_and_status():
    client = TestClient(app)
    rows = [{"id": 1, "import_id": 1, "vehicle_identifier": "UNIT-A", "litres": 60.0, "total_cost": 180.0, **SUMMARY}]
    with Env(), patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.routes.login", return_value="s"), \
         patch("app.fuel.routes.get_all_vehicles", return_value=UNITS), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=rows):
        r = client.get(f"/api/fuel/analytics/vehicle-detail?vehicle_identifier=UNIT-A&{MONTH_Q}",
                       headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    ab = body["activity_breakdown"]
    assert ab["driving_basis"] == "engine_report_in_motion" and ab["status"] == "complete"
    assert body["vehicle_summary"]["driving_duration_hours"] == 7.0
    print("[PASS] /analytics/vehicle-detail exposes driving_basis/status and uses in-motion hours.")


# ─── 17-19. Fuel endpoints and legacy maps ─────────────────────────────────────

def test_float_map_still_supported():
    s = calculate_fleet_efficiency(
        [{"vehicle_identifier": "VEH-A", "litres": 100.0, "total_cost": 360.0},
         {"vehicle_identifier": "VEH-B", "litres": 100.0, "total_cost": 360.0}],
        {"VEH-A": 100.0, "VEH-B": 1000.0}, [{"id": 1, "nm": "VEH-A"}, {"id": 2, "nm": "VEH-B"}])
    assert s.fuel_efficiency_l_per_100km == 18.18 and s.total_distance_km == 1100.0
    assert s.total_idle_hours is None  # a distance-only map says nothing about hours
    with Env():
        dmap = wd.get_fleet_distances_map(CLOSED_FROM, CLOSED_TO, sid="s")
    assert dmap == {"UNIT-A": 250.75, "UNIT-B": 1234.5, "UNIT-C": 0.0}
    print("[PASS] Legacy float distance maps (AI tools) still work; unknown hours stay unknown.")


def test_efficiency_endpoints_report_partial_status():
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["rows"][0]["c"][8] = "--:--"
    client = TestClient(app)
    txs = [{"id": 1, "import_id": 1, "vehicle_identifier": "UNIT-A", "litres": 50.0, "total_cost": 150.0, **SUMMARY},
           {"id": 2, "import_id": 1, "vehicle_identifier": "UNIT-B", "litres": 80.0, "total_cost": 240.0, **SUMMARY}]
    with Env(engine=FakeReportEngine(tables=tables)), \
         patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=txs), \
         patch("app.fuel.routes.login", return_value="s"), \
         patch("app.fuel.routes.get_all_vehicles", return_value=UNITS):
        q = MONTH_Q
        eff = client.get(f"/api/fuel/analytics/efficiency?{q}", headers=AUTH).json()
        veh = client.get(f"/api/fuel/analytics/efficiency/vehicles?{q}", headers=AUTH).json()
        waste = client.get(f"/api/fuel/analytics/waste?{q}", headers=AUTH).json()
        trend = client.get(f"/api/fuel/analytics/efficiency/trend?{q}&group_by=month", headers=AUTH).json()
    for name, body in [("efficiency", eff), ("vehicles", veh), ("waste", waste), ("trend", trend)]:
        assert body["telematics_status"] == "partial", (name, body.get("telematics_status"))
        assert body["data_warnings"], name
    a = next(v for v in veh["data"] if v["vehicle_identifier"] == "UNIT-A")
    assert a["idle_duration_hours"] is None and a["estimated_idle_fuel_litres"] is None
    assert a["estimation_status"] == "unavailable" and a["fuel_efficiency_l_per_100km"] == round(50 / 250.75 * 100, 2)
    assert eff["fuel_efficiency_l_per_100km"] == round(130 / (250.75 + 1234.5) * 100, 2)
    print("[PASS] /efficiency, /efficiency/vehicles, /waste and /efficiency/trend report partial telematics; unknown idle stays null.")


def test_complete_data_reports_complete():
    client = TestClient(app)
    txs = [{"id": 1, "import_id": 1, "vehicle_identifier": "UNIT-A", "litres": 50.0, "total_cost": 150.0, **SUMMARY}]
    with Env(), patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", return_value=txs), \
         patch("app.fuel.routes.login", return_value="s"), \
         patch("app.fuel.routes.get_all_vehicles", return_value=UNITS):
        eff = client.get(f"/api/fuel/analytics/efficiency?{MONTH_Q}", headers=AUTH).json()
    assert eff["telematics_status"] == "complete" and eff["data_warnings"] == [], eff
    assert eff["total_idle_hours"] == 1.5 and eff["total_driving_hours"] == 4.5
    print("[PASS] Complete Wialon data reports telematics_status=complete with no warnings.")


# ─── Review follow-ups ──────────────────────────────────────────────────────────

def test_unit_gaps_keep_normal_ttl_whole_report_gaps_retry_soon():
    units_gap = default_tables()
    units_gap[TEMPLATE_GROUP_TRIPS]["rows"].append(group_trip_row("UNIT-Z", 5.0))  # not in engine report
    with Env(engine=FakeReportEngine(tables=units_gap)) as env:
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        assert b["status"] == "partial" and not b["errors"]
        assert b["by_name"]["UNIT-Z"]["idle_seconds"] is None  # unknown, not 0
        env.clock.t += 600
        wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        assert env.execs() == 2  # still cached: unit-level gaps do not force 30 s refetches
    with Env(engine=FakeReportEngine(empty_tables={TEMPLATE_GROUP_ENGINE})) as env:
        wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        env.clock.t += 31
        wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        assert env.execs() == 4  # empty whole report retried after 30 s
    print("[PASS] Unit-level gaps stay 'partial' with the normal TTL; an empty whole report is retried after 30 s.")


def test_column_fallback_marks_partial():
    tables = default_tables()
    tables[TEMPLATE_GROUP_ENGINE]["header"] = [h if h != "Idling" else "Idle time" for h in GROUP_ENGINE_HEADER]
    with Env(engine=FakeReportEngine(tables=tables)):
        b = wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        d = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
    assert b["status"] == "partial" and b["column_fallbacks"] and not b["errors"]
    assert d["status"] == "complete"  # unit engine template header unaffected
    t2 = default_tables()
    t2[TEMPLATE_UNIT_ENGINE]["header"] = [h if h != "In motion" else "Motion" for h in t2[TEMPLATE_UNIT_ENGINE]["header"]]
    with Env(engine=FakeReportEngine(tables=t2)):
        d2 = wd.get_vehicle_telematics_detail(101, CLOSED_FROM, CLOSED_TO, sid="s")
    assert d2["status"] == "partial" and any("'In motion' not found" in w for w in d2["warnings"])
    print("[PASS] A column read by position (header name missing) marks telematics partial, never 'complete'.")


def test_cache_shares_failure_with_queued_waiters_and_cleans_state():
    cache = TTLCache()
    calls = []
    gate = threading.Event()

    def failing():
        calls.append(1)
        gate.wait(0.3)
        raise WialonAPIError("down")

    errors = []

    def worker():
        try:
            cache.get_or_compute("k", failing, lambda v: 60)
        except WialonAPIError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(errors) == 4, (len(calls), len(errors))
    assert cache._states == {} and cache.get("k") is None
    assert cache.get_or_compute("k", lambda: {"ok": 1}, lambda v: 60) == {"ok": 1}  # later call recomputes
    print("[PASS] TTLCache: queued waiters share one failure (no serial retries), state is cleaned, nothing cached.")


def test_forced_relogin_happens_under_report_lock():
    class SessionExpired(FakeReportEngine):
        # The engine report keeps answering Wialon error 1 (invalid session): the only error that forces a new login
        def __call__(self, svc, params, sid=None, **kw):
            if svc == "report/exec_report" and params.get("reportTemplateId") == TEMPLATE_GROUP_ENGINE:
                self.calls.append((svc, params))
                return {"error": 1}
            return super().__call__(svc, params, sid=sid, **kw)

    seen = []
    with Env(engine=SessionExpired()) as env:
        env.login.side_effect = lambda force=False, session=None: (force and seen.append(wr._report_lock.locked())) or "fresh-sid"
        try:
            wd.get_fleet_telematics_bundle(CLOSED_FROM, CLOSED_TO, sid="s")
        except WialonAPIError:
            pass
    assert seen == [True], seen
    print("[PASS] Forced re-login runs while holding the report lock (no session swap mid-report).")


def test_subrow_error_flags_events_without_failing_idle_page():
    import app.main as main
    from wialon_report_fixtures import CALCULATED, GROUPS, live_records
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(subrow_error_rows={0})):
        g = wr.execute_wialon_group_engine_idle_report("s", BASE_TS, BASE_TS + 86400)
    a = g["vehicles"][0]
    assert g["events_incomplete"] == ["UNIT-A"] and "idle_events" in a["incomplete_fields"]
    assert a["total_idle_seconds"] == 5400 and a["events"] == []  # totals intact, events flagged
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(subrow_error_rows={0})), \
         patch("app.main.login", return_value="s"), patch("app.main.get_vehicle_groups", return_value=GROUPS), \
         patch("app.main.get_all_vehicles", return_value=UNITS), \
         patch("app.main.get_calculated_data", return_value=CALCULATED), \
         patch("app.main.get_vehicle_live_data", side_effect=lambda *a, **k: live_records()):
        page = main.get_fleet_idle_analytics(from_time=BASE_TS, to_time=BASE_TS + 3 * 86400 - 1, skip_cache=True)
    assert page["kpis"]["total_idle_seconds"] == 5400 + 4530
    print("[PASS] A sub-row error flags that unit's events as incomplete; the fleet idle page still loads.")


def test_recorded_zero_hours_stay_zero():
    tele = {"UNIT-A": {"mileage_km": 100.0, "driving_seconds": 3600, "idle_seconds": 0, "engine_hours_seconds": 3600}}
    s = calculate_fleet_efficiency([{"vehicle_identifier": "UNIT-A", "litres": 10.0, "total_cost": 30.0}], tele, UNITS)
    assert s.total_idle_hours == 0.0 and s.total_driving_hours == 1.0 and s.estimated_idle_fuel_litres is None
    print("[PASS] A recorded 0 idle hours is reported as 0.0, not as unknown.")


TESTS = [
    test_closed_range_cached,
    test_today_range_refreshes,
    test_single_flight,
    test_whole_report_failure_retries_is_not_cached_and_route_degrades,
    test_empty_engine_report_is_partial_not_zero,
    test_paging_reads_past_old_caps,
    test_short_page_raises,
    test_dict_cells_parse_like_string_cells,
    test_columns_found_by_header_name,
    test_unreadable_cells_are_none_not_zero,
    test_missing_unit_is_unknown_recorded_zero_stays_zero,
    test_duplicate_names_withheld,
    test_detail_driving_hours_from_in_motion,
    test_detail_unreadable_values_are_none_and_failure_is_502,
    test_detail_route_exposes_basis_and_status,
    test_float_map_still_supported,
    test_efficiency_endpoints_report_partial_status,
    test_complete_data_reports_complete,
    test_unit_gaps_keep_normal_ttl_whole_report_gaps_retry_soon,
    test_column_fallback_marks_partial,
    test_cache_shares_failure_with_queued_waiters_and_cleans_state,
    test_forced_relogin_happens_under_report_lock,
    test_subrow_error_flags_events_without_failing_idle_page,
    test_recorded_zero_hours_stay_zero,
]


if __name__ == "__main__":
    print("=" * 60)
    print("FUEL PHASE 2 - WIALON TELEMATICS COMPLETENESS (OFFLINE)")
    print("=" * 60)
    guard = OfflineGuard().start()
    # Report executors discover the account's "All Unit" group; serve the fixture groups offline
    patch("app.wialon_reports.get_vehicle_groups", return_value=GROUPS).start()
    failures = 0
    try:
        for t in TESTS:
            try:
                reset_fuel_read_caches()
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
