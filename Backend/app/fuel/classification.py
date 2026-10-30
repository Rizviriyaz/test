"""
Value classification attached to fuel analytics responses (value_classification: field -> class).

  recorded  : taken directly from fuel statements or Wialon reports (litres, invoice cost, trip distance, hours)
  derived   : computed from recorded values (L/100 km, cost/km, average price, net litres after refunds)
  estimated : depends on an assumed rate (idle fuel = idle hours x L/h, driving fuel = purchased - idle)

Purchased fuel is never measured consumption; L/100 km here is purchase-based and therefore derived.
The frontend renders badges only from this mapping.
"""
from typing import Dict

RECORDED = "recorded"
DERIVED = "derived"
ESTIMATED = "estimated"

FLEET_EFFICIENCY = {
    "total_litres": DERIVED,  # net of refund adjustments
    "total_cost": DERIVED,
    "transaction_count": RECORDED,
    "dated_purchase_count": RECORDED,
    "refund_count": RECORDED,
    "monthly_summary_rows": RECORDED,
    "idle_basis_litres": DERIVED,
    "tracked_litres": DERIVED,
    "tracked_cost": DERIVED,
    "total_distance_km": RECORDED,
    "total_driving_hours": RECORDED,
    "total_idle_hours": RECORDED,
    "total_engine_hours": RECORDED,
    "fuel_efficiency_l_per_100km": DERIVED,
    "fuel_cost_per_km": DERIVED,
    "estimated_idle_fuel_litres": ESTIMATED,
    "estimated_idle_cost": ESTIMATED,
    "estimated_driving_fuel_litres": ESTIMATED,
    "estimated_idle_percentage": ESTIMATED,
    "estimated_driving_percentage": ESTIMATED,
}

VEHICLE_EFFICIENCY = {
    "litres": DERIVED,
    "total_cost": DERIVED,
    "transaction_count": RECORDED,
    "distance_km": RECORDED,
    "driving_duration_hours": RECORDED,
    "idle_duration_hours": RECORDED,
    "engine_hours": RECORDED,
    "fuel_efficiency_l_per_100km": DERIVED,
    "fuel_cost_per_km": DERIVED,
    "estimated_idle_fuel_litres": ESTIMATED,
    "estimated_idle_cost": ESTIMATED,
    "estimated_driving_fuel_litres": ESTIMATED,
    "estimated_idle_percentage": ESTIMATED,
    "estimated_driving_percentage": ESTIMATED,
}

EFFICIENCY_TREND = {
    "litres": DERIVED,
    "cost": DERIVED,
    "tracked_litres": DERIVED,
    "transaction_count": RECORDED,
    "distance_km": RECORDED,
    "fuel_efficiency_l_per_100km": DERIVED,
    "fuel_cost_per_km": DERIVED,
    "cumulative_l_per_100km": DERIVED,
}

FUEL_SUMMARY = {
    "total_litres": DERIVED,
    "total_cost": DERIVED,
    "gross_purchased_litres": RECORDED,
    "gross_purchased_cost": RECORDED,
    "refund_litres": RECORDED,
    "refund_cost": RECORDED,
    "transaction_count": RECORDED,
    "dated_purchase_count": RECORDED,
    "monthly_summary_rows": RECORDED,
    "average_unit_price": DERIVED,
}

WASTE = {
    "total_idle_waste_litres": ESTIMATED,
    "total_idle_waste_cost": ESTIMATED,
    "fleet_estimated_idle_fuel_litres": ESTIMATED,
}


def classification_for(mapping: Dict[str, str]) -> Dict[str, str]:
    return dict(mapping)
