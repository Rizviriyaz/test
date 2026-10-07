"""
Wialon Dynamic Vehicle Distance and Matching Service for ATS Fleet Management.
Authoritatively resolves vehicle mileage over date intervals using Wialon's native report engine.
"""
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from app.wialon import login, WialonAPIError
from app.vehicles import get_all_vehicles
from app.wialon_reports import execute_wialon_unit_trips_report, execute_wialon_group_trips_report
from app.utils.datetime_helpers import parse_date_to_uae_bounds

logger = logging.getLogger(__name__)

# Request-scoped in-memory cache for fleet distances: (date_from, date_to) -> {unit_name: mileage_km}
_FLEET_DIST_CACHE: Dict[Tuple[str, str], Dict[str, float]] = {}


def clear_distance_cache() -> None:
    """Clear in-memory distance cache (used during testing)."""
    _FLEET_DIST_CACHE.clear()


def normalize_alphanumeric(val: Any) -> str:
    """Normalize string by removing non-alphanumeric characters and lowercasing."""
    if not val:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(val)).lower()


def extract_plate_tokens(val: Any) -> List[str]:
    """Extract split tokens and trailing numeric registration digits from plate identifier."""
    if not val:
        return []
    s = str(val).strip()
    tokens = [t.lower() for t in re.split(r"[\s\-_/]+", s) if t]
    
    # Extract trailing digits (e.g. '13-AUH-30561' -> '30561', 'T-DXB-15770' -> '15770')
    trailing = re.search(r"(\d+)$", s)
    if trailing:
        tokens.append(trailing.group(1).lower())
        tokens.append(trailing.group(1).lstrip("0").lower())
    return list(dict.fromkeys(tokens))  # preserve order, unique


def match_fuel_vehicle_to_wialon(
    vehicle_identifier: str,
    wialon_units: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Dynamically matches a fuel vehicle identifier (e.g. 'T-DXB-15770', '13-AUH-30561')
    to an active Wialon unit from a dynamically fetched unit list.
    
    If multiple Wialon units match the same identifier, does NOT guess; returns an ambiguous marker.
    Zero hardcoded values: matching is purely algorithmic and scales to any fleet size.
    """
    if not vehicle_identifier or not wialon_units:
        return None

    fuel_raw = vehicle_identifier.strip()
    fuel_lower = fuel_raw.lower()
    fuel_norm = normalize_alphanumeric(fuel_raw)
    fuel_tokens = extract_plate_tokens(fuel_raw)

    candidates: List[Dict[str, Any]] = []

    # 1. Exact match against unit name or registration plate
    for u in wialon_units:
        u_nm = str(u.get("nm", "")).strip().lower()
        if u_nm and (u_nm == fuel_lower or u_nm == fuel_norm):
            if u not in candidates:
                candidates.append(u)

    if candidates:
        distinct_ids = {c.get("id") for c in candidates if c.get("id")}
        if len(distinct_ids) == 1:
            return candidates[0]
        elif len(distinct_ids) > 1:
            return {"ambiguous": True, "candidates": candidates, "id": None, "nm": None}

    # 2. Match against fuel tokens (e.g., trailing plate number '30561' matches Wialon unit '30561')
    for u in wialon_units:
        u_nm = str(u.get("nm", "")).strip().lower()
        u_nm_norm = normalize_alphanumeric(u.get("nm", ""))
        if u_nm in fuel_tokens or (u_nm_norm and u_nm_norm in fuel_tokens):
            if u not in candidates:
                candidates.append(u)

    distinct_ids = {c.get("id") for c in candidates if c.get("id")}
    if len(distinct_ids) == 1:
        return candidates[0]
    elif len(distinct_ids) > 1:
        return {"ambiguous": True, "candidates": candidates, "id": None, "nm": None}

    return None


def get_fleet_distances_map(
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, float]:
    """
    Retrieves distances for all fleet units in 1 single Wialon call using the Group Trips Report.
    Deduplicates requests across the request lifecycle using in-memory caching.
    Returns: {wialon_unit_name: mileage_km}
    """
    cache_key = (date_from, date_to)
    if cache_key in _FLEET_DIST_CACHE:
        return _FLEET_DIST_CACHE[cache_key]

    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    current_sid = sid or login()

    try:
        group_rows = execute_wialon_group_trips_report(current_sid, from_ts, to_ts)
    except WialonAPIError:
        current_sid = login(force=True)
        group_rows = execute_wialon_group_trips_report(current_sid, from_ts, to_ts)

    dist_map: Dict[str, float] = {}
    if group_rows:
        for row in group_rows:
            v_name = str(row.get("vehicle_name", "")).strip()
            dist_km = float(row.get("mileage_km", 0.0))
            if v_name:
                dist_map[v_name] = dist_km

    _FLEET_DIST_CACHE[cache_key] = dist_map
    return dist_map


def get_vehicle_distance(
    wialon_unit_id: int,
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Authoritatively retrieves vehicle distance traveled over a date range directly
    from Wialon's native Trips Report engine for a single unit.
    """
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)

    current_sid = sid or login()

    try:
        rep_result = execute_wialon_unit_trips_report(current_sid, int(wialon_unit_id), from_ts, to_ts)
    except WialonAPIError:
        # Retry with fresh session in case of unexpected transport/session drop
        current_sid = login(force=True)
        rep_result = execute_wialon_unit_trips_report(current_sid, int(wialon_unit_id), from_ts, to_ts)

    if not rep_result:
        return {
            "distance": 0.0,
            "unit": "km",
            "source": "wialon_native_trips_report",
            "date_from": date_from,
            "date_to": date_to,
            "trip_count": 0,
            "has_data": False,
        }

    trips = rep_result.get("trips", [])
    total_km = sum(float(t.get("distance_km", 0.0)) for t in trips)

    has_data = len(trips) > 0

    return {
        "distance": round(total_km, 2),
        "unit": "km",
        "source": "wialon_native_trips_report",
        "date_from": date_from,
        "date_to": date_to,
        "trip_count": len(trips),
        "has_data": has_data,
    }

