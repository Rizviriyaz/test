"""
Offline tests: monthly-summary rows (date_basis 'reporting_month_end') in fuel analytics. They must count as a
month's total only for ranges covering the whole month, never be compared with one day's distance, never appear as
daily refuels in trends, never trigger refuel-pattern anomalies, and be reported as such to the AI tools.

Fixtures only (FUEL_IMPORT_REPLACE=1 for the range probe); OfflineGuard blocks every outbound HTTP call.
These tests prove the rules on fixtures, not production data accuracy.

Run from Backend/:  python scripts/test_fuel_summary_analytics.py
"""

import os
import sys
import traceback
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402

from app.ai import tools as ai_tools  # noqa: E402
from app.fuel.analytics import calculate_fuel_summary, calculate_fuel_trend  # noqa: E402
from app.fuel.anomalies import detect_fuel_waste_and_anomalies  # noqa: E402
from app.fuel.efficiency import calculate_efficiency_trend, calculate_vehicle_efficiency_records  # noqa: E402
from app.fuel.scope import (  # noqa: E402
    REASON_PARTIAL_SUMMARY,
    build_scope,
    resolve_effective_dates,
    summarize_excluded,
)
from app.utils.datetime_helpers import UAE_TZ  # noqa: E402

UNITS = [{"id": 1, "nm": "AUH 44120"}, {"id": 2, "nm": "DXB 10293"}]
SUMMARY = {"id": 10, "import_id": 3, "transaction_date": "2026-08-31", "vehicle_identifier": "AUH 44120",
           "litres": 500.0, "total_cost": 1500.0, "fuel_type": "Diesel", "date_basis": "reporting_month_end"}
DATED = [
    {"id": 11, "import_id": 2, "transaction_date": "2026-08-05T08:00:00", "vehicle_identifier": "DXB 10293",
     "litres": 60.0, "total_cost": 180.0, "fuel_type": "Diesel", "date_basis": "transaction"},
    {"id": 12, "import_id": 2, "transaction_date": "2026-08-20T08:00:00", "vehicle_identifier": "DXB 10293",
     "litres": 40.0, "total_cost": 120.0, "fuel_type": "Diesel", "date_basis": None},  # legacy row: dated
]
ROWS = [SUMMARY] + DATED


def fetch_fn(filters):
    """Fake database read honouring the date filter the way PostgREST does (whole days)."""
    lo, hi = filters.get("date_from") or "0000", filters.get("date_to") or "9999"
    return [dict(r) for r in ROWS if lo <= str(r["transaction_date"])[:10] <= hi]


def scope(date_from=None, date_to=None):
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1"}):
        return build_scope({"date_from": date_from, "date_to": date_to}, fetch_fn, lambda: "sid", lambda sid: UNITS)


def ts(day, hour=8):
    return int(datetime(2026, 8, day, hour, tzinfo=UAE_TZ).timestamp())


def test_full_month_range_counts_the_summary():
    s = scope("2026-08-01", "2026-08-31")
    ex = s.excluded_scope
    assert len(s.matched) == 3 and ex["partial_period_summary"]["transaction_count"] == 0, ex
    assert ex["monthly_summary_rows"] == 1
    summ = calculate_fuel_summary(s.matched)
    assert summ.total_litres == 600.0 and summ.total_cost == 1800.0, summ
    print("[PASS] Whole-month range: the monthly summary counts in fuel and cost totals.")


def test_range_starting_mid_month_excludes_the_summary():
    s = scope("2026-08-15", "2026-08-31")
    ex = s.excluded_scope
    assert [r["id"] for r in s.matched] == [12], s.matched
    assert ex["partial_period_summary"]["transaction_count"] == 1 and ex["partial_period_summary"]["litres"] == 500.0
    assert ex["all_transaction_count"] == ex["kpi"]["transaction_count"] + ex["excluded_transaction_count"], ex
    assert any("cannot be split by day" in w for w in ex["warnings"]), ex["warnings"]
    groups = summarize_excluded(s.partition, REASON_PARTIAL_SUMMARY)
    assert groups and groups[0]["reason"] == REASON_PARTIAL_SUMMARY
    print("[PASS] Range starting after the 1st: the summary is excluded and reported; reconciliation still balances.")


