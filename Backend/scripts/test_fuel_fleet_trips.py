"""
Offline regression tests for the fleet-wide trend distance and the faster fuel / idle report paths (MOCKED / offline).

  - template 5 per-trip sub-rows: parsing, "-----" placeholder, unreadable/malformed/undetailed rows, default output unchanged
  - fleet trips per unit: name -> unit id attribution, duplicates, foreign unit ids, mileage cross-check, absent units
  - equivalence with one template-4 report per unit (trips, statuses and the whole trend, day/week/month)
  - UAE day boundaries, empty periods (recorded 0 km), report failures (per-unit fallback, never 0 km), caching
  - telematics bundle: trips and engine reports run at the same time on their own fuel sessions, results unchanged
  - group engine report: idle-event sub-rows fetched in core/batch calls, only for the rows that use them

Run from Backend/:  python scripts/test_fuel_fleet_trips.py
"""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import run  # noqa: E402
from wialon_report_fixtures import (  # noqa: E402
    GROUP_TRIPS_HEADER, TEMPLATE_GROUP_ENGINE, TEMPLATE_GROUP_TRIPS, TEMPLATE_UNIT_TRIPS, UNIT_TRIPS_HEADER,
    FakeReportEngine, default_tables, unit_trip_row,
)

import app.fuel.wialon_distance as wd  # noqa: E402
import app.wialon_reports as wr  # noqa: E402
from app.fuel.efficiency import calculate_efficiency_trend  # noqa: E402
from app.utils.datetime_helpers import parse_date_to_uae_bounds  # noqa: E402
from app.utils.ttl_cache import TTLCache  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

UAE = timezone(timedelta(hours=4))
CLOSED = ("2026-08-03", "2026-08-05")
UNITS = [{"id": 101, "nm": "UNIT-A"}, {"id": 102, "nm": "UNIT-B"}, {"id": 103, "nm": "UNIT-C"}]


def ts(d, hh, mm=0, tz=UAE):
    return int(datetime(2026, 8, d, hh, mm, tzinfo=tz).timestamp())


# Trips per unit: (start, minutes, km). UNIT-C has none in the period.
TRIPS = {
    101: [(ts(3, 9), 60, 120.5), (ts(3, 23, 30), 70, 40.25), (ts(4, 20, 30, tz=timezone.utc), 30, 15.0)],
    102: [(ts(4, 8), 45, 300.0), (ts(5, 17), 90, 10.1)],
    103: [],
}


def _t(v, label):
    return {"t": label, "v": v}


def _loc(label, uid):
    return {"t": label, "x": 55.2, "y": 25.2, "u": uid}


def t5_subrow(name, uid, i, start, minutes, km):
    end = start + minutes * 60
    return {"n": i, "t1": start, "t2": end, "d": 0, "c": [
        name, {**_t(start, f"s{i}"), "u": uid}, _loc("From", uid), {**_t(end, f"e{i}"), "u": uid}, _loc("To", uid),
        f"{km:.2f} km", "1:00:00", "0:50:00", "40 km/h", {"t": "90 km/h", "u": uid}, ""]}


def t5_placeholder(name):
    return {"n": 0, "t1": 0, "t2": 0, "d": 0, "c": [name, "-----", "", "-----", "", "0.00 km", "0:00:00", "0:00:00",
                                                    "0 km/h", "0 km/h", ""]}


def t5_row(name, uid, trips, total_km=None):
    km = sum(k for _, _, k in trips) if total_km is None else total_km
    first = _t(trips[0][0], "b") if trips else "-----"
    return {"c": [name, first, _loc("A", uid) if trips else "", _t(trips[-1][0], "e") if trips else "-----", "",
                  f"{km:.2f} km", "10:00:00", "8:00:00", "45 km/h", "110 km/h", ""], "d": max(1, len(trips))}


