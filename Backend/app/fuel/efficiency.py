"""
Fuel Efficiency and Telematics Intelligence Calculation Engine for ATS Fleet Management.
Authoritatively computes vehicle and fleet efficiency (L/100 km, cost/km, driving vs idling breakdown)
by uniting Supabase fuel transactions with Wialon native telematics and engine activity.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.fuel.schemas import (
    FleetEfficiencyResponse,
    VehicleEfficiencyItem,
    VehicleEfficiencyResponse,
    EfficiencyTrendItem,
    EfficiencyTrendResponse,
)
from app.fuel.scope import REASON_AMBIGUOUS, UnitResolver, tx_local_date, unit_key
from app.utils.datetime_helpers import UAE_TZ
from app.fuel.wialon_distance import match_fuel_vehicle_to_wialon
from app.vehicles import extract_registration_plate

logger = logging.getLogger(__name__)

DEFAULT_MIN_EFFICIENCY_KM = 25.0

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


def _unknown_telematics() -> Dict[str, Any]:
    return {
        "mileage_km": None,
        "driving_seconds": None,
        "idle_seconds": None,
        "engine_hours_seconds": None,
        "driving_hours": None,
        "idle_hours": None,
        "engine_hours": None,
        "has_telemetry": False,
        "present": False,
    }


def _metric_pair(t_data: Dict[str, Any], seconds_key: str, hours_key: str) -> Tuple[Optional[int], Optional[float]]:
    """(seconds, hours) for one duration metric; None when the entry does not report it."""
    sec = t_data.get(seconds_key)
    hrs = t_data.get(hours_key)
    if sec is None and hrs is None:
        return None, None
    if sec is None:
        sec = int(round(float(hrs) * 3600))
    if hrs is None:
        hrs = float(sec) / 3600.0
    return int(sec), float(hrs)


def _normalize_telematics_entry(t_data: Any) -> Dict[str, Any]:
    """
    Normalize one vehicle's telematics. Unknown values stay None - an absent unit or an unreadable
    Wialon cell is never turned into 0. Supports bundle dict entries and legacy float distance maps
    (distance only; hours unknown).
    """
    if t_data is None:
        return _unknown_telematics()
    if isinstance(t_data, (int, float, Decimal)):
        dist = float(t_data)
        entry = _unknown_telematics()
        entry.update({"mileage_km": dist, "has_telemetry": dist > 0, "present": True})
        return entry
    if isinstance(t_data, dict):
        dist_raw = t_data.get("mileage_km")
        dist = float(dist_raw) if dist_raw is not None else None
        drv_sec, drv_hrs = _metric_pair(t_data, "driving_seconds", "driving_hours")
        idle_sec, idle_hrs = _metric_pair(t_data, "idle_seconds", "idle_hours")
        eng_sec, eng_hrs = _metric_pair(t_data, "engine_hours_seconds", "engine_hours")
        has_tel = bool(t_data.get("has_telemetry", False)) or (dist or 0) > 0 or (eng_sec or 0) > 0
        return {
            "mileage_km": dist,
            "driving_seconds": drv_sec,
            "idle_seconds": idle_sec,
            "engine_hours_seconds": eng_sec,
            "driving_hours": drv_hrs,
            "idle_hours": idle_hrs,
            "engine_hours": eng_hrs,
            "has_telemetry": has_tel,
            "present": True,
        }
    return _unknown_telematics()


def index_telematics_by_unit_id(
    telematics_map: Dict[str, Any],
    wialon_units: List[Dict[str, Any]],
    duplicate_names: Optional[List[str]] = None,
) -> Tuple[Dict[int, Any], set]:
    """
    Key telematics (reported by unit NAME) by Wialon unit id. A name shared by several units in the
    unit list, or reported twice by Wialon (duplicate_names), cannot be attributed: those unit ids get
    no entry and their names are returned as ambiguous.
    """
    ids_by_name: Dict[str, List[int]] = defaultdict(list)
    for u in wialon_units or []:
        name = str(u.get("nm", "")).strip()
        if name and u.get("id") is not None:
            ids_by_name[name].append(int(u["id"]))
    ambiguous = {n for n, ids in ids_by_name.items() if len(set(ids)) > 1}
    ambiguous.update(duplicate_names or [])
    by_id: Dict[int, Any] = {}
    if isinstance(telematics_map, dict):
        for name, entry in telematics_map.items():
            name = str(name).strip()
            if name in ambiguous:
                continue
            for uid in ids_by_name.get(name, []):
                by_id[uid] = entry
    return by_id, ambiguous


def _telematics_for_unit(matched: Dict[str, Any], by_id: Dict[int, Any], ambiguous: set) -> Tuple[Dict[str, Any], Optional[str]]:
    """Normalized telematics for a matched unit, plus a warning when it was withheld or is missing."""
    w_name = str(matched.get("nm", "")).strip()
    if w_name in ambiguous:
        return _unknown_telematics(), "Wialon unit name shared by multiple units; telemetry withheld."
    raw = by_id.get(int(matched["id"])) if matched.get("id") is not None else None
    if raw is None:
        return _unknown_telematics(), "Unit not present in Wialon reports for this period; distance and hours unavailable."
    return _normalize_telematics_entry(raw), None


def _group_fuel_by_unit(
    transactions: List[Dict[str, Any]], wialon_units: List[Dict[str, Any]]
) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    Aggregate fuel per Wialon unit id (several identifiers of one unit are combined, so its distance is
    counted once) and, separately, per identifier for rows that resolve to no unit or to several units.
    Rows already tagged by scope.partition_transactions (_unit_id) are used as tagged.
    """
    resolver = UnitResolver(wialon_units, match_fn=match_fuel_vehicle_to_wialon)
    units_by_id = {int(u["id"]): u for u in wialon_units or [] if u.get("id") is not None}
    by_unit: Dict[int, Dict[str, Any]] = {}
    others: Dict[str, Dict[str, Any]] = {}
    for tx in transactions:
        vid = str(tx.get("vehicle_identifier", "") or "").strip()
        l = Decimal(str(tx.get("litres") or 0))
        c = Decimal(str(tx.get("total_cost") or 0))
        kind, key = unit_key(tx, resolver)
        if kind == "unit":
            g = by_unit.get(key)
            if g is None:
                unit = units_by_id.get(key) or {"id": key, "nm": tx.get("_unit_name") or resolver.resolve(vid).get("unit_name")}
                g = by_unit[key] = {"unit": unit, "litres": Decimal(0), "cost": Decimal(0), "count": 0, "identifiers": Counter()}
        else:
            if not vid:
                continue
            g = others.get(vid)
            if g is None:
                g = others[vid] = {"status": resolver.resolve(vid)["status"], "litres": Decimal(0), "cost": Decimal(0), "count": 0}
        g["litres"] += l
        g["cost"] += c
        g["count"] += 1
        if kind == "unit":
            g["identifiers"][vid] += 1
    return by_unit, others