def test_range_ending_before_month_end_warns():
    s = scope("2026-08-01", "2026-08-20")
    assert [r["id"] for r in s.matched] == [11, 12]
    assert any("is not included" in w and "2026-08-31" in w for w in s.excluded_scope["warnings"]), s.excluded_scope
    print("[PASS] Range ending before the month end: the summary is not counted, and the response says so.")


def test_efficiency_window_spans_the_summary_month():
    only_summary = [dict(SUMMARY, _unit_id=1)]
    assert resolve_effective_dates(only_summary, None, None) == ("2026-08-01", "2026-08-31")
    mixed = only_summary + [dict(DATED[0], transaction_date="2026-09-03")]
    assert resolve_effective_dates(mixed, None, None) == ("2026-08-01", "2026-09-03")
    assert resolve_effective_dates(DATED, None, None) == ("2026-08-05", "2026-08-20"), "dated rows unchanged"
    assert resolve_effective_dates(only_summary, "2026-08-01", "2026-08-31") == ("2026-08-01", "2026-08-31")
    print("[PASS] Efficiency window: a summary row stands for its whole month, never just its month-end date.")


def test_ai_tools_use_the_same_window_and_label_summaries():
    s = scope()
    seen = []
    with patch("app.fuel.wialon_distance.get_fleet_telematics_bundle",
               side_effect=lambda a, b, sid=None: seen.append((a, b)) or {"by_name": {}, "duplicate_names": []}):
        _, dates = ai_tools._telematics_for(s, None, None)
    assert dates == ("2026-08-01", "2026-08-31") and seen == [("2026-08-01", "2026-08-31")], (dates, seen)
    brief = ai_tools._excluded_brief(s)
    assert brief["monthly_summary_rows_in_scope"] == 1 and "not refuelling events" in brief["monthly_summary_note"]
    brief = ai_tools._excluded_brief(scope("2026-08-15", "2026-08-31"))
    assert brief["partial_month_summaries_excluded"] == 1
    print("[PASS] AI tools: Wialon distance over the whole summary month; summaries labelled as monthly totals.")


def test_ai_vehicle_performance_does_not_count_summaries_as_refuels():
    s = scope("2026-08-01", "2026-08-31")
    auh = [r for r in s.matched if r["vehicle_identifier"] == "AUH 44120"]
    s.partition.matched = auh
    with patch.object(ai_tools, "_fuel_scope", return_value=s), \
         patch("app.fuel.wialon_distance.get_fleet_telematics_bundle",
               return_value={"by_name": {}, "duplicate_names": [], "status": "complete"}):
        out = ai_tools.tool_get_vehicle_fuel_performance("AUH 44120", "2026-08-01", "2026-08-31")
    assert out["refuel_transactions"] == 0 and out["monthly_summary_rows"] == 1 and out["litres"] == 500.0, out
    print("[PASS] AI vehicle performance: a monthly summary is not reported as a refuel transaction.")


def test_fuel_trend_never_shows_summaries_as_daily_refuels():
    s = scope("2026-08-01", "2026-08-31")
    day = calculate_fuel_trend(s.matched, "day")
    assert [p.period for p in day.data] == ["2026-08-05", "2026-08-20"], day.data
    assert day.monthly_summary_excluded == {"rows": 1, "litres": 500.0, "cost": 1500.0, "months": ["2026-08"]}
    assert day.data_warnings and "monthly view" in day.data_warnings[0]
    week = calculate_fuel_trend(s.matched, "week")
    assert sum(p.litres for p in week.data) == 100.0 and week.monthly_summary_excluded["rows"] == 1
    month = calculate_fuel_trend(s.matched, "month")
    assert [(p.period, p.litres) for p in month.data] == [("2026-08", 600.0)] and month.monthly_summary_excluded is None
    print("[PASS] Fuel trend: summaries only in the monthly view; day/week views report them separately.")