def fleet_tables(units=UNITS, trips=TRIPS, edit=None):
    """Template 5 (rows + per-trip sub-rows) and per-unit template 4 tables built from the same trips."""
    tables = default_tables()
    rows, subrows = [], {}
    for idx, u in enumerate(units):
        tl = trips.get(u["id"], [])
        rows.append(t5_row(u["nm"], u["id"], tl))
        subrows[idx] = ([t5_subrow(u["nm"], u["id"], i, *t) for i, t in enumerate(tl)] if tl else [t5_placeholder(u["nm"])])
    tables[TEMPLATE_GROUP_TRIPS] = {"header": GROUP_TRIPS_HEADER, "rows": rows, "subrows": subrows}
    per_unit = {u["id"]: {"header": UNIT_TRIPS_HEADER, "subrows": {},
                          "rows": [unit_trip_row(i + 1, s, m, "0:50:00", k) for i, (s, m, k) in enumerate(trips.get(u["id"], []))]}
                for u in units}
    if edit:
        edit(tables, per_unit)
    return tables, per_unit


class Engine(FakeReportEngine):
    """FakeReportEngine whose template-4 report is the requested unit's own table."""

    def __init__(self, per_unit, **kw):
        super().__init__(**kw)
        self.per_unit = per_unit
        self.lock = threading.Lock()

    def __call__(self, svc, params, sid=None, timeout=None, **kwargs):
        if svc == "report/exec_report" and params["reportTemplateId"] == TEMPLATE_UNIT_TRIPS:
            with self.lock:
                self.tables = dict(self.tables)
                self.tables[TEMPLATE_UNIT_TRIPS] = self.per_unit.get(params["reportObjectId"], {"header": UNIT_TRIPS_HEADER, "rows": [], "subrows": {}})
                return super().__call__(svc, params, sid=sid, timeout=timeout, **kwargs)
        return super().__call__(svc, params, sid=sid, timeout=timeout, **kwargs)

    def execs(self, template):
        return sum(1 for s, p in self.calls if s == "report/exec_report" and p["reportTemplateId"] == template)


class Env:
    def __init__(self, engine, env=None):
        self.engine = engine
        self._patches = [
            patch("app.wialon_reports.wialon_request", engine),
            patch("app.wialon_reports.get_vehicle_groups", return_value=[{"id": 1, "nm": "All Unit", "u": [101, 102, 103]}]),
            patch("app.fuel.wialon_distance.login", side_effect=lambda force=False, session=None: f"sid-{session or 'shared'}"),
            patch("app.fuel.wialon_distance.current_sid", return_value="sid-shared"),
            patch.object(wd, "_FLEET_TRIPS_CACHE", TTLCache()),
            patch.object(wd, "_UNIT_TRIPS_CACHE", TTLCache()),
            patch.object(wd, "_TELEMATICS_CACHE", TTLCache()),
            patch.dict(os.environ, env or {}),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()


def trips_for(engine, units=UNITS, rng=CLOSED, env=None):
    with Env(engine, env):
        return wd.get_units_trips_for_range([u["id"] for u in units], *rng, "sid-shared", units)


def legacy_trips(per_unit, units=UNITS):
    with Env(Engine(per_unit)):
        return wd.get_units_trips_for_range([u["id"] for u in units], *CLOSED, "sid-shared")


# ─── Report parsing ────────────────────────────────────────────────────────────

def test_group_trips_subrows_parsed():
    tables, _ = fleet_tables()
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)) as eng:
        rows = wr.execute_wialon_group_trips_report("s", *parse_date_to_uae_bounds(*CLOSED), group_id=1, include_trips=True)
    a, b, c = rows
    assert [(t["start_time"], t["distance_km"]) for t in a["trips"]] == [(s, k) for s, _, k in TRIPS[101]]
    assert a["trips"][0]["end_time"] == TRIPS[101][0][0] + 3600 and a["trip_unit_ids"] == [101] and a["trips_complete"]
    assert b["trips_complete"] and len(b["trips"]) == 2
    assert c["trips"] == [] and c["trips_complete"] and c["trip_count"] == 0, "the '-----' placeholder is not a trip"
    assert eng.count("core/batch") == 1 and eng.count("report/get_result_subrows") == 3, "one batch, no per-row calls"

    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)) as eng:
        plain = wr.execute_wialon_group_trips_report("s", 0, 1, group_id=1)
    assert eng.count("core/batch") == 0 and eng.count("report/get_result_subrows") == 0
    assert all("trips" not in r and "trips_complete" not in r for r in plain), "default output unchanged"
    assert [r["mileage_km"] for r in plain] == [175.75, 310.1, 0.0]
    print("[PASS] Template 5 sub-rows -> trips (UTC starts, km, unit ids); placeholder = no trip; default output unchanged.")


