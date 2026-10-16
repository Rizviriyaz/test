"""
Wialon Dynamic Vehicle Distance, Activity and Matching Service for ATS Fleet Management.
Authoritatively resolves vehicle mileage, driving duration, and idling duration
over date intervals using Wialon's native report engine.
"""
import logging
import re
from typing import Dict, List, Any, Optional, Tuple
from app.wialon import login, WialonAPIError
from app.vehicles import get_all_vehicles, extract_registration_plate
from app.wialon_reports import (
    execute_wialon_unit_trips_report,
    execute_wialon_unit_engine_report,
    execute_wialon_group_trips_report,
    execute_wialon_group_engine_idle_report,
    GROUP_ID_ALL_UNITS,
)
from app.utils.datetime_helpers import parse_date_to_uae_bounds

logger = logging.getLogger(__name__)

# Request-scoped in-memory cache for fleet distances: (date_from, date_to) -> {unit_name: mileage_km}
_FLEET_DIST_CACHE: Dict[Tuple[str, str], Dict[str, float]] = {}
# Request-scoped in-memory cache for fleet telematics: (date_from, date_to) -> {unit_name: telematics_dict}
_FLEET_TELEMATICS_CACHE: Dict[Tuple[str, str], Dict[str, Dict[str, Any]]] = {}