def test_efficiency_trend_keeps_fuel_and_distance_on_the_same_span():
    s = scope("2026-08-01", "2026-08-31")
    trips = {
        1: {"status": "complete", "trips": [{"start_time": ts(d), "end_time": ts(d, 10), "distance_km": 100}
                                            for d in (2, 9, 16, 23, 30)]},
        2: {"status": "complete", "trips": [{"start_time": ts(5), "end_time": ts(5, 10), "distance_km": 300},
                                            {"start_time": ts(20), "end_time": ts(20, 10), "distance_km": 200}]},
    }
    with patch.dict(os.environ, {"FUEL_MIN_EFFICIENCY_KM": "10"}):
        day = calculate_efficiency_trend(s.matched, {}, UNITS, "day", unit_trips=trips)
        month = calculate_efficiency_trend(s.matched, {}, UNITS, "month", unit_trips=trips)
    by_day = {p.period: p for p in day.data}
    assert set(by_day) == {"2026-08-05", "2026-08-20"}, sorted(by_day)  # AUH's daily km left out with its summary
    assert by_day["2026-08-05"].distance_km == 300 and by_day["2026-08-05"].fuel_efficiency_l_per_100km == 20.0
    assert day.monthly_summary_excluded["litres"] == 500.0
    assert any("distance in the same months is left out" in w for w in day.data_warnings), day.data_warnings
    (aug,) = month.data
    assert aug.distance_km == 1000 and aug.tracked_litres == 600.0 and aug.fuel_efficiency_l_per_100km == 60.0, aug
    print("[PASS] Efficiency trend: day view drops a summarised unit's km with its fuel; month view compares the whole month.")


def test_anomalies_skip_summaries_but_still_check_dated_refuels():
    purchases = [{"id": 20 + i, "import_id": 2, "transaction_date": f"2026-09-0{i + 1}T08:00:00",
                  "vehicle_identifier": "AUH 44120", "litres": 50.0, "total_cost": 150.0, "odometer_km": 1000 + i * 100}
                 for i in range(4)]
    spike = {"id": 30, "import_id": 2, "transaction_date": "2026-09-08T08:00:00", "vehicle_identifier": "AUH 44120",
             "litres": 400.0, "total_cost": 1200.0, "odometer_km": 2000}
    summaries = [dict(SUMMARY), dict(SUMMARY, id=13, fuel_type="Petrol", litres=450.0, total_cost=1400.0)]
    txs = [dict(t, _unit_id=1) for t in purchases + [spike] + summaries]
    with patch.dict(os.environ, {"FUEL_MIN_EFFICIENCY_KM": "10"}):
        items = calculate_vehicle_efficiency_records(txs, {}, UNITS)
        res = detect_fuel_waste_and_anomalies(txs, items)
    flagged = {a.transaction_id for a in res.anomalies if a.transaction_id is not None}
    assert flagged.isdisjoint({10, 13}), [(a.anomaly_type, a.transaction_id) for a in res.anomalies]
    assert not any(a.anomaly_type == "same_day_multiple_refuels" for a in res.anomalies)
    assert any(a.anomaly_type == "volume_spike" and a.transaction_id == 30 for a in res.anomalies), res.anomalies
    partial_group = [{"reason": REASON_PARTIAL_SUMMARY, "vehicle_identifier": "AUH 44120", "transaction_count": 1,
                      "litres": 500.0, "cost": 1500.0}]
    res = detect_fuel_waste_and_anomalies(txs, items, excluded_groups=partial_group)
    assert not any("partial_period_summary" in a.id for a in res.anomalies)
    print("[PASS] Anomalies: summaries never flagged as spikes or same-day refuels; dated spikes still detected.")