def test_unreadable_malformed_and_undetailed_rows_incomplete():
    def edit(t, _):
        t[TEMPLATE_GROUP_TRIPS]["subrows"][0][1]["c"][5] = "--- km"               # unreadable distance
        t[TEMPLATE_GROUP_TRIPS]["subrows"][1][0]["c"] = t[TEMPLATE_GROUP_TRIPS]["subrows"][1][0]["c"][:4]  # malformed
        t[TEMPLATE_GROUP_TRIPS]["rows"][2]["d"] = 0                                 # detail off: no sub-rows
    tables, _ = fleet_tables(edit=edit)
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)):
        a, b, c = wr.execute_wialon_group_trips_report("s", 0, 1, group_id=1, include_trips=True)
    assert not a["trips_complete"] and a["trips"][1]["distance_km"] is None, "unreadable km is None, never 0"
    assert not b["trips_complete"] and len(b["trips"]) == 1
    assert not c["trips_complete"], "a row without sub-rows cannot prove it had no trips"
    tables, _ = fleet_tables()
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables, subrow_error_rows={1})):
        rows = wr.execute_wialon_group_trips_report("s", 0, 1, group_id=1, include_trips=True)
    assert [r["trips_complete"] for r in rows] == [True, False, True], "a sub-row error flags only that unit"
    print("[PASS] Unreadable km, malformed sub-rows, rows without detail and sub-row errors -> trips_complete False.")


# ─── Equivalence and attribution ──────────────────────────────────────────────

def test_fleet_equals_per_unit_reports():
    tables, per_unit = fleet_tables()
    engine = Engine(per_unit, tables=tables)
    fleet = trips_for(engine)
    legacy = legacy_trips(per_unit)
    norm = lambda res: {u: (r["status"], [(t["start_time"], t["end_time"], t["distance_km"]) for t in r["trips"]]) for u, r in res.items()}
    assert norm(fleet) == norm(legacy), (norm(fleet), norm(legacy))
    assert engine.execs(TEMPLATE_GROUP_TRIPS) == 1 and engine.execs(TEMPLATE_UNIT_TRIPS) == 0
    txs = [{"vehicle_identifier": "UNIT-A", "transaction_date": "2026-08-03T10:00:00", "litres": 30.0, "total_cost": 90.0},
           {"vehicle_identifier": "UNIT-B", "transaction_date": "2026-08-04T10:00:00", "litres": 60.0, "total_cost": 180.0},
           {"vehicle_identifier": "UNIT-C", "transaction_date": "2026-08-05T10:00:00", "litres": 5.0, "total_cost": 15.0}]
    for g in ("day", "week", "month"):
        a = calculate_efficiency_trend(txs, {}, UNITS, g, unit_trips=fleet).model_dump()
        b = calculate_efficiency_trend(txs, {}, UNITS, g, unit_trips=legacy).model_dump()
        assert a == b, g
    print("[PASS] One fleet report = 3 per-unit reports: same trips/statuses, identical day/week/month trend; 1 report instead of 3.")


