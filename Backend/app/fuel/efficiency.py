"""
Fuel Efficiency and Telematics Intelligence Calculation Engine for ATS Fleet Management.
Authoritatively computes vehicle and fleet efficiency (L/100 km, cost/km, driving vs idling breakdown)
by uniting Supabase fuel transactions with Wialon native telematics and engine activity.
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import logging
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
    "idle_hours",
    "driving_hours",
    "estimated_idle_fuel",
    "estimated_idle_cost",
    "estimated_driving_fuel",
    "idle_percentage",
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


def _normalize_telematics_entry(t_data: Any) -> Dict[str, Any]:
    """
    Safely normalizes telematics entry for a vehicle, supporting both
    dictionary data and legacy/unit-test float/int mileage values.
    """
    if t_data is None:
        return {
            "mileage_km": 0.0,
            "driving_seconds": 0,
            "idle_seconds": 0,
            "engine_hours_seconds": 0,
            "driving_hours": 0.0,
            "idle_hours": 0.0,
            "engine_hours": 0.0,
            "has_telemetry": False,
        }
    if isinstance(t_data, (int, float, Decimal)):
        dist = float(t_data)
        return {
            "mileage_km": dist,
            "driving_seconds": 0,
            "idle_seconds": 0,
            "engine_hours_seconds": 0,
            "driving_hours": 0.0,
            "idle_hours": 0.0,
            "engine_hours": 0.0,
            "has_telemetry": dist > 0,
        }
    if isinstance(t_data, dict):
        dist = float(t_data.get("mileage_km", 0.0) or 0.0)
        drv_sec = int(t_data.get("driving_seconds", 0) or 0)
        idle_sec = int(t_data.get("idle_seconds", 0) or 0)
        eng_sec = int(t_data.get("engine_hours_seconds", 0) or 0)
        drv_hrs = float(t_data.get("driving_hours", 0.0) or (drv_sec / 3600.0 if drv_sec else 0.0))
        idle_hrs = float(t_data.get("idle_hours", 0.0) or (idle_sec / 3600.0 if idle_sec else 0.0))
        eng_hrs = float(t_data.get("engine_hours", 0.0) or (eng_sec / 3600.0 if eng_sec else 0.0))
        has_tel = bool(t_data.get("has_telemetry", False)) or dist > 0 or eng_sec > 0 or eng_hrs > 0
        return {
            "mileage_km": dist,
            "driving_seconds": drv_sec,
            "idle_seconds": idle_sec,
            "engine_hours_seconds": eng_sec,
            "driving_hours": drv_hrs,
            "idle_hours": idle_hrs,
            "engine_hours": eng_hrs,
            "has_telemetry": has_tel,
        }
    return {
        "mileage_km": 0.0,
        "driving_seconds": 0,
        "idle_seconds": 0,
        "engine_hours_seconds": 0,
        "driving_hours": 0.0,
        "idle_hours": 0.0,
        "engine_hours": 0.0,
        "has_telemetry": False,
    }


def calculate_fleet_efficiency(
    transactions: List[Dict[str, Any]],
    telematics_map: Dict[str, Any],
    wialon_units: List[Dict[str, Any]],
    idle_rate_lph: float = 2.0,
) -> FleetEfficiencyResponse:
    """
    Computes overall fleet fuel efficiency, driving vs idling estimates, and data coverage metrics.
    
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

    total_driving_sec = 0
    total_idle_sec = 0
    total_engine_sec = 0

    unmatched_tx_count = 0
    unmatched_litres_dec = Decimal(0)
    unmatched_cost_dec = Decimal(0)

    for vid in unique_vehicles:
        matched = match_fuel_vehicle_to_wialon(vid, wialon_units)
        if matched and not matched.get("ambiguous"):
            matched_vehicle_count += 1
            w_name = str(matched.get("nm", "")).strip()
            raw_t = telematics_map.get(w_name) if isinstance(telematics_map, dict) else None
            t_data = _normalize_telematics_entry(raw_t)
            dist_km = t_data["mileage_km"]
            
            driving_sec = t_data["driving_seconds"]
            idle_sec = t_data["idle_seconds"]
            engine_sec = t_data["engine_hours_seconds"]

            if dist_km > 0 or engine_sec > 0:
                vehicles_with_distance += 1
                dist_dec = Decimal(str(dist_km))
                total_distance_km += dist_dec
                tracked_litres += fuel_by_vehicle[vid]["litres"]
                tracked_cost += fuel_by_vehicle[vid]["cost"]
                total_driving_sec += driving_sec
                total_idle_sec += idle_sec
                total_engine_sec += engine_sec
            else:
                vehicles_without_distance += 1
        else:
            unmatched_vehicle_count += 1
            unmatched_tx_count += int(fuel_by_vehicle[vid]["count"])
            unmatched_litres_dec += fuel_by_vehicle[vid]["litres"]
            unmatched_cost_dec += fuel_by_vehicle[vid]["cost"]

    # Weighted efficiency calculations
    fuel_eff_l_100km = None
    fuel_cost_km = None
    if total_distance_km > 0:
        eff_dec = (tracked_litres / total_distance_km) * Decimal(100)
        fuel_eff_l_100km = _round2(eff_dec)
        cost_km_dec = tracked_cost / total_distance_km
        fuel_cost_km = _round2(cost_km_dec)

    total_driving_hours = round(total_driving_sec / 3600.0, 1) if total_driving_sec > 0 else 0.0
    total_idle_hours = round(total_idle_sec / 3600.0, 1) if total_idle_sec > 0 else 0.0
    total_engine_hours = round(total_engine_sec / 3600.0, 1) if total_engine_sec > 0 else 0.0

    # Fleet Idle Fuel & Cost Estimates
    est_idle_fuel = None
    est_idle_cost = None
    est_driving_fuel = None
    est_idle_pct = None
    est_driving_pct = None

    if total_idle_hours > 0:
        idle_l = total_idle_hours * idle_rate_lph
        est_idle_fuel = round(idle_l, 1)
        
        tracked_l_float = float(tracked_litres)
        tracked_c_float = float(tracked_cost)
        avg_price = (tracked_c_float / tracked_l_float) if tracked_l_float > 0 else 0.0
        est_idle_cost = round(idle_l * avg_price, 2)

        if tracked_l_float >= idle_l and tracked_l_float > 0:
            est_driving_fuel = round(tracked_l_float - idle_l, 1)
            est_idle_pct = round((idle_l / tracked_l_float) * 100.0, 1)
            est_driving_pct = round((est_driving_fuel / tracked_l_float) * 100.0, 1)

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
        total_driving_hours=total_driving_hours if total_driving_hours > 0 else None,
        total_idle_hours=total_idle_hours if total_idle_hours > 0 else None,
        total_engine_hours=total_engine_hours if total_engine_hours > 0 else None,
        idle_rate_lph=idle_rate_lph,
        estimated_idle_fuel_litres=est_idle_fuel,
        estimated_idle_cost=est_idle_cost,
        estimated_driving_fuel_litres=est_driving_fuel,
        estimated_idle_percentage=est_idle_pct,
        estimated_driving_percentage=est_driving_pct,
        fuel_efficiency_l_per_100km=fuel_eff_l_100km,
        fuel_cost_per_km=fuel_cost_km,
        unmatched_transactions_count=unmatched_tx_count,
        unmatched_litres=float(_round2(unmatched_litres_dec) or 0.0),
        unmatched_cost=float(_round2(unmatched_cost_dec) or 0.0),
    )