def _primary_identifier(identifiers: Counter) -> str:
    """The identifier with the most transactions (ties: alphabetical) labels a merged unit record."""
    return sorted(identifiers.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _merge_warning(identifiers: Counter) -> Optional[str]:
    if len(identifiers) < 2:
        return None
    return (f"Fuel identifiers {', '.join(sorted(identifiers))} resolve to the same Wialon unit; "
            "their fuel is combined and the unit's distance counted once.")


def _telemetry_status(t_data: Dict[str, Any]) -> str:
    keys = ("mileage_km", "driving_hours", "idle_hours", "engine_hours")
    known = [t_data.get(k) is not None for k in keys]
    if all(known):
        return "complete"
    return "unavailable" if not any(known) else "partial"


def min_efficiency_distance_km() -> float:
    """Minimum Wialon distance for a purchase-based L/100 km (FUEL_MIN_EFFICIENCY_KM, default 25 km)."""
    raw = os.getenv("FUEL_MIN_EFFICIENCY_KM", "").strip()
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_MIN_EFFICIENCY_KM
    except ValueError:
        return DEFAULT_MIN_EFFICIENCY_KM


def _suppression_reason(distance_km: float, litres: Decimal, min_km: float, mixed: bool) -> Optional[str]:
    """Why a purchase-based ratio is withheld, or None when it can be computed."""
    if mixed:
        return "mixed_fuel_types"
    if distance_km <= 0:
        return "no_distance"
    if litres <= 0:
        return "non_positive_net_fuel"
    if distance_km < min_km:
        return "insufficient_distance"
    return None


def calculate_fleet_efficiency(
    transactions: List[Dict[str, Any]],
    telematics_map: Dict[str, Any],
    wialon_units: List[Dict[str, Any]],
    idle_rate_lph: float = 2.0,
    telematics_status: str = "complete",
    data_warnings: Optional[List[str]] = None,
    duplicate_names: Optional[List[str]] = None,
    mixed_fuel_unit_ids: Optional[set] = None,
    min_distance_km: Optional[float] = None,
) -> FleetEfficiencyResponse:
    """
    Fleet purchase-based efficiency (derived, not measured consumption):
        L/100 km = distance_set_litres / distance_set_km * 100      Cost/km = distance_set_cost / distance_set_km
    Never averages per-vehicle ratios.

    Two vehicle sets, per Wialon unit (identifiers of one unit combined, distance counted once):
      - distance set  : units with Wialon distance > 0 (and not mixed-fuel under a fuel-type filter). Only
                        these feed tracked_litres/cost, total_distance_km, L/100 km and cost/km.
      - telemetry set : units with distance > 0 or engine hours > 0. These feed hour totals and idle estimates.
    Unknown values (None) are excluded and reported, never counted as 0. A ratio is withheld when the fleet
    distance is below the minimum, or net litres (after refunds) are not positive.
    KPI scope: transactions resolving to exactly one Wialon unit; unmatched/ambiguous rows go to unmatched_*.
    """
    tele_by_id, ambiguous_names = index_telematics_by_unit_id(telematics_map, wialon_units, duplicate_names)
    warnings_out: List[str] = list(data_warnings or [])
    min_km = min_efficiency_distance_km() if min_distance_km is None else float(min_distance_km)
    mixed = set(mixed_fuel_unit_ids or [])

    fuel_by_unit, excluded_groups = _group_fuel_by_unit(transactions, wialon_units)
    total_litres = sum((g["litres"] for g in fuel_by_unit.values()), Decimal(0))
    total_cost = sum((g["cost"] for g in fuel_by_unit.values()), Decimal(0))
    tx_count = sum(g["count"] for g in fuel_by_unit.values())

    vehicles_telemetry_unknown = vehicles_hours_unknown = 0
    vehicles_with_distance = vehicles_without_distance = 0
    vehicles_engine_only = vehicles_mixed = vehicles_non_positive = 0
    tracked_litres = tracked_cost = total_distance_km = Decimal(0)
    telemetry_litres = telemetry_cost = engine_only_litres = mixed_litres = Decimal(0)
    total_driving_sec = total_idle_sec = total_engine_sec = 0
    # How many included units reported each duration: a recorded 0 total stays 0.0, while a total with no
    # known contributors is None (unknown).
    driving_known = idle_known = engine_known = 0

    for uid, g in fuel_by_unit.items():
        t_data, _ = _telematics_for_unit(g["unit"], tele_by_id, ambiguous_names)
        dist_km = t_data["mileage_km"]
        driving_sec = t_data["driving_seconds"]
        idle_sec = t_data["idle_seconds"]
        engine_sec = t_data["engine_hours_seconds"]

        if dist_km is None:
            vehicles_telemetry_unknown += 1
        has_distance = dist_km is not None and dist_km > 0
        if has_distance and uid in mixed:
            vehicles_mixed += 1
            mixed_litres += g["litres"]
        elif has_distance and g["litres"] <= 0:
            # Refunds exceed purchases for this unit; its own ratio is withheld, so the fleet ratio excludes it too.
            vehicles_non_positive += 1
        elif has_distance:
            vehicles_with_distance += 1
            total_distance_km += Decimal(str(dist_km))
            tracked_litres += g["litres"]
            tracked_cost += g["cost"]
        elif dist_km is not None:
            vehicles_without_distance += 1

        if has_distance or (engine_sec or 0) > 0:
            telemetry_litres += g["litres"]
            telemetry_cost += g["cost"]
            if not has_distance:
                vehicles_engine_only += 1
                engine_only_litres += g["litres"]
            if driving_sec is None or idle_sec is None or engine_sec is None:
                vehicles_hours_unknown += 1
            if driving_sec is not None:
                total_driving_sec += driving_sec
                driving_known += 1
            if idle_sec is not None:
                total_idle_sec += idle_sec
                idle_known += 1
            if engine_sec is not None:
                total_engine_sec += engine_sec
                engine_known += 1

    unmatched_tx_count = sum(g["count"] for g in excluded_groups.values())
    unmatched_litres_dec = sum((g["litres"] for g in excluded_groups.values()), Decimal(0))
    unmatched_cost_dec = sum((g["cost"] for g in excluded_groups.values()), Decimal(0))

    fuel_eff_l_100km = None
    fuel_cost_km = None
    suppressed = _suppression_reason(float(total_distance_km), tracked_litres, min_km, False)
    if suppressed is None:
        fuel_eff_l_100km = _round2((tracked_litres / total_distance_km) * Decimal(100))
        fuel_cost_km = _round2(tracked_cost / total_distance_km)
    elif suppressed == "insufficient_distance":
        warnings_out.append(f"Fleet Wialon distance ({float(total_distance_km):.1f} km) is below the {min_km:.0f} km minimum; efficiency withheld.")
    elif suppressed == "non_positive_net_fuel":
        warnings_out.append("Refund adjustments make net purchased litres non-positive for vehicles with distance; efficiency withheld.")

    total_driving_hours = round(total_driving_sec / 3600.0, 1) if driving_known else None
    total_idle_hours = round(total_idle_sec / 3600.0, 1) if idle_known else None
    total_engine_hours = round(total_engine_sec / 3600.0, 1) if engine_known else None

    est_idle_fuel = est_idle_cost = est_driving_fuel = est_idle_pct = est_driving_pct = None
    if total_idle_hours is not None and total_idle_hours > 0:
        idle_l = total_idle_hours * idle_rate_lph
        est_idle_fuel = round(idle_l, 1)
        tel_l = float(telemetry_litres)
        tel_c = float(telemetry_cost)
        avg_price = (tel_c / tel_l) if tel_l > 0 else 0.0
        est_idle_cost = round(idle_l * avg_price, 2)
        if tel_l >= idle_l and tel_l > 0:
            est_driving_fuel = round(tel_l - idle_l, 1)
            est_idle_pct = round((idle_l / tel_l) * 100.0, 1)
            est_driving_pct = round((est_driving_fuel / tel_l) * 100.0, 1)

    if vehicles_telemetry_unknown:
        warnings_out.append(
            f"{vehicles_telemetry_unknown} matched vehicle(s) have no usable Wialon distance for this period; "
            "excluded from L/100 km and cost/km."
        )
    if vehicles_hours_unknown:
        warnings_out.append(
            f"Driving/idle/engine hours unavailable for {vehicles_hours_unknown} vehicle(s); hour totals and idle "
            "estimates exclude them."
        )
    if vehicles_non_positive:
        warnings_out.append(
            f"{vehicles_non_positive} vehicle(s) have zero or negative net litres after refunds; excluded from L/100 km and cost/km."
        )
    if vehicles_mixed:
        warnings_out.append(
            f"{vehicles_mixed} vehicle(s) bought more than one fuel type in this period; under a fuel-type filter "
            "their distance cannot be attributed, so they are excluded from L/100 km and cost/km."
        )
    final_status = telematics_status
    if final_status == "complete" and (vehicles_telemetry_unknown or vehicles_hours_unknown):
        final_status = "partial"

    return FleetEfficiencyResponse(
        total_litres=float(_round2(total_litres) or 0.0),
        total_cost=float(_round2(total_cost) or 0.0),
        transaction_count=tx_count,
        vehicle_count=len(fuel_by_unit),
        matched_vehicle_count=len(fuel_by_unit),
        unmatched_vehicle_count=len(excluded_groups),
        vehicles_with_distance=vehicles_with_distance,
        vehicles_without_distance=vehicles_without_distance,
        tracked_litres=float(_round2(tracked_litres) or 0.0),
        tracked_cost=float(_round2(tracked_cost) or 0.0),
        total_distance_km=_round2(total_distance_km) if total_distance_km > 0 else None,
        total_driving_hours=total_driving_hours,
        total_idle_hours=total_idle_hours,
        total_engine_hours=total_engine_hours,
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
        telematics_status=final_status,
        data_warnings=warnings_out,
        vehicles_telemetry_unknown=vehicles_telemetry_unknown,
        telemetry_litres=float(_round2(telemetry_litres) or 0.0),
        vehicles_engine_hours_only=vehicles_engine_only,
        engine_only_litres=float(_round2(engine_only_litres) or 0.0),
        vehicles_fuel_type_mixed=vehicles_mixed,
        fuel_type_mixed_excluded_litres=float(_round2(mixed_litres) or 0.0),
        min_efficiency_distance_km=min_km,
        efficiency_suppressed_reason=None if suppressed in (None, "no_distance") else suppressed,
    )


def calculate_vehicle_efficiency_records(
    transactions: List[Dict[str, Any]],
    telematics_map: Dict[str, Any],
    wialon_units: List[Dict[str, Any]],
    idle_rate_lph: float = 2.0,
    duplicate_names: Optional[List[str]] = None,
    mixed_fuel_unit_ids: Optional[set] = None,
    min_distance_km: Optional[float] = None,
) -> List[VehicleEfficiencyItem]:
    """
    Computes per-vehicle fuel efficiency and driving vs idling estimates.
    Differentiates statuses: 'matched', 'not_tracked', 'no_distance_data', 'ambiguous'.
    'no_distance_data' with distance_km=None means Wialon distance is unknown (distance_source='unavailable');
    with distance_km=0.0 it means Wialon recorded no movement.
    L/100 km and cost/km are purchase-based (derived). They are withheld, with efficiency_suppressed_reason,
    below the minimum distance, for non-positive net litres, or for mixed fuel types under a fuel-type filter.
    """
    min_km = min_efficiency_distance_km() if min_distance_km is None else float(min_distance_km)
    mixed = set(mixed_fuel_unit_ids or [])
    tele_by_id, ambiguous_names = index_telematics_by_unit_id(telematics_map, wialon_units, duplicate_names)
    fuel_by_unit, excluded_groups = _group_fuel_by_unit(transactions, wialon_units)

    # One record per Wialon unit (identifiers of the same unit combined), then one per excluded identifier.
    groups: List[Tuple[str, Optional[Dict[str, Any]], Dict[str, Any]]] = []
    for g in fuel_by_unit.values():
        groups.append((_primary_identifier(g["identifiers"]), g["unit"], g))
    for vid, g in excluded_groups.items():
        groups.append((vid, {"ambiguous": True} if g["status"] == REASON_AMBIGUOUS else None, g))

    results: List[VehicleEfficiencyItem] = []

    for vid, matched, f_data in groups:
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
        w_plate = extract_registration_plate(matched)
        t_data, tele_warning = _telematics_for_unit(matched, tele_by_id, ambiguous_names)

        dist_km = t_data["mileage_km"]
        driving_hrs = t_data["driving_hours"]
        idle_hrs = t_data["idle_hours"]
        eng_hrs = t_data["engine_hours"]
        has_tel = t_data["has_telemetry"]
        tele_status = _telemetry_status(t_data)

        if dist_km is None:
            # Distance unknown (not reported, unreadable or withheld): never shown as 0 km
            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    registration_plate=w_plate,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=None,
                    driving_duration_hours=driving_hrs,
                    idle_duration_hours=idle_hrs,
                    engine_hours=eng_hrs,
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
                    quality_warnings=[tele_warning or "Wialon distance for this unit could not be read; distance unavailable."],
                    distance_source="unavailable",
                    telemetry_status=tele_status,
                )
            )
        elif not has_tel and dist_km <= 0 and (eng_hrs or 0) <= 0:
            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    registration_plate=w_plate,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=0.0,
                    driving_duration_hours=driving_hrs,
                    idle_duration_hours=idle_hrs,
                    engine_hours=eng_hrs,
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
                    distance_source="wialon_report",
                    telemetry_status=tele_status,
                )
            )
        else:
            dist_dec = Decimal(str(dist_km))
            suppressed = _suppression_reason(float(dist_km), f_data["litres"], min_km, w_id in mixed)
            fuel_eff = _round2((f_data["litres"] / dist_dec) * Decimal(100)) if suppressed is None else None
            fuel_c_km = _round2(f_data["cost"] / dist_dec) if suppressed is None else None

            # Driving vs Idling estimation (impossible when Wialon idle hours are unknown)
            idle_known = idle_hrs is not None
            est_idle_l = round(idle_hrs * idle_rate_lph, 2) if idle_known and idle_hrs > 0 else 0.0
            est_idle_cost = round(est_idle_l * avg_price, 2) if est_idle_l > 0 else 0.0

            warnings = []
            if suppressed == "non_positive_net_fuel":
                warnings.append("Refund adjustments leave zero or negative net litres in this period; efficiency withheld." if f_data["litres"] == 0 else "Refund adjustments exceed purchases in this period (negative net litres); efficiency withheld.")
            elif suppressed == "insufficient_distance":
                warnings.append(f"Wialon distance {dist_km:.1f} km is below the {min_km:.0f} km minimum; efficiency withheld.")
            elif suppressed == "mixed_fuel_types":
                warnings.append("Vehicle bought more than one fuel type in this period; distance cannot be attributed to the filtered fuel type.")
            est_driving_l = None
            est_idle_pct = None
            est_driving_pct = None
            # Purchased fuel split is always an estimate (rate x Wialon idle hours), never a measurement.
            est_status = "estimated"
            if not idle_known:
                est_status = "unavailable"
                warnings.append("Wialon idle hours unavailable for this unit; idle/driving fuel split not estimated.")

            if idle_known and v_litres >= est_idle_l and v_litres > 0:
                est_driving_l = round(v_litres - est_idle_l, 2)
                est_idle_pct = round((est_idle_l / v_litres) * 100.0, 1)
                est_driving_pct = round((est_driving_l / v_litres) * 100.0, 1)
            elif idle_known and est_idle_l > v_litres:
                est_status = "provisional"
                warnings.append(
                    f"Estimated idle fuel ({est_idle_l:.1f} L) exceeds recorded purchases ({v_litres:.1f} L) for period. Driving split withheld."
                )

            results.append(
                VehicleEfficiencyItem(
                    vehicle_identifier=vid,
                    wialon_unit_id=w_id,
                    wialon_unit_name=w_name,
                    registration_plate=w_plate,
                    litres=v_litres,
                    total_cost=v_cost,
                    transaction_count=v_count,
                    distance_km=round(dist_km, 2) if dist_km > 0 else 0.0,
                    driving_duration_hours=driving_hrs,
                    idle_duration_hours=idle_hrs,
                    engine_hours=eng_hrs,
                    idle_rate_lph=idle_rate_lph,
                    estimated_idle_fuel_litres=est_idle_l if idle_known and idle_hrs > 0 else None,
                    estimated_idle_cost=est_idle_cost if idle_known and idle_hrs > 0 else None,
                    estimated_driving_fuel_litres=est_driving_l,
                    estimated_idle_percentage=est_idle_pct,
                    estimated_driving_percentage=est_driving_pct,
                    fuel_efficiency_l_per_100km=fuel_eff,
                    fuel_cost_per_km=fuel_c_km,
                    status="matched",
                    estimation_status=est_status,
                    quality_warnings=warnings,
                    distance_source="wialon_report",
                    telemetry_status=tele_status,
                    efficiency_suppressed_reason=suppressed if suppressed != "no_distance" else None,
                )
            )

    # Each group produced exactly one record; label units fuelled under several identifiers.
    for (_, _, f_data), item in zip(groups, results):
        idents = f_data.get("identifiers")
        if idents:
            item.merged_identifiers = sorted(idents)
            warning = _merge_warning(idents)
            if warning:
                item.quality_warnings = [warning] + list(item.quality_warnings)
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
        filtered = [
            v for v in filtered
            if veh_clean in v.vehicle_identifier.lower()
            or (v.registration_plate and veh_clean in v.registration_plate.lower())
            or (v.wialon_unit_name and veh_clean in v.wialon_unit_name.lower())
            or any(veh_clean in m.lower() for m in (v.merged_identifiers or []))
        ]

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