def test_uae_day_boundaries_and_empty_periods():
    tables, per_unit = fleet_tables()
    engine = Engine(per_unit, tables=tables)
    res = trips_for(engine)
    exec_params = next(p for s, p in engine.calls if s == "report/exec_report")
    assert (exec_params["interval"]["from"], exec_params["interval"]["to"]) == parse_date_to_uae_bounds(*CLOSED)
    txs = [{"vehicle_identifier": "UNIT-A", "transaction_date": "2026-08-03T10:00:00", "litres": 30.0, "total_cost": 90.0},
           {"vehicle_identifier": "UNIT-B", "transaction_date": "2026-08-04T10:00:00", "litres": 60.0, "total_cost": 180.0},
           {"vehicle_identifier": "UNIT-C", "transaction_date": "2026-08-04T10:00:00", "litres": 5.0, "total_cost": 15.0}]
    day = {d.period: d for d in calculate_efficiency_trend(txs, {}, UNITS, "day", unit_trips=res).data}
    assert day["2026-08-03"].distance_km == 160.75 and day["2026-08-03"].boundary_trip_km == 40.25, "23:30 trip stays on its start day"
    assert day["2026-08-05"].distance_km == 25.1 and day["2026-08-05"].units_with_distance == 2, "20:30 UTC on the 4th is the 5th in the UAE"
    assert res[103] == {"status": "complete", "trips": [], "error": None}, "no trips is a recorded 0, not unknown"
    assert day["2026-08-04"].distance_km == 300.0 and day["2026-08-04"].units_with_distance == 1, "UNIT-C: 0 km, not unknown"
    alone = calculate_efficiency_trend(txs[2:], {}, UNITS, "day", unit_trips=res).data
    assert [(d.distance_km, d.efficiency_status) for d in alone] == [(0.0, "no_distance")], "an idle unit's period is 0 km"
    print("[PASS] Interval = UAE day bounds; UTC trip starts bucket by UAE day; a unit without trips is complete with 0 km.")


def test_uncertain_units_fall_back_to_their_own_report():
    units = UNITS + [{"id": 104, "nm": "UNIT-D"}, {"id": 105, "nm": "UNIT-B"}]  # UNIT-B name shared by 102 and 105

    def edit(t, per_unit):
        rows, subs = t[TEMPLATE_GROUP_TRIPS]["rows"], t[TEMPLATE_GROUP_TRIPS]["subrows"]
        rows.pop(3), subs.pop(3)                                    # UNIT-D absent from the fleet report
        subs[0][0]["c"][1]["u"] = 999                               # a UNIT-A trip cell names another unit
        rows[2]["c"][5] = "5.00 km"                                 # UNIT-C total disagrees with its (no) trips
    tables, per_unit = fleet_tables(units=units, trips={**TRIPS, 104: TRIPS[102][:1], 105: TRIPS[102][1:]}, edit=edit)
    tables[TEMPLATE_GROUP_TRIPS]["rows"] = tables[TEMPLATE_GROUP_TRIPS]["rows"][:4]
    engine = Engine(per_unit, tables=tables)
    res = trips_for(engine, units=units)
    per_unit_reports = sorted(p["reportObjectId"] for s, p in engine.calls
                              if s == "report/exec_report" and p["reportTemplateId"] == TEMPLATE_UNIT_TRIPS)
    assert per_unit_reports == [101, 102, 103, 104, 105], per_unit_reports
    assert res == legacy_trips(per_unit, units), "every fallback unit gets exactly its own report's result"
    print("[PASS] Absent unit, shared unit name, foreign unit id in a trip cell and km mismatch -> that unit's own report.")