def calculate_vehicle_efficiency_records(
    transactions: List[Dict[str, Any]],
    telematics_map: Dict[str, Any],
    wialon_units: List[Dict[str, Any]],
    idle_rate_lph: float = 2.0,
) -> List[VehicleEfficiencyItem]:
    """
    Computes per-vehicle fuel efficiency and driving vs idling estimates.
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
        avg_price = (v_cost / v_litres) if v_litres > 0 else 0.0

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
                    driving_duration_hours=None,
                    idle_duration_hours=None,
                    engine_hours=None,
                    idle_rate_lph=idle_rate_lph,
                    estimated_idle_fuel_litres=None,
                    estimated_idle_cost=None,
                    estimated_driving_fuel_litres=None,
                    estimated_idle_percentage=None,
                    estimated_driving_percentage=None,
                    fuel_efficiency_l_per_100km=None,
                    fuel_cost_per_km=None,
                    status="not_tracked",
                    estimation_status="unavailable",
                    quality_warnings=["Vehicle identifier not found in Wialon unit registry."],
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
                    driving_duration_hours=None,
                    idle_duration_hours=None,
                    engine_hours=None,
                    idle_rate_lph=idle_rate_lph,
                    estimated_idle_fuel_litres=None,
                    estimated_idle_cost=None,
                    estimated_driving_fuel_litres=None,
                    estimated_idle_percentage=None,
                    estimated_driving_percentage=None,
                    fuel_efficiency_l_per_100km=None,
                    fuel_cost_per_km=None,
                    status="ambiguous",
                    estimation_status="unavailable",
                    quality_warnings=["Multiple Wialon units matched identifier. Cannot resolve distance."],
                )
            )
            continue

        w_id = int(matched["id"])
        w_name = str(matched.get("nm", "")).strip()
        raw_t = telematics_map.get(w_name) if isinstance(telematics_map, dict) else None
        t_data = _normalize_telematics_entry(raw_t)
        
        dist_km = t_data["mileage_km"]
        driving_hrs = t_data["driving_hours"]
        idle_hrs = t_data["idle_hours"]
        eng_hrs = t_data["engine_hours"]
        has_tel = t_data["has_telemetry"]

        if not has_tel and dist_km <= 0 and eng_hrs <= 0:
            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=0.0,
                    driving_duration_hours=0.0,
                    idle_duration_hours=0.0,
                    engine_hours=0.0,
                    idle_rate_lph=idle_rate_lph,
                    estimated_idle_fuel_litres=None,
                    estimated_idle_cost=None,
                    estimated_driving_fuel_litres=None,
                    estimated_idle_percentage=None,
                    estimated_driving_percentage=None,
                    fuel_efficiency_l_per_100km=None,
                    fuel_cost_per_km=None,
                    status="no_distance_data",
                    estimation_status="unavailable",
                    quality_warnings=["GPS unit online but 0 km / 0 engine hours recorded in selected period."],
                )
            )
        else:
            dist_dec = Decimal(str(dist_km))
            fuel_eff = _round2((f_data["litres"] / dist_dec) * Decimal(100)) if dist_km > 0 else None
            fuel_c_km = _round2(f_data["cost"] / dist_dec) if dist_km > 0 else None

            # Driving vs Idling estimation
            est_idle_l = round(idle_hrs * idle_rate_lph, 2) if idle_hrs > 0 else 0.0
            est_idle_cost = round(est_idle_l * avg_price, 2) if est_idle_l > 0 else 0.0

            warnings = []
            est_driving_l = None
            est_idle_pct = None
            est_driving_pct = None
            est_status = "measured" if idle_hrs == 0 else "estimated"

            if v_litres >= est_idle_l and v_litres > 0:
                est_driving_l = round(v_litres - est_idle_l, 2)
                est_idle_pct = round((est_idle_l / v_litres) * 100.0, 1)
                est_driving_pct = round((est_driving_l / v_litres) * 100.0, 1)
            elif est_idle_l > v_litres:
                est_status = "provisional"
                warnings.append(
                    f"Estimated idle fuel ({est_idle_l:.1f} L) exceeds recorded purchases ({v_litres:.1f} L) for period. Driving split withheld."
                )

            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=round(dist_km, 2) if dist_km > 0 else 0.0,
                    driving_duration_hours=driving_hrs,
                    idle_duration_hours=idle_hrs,
                    engine_hours=eng_hrs,
                    idle_rate_lph=idle_rate_lph,
                    estimated_idle_fuel_litres=est_idle_l if idle_hrs > 0 else None,
                    estimated_idle_cost=est_idle_cost if idle_hrs > 0 else None,
                    estimated_driving_fuel_litres=est_driving_l,
                    estimated_idle_percentage=est_idle_pct,
                    estimated_driving_percentage=est_driving_pct,
                    fuel_efficiency_l_per_100km=fuel_eff,
                    fuel_cost_per_km=fuel_c_km,
                    status="matched",
                    estimation_status=est_status,
                    quality_warnings=warnings,
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
        elif field in ("idle_hours", "idle_duration_hours"):
            val = item.idle_duration_hours
        elif field in ("driving_hours", "driving_duration_hours"):
            val = item.driving_duration_hours
        elif field in ("estimated_idle_fuel", "estimated_idle_fuel_litres"):
            val = item.estimated_idle_fuel_litres
        elif field in ("estimated_idle_cost",):
            val = item.estimated_idle_cost
        elif field in ("estimated_driving_fuel", "estimated_driving_fuel_litres"):
            val = item.estimated_driving_fuel_litres
        elif field in ("idle_percentage", "estimated_idle_percentage"):
            val = item.estimated_idle_percentage
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
    telematics_map: Dict[str, Any],
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
                raw_t = telematics_map.get(w_name) if isinstance(telematics_map, dict) else None
                t_info = _normalize_telematics_entry(raw_t)
                dist = t_info["mileage_km"]
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

    for p in sorted_periods:
        p_data = periods_data[p]
        p_litres = float(_round2(p_data["litres"]) or 0.0)
        p_cost = float(_round2(p_data["cost"]) or 0.0)
        p_count = p_data["count"]
        p_tracked_litres = p_data["tracked_litres"]
        p_tracked_cost = p_data["tracked_cost"]

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