@lru_cache(maxsize=4096)
def period_key_for_date(day: date, group_by: str) -> str:
    """Period key shared by fuel and distance: YYYY-MM-DD (day), Monday YYYY-MM-DD (week), YYYY-MM (month).
    Memoized: trip bucketing calls this twice per trip over few distinct dates, and strftime dominated it."""
    if group_by == "month":
        return day.strftime("%Y-%m")
    if group_by == "week":
        return (day - timedelta(days=day.weekday())).strftime("%Y-%m-%d")
    return day.strftime("%Y-%m-%d")


def _uae_date(ts: Any) -> Optional[date]:
    try:
        return datetime.fromtimestamp(int(ts), UAE_TZ).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def bucket_trips_by_period(trips: List[Dict[str, Any]], group_by: str) -> Tuple[Dict[str, Decimal], Dict[str, Decimal], bool]:
    """
    Sum trip km per period of each trip's START (UAE day). A trip ending in a later period stays in its start
    period; its km is also reported as boundary km. Returns (km_by_period, boundary_km_by_period, complete);
    complete is False when any trip's distance or start time is unknown.
    """
    km: Dict[str, Decimal] = defaultdict(Decimal)
    boundary: Dict[str, Decimal] = defaultdict(Decimal)
    complete = True
    for t in trips or []:
        start = _uae_date(t.get("start_time"))
        dist = t.get("distance_km")
        if start is None or dist is None:
            complete = False
            continue
        p = period_key_for_date(start, group_by)
        km[p] += Decimal(str(dist))
        end = _uae_date(t.get("end_time"))
        if end is not None and period_key_for_date(end, group_by) != p:
            boundary[p] += Decimal(str(dist))
    return dict(km), dict(boundary), complete