def clear_distance_cache() -> None:
    """Clear in-memory distance and telematics cache."""
    _FLEET_DIST_CACHE.clear()
    _FLEET_TELEMATICS_CACHE.clear()


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

    Matching hierarchy:
    1. Primary exact match against verified Wialon Profile Registration Plate.
    2. Primary token match against verified Wialon Profile Registration Plate.
    3. Fallback exact match against Wialon unit name (General -> Name) for backward compatibility.
    4. Fallback token match against Wialon unit name.

    If multiple Wialon units match the same identifier, does NOT guess; returns an ambiguous marker.
    Zero hardcoded values: matching is purely algorithmic and scales to any fleet size.
    """
    if not vehicle_identifier or not wialon_units:
        return None

    fuel_raw = vehicle_identifier.strip()
    fuel_lower = fuel_raw.lower()
    fuel_norm = normalize_alphanumeric(fuel_raw)
    fuel_tokens = extract_plate_tokens(fuel_raw)

    # 1. Primary: Exact match against verified Wialon Profile Registration Plate
    candidates_primary: List[Dict[str, Any]] = []
    for u in wialon_units:
        u_plate = extract_registration_plate(u)
        if u_plate:
            u_plate_lower = str(u_plate).strip().lower()
            u_plate_norm = normalize_alphanumeric(u_plate)
            if u_plate_lower == fuel_lower or (u_plate_norm and u_plate_norm == fuel_norm):
                if u not in candidates_primary:
                    candidates_primary.append(u)

    if candidates_primary:
        distinct_ids = {c.get("id") for c in candidates_primary if c.get("id")}
        if len(distinct_ids) == 1:
            return candidates_primary[0]
        elif len(distinct_ids) > 1:
            return {"ambiguous": True, "candidates": candidates_primary, "id": None, "nm": None}

    # 2. Primary: Match against fuel tokens using verified Profile Registration Plate
    for u in wialon_units:
        u_plate = extract_registration_plate(u)
        if u_plate:
            u_plate_lower = str(u_plate).strip().lower()
            u_plate_norm = normalize_alphanumeric(u_plate)
            if u_plate_lower in fuel_tokens or (u_plate_norm and u_plate_norm in fuel_tokens):
                if u not in candidates_primary:
                    candidates_primary.append(u)

    if candidates_primary:
        distinct_ids = {c.get("id") for c in candidates_primary if c.get("id")}
        if len(distinct_ids) == 1:
            return candidates_primary[0]
        elif len(distinct_ids) > 1:
            return {"ambiguous": True, "candidates": candidates_primary, "id": None, "nm": None}

    # 3. Explicit Fallback: Exact match against Wialon unit name (General -> Name) for backward compatibility
    candidates_fallback: List[Dict[str, Any]] = []
    for u in wialon_units:
        u_nm = str(u.get("nm", "")).strip().lower()
        if u_nm and (u_nm == fuel_lower or u_nm == fuel_norm):
            if u not in candidates_fallback:
                candidates_fallback.append(u)

    if candidates_fallback:
        distinct_ids = {c.get("id") for c in candidates_fallback if c.get("id")}
        if len(distinct_ids) == 1:
            return candidates_fallback[0]
        elif len(distinct_ids) > 1:
            return {"ambiguous": True, "candidates": candidates_fallback, "id": None, "nm": None}

    # 4. Explicit Fallback: Match against fuel tokens using unit name
    for u in wialon_units:
        u_nm = str(u.get("nm", "")).strip().lower()
        u_nm_norm = normalize_alphanumeric(u.get("nm", ""))
        if u_nm in fuel_tokens or (u_nm_norm and u_nm_norm in fuel_tokens):
            if u not in candidates_fallback:
                candidates_fallback.append(u)

    if candidates_fallback:
        distinct_ids = {c.get("id") for c in candidates_fallback if c.get("id")}
        if len(distinct_ids) == 1:
            return candidates_fallback[0]
        elif len(distinct_ids) > 1:
            return {"ambiguous": True, "candidates": candidates_fallback, "id": None, "nm": None}

    return None


def get_fleet_distances_map(
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, float]:
    """
    Retrieves distances for all fleet units in 1 single Wialon call using the Group Trips Report.
    Returns: {wialon_unit_name: mileage_km}
    """
    telematics = get_fleet_telematics_map(date_from, date_to, sid)
    return {k: v.get("mileage_km", 0.0) for k, v in telematics.items()}


def get_fleet_telematics_map(
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Retrieves authoritative distance, driving duration, idling duration, and engine hours
    for all fleet units in unified cached calls using Wialon Group Reports.
    Returns: {wialon_unit_name: {"mileage_km": float, "driving_seconds": int, "idle_seconds": int, "engine_hours_seconds": int, "total_time_seconds": int, ...}}
    """
    cache_key = (date_from, date_to)
    if cache_key in _FLEET_TELEMATICS_CACHE:
        return _FLEET_TELEMATICS_CACHE[cache_key]

    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    current_sid = sid or login()

    telematics_map: Dict[str, Dict[str, Any]] = {}

    # 1. Fetch Group Trips for mileage & motion
    try:
        group_trips = execute_wialon_group_trips_report(current_sid, from_ts, to_ts)
    except WialonAPIError:
        current_sid = login(force=True)
        group_trips = execute_wialon_group_trips_report(current_sid, from_ts, to_ts)
    except Exception as exc:
        logger.warning("Group trips report error: %s", exc)
        group_trips = []

    if group_trips:
        for row in group_trips:
            v_name = str(row.get("vehicle_name", "")).strip()
            if not v_name:
                continue
            dist_km = float(row.get("mileage_km", 0.0))
            telematics_map[v_name] = {
                "vehicle_name": v_name,
                "mileage_km": dist_km,
                "driving_seconds": 0,
                "idle_seconds": 0,
                "engine_hours_seconds": 0,
                "total_time_seconds": 0,
                "driving_hours": 0.0,
                "idle_hours": 0.0,
                "engine_hours": 0.0,
                "total_time_hours": 0.0,
                "has_telemetry": dist_km > 0,
            }

    # 2. Fetch Group Engine & Idling Report
    try:
        group_engine = execute_wialon_group_engine_idle_report(
            sid=current_sid,
            from_time_utc=from_ts,
            to_time_utc=to_ts,
            group_id=GROUP_ID_ALL_UNITS,
            include_events=False
        )
    except WialonAPIError:
        current_sid = login(force=True)
        group_engine = execute_wialon_group_engine_idle_report(
            sid=current_sid,
            from_time_utc=from_ts,
            to_time_utc=to_ts,
            group_id=GROUP_ID_ALL_UNITS,
            include_events=False
        )
    except Exception as exc:
        logger.warning("Group engine report error: %s", exc)
        group_engine = None

    if group_engine and group_engine.get("vehicles"):
        for row in group_engine.get("vehicles", []):
            v_name = str(row.get("vehicle_name", "")).strip()
            if not v_name:
                continue
            
            in_motion_sec = int(row.get("in_motion_seconds", 0))
            idle_sec = int(row.get("total_idle_seconds", 0))
            engine_sec = int(row.get("engine_hours_seconds", 0))
            total_sec = int(row.get("total_time_seconds", 0))

            if v_name not in telematics_map:
                # Extract mileage if available
                m_str = str(row.get("mileage_str", "")).replace("km", "").strip()
                try:
                    dist_km = float(m_str)
                except ValueError:
                    dist_km = 0.0
                telematics_map[v_name] = {
                    "vehicle_name": v_name,
                    "mileage_km": dist_km,
                    "driving_seconds": in_motion_sec,
                    "idle_seconds": idle_sec,
                    "engine_hours_seconds": engine_sec,
                    "total_time_seconds": total_sec,
                    "driving_hours": round(in_motion_sec / 3600.0, 2),
                    "idle_hours": round(idle_sec / 3600.0, 2),
                    "engine_hours": round(engine_sec / 3600.0, 2),
                    "total_time_hours": round(total_sec / 3600.0, 2),
                    "has_telemetry": (dist_km > 0 or engine_sec > 0),
                }
            else:
                telematics_map[v_name]["driving_seconds"] = in_motion_sec
                telematics_map[v_name]["idle_seconds"] = idle_sec
                telematics_map[v_name]["engine_hours_seconds"] = engine_sec
                telematics_map[v_name]["total_time_seconds"] = total_sec
                telematics_map[v_name]["driving_hours"] = round(in_motion_sec / 3600.0, 2)
                telematics_map[v_name]["idle_hours"] = round(idle_sec / 3600.0, 2)
                telematics_map[v_name]["engine_hours"] = round(engine_sec / 3600.0, 2)
                telematics_map[v_name]["total_time_hours"] = round(total_sec / 3600.0, 2)
                if in_motion_sec > 0 or idle_sec > 0 or engine_sec > 0:
                    telematics_map[v_name]["has_telemetry"] = True

    _FLEET_TELEMATICS_CACHE[cache_key] = telematics_map
    _FLEET_DIST_CACHE[cache_key] = {k: v.get("mileage_km", 0.0) for k, v in telematics_map.items()}
    return telematics_map


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


