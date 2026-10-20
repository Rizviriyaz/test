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


TESTS = [
    test_spike_leave_one_out_median,
    test_rapid_and_spike_count_once,
    test_date_only_same_day,
    test_missing_odometer_aggregated_and_counts_match_items,
    test_per_day_idle_threshold,
    test_unknown_idle_not_zero_and_fleet_idle_matches_kpi,
    test_refunds_never_flagged,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PHASE 6 - ANOMALY AND WASTE RULES (OFFLINE)") else 0)
