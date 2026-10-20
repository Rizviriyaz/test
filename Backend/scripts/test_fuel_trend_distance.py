"""
Offline regression tests for Fuel Phase 5: ACTUAL Wialon distance per trend period.

  - per-period km from each unit's trips (by trip start, UAE day), never apportioned by fuel share
  - boundary trips, UTC->UAE day bucketing, minimum distance, cumulative ratio
  - failed/partial units isolated (never 0 km), reconciliation vs group report, one fetch per unit+range

Run from Backend/:  python scripts/test_fuel_trend_distance.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import run  # noqa: E402
from wialon_report_fixtures import TEMPLATE_UNIT_TRIPS, FakeReportEngine, default_tables  # noqa: E402

import app.fuel.wialon_distance as wd  # noqa: E402
from app.fuel.efficiency import calculate_efficiency_trend  # noqa: E402
from app.utils.ttl_cache import TTLCache  # noqa: E402

UAE = timezone(timedelta(hours=4))
UNITS = [{"id": 1, "nm": "V-1"}, {"id": 2, "nm": "V-2"}]


def ts(y, m, d, hh, mm=0, tz=UAE):
    return int(datetime(y, m, d, hh, mm, tzinfo=tz).timestamp())


def trip(start, km, minutes=60):
    return {"start_time": start, "end_time": start + minutes * 60, "distance_km": km}


def fuel(vid, date, litres):
    return {"vehicle_identifier": vid, "transaction_date": date, "litres": litres, "total_cost": litres * 3.0}


def ok(*trips):
    return {"status": "complete", "error": None, "trips": list(trips)}


def test_actual_daily_distance_not_apportioned():
    txs = [fuel("V-1", "2026-08-03T10:00:00", 50.0), fuel("V-1", "2026-08-04T10:00:00", 50.0),
           fuel("V-1", "2026-08-10T10:00:00", 100.0)]
    trips = {1: ok(trip(ts(2026, 8, 3, 12), 300.0), trip(ts(2026, 8, 4, 12), 100.0), trip(ts(2026, 8, 10, 12), 600.0))}
    day = calculate_efficiency_trend(txs, {"V-1": 1000.0}, UNITS[:1], "day", unit_trips=trips)
    assert [d.distance_km for d in day.data] == [300.0, 100.0, 600.0]
    assert [d.fuel_efficiency_l_per_100km for d in day.data] == [16.67, 50.0, 16.67]
    assert day.data[-1].cumulative_l_per_100km == 20.0 and day.data[0].distance_source == "wialon_unit_trips"
    month = calculate_efficiency_trend(txs, {"V-1": 1000.0}, UNITS[:1], "month", unit_trips=trips)
    assert month.data[0].distance_km == 1000.0 and month.data[0].fuel_efficiency_l_per_100km == 20.0
    assert month.distance_reconciliation["mismatches"] == [] and month.distance_reconciliation["units_checked"] == 1
    print("[PASS] Daily km 300/100/600 from trips (not fuel share); L/100 km 16.67/50/16.67; month 1000 km / 20.0.")


def test_distance_only_day_and_boundaries():
    txs = [fuel("V-1", "2026-08-03T10:00:00", 60.0)]
    trips = {1: ok(trip(ts(2026, 8, 3, 9), 300.0),
                   trip(ts(2026, 8, 4, 23, 30), 40.0, minutes=70),            # 23:30 UAE -> 00:40 next day
                   trip(ts(2026, 8, 5, 22, 0, tz=timezone.utc), 30.0))}         # 22:00 UTC = 02:00 UAE on the 6th
    day = calculate_efficiency_trend(txs, {}, UNITS[:1], "day", unit_trips=trips)
    by = {d.period: d for d in day.data}
    assert by["2026-08-04"].efficiency_status == "no_fuel" and by["2026-08-04"].distance_km == 40.0
    assert by["2026-08-04"].boundary_trip_km == 40.0, "trip stays in its start day; km reported as boundary"
    assert "2026-08-05" not in by and by["2026-08-06"].distance_km == 30.0, "UTC timestamps bucket by UAE day"
    assert by["2026-08-03"].fuel_efficiency_l_per_100km == 20.0
    print("[PASS] Distance-only day shows no_fuel; boundary trip kept in start day; 22:00 UTC is the next UAE day.")


def test_minimum_distance_period_and_cumulative():
    txs = [fuel("V-1", "2026-08-03T10:00:00", 5.0), fuel("V-1", "2026-08-04T10:00:00", 30.0)]
    trips = {1: ok(trip(ts(2026, 8, 3, 12), 10.0), trip(ts(2026, 8, 4, 12), 190.0))}
    day = calculate_efficiency_trend(txs, {}, UNITS[:1], "day", unit_trips=trips)
    d3, d4 = day.data
    assert d3.efficiency_status == "insufficient_distance" and d3.fuel_efficiency_l_per_100km is None
    assert d3.cumulative_l_per_100km is None  # 10 km cumulative is below the minimum too
    assert d4.cumulative_l_per_100km == 17.5  # 35 L / 200 km
    print("[PASS] A 10 km day is insufficient_distance; the cumulative ratio appears once the minimum is reached.")


def test_failed_unit_isolated_and_reconciliation():
    txs = [fuel("V-1", "2026-08-03T10:00:00", 50.0), fuel("V-2", "2026-08-03T11:00:00", 40.0)]
    trips = {1: ok(trip(ts(2026, 8, 3, 12), 300.0)), 2: {"status": "failed", "trips": [], "error": "boom"}}
    res = calculate_efficiency_trend(txs, {"V-1": 400.0, "V-2": 100.0}, UNITS, "day", unit_trips=trips)
    d = res.data[0]
    assert d.distance_km == 300.0 and d.tracked_litres == 50.0 and d.litres == 90.0
    assert d.fuel_efficiency_l_per_100km == round(50 / 300 * 100, 2)
    assert [u["wialon_unit_id"] for u in res.units_failed] == [2] and res.data_warnings
    mism = res.distance_reconciliation["mismatches"]
    assert mism == [{"wialon_unit_id": 1, "wialon_unit_name": "V-1", "trips_km": 300.0, "group_report_km": 400.0, "difference_km": -100.0}]
    partial = {1: {"status": "partial", "trips": [trip(ts(2026, 8, 3, 12), None)], "error": None}}
    res = calculate_efficiency_trend(txs[:1], {}, UNITS[:1], "day", unit_trips=partial)
    assert res.data[0].distance_km is None and res.units_failed[0]["status"] == "partial"
    print("[PASS] Failed/partial units are excluded and listed (never 0 km); group-report mismatch flagged, unadjusted.")


def test_two_identifiers_one_unit_counted_once():
    units = [{"id": 1, "nm": "7AUH55943"}]
    txs = [fuel("7-AUH-55943", "2026-08-03T10:00:00", 30.0), fuel("7 AUH 55943", "2026-08-03T12:00:00", 30.0)]
    res = calculate_efficiency_trend(txs, {}, units, "day", unit_trips={1: ok(trip(ts(2026, 8, 3, 12), 200.0))})
    assert res.data[0].distance_km == 200.0 and res.data[0].units_with_distance == 1
    assert res.data[0].fuel_efficiency_l_per_100km == 30.0
    print("[PASS] Two fuel identifiers of one unit: its trip km counted once per period.")


def test_no_tracked_units_all_none():
    txs = [fuel("V-1", "2026-08-03T10:00:00", 50.0)]
    res = calculate_efficiency_trend(txs, {}, UNITS[:1], "day", unit_trips={1: {"status": "failed", "trips": [], "error": "x"}})
    assert res.data[0].distance_km is None and res.data[0].efficiency_status == "unavailable"
    res = calculate_efficiency_trend(txs, {}, UNITS[:1], "day", unit_trips=None)
    assert res.data[0].distance_km is None and res.data_warnings
    print("[PASS] No unit with complete trips: distance and efficiency None for every period (never apportioned).")


def test_unit_trips_fetched_once_per_unit_and_range():
    engine = FakeReportEngine()
    with patch("app.wialon_reports.wialon_request", engine), \
         patch("app.fuel.wialon_distance.login", return_value="sid"), \
         patch.object(wd, "_UNIT_TRIPS_CACHE", TTLCache()):
        a = wd.get_units_trips_for_range([101], "2026-08-03", "2026-08-05", "sid")
        execs = engine.count("report/exec_report")
        b = wd.get_units_trips_for_range([101], "2026-08-03", "2026-08-05", "sid")  # week/month view of same range
        assert engine.count("report/exec_report") == execs == 1 and a == b
        assert a[101]["status"] == "complete" and len(a[101]["trips"]) == 3
    tables = default_tables()
    tables[TEMPLATE_UNIT_TRIPS]["rows"][0]["c"][9] = "--- km"
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)), \
         patch.object(wd, "_UNIT_TRIPS_CACHE", TTLCache()):
        p = wd.get_units_trips_for_range([101], "2026-08-03", "2026-08-05", "sid")
    assert p[101]["status"] == "partial" and p[101]["trips"][0]["distance_km"] is None
    tables = default_tables()
    tables[TEMPLATE_UNIT_TRIPS]["rows"][1]["c"] = tables[TEMPLATE_UNIT_TRIPS]["rows"][1]["c"][:5]  # malformed row
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(tables=tables)), \
         patch.object(wd, "_UNIT_TRIPS_CACHE", TTLCache()):
        short = wd.get_units_trips_for_range([101], "2026-08-03", "2026-08-05", "sid")
    assert short[101]["status"] == "partial", "a dropped malformed row must not look complete"
    with patch("app.wialon_reports.wialon_request", FakeReportEngine(fail_exec={TEMPLATE_UNIT_TRIPS})), \
         patch("app.fuel.wialon_distance.login", return_value="sid2"), \
         patch.object(wd, "_UNIT_TRIPS_CACHE", TTLCache()):
        f = wd.get_units_trips_for_range([101], "2026-08-03", "2026-08-05", "sid")
    assert f[101]["status"] == "failed" and f[101]["trips"] == []
    print("[PASS] Unit trips: one report per unit and range (cached across day/week/month); unreadable -> partial; failure -> failed.")


TESTS = [
    test_actual_daily_distance_not_apportioned,
    test_distance_only_day_and_boundaries,
    test_minimum_distance_period_and_cumulative,
    test_failed_unit_isolated_and_reconciliation,
    test_two_identifiers_one_unit_counted_once,
    test_no_tracked_units_all_none,
    test_unit_trips_fetched_once_per_unit_and_range,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PHASE 5 - ACTUAL PER-PERIOD WIALON DISTANCE (OFFLINE)") else 0)