def test_duplicate_report_rows_fall_back():
    tables, per_unit = fleet_tables()
    tables[TEMPLATE_GROUP_TRIPS]["rows"].append(dict(tables[TEMPLATE_GROUP_TRIPS]["rows"][0]))
    tables[TEMPLATE_GROUP_TRIPS]["subrows"][3] = tables[TEMPLATE_GROUP_TRIPS]["subrows"][0]
    engine = Engine(per_unit, tables=tables)
    res = trips_for(engine)
    assert [p["reportObjectId"] for s, p in engine.calls if s == "report/exec_report" and p["reportTemplateId"] == TEMPLATE_UNIT_TRIPS] == [101]
    assert res[101]["status"] == "complete" and len(res[101]["trips"]) == 3
    print("[PASS] A unit name appearing twice in the fleet report is never attributed from it (own report instead).")


# ─── Failures and caching ─────────────────────────────────────────────────────

def test_fleet_failure_falls_back_never_zero():
    tables, per_unit = fleet_tables()
    engine = Engine(per_unit, tables=tables, fail_exec={TEMPLATE_GROUP_TRIPS})
    res = trips_for(engine)
    assert engine.execs(TEMPLATE_GROUP_TRIPS) == 2, "a non-limit error keeps its one re-login retry"
    assert engine.execs(TEMPLATE_UNIT_TRIPS) == 0, "a failed fleet report starts no per-unit reports"
    assert all(r["status"] == "failed" and r["trips"] == [] for r in res.values()), "failures stay failed, never 0 km"
    tr = calculate_efficiency_trend([{"vehicle_identifier": "UNIT-A", "transaction_date": "2026-08-03T10:00:00",
                                      "litres": 30.0, "total_cost": 90.0}], {}, UNITS, "day", unit_trips=res)
    assert tr.data[0].distance_km is None and tr.units_failed
    tables, per_unit = fleet_tables()
    tables[TEMPLATE_GROUP_TRIPS] = {"header": GROUP_TRIPS_HEADER, "rows": [], "subrows": {}}  # no tables at all
    engine = Engine(per_unit, tables=tables)
    assert trips_for(engine) == legacy_trips(per_unit) and engine.execs(TEMPLATE_UNIT_TRIPS) == 3
    print("[PASS] Fleet report failing (after a retry) -> 'failed', no per-unit reports, never 0 km; empty -> per-unit reports.")


def test_limit_error_1004_is_not_retried():
    tables, per_unit = fleet_tables()

    class Limited(Engine):
        def __call__(self, svc, params, sid=None, **kw):
            if svc == "report/exec_report" and params["reportTemplateId"] == TEMPLATE_GROUP_TRIPS:
                self.calls.append((svc, params))
                return {"error": 1004, "reason": "LIMIT msgs_activity"}
            return super().__call__(svc, params, sid=sid, **kw)

    engine = Limited(per_unit, tables=tables)
    forced = []
    with Env(engine), patch("app.fuel.wialon_distance.login",
                            side_effect=lambda force=False, session=None: forced.append(force) or f"sid-{session or 'shared'}"):
        res = wd.get_units_trips_for_range([u["id"] for u in UNITS], *CLOSED, "sid-shared", UNITS)
        try:
            wd._run_report_with_retry(wr.execute_wialon_group_trips_report, "sid-fuel", 0, 1)
            raise AssertionError("1004 must propagate")
        except WialonAPIError as exc:
            assert wd._wialon_error_code(exc) == 1004
    assert engine.execs(TEMPLATE_GROUP_TRIPS) == 2, "one exec per call: no re-login rerun after 1004"
    assert True not in forced, "no forced re-login after 1004"
    assert engine.execs(TEMPLATE_UNIT_TRIPS) == 0 and all(r["status"] == "failed" for r in res.values())
    print("[PASS] Wialon 1004 (LIMIT msgs_activity): no forced re-login, no rerun, no per-unit reports.")