TESTS = [
    test_full_month_range_counts_the_summary,
    test_range_starting_mid_month_excludes_the_summary,
    test_range_ending_before_month_end_warns,
    test_efficiency_window_spans_the_summary_month,
    test_ai_tools_use_the_same_window_and_label_summaries,
    test_ai_vehicle_performance_does_not_count_summaries_as_refuels,
    test_fuel_trend_never_shows_summaries_as_daily_refuels,
    test_efficiency_trend_keeps_fuel_and_distance_on_the_same_span,
    test_anomalies_skip_summaries_but_still_check_dated_refuels,
]


# ─── Monthly summary + dated rows for the same unit-month (P1-C) ──────────────
ALIAS_DATED = [  # "auh 44120" is another identifier resolving to the summary's Wialon unit ("AUH 44120")
    {"id": 21, "import_id": 4, "transaction_date": "2026-08-07T09:00:00", "vehicle_identifier": "auh 44120",
     "litres": 70.25, "total_cost": 210.77, "fuel_type": "Diesel", "date_basis": "transaction"},
    {"id": 22, "import_id": 4, "transaction_date": "2026-08-25T09:00:00", "vehicle_identifier": "auh 44120",
     "litres": 30.5, "total_cost": 91.53, "fuel_type": "Super", "date_basis": None},
]
FULL_MONTH = {"status": "completed", "import_mode": "dated",
              "replace_scope": {"period_from": "2026-08-01", "period_to": "2026-08-31", "extended_to_full_months": True}}


def overlap_scope(coverage, extra=ALIAS_DATED, replace="1", fuel_type=None):
    rows = ROWS + extra
    calls = []

    def fetch(filters):
        lo, hi = filters.get("date_from") or "0000", filters.get("date_to") or "9999"
        return [dict(r) for r in rows if lo <= str(r["transaction_date"])[:10] <= hi]

    def coverage_fn(ids):
        calls.append(sorted(ids))
        return {i: m for i, m in (coverage or {}).items() if i in ids}

    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": replace}):
        s = build_scope({"date_from": "2026-08-01", "date_to": "2026-08-31", "fuel_type": fuel_type}, fetch,
                        lambda: "sid", lambda sid: UNITS, None, coverage_fn)
    return s, calls


def assert_reconciles(s):
    ex = s.excluded_scope
    buckets = ("kpi", "unmatched", "ambiguous", "suspected_duplicates", "partial_period_summary", "summary_dated_overlap")
    assert sum(ex[b]["transaction_count"] for b in buckets) == ex["all_transaction_count"], ex
    assert round(sum(ex[b]["litres"] for b in buckets), 2) == ex["all_litres"]
    assert round(sum(ex[b]["cost"] for b in buckets), 2) == ex["all_cost"]


def test_monthly_summary_supersedes_dated_rows_of_an_identifier_alias():
    # "auh 44120" resolves to the summary's unit: the summary (500 L) wins, the alias' dated rows (100.75 L) are excluded
    s, calls = overlap_scope(coverage={})
    ex = s.excluded_scope
    assert sorted(r["id"] for r in s.matched) == [10, 11, 12], [r["id"] for r in s.matched]
    assert calculate_fuel_summary(s.matched).total_litres == 600.0, "summary + other vehicle; never 700.75"
    assert ex["summary_dated_overlap"]["transaction_count"] == 2 and ex["summary_dated_overlap"]["litres"] == 100.75
    (c,) = ex["overlap_conflicts"]
    assert (c["wialon_unit_id"], c["month"], c["resolution"]) == (1, "2026-08", "summary_used_dated_excluded"), c
    assert c["excluded_summary"]["litres"] == 0.0 and c["excluded_dated"]["litres"] == 100.75
    assert c["identifiers"] == ["auh 44120"] and ex["monthly_summary_rows"] == 1
    assert calls == [] and any("monthly summary takes precedence" in w for w in s.warnings)
    assert {r["_exclusion_reason"] for r in s.partition.summary_overlap} == {"dated_superseded_by_summary"}
    assert_reconciles(s)
    groups = summarize_excluded(s.partition, "dated_superseded_by_summary")
    assert sum(g["transaction_count"] for g in groups) == 2
    print("[PASS] Alias overlap (one unit): the monthly summary is kept, the dated rows are excluded; KPIs reconcile.")