def get_vehicle_telematics_detail(
    wialon_unit_id: int,
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Authoritatively retrieves complete telematics detail (trips, engine hours, idle segments)
    for a single unit.
    """
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    current_sid = sid or login()

    # 1. Trips report
    try:
        trips_rep = execute_wialon_unit_trips_report(current_sid, int(wialon_unit_id), from_ts, to_ts)
    except Exception:
        trips_rep = None

    # 2. Engine & Idle report
    try:
        engine_rep = execute_wialon_unit_engine_report(current_sid, int(wialon_unit_id), from_ts, to_ts)
    except Exception:
        engine_rep = None

    trips = trips_rep.get("trips", []) if trips_rep else []
    total_distance_km = sum(float(t.get("distance_km", 0.0)) for t in trips)
    moving_duration_minutes = sum(float(t.get("duration_minutes", 0.0)) for t in trips)

    total_idle_seconds = engine_rep.get("total_idle_seconds", 0) if engine_rep else 0
    total_engine_seconds = engine_rep.get("total_engine_seconds", 0) if engine_rep else 0
    idle_events = engine_rep.get("idle_events", []) if engine_rep else []

    total_interval_seconds = max(0, to_ts - from_ts)
    stopped_seconds = max(0, total_interval_seconds - total_engine_seconds)

    return {
        "distance_km": round(total_distance_km, 2),
        "trip_count": len(trips),
        "driving_duration_hours": round(moving_duration_minutes / 60.0, 2),
        "idle_duration_hours": round(total_idle_seconds / 3600.0, 2),
        "engine_hours": round(total_engine_seconds / 3600.0, 2),
        "stopped_hours": round(stopped_seconds / 3600.0, 2),
        "idle_events_count": len(idle_events),
        "idle_events": idle_events,
        "trips": trips,
        "has_data": (len(trips) > 0 or total_engine_seconds > 0),
    }


