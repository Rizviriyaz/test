"""
Fuel Efficiency Calculation Engine for ATS Fleet Management.
Authoritatively computes vehicle and fleet efficiency (L/100 km, cost/km)
by uniting Supabase fuel transactions with Wialon native distance telemetry.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from app.fuel.schemas import (
    FleetEfficiencyResponse,
    VehicleEfficiencyItem,
    VehicleEfficiencyResponse,
    EfficiencyTrendItem,
    EfficiencyTrendResponse,
)
from app.fuel.wialon_distance import match_fuel_vehicle_to_wialon

logger = logging.getLogger(__name__)

VALID_EFFICIENCY_SORT_FIELDS = {
    "litres",
    "cost",
    "total_cost",
    "distance",
    "distance_km",
    "efficiency",
    "fuel_efficiency_l_per_100km",
    "cost_per_km",
    "fuel_cost_per_km",
    "transactions",
    "transaction_count",
    "vehicle_identifier",
}

VALID_EFFICIENCY_STATUSES = {
    "matched",
    "not_tracked",
    "no_distance_data",
    "ambiguous",
}


def _round2(val: Optional[Decimal]) -> Optional[float]:
    if val is None:
        return None
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def calculate_fleet_efficiency(
    transactions: List[Dict[str, Any]],
    distance_map: Dict[str, float],
    wialon_units: List[Dict[str, Any]],
) -> FleetEfficiencyResponse:
    """
    Computes overall fleet fuel efficiency and data coverage metrics.
    
    CRITICAL: Fleet efficiency is calculated via weighted aggregation:
        L/100 km = (tracked_litres / total_distance_km) * 100
        Cost/km  = tracked_cost / total_distance_km
    Never averages per-vehicle percentages.
    """
    total_litres = Decimal(0)
    total_cost = Decimal(0)
    tx_count = len(transactions)

    # Aggregate fuel per unique vehicle identifier
    fuel_by_vehicle: Dict[str, Dict[str, Decimal]] = defaultdict(
        lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": Decimal(0)}
    )

    for tx in transactions:
        vid = str(tx.get("vehicle_identifier", "")).strip()
        if not vid:
            continue
        l = Decimal(str(tx.get("litres") or 0))
        c = Decimal(str(tx.get("total_cost") or 0))
        total_litres += l
        total_cost += c
        fuel_by_vehicle[vid]["litres"] += l
        fuel_by_vehicle[vid]["cost"] += c
        fuel_by_vehicle[vid]["count"] += 1

    unique_vehicles = list(fuel_by_vehicle.keys())
    vehicle_count = len(unique_vehicles)

    matched_vehicle_count = 0
    unmatched_vehicle_count = 0
    vehicles_with_distance = 0
    vehicles_without_distance = 0

    tracked_litres = Decimal(0)
    tracked_cost = Decimal(0)
    total_distance_km = Decimal(0)

    for vid in unique_vehicles:
        matched = match_fuel_vehicle_to_wialon(vid, wialon_units)
        if matched and not matched.get("ambiguous"):
            matched_vehicle_count += 1
            w_name = str(matched.get("nm", "")).strip()
            dist_km = distance_map.get(w_name, 0.0)
            if dist_km > 0:
                vehicles_with_distance += 1
                dist_dec = Decimal(str(dist_km))
                total_distance_km += dist_dec
                tracked_litres += fuel_by_vehicle[vid]["litres"]
                tracked_cost += fuel_by_vehicle[vid]["cost"]
            else:
                vehicles_without_distance += 1
        else:
            unmatched_vehicle_count += 1

    # Weighted efficiency calculations
    fuel_eff_l_100km = None
    fuel_cost_km = None
    if total_distance_km > 0:
        eff_dec = (tracked_litres / total_distance_km) * Decimal(100)
        fuel_eff_l_100km = _round2(eff_dec)
        cost_km_dec = tracked_cost / total_distance_km
        fuel_cost_km = _round2(cost_km_dec)

    return FleetEfficiencyResponse(
        total_litres=float(_round2(total_litres) or 0.0),
        total_cost=float(_round2(total_cost) or 0.0),
        transaction_count=tx_count,
        vehicle_count=vehicle_count,
        matched_vehicle_count=matched_vehicle_count,
        unmatched_vehicle_count=unmatched_vehicle_count,
        vehicles_with_distance=vehicles_with_distance,
        vehicles_without_distance=vehicles_without_distance,
        tracked_litres=float(_round2(tracked_litres) or 0.0),
        tracked_cost=float(_round2(tracked_cost) or 0.0),
        total_distance_km=_round2(total_distance_km) if total_distance_km > 0 else None,
        fuel_efficiency_l_per_100km=fuel_eff_l_100km,
        fuel_cost_per_km=fuel_cost_km,
    )


def calculate_vehicle_efficiency_records(
    transactions: List[Dict[str, Any]],
    distance_map: Dict[str, float],
    wialon_units: List[Dict[str, Any]],
) -> List[VehicleEfficiencyItem]:
    """
    Computes per-vehicle fuel efficiency records.
    Differentiates statuses: 'matched', 'not_tracked', 'no_distance_data', 'ambiguous'.
    """
    fuel_by_vehicle: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": 0}
    )

    for tx in transactions:
        vid = str(tx.get("vehicle_identifier", "")).strip()
        if not vid:
            continue
        fuel_by_vehicle[vid]["litres"] += Decimal(str(tx.get("litres") or 0))
        fuel_by_vehicle[vid]["cost"] += Decimal(str(tx.get("total_cost") or 0))
        fuel_by_vehicle[vid]["count"] += 1

    results: List[VehicleEfficiencyItem] = []

    for vid, f_data in fuel_by_vehicle.items():
        matched = match_fuel_vehicle_to_wialon(vid, wialon_units)
        v_litres = float(_round2(f_data["litres"]) or 0.0)
        v_cost = float(_round2(f_data["cost"]) or 0.0)
        v_count = f_data["count"]

        if not matched:
            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=None,
                    wialon_unit_name=None,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=None,
                    fuel_efficiency_l_per_100km=None,
                    fuel_cost_per_km=None,
                    status="not_tracked",
                )
            )
            continue

        if matched.get("ambiguous"):
            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=None,
                    wialon_unit_name=None,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=None,
                    fuel_efficiency_l_per_100km=None,
                    fuel_cost_per_km=None,
                    status="ambiguous",
                )
            )
            continue

        w_id = int(matched["id"])
        w_name = str(matched.get("nm", "")).strip()
        dist_km = distance_map.get(w_name)

        if dist_km is None or dist_km <= 0:
            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=0.0,
                    fuel_efficiency_l_per_100km=None,
                    fuel_cost_per_km=None,
                    status="no_distance_data",
                )
            )
        else:
            dist_dec = Decimal(str(dist_km))
            eff_dec = (f_data["litres"] / dist_dec) * Decimal(100)
            cost_km_dec = f_data["cost"] / dist_dec

            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=round(dist_km, 2),
                    fuel_efficiency_l_per_100km=_round2(eff_dec),
                    fuel_cost_per_km=_round2(cost_km_dec),
                    status="matched",
                )
            )

    return results


def paginate_and_sort_vehicle_efficiency(
    items: List[VehicleEfficiencyItem],
    page: int = 1,
    page_size: int = 25,
    sort_by: str = "litres",
    sort_order: str = "desc",
    status_filter: Optional[str] = None,
    vehicle_filter: Optional[str] = None,
) -> VehicleEfficiencyResponse:
    """Filters, sorts, and paginates vehicle efficiency items."""
    filtered = items

    if status_filter:
        stat_clean = status_filter.strip().lower()
        filtered = [v for v in filtered if v.status.lower() == stat_clean]

    if vehicle_filter:
        veh_clean = vehicle_filter.strip().lower()
        filtered = [v for v in filtered if veh_clean in v.vehicle_identifier.lower()]

    is_desc = sort_order.strip().lower() == "desc"
    field = sort_by.strip().lower()

    def sort_key(item: VehicleEfficiencyItem):
        if field in ("litres",):
            val = item.litres
        elif field in ("cost", "total_cost"):
            val = item.total_cost
        elif field in ("distance", "distance_km"):
            val = item.distance_km
        elif field in ("efficiency", "fuel_efficiency_l_per_100km"):
            val = item.fuel_efficiency_l_per_100km
        elif field in ("cost_per_km", "fuel_cost_per_km"):
            val = item.fuel_cost_per_km
        elif field in ("transactions", "transaction_count"):
            val = item.transaction_count
        elif field in ("vehicle_identifier",):
            return item.vehicle_identifier.lower()
        else:
            val = item.litres

        # Push None values to the back regardless of sort direction
        if val is None:
            return (1, 0.0) if is_desc else (1, 0.0)
        return (0, -val if is_desc else val)

    filtered.sort(key=sort_key)

    total_count = len(filtered)
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    paged_data = filtered[start_idx:end_idx]

    return VehicleEfficiencyResponse(
        page=page,
        page_size=page_size,
        total=total_count,
        data=paged_data,
    )


def calculate_efficiency_trend(
    transactions: List[Dict[str, Any]],
    distance_map: Dict[str, float],
    wialon_units: List[Dict[str, Any]],
    group_by: str = "day",
) -> EfficiencyTrendResponse:
    """
    Computes chronological efficiency trends grouped by 'day', 'week', or 'month'.
    Calculates weighted efficiency (L/100 km) and cost/km per period.
    """
    from datetime import datetime, timedelta

    def get_period_key(iso_str: str) -> str:
        date_part = iso_str[:10]
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        if group_by == "month":
            return dt.strftime("%Y-%m")
        elif group_by == "week":
            # Monday start of week
            monday = dt - timedelta(days=dt.weekday())
            return monday.strftime("%Y-%m-%d")
        else:
            return date_part

    periods_data = defaultdict(
        lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": 0, "tracked_litres": Decimal(0), "tracked_cost": Decimal(0)}
    )

    # First discover which vehicles are tracked with distance
    tracked_vids = set()
    total_tracked_dist = Decimal(0)
    for tx in transactions:
        vid = str(tx.get("vehicle_identifier", "")).strip()
        if vid and vid not in tracked_vids:
            m = match_fuel_vehicle_to_wialon(vid, wialon_units)
            if m and not m.get("ambiguous"):
                w_name = str(m.get("nm", "")).strip()
                dist = distance_map.get(w_name, 0.0)
                if dist > 0:
                    tracked_vids.add(vid)
                    total_tracked_dist += Decimal(str(dist))

    total_tracked_fuel = Decimal(0)
    for tx in transactions:
        d_str = tx.get("transaction_date", "")
        if not d_str:
            continue
        try:
            period_key = get_period_key(d_str)
        except Exception:
            continue

        l = Decimal(str(tx.get("litres") or 0))
        c = Decimal(str(tx.get("total_cost") or 0))
        vid = str(tx.get("vehicle_identifier", "")).strip()

        periods_data[period_key]["litres"] += l
        periods_data[period_key]["cost"] += c
        periods_data[period_key]["count"] += 1

        if vid in tracked_vids:
            periods_data[period_key]["tracked_litres"] += l
            periods_data[period_key]["tracked_cost"] += c
            total_tracked_fuel += l

    sorted_periods = sorted(periods_data.keys())
    result_items: List[EfficiencyTrendItem] = []

    # Distribute distance proportionally by fuel consumed in the period by tracked vehicles
    # (Authoritative distance weighting across periods)
    for p in sorted_periods:
        p_data = periods_data[p]
        p_litres = float(_round2(p_data["litres"]) or 0.0)
        p_cost = float(_round2(p_data["cost"]) or 0.0)
        p_count = p_data["count"]
        p_tracked_litres = p_data["tracked_litres"]
        p_tracked_cost = p_data["tracked_cost"]

        # If period tracked fuel > 0 and total tracked fuel > 0
        p_dist_km = None
        p_eff = None
        p_cost_km = None

        if total_tracked_fuel > 0 and total_tracked_dist > 0 and p_tracked_litres > 0:
            period_ratio = p_tracked_litres / total_tracked_fuel
            period_dist_dec = period_ratio * total_tracked_dist
            p_dist_km = float(_round2(period_dist_dec) or 0.0)
            if period_dist_dec > 0:
                p_eff_dec = (p_tracked_litres / period_dist_dec) * Decimal(100)
                p_eff = _round2(p_eff_dec)
                p_cost_km_dec = p_tracked_cost / period_dist_dec
                p_cost_km = _round2(p_cost_km_dec)

        result_items.append(
            EfficiencyTrendItem(
                period=p,
                litres=p_litres,
                cost=p_cost,
                transaction_count=p_count,
                distance_km=p_dist_km,
                fuel_efficiency_l_per_100km=p_eff,
                fuel_cost_per_km=p_cost_km,
            )
        )

    return EfficiencyTrendResponse(group_by=group_by, data=result_items)