def test_dated_coverage_and_flags_never_override_the_summary():
    for coverage, replace in (({4: FULL_MONTH}, "1"), ({}, "1"), ({4: FULL_MONTH}, "")):
        s, _ = overlap_scope(coverage=coverage, replace=replace)
        assert sorted(r["id"] for r in s.matched) == [10, 11, 12], (coverage, replace)
        assert s.excluded_scope["overlap_conflicts"][0]["resolution"] == "summary_used_dated_excluded"
    legacy = [{"id": 31, "import_id": 1, "transaction_date": "2026-08-09T09:00:00", "vehicle_identifier": "AUH 44120",
               "litres": 45.0, "total_cost": 135.0, "fuel_type": "Diesel"}]  # pre-SQL row: no date_basis
    s, _ = overlap_scope(coverage={}, extra=legacy)
    assert s.excluded_scope["summary_dated_overlap"]["transaction_count"] == 1 and len(s.matched) == 3
    assert_reconciles(s)
    print("[PASS] Confirmed dated coverage, flag state or legacy rows never override a monthly summary.")


def test_summary_split_by_the_range_still_supersedes_its_dated_rows():
    rows = ROWS + ALIAS_DATED

    def fetch(filters):
        lo, hi = filters.get("date_from") or "0000", filters.get("date_to") or "9999"
        return [dict(r) for r in rows if lo <= str(r["transaction_date"])[:10] <= hi]

    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1"}):
        starts_mid = build_scope({"date_from": "2026-08-06", "date_to": "2026-08-31"}, fetch, lambda: "sid", lambda sid: UNITS)
        ends_early = build_scope({"date_from": "2026-08-01", "date_to": "2026-08-30"}, fetch, lambda: "sid", lambda sid: UNITS)
    for s in (starts_mid, ends_early):
        assert not any(r["vehicle_identifier"] == "auh 44120" for r in s.matched), [r["id"] for r in s.matched]
        assert not any(r.get("date_basis") == "reporting_month_end" for r in s.matched), "the split summary is not counted"
        assert s.excluded_scope["summary_dated_overlap"]["transaction_count"] == 2
    print("[PASS] A summary the range splits is not counted, and its unit-month's dated rows stay superseded.")


def test_overlap_is_unit_month_wide_and_months_are_independent():
    s, _ = overlap_scope(coverage={}, fuel_type="Super")
    assert s.matched == [] and s.excluded_scope["summary_dated_overlap"]["transaction_count"] == 1  # the Super row
    no_overlap, _ = overlap_scope(coverage={}, extra=[])
    assert no_overlap.excluded_scope["overlap_conflicts"] == []
    sept = [{**ALIAS_DATED[0], "id": 41, "transaction_date": "2026-09-03T09:00:00"}]
    other_month, _ = overlap_scope(coverage={}, extra=sept)
    assert other_month.excluded_scope["summary_dated_overlap"]["transaction_count"] == 0
    from app.fuel.scope import EXCLUDED_REASONS
    assert "dated_superseded_by_summary" in EXCLUDED_REASONS
    print("[PASS] Precedence is judged per unit-month across fuel types; other months are untouched.")


TESTS += [
    test_monthly_summary_supersedes_dated_rows_of_an_identifier_alias,
    test_dated_coverage_and_flags_never_override_the_summary,
    test_summary_split_by_the_range_still_supersedes_its_dated_rows,
    test_overlap_is_unit_month_wide_and_months_are_independent,
]


