"""
Offline regression tests for Fuel Phase 4: purchase-based efficiency formulas and honest labels.

  - distance set (km > 0) alone feeds L/100 km; telemetry set (km or engine hours) feeds hours/idle
  - minimum distance, non-positive net litres and mixed fuel types withhold ratios (with a reason)
  - estimation_status is never "measured"; value_classification contract; envelope stability

Run from Backend/:  python scripts/test_fuel_efficiency_accuracy.py
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from test_fuel_scope import Api, QM, run, summary  # noqa: E402

from app.fuel import classification  # noqa: E402
from app.fuel.efficiency import calculate_fleet_efficiency, calculate_vehicle_efficiency_records  # noqa: E402
from app.fuel.schemas import (  # noqa: E402
    EfficiencyTrendItem, FleetEfficiencyResponse, FuelSummaryResponse, FuelWasteResponse, VehicleEfficiencyItem,
)

UNITS = [{"id": 1, "nm": "A"}, {"id": 2, "nm": "B"}]


def t(vid, litres, cost=None, fuel="DIESEL", i=0, date="2026-08-03T10:00:00"):
    return {"id": i, "import_id": 1, "vehicle_identifier": vid, "litres": litres,
            "total_cost": cost if cost is not None else litres * 3.0, "fuel_type": fuel, "transaction_date": date}


def test_engine_only_vehicle_not_in_distance_ratio():
    tele = {"A": {"mileage_km": 1000.0, "engine_hours_seconds": 36000, "idle_seconds": 3600, "driving_seconds": 30000},
            "B": {"mileage_km": 0.0, "engine_hours_seconds": 36000, "idle_seconds": 7200, "driving_seconds": 0}}
    s = calculate_fleet_efficiency([t("A", 100.0), t("B", 50.0)], tele, UNITS)
    assert s.fuel_efficiency_l_per_100km == 10.0, s.fuel_efficiency_l_per_100km  # was 15.0 (B's 50 L over A's km)
    assert s.tracked_litres == 100.0 and s.telemetry_litres == 150.0
    assert s.vehicles_engine_hours_only == 1 and s.engine_only_litres == 50.0
    assert s.total_engine_hours == 20.0 and s.total_idle_hours == 3.0  # hours still include B
    assert s.estimated_idle_fuel_litres == 6.0 and s.estimated_idle_percentage == 4.0  # 6 L of 150 L
    print("[PASS] Engine-hours-only vehicles feed hours/idle, never the L/100 km numerator (10.0, not 15.0).")


def test_minimum_distance_suppresses():
    tele = {"A": {"mileage_km": 12.0}}
    items = calculate_vehicle_efficiency_records([t("A", 20.0)], tele, UNITS[:1])
    assert items[0].fuel_efficiency_l_per_100km is None and items[0].efficiency_suppressed_reason == "insufficient_distance"
    assert items[0].distance_km == 12.0
    fleet = calculate_fleet_efficiency([t("A", 20.0)], tele, UNITS[:1])
    assert fleet.fuel_efficiency_l_per_100km is None and fleet.efficiency_suppressed_reason == "insufficient_distance"
    assert fleet.min_efficiency_distance_km == 25.0
    with patch.dict(os.environ, {"FUEL_MIN_EFFICIENCY_KM": "10"}):
        assert calculate_vehicle_efficiency_records([t("A", 20.0)], tele, UNITS[:1])[0].fuel_efficiency_l_per_100km == 166.67
    print("[PASS] Below FUEL_MIN_EFFICIENCY_KM (default 25 km) L/100 km is withheld with insufficient_distance.")


def test_non_positive_net_fuel():
    tele = {"A": {"mileage_km": 500.0}}
    items = calculate_vehicle_efficiency_records([t("A", 10.0), t("A", -15.0, -45.0)], tele, UNITS[:1])
    assert items[0].litres == -5.0 and items[0].fuel_efficiency_l_per_100km is None
    assert items[0].efficiency_suppressed_reason == "non_positive_net_fuel"
    fleet = calculate_fleet_efficiency([t("A", 10.0), t("A", -15.0, -45.0)], tele, UNITS[:1])
    assert fleet.fuel_efficiency_l_per_100km is None and fleet.fuel_cost_per_km is None
    print("[PASS] Net litres <= 0 after refunds: efficiency and cost/km withheld (non_positive_net_fuel).")


def test_negative_net_unit_excluded_from_fleet_ratio():
    tele = {"A": {"mileage_km": 1000.0}, "B": {"mileage_km": 500.0}}
    s = calculate_fleet_efficiency([t("A", 100.0), t("B", 10.0), t("B", -30.0, -90.0)], tele, UNITS)
    assert s.total_distance_km == 1000.0 and s.tracked_litres == 100.0 and s.fuel_efficiency_l_per_100km == 10.0
    assert any("negative net litres" in w for w in s.data_warnings)
    print("[PASS] A unit whose refunds exceed purchases is excluded from the fleet ratio, as from its own row.")


def test_mixed_fuel_types_under_filter():
    # August monthly summaries per product: unit 1 bought DIESEL and SUPER in the month
    rows = [
        summary(1, 1, "7-AUH-55943", 60.0, 180.0, fuel="DIESEL"),
        summary(2, 1, "7-AUH-55943", 40.0, 120.0, fuel="SUPER"),
        summary(3, 1, "17-AUH-55943", 50.0, 150.0, fuel="DIESEL"),
    ]
    with Api(rows=rows) as api:
        eff = api.get(f"/api/fuel/analytics/efficiency?{QM}&fuel_type=DIESEL")
        veh = api.get(f"/api/fuel/analytics/efficiency/vehicles?{QM}&fuel_type=DIESEL")
        unfiltered = api.get(f"/api/fuel/analytics/efficiency?{QM}")
    assert eff["total_litres"] == 110.0  # recorded DIESEL purchases still counted
    assert eff["vehicles_fuel_type_mixed"] == 1 and eff["fuel_type_mixed_excluded_litres"] == 60.0
    assert eff["tracked_litres"] == 50.0 and eff["total_distance_km"] == 200.0 and eff["fuel_efficiency_l_per_100km"] == 25.0
    mixed = next(v for v in veh["data"] if v["wialon_unit_id"] == 1)
    assert mixed["fuel_efficiency_l_per_100km"] is None and mixed["efficiency_suppressed_reason"] == "mixed_fuel_types"
    assert unfiltered["vehicles_fuel_type_mixed"] == 0 and unfiltered["total_litres"] == 150.0
    print("[PASS] Fuel-type filter: mixed-fuel units excluded from L/100 km numerator and denominator, with a reason.")


def test_never_measured():
    tele = {"A": {"mileage_km": 500.0, "idle_seconds": 0, "driving_seconds": 3600, "engine_hours_seconds": 3600}}
    items = calculate_vehicle_efficiency_records([t("A", 50.0)], tele, UNITS[:1])
    assert items[0].estimation_status == "estimated", items[0].estimation_status
    import inspect
    import app.fuel.efficiency as eff_mod
    assert '"measured"' not in inspect.getsource(eff_mod)
    print("[PASS] estimation_status is never 'measured' (purchased fuel split is always an estimate).")


def test_classification_contract():
    checks = [
        (classification.FLEET_EFFICIENCY, FleetEfficiencyResponse),
        (classification.VEHICLE_EFFICIENCY, VehicleEfficiencyItem),
        (classification.EFFICIENCY_TREND, EfficiencyTrendItem),
        (classification.FUEL_SUMMARY, FuelSummaryResponse),
        (classification.WASTE, FuelWasteResponse),
    ]
    for mapping, model in checks:
        missing = set(mapping) - set(model.model_fields)
        assert not missing, (model.__name__, missing)
        assert set(mapping.values()) <= {"recorded", "derived", "estimated"}
    f = classification.FLEET_EFFICIENCY
    assert f["total_distance_km"] == "recorded" and f["fuel_efficiency_l_per_100km"] == "derived"
    assert f["estimated_idle_fuel_litres"] == "estimated" and f["total_litres"] == "derived"
    print("[PASS] value_classification: every key is a real field; distance recorded, L/100 km derived, idle estimated.")


def test_envelope_stability():
    legacy = {"total_litres", "total_cost", "transaction_count", "vehicle_count", "matched_vehicle_count",
              "unmatched_vehicle_count", "vehicles_with_distance", "vehicles_without_distance", "tracked_litres",
              "tracked_cost", "total_distance_km", "total_driving_hours", "total_idle_hours", "total_engine_hours",
              "idle_rate_lph", "estimated_idle_fuel_litres", "estimated_idle_cost", "estimated_driving_fuel_litres",
              "estimated_idle_percentage", "estimated_driving_percentage", "fuel_efficiency_l_per_100km",
              "fuel_cost_per_km", "unmatched_transactions_count", "unmatched_litres", "unmatched_cost"}
    assert legacy <= set(FleetEfficiencyResponse.model_fields)
    legacy_item = {"period", "litres", "cost", "transaction_count", "distance_km", "fuel_efficiency_l_per_100km", "fuel_cost_per_km"}
    assert legacy_item <= set(EfficiencyTrendItem.model_fields)
    print("[PASS] Response envelopes: every pre-existing key is still present (additive changes only).")


def test_driving_idle_hour_coverage_is_explicit():
    units = UNITS + [{"id": 3, "nm": "C"}]
    tele = {"A": {"mileage_km": 500.0, "driving_seconds": 36000, "idle_seconds": 3600, "engine_hours_seconds": 39600},
            "B": {"mileage_km": 300.0},                                                   # hours not reported
            "C": {"mileage_km": 200.0, "driving_seconds": 7200, "engine_hours_seconds": 9000}}  # idle missing
    s = calculate_fleet_efficiency([t("A", 50.0), t("B", 30.0), t("C", 20.0)], tele, units)
    assert (s.vehicles_hours_known, s.vehicles_hours_unknown) == (1, 2), (s.vehicles_hours_known, s.vehicles_hours_unknown)
    assert s.total_idle_hours == 1.0 and s.total_driving_hours == 12.0  # unknown hours are left out, never 0
    assert s.telematics_status == "partial" and any("hours unavailable for 2 vehicle(s)" in w for w in s.data_warnings)
    empty = calculate_fleet_efficiency([t("A", 50.0)], {}, UNITS[:1])
    assert (empty.vehicles_hours_known, empty.vehicles_hours_unknown) == (0, 1) and empty.total_idle_hours is None
    print("[PASS] Hour coverage: 1 of 3 vehicles with all hours known, 2 unknown (left out, not 0 h); reported explicitly.")


TESTS = [
    test_driving_idle_hour_coverage_is_explicit,
    test_engine_only_vehicle_not_in_distance_ratio,
    test_minimum_distance_suppresses,
    test_non_positive_net_fuel,
    test_negative_net_unit_excluded_from_fleet_ratio,
    test_mixed_fuel_types_under_filter,
    test_never_measured,
    test_classification_contract,
    test_envelope_stability,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PHASE 4 - EFFICIENCY FORMULAS AND LABELS (OFFLINE)") else 0)