def calculate_efficiency_trend(
    transactions: List[Dict[str, Any]],
    telematics_map: Dict[str, Any],
    wialon_units: List[Dict[str, Any]],
    group_by: str = "day",
    duplicate_names: Optional[List[str]] = None,
    unit_trips: Optional[Dict[int, Dict[str, Any]]] = None,
    min_distance_km: Optional[float] = None,
    mixed_fuel_unit_ids: Optional[set] = None,
) -> EfficiencyTrendResponse:
    """
    Chronological purchase-based efficiency with ACTUAL Wialon distance per period.

    Distance per period = sum of the period's trips (by trip start, UAE day) over units in the fuel scope whose
    trips report is complete (unit_trips from get_units_trips_for_range). Fuel is never used to apportion
    distance. Units whose report failed or was partial are excluded and listed (never counted as 0 km).
    Per-period L/100 km = tracked litres (units with distance) / period km x 100, only when the period km
    reaches the minimum and tracked litres are positive; cumulative_l_per_100km is the running ratio of totals.
    Reconciliation: each unit's trip total is compared with the group-report distance; a difference above
    max(1 km, 2%) is reported, values are never adjusted.
    """
    min_km = min_efficiency_distance_km() if min_distance_km is None else float(min_distance_km)
    mixed = set(mixed_fuel_unit_ids or [])
    tele_by_id, ambiguous_names = index_telematics_by_unit_id(telematics_map, wialon_units, duplicate_names)
    fuel_by_unit, _ = _group_fuel_by_unit(transactions, wialon_units)
    resolver = UnitResolver(wialon_units, match_fn=match_fuel_vehicle_to_wialon)
    warnings: List[str] = []

    # Units with usable trip distance
    tracked: Dict[int, Dict[str, Any]] = {}
    units_failed: List[Dict[str, Any]] = []
    mismatches: List[Dict[str, Any]] = []
    units_checked = 0
    if unit_trips is None:
        warnings.append("Per-period Wialon distance was not requested; distance and efficiency unavailable.")
    else:
        for uid, g in fuel_by_unit.items():
            res = unit_trips.get(uid)
            name = g["unit"].get("nm")
            if res is None or res.get("status") != "complete":
                units_failed.append({"wialon_unit_id": uid, "wialon_unit_name": name,
                                     "status": (res or {}).get("status", "missing"), "error": (res or {}).get("error")})
                continue
            km, boundary, complete = bucket_trips_by_period(res.get("trips") or [], group_by)
            if not complete:
                units_failed.append({"wialon_unit_id": uid, "wialon_unit_name": name, "status": "partial", "error": None})
                continue
            tracked[uid] = {"km": km, "boundary": boundary}
            t_info, _ = _telematics_for_unit(g["unit"], tele_by_id, ambiguous_names)
            group_km = t_info["mileage_km"]
            if group_km is not None:
                units_checked += 1
                trips_km = float(sum(km.values(), Decimal(0)))
                diff = trips_km - float(group_km)
                if abs(diff) > max(1.0, 0.02 * max(abs(float(group_km)), abs(trips_km))):
                    mismatches.append({"wialon_unit_id": uid, "wialon_unit_name": name, "trips_km": round(trips_km, 2),
                                       "group_report_km": round(float(group_km), 2), "difference_km": round(diff, 2)})
        if units_failed:
            warnings.append(f"Wialon trips unavailable or incomplete for {len(units_failed)} unit(s); their distance is excluded, not counted as 0.")
        if mismatches:
            warnings.append(f"Per-trip distance differs from the group report for {len(mismatches)} unit(s); values shown unadjusted.")

    periods: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "litres": Decimal(0), "cost": Decimal(0), "count": 0, "tracked_litres": Decimal(0), "tracked_cost": Decimal(0),
        "km": Decimal(0), "boundary": Decimal(0), "units": set(),
    })
    for tx in transactions:
        d = tx_local_date(tx.get("transaction_date"))
        kind, uid = unit_key(tx, resolver)
        if not d or kind != "unit":
            continue  # undated, unmatched or ambiguous rows are outside the KPI scope
        p = period_key_for_date(datetime.strptime(d, "%Y-%m-%d").date(), group_by)
        l = Decimal(str(tx.get("litres") or 0))
        c = Decimal(str(tx.get("total_cost") or 0))
        periods[p]["litres"] += l
        periods[p]["cost"] += c
        periods[p]["count"] += 1
        if uid in tracked and uid not in mixed:
            periods[p]["tracked_litres"] += l
            periods[p]["tracked_cost"] += c
    for uid, t in tracked.items():
        if uid in mixed:
            continue
        for p, km in t["km"].items():
            periods[p]["km"] += km
            periods[p]["units"].add(uid)
        for p, km in t["boundary"].items():
            periods[p]["boundary"] += km

    has_distance = bool(tracked) and any(uid not in mixed for uid in tracked)
    items: List[EfficiencyTrendItem] = []
    cum_l = cum_c = cum_km = Decimal(0)
    for p in sorted(periods):
        d = periods[p]
        dist = d["km"] if has_distance else None
        eff = cost_km = None
        if dist is None:
            status = "unavailable"
        elif dist <= 0:
            status = "no_distance"
        elif d["tracked_litres"] == 0:
            status = "no_fuel"
        else:
            status = _suppression_reason(float(dist), d["tracked_litres"], min_km, False) or "ok"
        if status == "ok":
            eff = _round2(d["tracked_litres"] / dist * Decimal(100))
            cost_km = _round2(d["tracked_cost"] / dist)
        if dist is not None:
            cum_l += d["tracked_litres"]
            cum_c += d["tracked_cost"]
            cum_km += dist
        cum_eff = _round2(cum_l / cum_km * Decimal(100)) if has_distance and cum_km >= Decimal(str(min_km)) and cum_km > 0 and cum_l > 0 else None
        items.append(EfficiencyTrendItem(
            period=p,
            litres=float(_round2(d["litres"]) or 0.0),
            cost=float(_round2(d["cost"]) or 0.0),
            transaction_count=d["count"],
            distance_km=_round2(dist) if dist is not None else None,
            fuel_efficiency_l_per_100km=eff,
            fuel_cost_per_km=cost_km,
            tracked_litres=float(_round2(d["tracked_litres"]) or 0.0),
            distance_source="wialon_unit_trips" if dist is not None else "unavailable",
            efficiency_status=status,
            cumulative_l_per_100km=cum_eff,
            boundary_trip_km=_round2(d["boundary"]) if d["boundary"] else 0.0,
            units_with_distance=len(d["units"]),
        ))

    return EfficiencyTrendResponse(
        group_by=group_by,
        data=items,
        data_warnings=warnings,
        distance_reconciliation={"units_checked": units_checked, "tolerance": "max(1 km, 2%)", "mismatches": mismatches},
        units_failed=units_failed,
        min_distance_km=min_km,
    )