def test_cached_once_and_failures_not_cached():
    tables, per_unit = fleet_tables()
    engine = Engine(per_unit, tables=tables)
    with Env(engine):
        for _ in range(3):  # day, week and month views of one range
            wd.get_units_trips_for_range([101, 102], *CLOSED, "sid-shared", UNITS)
        wd.get_units_trips_for_range([103], *CLOSED, "sid-shared", UNITS)
    assert engine.execs(TEMPLATE_GROUP_TRIPS) == 1 and engine.execs(TEMPLATE_UNIT_TRIPS) == 0
    engine = Engine(per_unit, tables=tables, fail_exec={TEMPLATE_GROUP_TRIPS})
    with Env(engine, {"FUEL_TELEMATICS_TTL_PARTIAL_S": "30"}), patch.object(wd._FLEET_TRIPS_CACHE, "error_share_seconds", 0):
        wd.get_units_trips_for_range([101], *CLOSED, "sid-shared", UNITS)
        wd.get_units_trips_for_range([102], *CLOSED, "sid-shared", UNITS)
    assert engine.execs(TEMPLATE_GROUP_TRIPS) == 4, "a failed fleet report is retried by the next request, not cached"
    engine = Engine(per_unit, tables=tables)
    res = trips_for(engine, env={"FUEL_TREND_FLEET_TRIPS": "0"})
    assert engine.execs(TEMPLATE_GROUP_TRIPS) == 0 and engine.execs(TEMPLATE_UNIT_TRIPS) == 3 and res == legacy_trips(per_unit)
    print("[PASS] Fleet report cached once per range (day/week/month); failures not cached; FUEL_TREND_FLEET_TRIPS=0 = per-unit.")


def test_fleet_report_runs_on_its_own_session():
    tables, per_unit = fleet_tables()
    sids = []

    class Tracking(Engine):
        def __call__(self, svc, params, sid=None, **kw):
            if svc == "report/exec_report":
                sids.append((params["reportTemplateId"], sid))
            return super().__call__(svc, params, sid=sid, **kw)

    trips_for(Tracking(per_unit, tables=tables))
    assert sids == [(TEMPLATE_GROUP_TRIPS, "sid-fuel_trips")], sids
    print("[PASS] The fleet trips report runs on the fuel_trips session (Dashboard reports never queue behind it).")


# ─── Telematics bundle and idle events ────────────────────────────────────────

def test_bundle_reports_run_concurrently_on_fuel_sessions():
    in_flight, overlap, sids = [0], [False], {}
    guard = threading.Lock()

    class Slow(FakeReportEngine):
        def __call__(self, svc, params, sid=None, **kw):
            if svc == "report/exec_report":
                sids[params["reportTemplateId"]] = sid
                with guard:
                    in_flight[0] += 1
                    overlap[0] = overlap[0] or in_flight[0] > 1
                time.sleep(0.2)
                with guard:
                    in_flight[0] -= 1
            return super().__call__(svc, params, sid=sid, **kw)

    named = {"sid-fuel": "fuel", "sid-fuel_engine": "fuel_engine"}  # what app.wialon.login(session=...) registers
    with Env(Slow()), patch("app.wialon_reports.session_name", side_effect=named.get):
        b = wd.get_fleet_telematics_bundle(*CLOSED, sid="sid-shared")
    with Env(FakeReportEngine()):
        plain = dict(wd._build_fleet_telematics_bundle(*CLOSED, None), fetched_at_utc=None)
    assert overlap[0], "trips and engine reports were in flight at the same time"
    assert sids == {TEMPLATE_GROUP_TRIPS: "sid-fuel", TEMPLATE_GROUP_ENGINE: "sid-fuel_engine"}, sids
    assert dict(b, fetched_at_utc=None) == plain and b["status"] == "complete"
    assert b["by_name"]["UNIT-A"]["mileage_km"] == 250.75 and b["by_name"]["UNIT-A"]["idle_seconds"] == 5400
    print("[PASS] Bundle: trips and engine reports run concurrently on the fuel / fuel_engine sessions; values unchanged.")