# ─── AI fuel tools: same rules and parameters as the dashboard (P2-E) ─────────
AI_TELE = {"by_name": {"AUH 44120": {"mileage_km": 900.0, "idle_seconds": 5400, "driving_seconds": 36000,
                                     "engine_hours_seconds": 41400},
                       "DXB 10293": {"mileage_km": 400.0, "idle_seconds": 2700, "driving_seconds": 18000,
                                     "engine_hours_seconds": 20700}},
           "duplicate_names": [], "status": "complete", "warnings": []}


def test_ai_tools_pass_fuel_type_and_coverage_to_the_scope():
    seen = []

    def spy(filters, *args):
        seen.append((filters, args[4] if len(args) > 4 else None))
        return scope("2026-08-01", "2026-08-31")
    with patch("app.fuel.scope.build_scope", side_effect=spy):
        out = ai_tools.tool_get_fuel_summary("2026-08-01", "2026-08-31", fuel_type=" Diesel ")
    filters, coverage_fn = seen[0]
    assert filters["fuel_type"] == "Diesel" and coverage_fn.__name__ == "fetch_import_coverage", seen
    assert out["parameters"]["fuel_type"] == "Diesel"  # the effective (trimmed) filter is echoed
    print("[PASS] AI tools build the dashboard's scope: fuel-type filter and import-coverage reader passed through.")


def test_ai_idle_rate_matches_dashboard_and_is_reported():
    from app.fuel.efficiency import calculate_fleet_efficiency
    s = scope("2026-08-01", "2026-08-31")
    with patch.object(ai_tools, "_fuel_scope", return_value=s), \
         patch("app.fuel.wialon_distance.get_fleet_telematics_bundle", return_value=AI_TELE):
        out = ai_tools.tool_get_fleet_fuel_efficiency("2026-08-01", "2026-08-31", idle_rate_lph=3.0)
        bad = ai_tools.tool_get_fleet_fuel_efficiency("2026-08-01", "2026-08-31", idle_rate_lph=50)
        default = ai_tools.tool_get_fleet_fuel_efficiency("2026-08-01", "2026-08-31")
    kpi = calculate_fleet_efficiency(s.matched, AI_TELE["by_name"], s.units, 3.0)
    assert out["parameters"]["idle_rate_lph"] == 3.0 and default["parameters"]["idle_rate_lph"] == 2.0
    assert out["estimated_idle_fuel_litres"] == kpi.estimated_idle_fuel_litres == 6.8  # (1.5 h + 0.75 h) x 3.0 L/h
    assert out["estimated_idle_cost_aed"] == kpi.estimated_idle_cost and out["vehicles_idle_estimated"] == 2
    assert default["estimated_idle_fuel_litres"] == 4.5
    assert "between 0.1 and 20" in bad["error"]
    print("[PASS] AI efficiency: idle_rate_lph honoured and echoed (3.0 L/h -> 6.8 L, as the KPI); out-of-range rejected.")


def test_ai_counts_never_call_refunds_or_summaries_refuels():
    s = scope("2026-08-01", "2026-08-31")
    refund = {"id": 13, "import_id": 2, "transaction_date": "2026-08-21T08:00:00", "vehicle_identifier": "DXB 10293",
              "litres": -5.0, "total_cost": -15.0, "fuel_type": "Diesel", "date_basis": "transaction", "_unit_id": 2,
              "_unit_name": "DXB 10293"}
    s.partition.matched = s.partition.matched + [refund]
    with patch.object(ai_tools, "_fuel_scope", return_value=s):
        summ = ai_tools.tool_get_fuel_summary("2026-08-01", "2026-08-31")
    assert (summ["transaction_count"], summ["dated_purchase_count"], summ["refund_count"], summ["monthly_summary_rows"]) == (4, 2, 1, 1)
    s.partition.matched = [r for r in s.partition.matched if r["vehicle_identifier"] == "DXB 10293"]
    with patch.object(ai_tools, "_fuel_scope", return_value=s), \
         patch("app.fuel.wialon_distance.get_fleet_telematics_bundle", return_value=AI_TELE):
        perf = ai_tools.tool_get_vehicle_fuel_performance("DXB 10293", "2026-08-01", "2026-08-31")
    assert perf["refuel_transactions"] == 2 and perf["refund_adjustments"] == 1 and perf["transaction_count"] == 3, perf
    print("[PASS] AI counts: 4 rows = 2 refuels + 1 refund + 1 monthly summary; a refund is never a refuel.")


