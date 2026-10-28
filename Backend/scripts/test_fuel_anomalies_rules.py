"""
Offline regression tests for Fuel Phase 6: consistent anomaly and waste rules.

  - spike baseline = median of the vehicle's OTHER purchases (leave-one-out)
  - rapid refuel needs a time of day on both rows; date-only same-day refuels -> one info item
  - missing odometers aggregated per vehicle; counts equal emitted items; distinct-transaction refuel count
  - per-day idle threshold scaled by period; unknown idle never 0; fleet idle matches the efficiency KPI

Run from Backend/:  python scripts/test_fuel_anomalies_rules.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import run  # noqa: E402

from app.fuel.anomalies import detect_fuel_waste_and_anomalies  # noqa: E402
from app.fuel.efficiency import calculate_fleet_efficiency, calculate_vehicle_efficiency_records  # noqa: E402

UNITS = [{"id": 1, "nm": "V-1"}, {"id": 2, "nm": "V-2"}]
DQ_TYPES = {"invalid_transaction_date", "duplicate_candidate", "missing_odometer", "odometer_inconsistency"}


def tx(i, litres, date, odo=None, vid="V-1"):
    return {"id": i, "import_id": 1, "vehicle_identifier": vid, "transaction_date": date, "litres": litres,
            "total_cost": litres * 3.0, "odometer_km": odo, "invoice_number": f"I{i}", "_unit_id": 1 if vid == "V-1" else 2}


def items_for(rows, tele=None):
    return calculate_vehicle_efficiency_records(rows, tele or {"V-1": {"mileage_km": 1000.0}}, UNITS)


def detect(rows, **kw):
    return detect_fuel_waste_and_anomalies(rows, items_for(rows, kw.pop("tele", None)), **kw)


def spikes(res):
    return {a.transaction_id for a in res.anomalies if a.anomaly_type == "volume_spike"}


def test_spike_leave_one_out_median():
    base = [tx(1, 50, "2026-08-01T08:00:00", 1000), tx(2, 52, "2026-08-03T08:00:00", 1500),
            tx(3, 48, "2026-08-05T08:00:00", 2000), tx(4, 51, "2026-08-07T08:00:00", 2500)]
    # median(50, 52, 48, 51) = 50.5 -> threshold 80.8
    for litres, flagged in ((80.0, False), (81.0, True), (200.0, True)):
        res = detect(base + [tx(9, litres, "2026-08-09T08:00:00", 3000)])
        assert (9 in spikes(res)) is flagged, (litres, spikes(res))
        assert spikes(res) <= {9}, "baseline purchases must not be flagged by the spike's own volume"
    few = detect(base[:2] + [tx(9, 200.0, "2026-08-09T08:00:00", 3000)])
    assert not spikes(few), "fewer than 3 other purchases: no baseline, no spike"
    print("[PASS] Spike baseline is the median of OTHER purchases (50.5 -> 80.8): 80 not flagged, 81 and 200 flagged.")


def test_rapid_and_spike_count_once():
    rows = [tx(1, 50, "2026-08-01T08:00:00", 1000), tx(2, 52, "2026-08-03T08:00:00", 1500),
            tx(3, 48, "2026-08-05T08:00:00", 2000), tx(4, 200, "2026-08-05T14:00:00", 2100)]
    res = detect(rows)
    types = [a.anomaly_type for a in res.anomalies if a.transaction_id == 4]
    assert "rapid_refuel" in types and "volume_spike" in types
    assert res.refuel_anomalies_count == 1, res.refuel_anomalies_count
    print("[PASS] One transaction flagged as both rapid refuel and spike counts once in refuel_anomalies_count.")


def test_date_only_same_day():
    rows = [tx(1, 40, "2026-08-01", 1000), tx(2, 45, "2026-08-01", 1100), tx(3, 42, "2026-08-02", 1200)]
    res = detect(rows)
    types = [a.anomaly_type for a in res.anomalies]
    assert "rapid_refuel" not in types, "no interval can be measured without a time of day"
    assert types.count("same_day_multiple_refuels") == 1
    timed = detect([tx(1, 40, "2026-08-01T08:00:00", 1000), tx(2, 45, "2026-08-01T08:00:00", 1100)])
    assert "same_day_multiple_refuels" not in [a.anomaly_type for a in timed.anomalies], "timed rows are never 'no time of day'"
    print("[PASS] Date-only same-day refuels give one same_day_multiple_refuels info item, never a rapid refuel.")


def test_missing_odometer_aggregated_and_counts_match_items():
    rows = [tx(1, 40, "2026-08-01T08:00:00"), tx(2, 45, "2026-08-03T08:00:00"), tx(3, 42, "2026-08-05T08:00:00"),
            tx(4, 41, "2026-08-07T08:00:00", 5000), tx(5, 43, "2026-08-09T08:00:00", 4000)]
    res = detect(rows)
    missing = [a for a in res.anomalies if a.anomaly_type == "missing_odometer"]
    assert len(missing) == 1 and "3 of 5" in missing[0].title
    dq_items = [a for a in res.anomalies if a.anomaly_type in DQ_TYPES]
    assert res.data_quality_issues_count == len(dq_items) == 2  # missing (aggregated) + one regression
    print("[PASS] Missing odometers: one item per vehicle; data_quality_issues_count equals the items emitted.")


def test_per_day_idle_threshold():
    rows = [tx(1, 100, "2026-08-01T08:00:00", 1000)]
    for idle_h, flagged in ((25.0, False), (31.0, True)):
        tele = {"V-1": {"mileage_km": 1000.0, "idle_hours": idle_h, "engine_hours": 100.0, "driving_hours": 50.0}}
        res = detect(rows, tele=tele, idle_threshold_hours=3.0, period_days=10)
        assert (res.excessive_idle_vehicles_count == 1) is flagged, (idle_h, res.excessive_idle_vehicles_count)
        assert res.effective_idle_threshold_hours == 30.0 and res.idle_threshold_mode == "per_day"
    tele = {"V-1": {"mileage_km": 1000.0, "idle_hours": 25.0, "engine_hours": 100.0, "driving_hours": 50.0}}
    total = detect(rows, tele=tele, idle_threshold_hours=3.0, period_days=10, idle_threshold_mode="total")
    assert total.excessive_idle_vehicles_count == 1 and total.effective_idle_threshold_hours == 3.0
    print("[PASS] per_day: 3 h/day x 10 days = 30 h (25 h not flagged, 31 h flagged); total mode keeps 3 h.")


def test_unknown_idle_not_zero_and_fleet_idle_matches_kpi():
    rows = [tx(1, 100, "2026-08-01T08:00:00", 1000), tx(2, 80, "2026-08-02T08:00:00", 900, vid="V-2")]
    tele = {"V-1": {"mileage_km": 500.0, "idle_hours": 4.0, "engine_hours": 10.0, "driving_hours": 6.0},
            "V-2": {"mileage_km": 400.0, "idle_hours": 6.0, "engine_hours": 12.0, "driving_hours": 6.0}}
    res = detect(rows, tele=tele, idle_threshold_hours=100.0)
    fleet = calculate_fleet_efficiency(rows, tele, UNITS)
    assert res.fleet_estimated_idle_fuel_litres == fleet.estimated_idle_fuel_litres == 20.0
    assert res.total_idle_waste_litres == 0.0 and res.idle_waste_basis == "vehicles_at_or_above_threshold"
    unknown = detect(rows[:1], tele={"V-1": {"mileage_km": 500.0}}, idle_threshold_hours=0.5)
    assert unknown.fleet_estimated_idle_fuel_litres is None and unknown.excessive_idle_vehicles_count == 0
    print("[PASS] Fleet idle fuel equals the efficiency KPI basis; unknown idle hours are never treated as 0.")


def test_refunds_never_flagged():
    rows = [tx(1, 50, "2026-08-01T08:00:00", 1000), tx(2, 52, "2026-08-03T08:00:00", 1500),
            tx(3, 48, "2026-08-05T08:00:00", 2000), tx(4, -300, "2026-08-05T09:00:00", 100)]
    res = detect(rows)
    assert not [a for a in res.anomalies if a.transaction_id == 4]
    print("[PASS] Refund adjustments never trigger refuel, spike or odometer rules.")


UNITS3 = UNITS + [{"id": 3, "nm": "V-3"}]


def row(i, vid, litres, cost, date="2026-08-03T08:00:00"):
    return {"id": i, "import_id": 1, "vehicle_identifier": vid, "transaction_date": date, "litres": litres,
            "total_cost": cost, "odometer_km": None, "invoice_number": f"R{i}"}


def all_three(rows, tele, rate=2.0, threshold=0.5, mixed=None):
    """Fleet KPI, vehicle records and waste report from the same inputs, as the routes build them."""
    fleet = calculate_fleet_efficiency(rows, tele, UNITS3, rate, mixed_fuel_unit_ids=mixed)
    items = calculate_vehicle_efficiency_records(rows, tele, UNITS3, rate, mixed_fuel_unit_ids=mixed)
    waste = detect_fuel_waste_and_anomalies(rows, items, threshold, rate, None, "total", 31)
    return fleet, items, waste


def test_fractional_idle_hours_reconcile_everywhere():
    rows = [row(1, "V-1", 100.0, 300.0), row(2, "V-2", 100.0, 300.0), row(3, "V-3", 100.0, 300.0)]
    tele = {f"V-{u}": {"mileage_km": 500.0, "idle_seconds": 3755, "driving_seconds": 36000, "engine_hours_seconds": 39755}
            for u in (1, 2, 3)}
    fleet, items, waste = all_three(rows, tele)
    per_vehicle = [v.estimated_idle_fuel_litres for v in items]
    assert per_vehicle == [2.09, 2.09, 2.09], per_vehicle  # 3755 s / 3600 x 2.0 L/h = 2.0861 L each
    assert fleet.estimated_idle_fuel_litres == waste.fleet_estimated_idle_fuel_litres == round(sum(per_vehicle), 1) == 6.3
    assert waste.total_idle_waste_litres == round(sum(per_vehicle), 2) == 6.27
    assert fleet.vehicles_idle_estimated == 3 and fleet.idle_basis_litres == 300.0
    assert fleet.estimated_idle_percentage == 2.1  # 6.27 / 300
    print("[PASS] Fractional idle hours: 3 x 2.09 L -> KPI = waste = sum of rankings = 6.3 L (old hours-first rounding gave 6.2).")


def test_unknown_distance_vehicle_keeps_its_idle_estimate():
    rows = [row(1, "V-1", 100.0, 300.0), row(2, "V-2", 60.0, 180.0)]
    tele = {"V-1": {"mileage_km": 400.0, "idle_seconds": 3600, "driving_seconds": 7200, "engine_hours_seconds": 10800},
            "V-2": {"idle_seconds": 7200, "driving_seconds": 3600, "engine_hours_seconds": 10800}}  # distance unknown
    fleet, items, waste = all_three(rows, tele)
    v2 = next(v for v in items if v.vehicle_identifier == "V-2")
    assert v2.status == "no_distance_data" and v2.distance_km is None, (v2.status, v2.distance_km)  # never 0 km
    assert v2.estimated_idle_fuel_litres == 4.0 and v2.estimated_idle_cost == 12.0 and v2.fuel_efficiency_l_per_100km is None
    assert fleet.estimated_idle_fuel_litres == waste.fleet_estimated_idle_fuel_litres == 6.0
    assert fleet.total_distance_km == 400.0 and fleet.vehicles_telemetry_unknown == 1
    flagged = {a.vehicle_identifier: a.wasted_litres for a in waste.anomalies if a.anomaly_type == "excessive_idling"}
    assert flagged == {"V-1": 2.0, "V-2": 4.0}, flagged
    print("[PASS] Known idle hours with unknown distance: 4.0 L idle estimate in rankings, KPI and waste; distance stays null.")


def test_mixed_fuel_units_excluded_from_idle_under_filter():
    rows = [row(1, "V-1", 100.0, 300.0), row(2, "V-2", 100.0, 300.0)]
    tele = {"V-1": {"mileage_km": 400.0, "idle_seconds": 18000, "driving_seconds": 7200, "engine_hours_seconds": 25200},
            "V-2": {"mileage_km": 400.0, "idle_seconds": 3600, "driving_seconds": 7200, "engine_hours_seconds": 10800}}
    fleet, items, waste = all_three(rows, tele, mixed={1})
    v1 = next(v for v in items if v.wialon_unit_id == 1)
    assert v1.estimated_idle_fuel_litres is None and v1.estimation_status == "unavailable"
    assert any("cannot be attributed to the filtered fuel type, so no idle fuel" in w for w in v1.quality_warnings)
    assert fleet.estimated_idle_fuel_litres == waste.fleet_estimated_idle_fuel_litres == 2.0  # V-2 only
    assert fleet.total_idle_hours == 1.0 and fleet.idle_basis_litres == 100.0 and fleet.vehicles_idle_estimated == 1
    assert any("mixed-fuel vehicle(s) are excluded from idle" in w for w in fleet.data_warnings)
    idle = {a.vehicle_identifier: a for a in waste.anomalies if a.anomaly_type == "excessive_idling"}
    assert idle["V-1"].wasted_litres is None and "not estimated" in idle["V-1"].description  # hours real, litres unknown
    assert waste.total_idle_waste_litres == 2.0
    print("[PASS] Mixed-fuel unit under a fuel filter: no idle estimate anywhere (2.0 L fleet, V-2 only); its hours still flagged.")


def test_idle_cost_uses_each_vehicles_price_and_reconciles():
    rows = [row(1, "V-1", 100.0, 287.35), row(2, "V-2", 50.0, 160.11),
            row(3, "V-3", 10.0, 31.0), row(4, "V-3", -30.0, -93.0)]  # V-3: net -20 L, no price
    tele = {f"V-{u}": {"mileage_km": 300.0, "idle_seconds": s, "driving_seconds": 7200, "engine_hours_seconds": 7200 + s}
            for u, s in ((1, 5400), (2, 2700), (3, 3600))}
    fleet, items, waste = all_three(rows, tele, rate=2.5)
    by = {v.vehicle_identifier: v for v in items}
    assert by["V-1"].estimated_idle_cost == 10.78  # 3.75 L x 2.8735 AED/L
    assert by["V-2"].estimated_idle_cost == 6.0    # 1.875 L x 3.2022 AED/L
    assert by["V-3"].estimated_idle_fuel_litres == 2.5 and by["V-3"].estimated_idle_cost is None  # price unknown, not 0
    costs = [v.estimated_idle_cost for v in items if v.estimated_idle_cost is not None]
    assert fleet.estimated_idle_cost == waste.total_idle_waste_cost == round(sum(costs), 2) == 16.78
    assert fleet.estimated_idle_fuel_litres == waste.fleet_estimated_idle_fuel_litres == 8.1  # 3.75 + 1.88 + 2.5
    assert any("idle cost excludes 1 vehicle" in w for w in fleet.data_warnings)
    assert fleet.idle_rate_lph == 2.5 and all(v.idle_rate_lph == 2.5 for v in items)
    print("[PASS] Idle cost = each vehicle's idle litres x its own price; fleet = waste = sum of vehicles (16.78 AED); no price -> null.")


TESTS = [
    test_spike_leave_one_out_median,
    test_rapid_and_spike_count_once,
    test_date_only_same_day,
    test_missing_odometer_aggregated_and_counts_match_items,
    test_per_day_idle_threshold,
    test_unknown_idle_not_zero_and_fleet_idle_matches_kpi,
    test_refunds_never_flagged,
    test_fractional_idle_hours_reconcile_everywhere,
    test_unknown_distance_vehicle_keeps_its_idle_estimate,
    test_mixed_fuel_units_excluded_from_idle_under_filter,
    test_idle_cost_uses_each_vehicles_price_and_reconciles,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PHASE 6 - ANOMALY AND WASTE RULES (OFFLINE)") else 0)