def test_named_session_relogin_under_its_own_lock():
    seen = []
    named = {"sid-fuel_engine": "fuel_engine"}

    def login(force=False, session=None):
        if force:
            seen.append((session, wr._report_lock_for("sid-fuel_engine").locked(), wr._report_lock.locked()))
        return f"sid-{session or 'shared'}"

    class SessionExpired(FakeReportEngine):
        # Wialon error 1 (invalid session) is the error that forces a new login (a non-session failure only retries)
        def __call__(self, svc, params, sid=None, **kw):
            if svc == "report/exec_report" and params.get("reportTemplateId") == TEMPLATE_GROUP_ENGINE:
                self.calls.append((svc, params))
                return {"error": 1}
            return super().__call__(svc, params, sid=sid, **kw)

    with Env(SessionExpired()), \
         patch("app.fuel.wialon_distance.login", side_effect=login), \
         patch("app.fuel.wialon_distance.session_name", side_effect=named.get), \
         patch("app.wialon_reports.session_name", side_effect=named.get):
        try:
            wd.get_fleet_telematics_bundle(*CLOSED, sid="sid-shared")
            raise AssertionError("expected WialonAPIError")
        except WialonAPIError:
            pass
    assert seen == [("fuel_engine", True, False)], seen
    print("[PASS] A failing fuel_engine report re-logs in that session, under that session's report lock only.")


def test_engine_idle_subrows_batched_for_used_rows_only():
    eng = FakeReportEngine()
    with patch("app.wialon_reports.wialon_request", eng):
        full = wr.execute_wialon_group_engine_idle_report("s", 0, 1, group_id=1)
    # Fixture rows: UNIT-A idles with 3 sub-rows, UNIT-B with 1 (its single interval), UNIT-C has no idling: the two
    # idling rows' first pages go out in one batch (the fake engine also counts each batched sub-row request)
    assert eng.count("core/batch") == 1 and eng.count("report/get_result_subrows") == 2, eng.calls
    assert len(full["events"]) == 4 and full["vehicles"][0]["idle_events"] == 3
    eng = FakeReportEngine()
    with patch("app.wialon_reports.wialon_request", eng):
        scoped = wr.execute_wialon_group_engine_idle_report("s", 0, 1, group_id=1, event_vehicle_names={"UNIT-B"})
    batched_rows = [c["params"]["rowIndex"] for s, p in eng.calls if s == "core/batch" for c in p["params"]]
    assert batched_rows == [1], f"only the scoped unit's row is read: {batched_rows}"
    eng = FakeReportEngine()
    with patch("app.wialon_reports.wialon_request", eng):
        none = wr.execute_wialon_group_engine_idle_report("s", 0, 1, group_id=1, include_events=False)
    assert eng.count("core/batch") == 0 and eng.count("report/get_result_subrows") == 0, "no sub-rows for a summary report"
    assert scoped["vehicles"][0]["idle_events"] is None and none["vehicles"][0]["total_idle_seconds"] == 5400
    print("[PASS] Idle events: sub-rows of the used rows come in one core/batch call; scoped/summary reports fetch none.")


TESTS = [
    test_group_trips_subrows_parsed,
    test_unreadable_malformed_and_undetailed_rows_incomplete,
    test_fleet_equals_per_unit_reports,
    test_uae_day_boundaries_and_empty_periods,
    test_uncertain_units_fall_back_to_their_own_report,
    test_duplicate_report_rows_fall_back,
    test_fleet_failure_falls_back_never_zero,
    test_limit_error_1004_is_not_retried,
    test_cached_once_and_failures_not_cached,
    test_fleet_report_runs_on_its_own_session,
    test_bundle_reports_run_concurrently_on_fuel_sessions,
    test_named_session_relogin_under_its_own_lock,
    test_engine_idle_subrows_batched_for_used_rows_only,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL TREND FLEET TRIPS + PARALLEL FUEL / BATCHED IDLE REPORTS (OFFLINE)") else 0)