def test_ai_unavailable_telematics_is_explicit_not_a_crash():
    from app.wialon import WialonAPIError
    s = scope("2026-08-01", "2026-08-31")
    with patch.object(ai_tools, "_fuel_scope", return_value=s), \
         patch("app.fuel.wialon_distance.get_fleet_telematics_bundle", side_effect=WialonAPIError("down")):
        eff = ai_tools.tool_get_fleet_fuel_efficiency("2026-08-01", "2026-08-31")
        rank = ai_tools.tool_get_vehicle_fuel_ranking(date_from="2026-08-01", date_to="2026-08-31")
        s.partition.matched = [r for r in s.matched if r["vehicle_identifier"] == "DXB 10293"]
        perf = ai_tools.tool_get_vehicle_fuel_performance("DXB 10293", "2026-08-01", "2026-08-31")
    assert eff["telematics_status"] == "unavailable" and eff["total_distance_km"] is None
    assert eff["estimated_idle_fuel_litres"] is None and eff["total_idle_hours"] is None and eff["total_litres"] == 600.0
    assert any("unknown, not zero" in w for w in eff["data_warnings"])
    assert rank["telematics_status"] == "unavailable" and all(v["distance_km"] is None for v in rank["vehicles"])
    assert "error" not in perf and perf["distance_km"] is None and perf["telemetry_status"] == "unavailable", perf
    with patch.object(ai_tools, "_fuel_scope", return_value=s), patch.object(ai_tools, "_telematics_for", return_value=(None, None)):
        undated = ai_tools.tool_get_vehicle_fuel_performance("DXB 10293")
    assert "cannot be determined" in undated["error"]
    print("[PASS] AI tools without Wialon: fuel kept, distance/idle unknown (null) with a note; no TypeError on a missing bundle or period.")


def test_ai_telematics_wait_is_bounded():
    import time
    s = scope("2026-08-01", "2026-08-31")
    started = time.perf_counter()
    with patch.dict(os.environ, {"FUEL_TELEMATICS_WAIT_S": "1"}), \
         patch("app.fuel.wialon_distance.get_fleet_telematics_bundle", side_effect=lambda *a: time.sleep(3) or AI_TELE):
        notes = []
        bundle, dates = ai_tools._telematics_for(s, "2026-08-01", "2026-08-31", notes)
    assert bundle is None and dates == ("2026-08-01", "2026-08-31") and notes == [ai_tools.TELEMATICS_UNAVAILABLE_NOTE]
    assert time.perf_counter() - started < 2.5
    print("[PASS] AI telematics wait is bounded by FUEL_TELEMATICS_WAIT_S (answered after ~1 s, not 3 s).")


TESTS += [
    test_ai_tools_pass_fuel_type_and_coverage_to_the_scope,
    test_ai_idle_rate_matches_dashboard_and_is_reported,
    test_ai_counts_never_call_refunds_or_summaries_refuels,
    test_ai_unavailable_telematics_is_explicit_not_a_crash,
    test_ai_telematics_wait_is_bounded,
]


def main():
    print("=" * 60)
    print("FUEL ANALYTICS - MONTHLY SUMMARY ACCURACY (OFFLINE)")
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
    return failures


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
