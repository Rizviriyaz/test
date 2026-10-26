"""
Fleet Analytics Service Module for ATS Fleet Management.

Provides high-performance, cached, and sanitized analytics across the fleet:
  1. Vehicle Health & Diagnostics (Battery, GPS quality, Connectivity)
  2. Fleet Utilization (Driving, Idle, Stopped, Engine-on, Utilization %)
  3. Trips & Mileage (Hardware Odometer vs GPS-derived distance)
  4. Safety Analytics (Harsh Braking, Acceleration, Cornering with sanitized G-force)
  5. Fleet Alerts (CRITICAL, WARNING, INFO)
  6. Group Analytics (Dynamic rollups by Wialon group)
  7. Single Vehicle Deep Analytics & Route Replay
"""

import math, os, logging, re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from app.utils.datetime_helpers import to_iso_uae, now_timestamp
from app.performance_diagnostics import log_cache_state, log_data_window, map_with_request_context, new_trace_id, request_trace_id
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional

from app.wialon import WialonAPIError, login, get_vehicle_history, get_vehicle_groups
from app.wialon_reports import (
    execute_wialon_unit_trips_report,
    execute_wialon_group_trips_report,
    execute_wialon_group_engine_idle_report,
    parse_duration_to_seconds,
    GROUP_ID_ALL_UNITS
)
from app.vehicles import get_all_vehicles, get_calculated_data, get_vehicle_live_data, get_sensor_by_name
from app.normalize import normalize_wialon_messages, segment_vehicle_activity, is_valid_sensor_value, INVALID_WIALON_VALUE
from app.utils.status_config import CONN_DELAYED_SECONDS, CONN_STABLE_SECONDS
from app.utils.ttl_cache import TTLCache

# ── Simple in-memory cache with TTL ──────────────────────────────────────────
# Bounded LRU (keys include client-chosen hours and windows). Entries are evicted after the longest
# ttl_seconds any caller passes to get_cached(); freshness is still checked per call.
_ANALYTICS_CACHE_RETENTION_SECONDS = 600
_analytics_cache = TTLCache(max_entries=128)

def get_cached(key: str, ttl_seconds: int = 120) -> Optional[Any]:
    entry = _analytics_cache.get(key)
    if entry and (time.time() - entry["time"]) < ttl_seconds:
        return entry["data"]
    return None

def set_cached(key: str, data: Any):
    _analytics_cache.set(key, {"data": data, "time": time.time()}, _ANALYTICS_CACHE_RETENTION_SECONDS)


# ── Health Threshold Constants ───────────────────────────────────────────────
def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return default

HEALTH_THRESHOLDS = {
    # 24V Systems (Heavy trucks / commercial delivery vehicles)
    "BATTERY_24V_CRITICAL": _env_float("BATTERY_24V_CRITICAL", 23.0),
    "BATTERY_24V_WARNING": _env_float("BATTERY_24V_WARNING", 24.5),
    "BATTERY_24V_HEALTHY": _env_float("BATTERY_24V_HEALTHY", 25.5),

    # 12V Systems (Passenger cars / light vans)
    "BATTERY_12V_CRITICAL": _env_float("BATTERY_12V_CRITICAL", 11.5),
    "BATTERY_12V_WARNING": _env_float("BATTERY_12V_WARNING", 12.2),
    "BATTERY_12V_HEALTHY": _env_float("BATTERY_12V_HEALTHY", 12.6),

    # GPS Quality
    "GPS_SATELLITES_EXCELLENT": _env_float("GPS_SATELLITES_EXCELLENT", 12),
    "GPS_SATELLITES_GOOD": _env_float("GPS_SATELLITES_GOOD", 8),
    "GPS_SATELLITES_WEAK": _env_float("GPS_SATELLITES_WEAK", 5),

    "GPS_HDOP_EXCELLENT": _env_float("GPS_HDOP_EXCELLENT", 1.2),
    "GPS_HDOP_GOOD": _env_float("GPS_HDOP_GOOD", 2.5),

    # Connectivity thresholds (seconds)
    "CONN_STABLE_SECONDS": CONN_STABLE_SECONDS,
    "CONN_DELAYED_SECONDS": CONN_DELAYED_SECONDS,
}

# ── Haversine Distance Helper ────────────────────────────────────────────────
def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    try:
        r = 6371.0
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2.0)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0)**2
        return 2.0 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))
    except Exception:
        return 0.0


def _parse_speed_kmh(value: Any) -> Optional[float]:
    if isinstance(value, dict):
        value = value.get("t", value.get("value", value.get("v")))
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", str(value or "").replace(",", ""))
    return float(match.group(0)) if match else None


# ── Helper: Evaluate Single Vehicle Health ───────────────────────────────────
def evaluate_vehicle_health(vehicle: Dict[str, Any], raw_unit: Dict[str, Any] = None, calc_data: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Evaluate transparent health indicators for a vehicle:
    - Battery state (Healthy / Warning / Critical)
    - GPS quality (Excellent / Good / Weak)
    - Connectivity (Stable / Delayed / Offline)
    - Health score (0-100)
    """
    vid = vehicle.get("vehicle_id")
    vname = vehicle.get("vehicle_name", f"Vehicle {vid}")

    # Battery
    sensors = vehicle.get("sensors") or {}
    voltage = sensors.get("battery_voltage")
    battery_level_label = "Unknown"
    battery_status = "UNKNOWN"
    system_type = "Unknown"

    try:
        if voltage is not None and is_valid_sensor_value(voltage):
            voltage = float(voltage)
        else:
            voltage = None
    except (ValueError, TypeError):
        voltage = None

    if voltage is not None and voltage > 0:
        if voltage > 18.0:
            system_type = "24V Commercial"
            if voltage < HEALTH_THRESHOLDS["BATTERY_24V_CRITICAL"]:
                battery_status = "CRITICAL"
                battery_level_label = f"Critical ({voltage:.1f}V)"
            elif voltage < HEALTH_THRESHOLDS["BATTERY_24V_WARNING"]:
                battery_status = "WARNING"
                battery_level_label = f"Low ({voltage:.1f}V)"
            else:
                battery_status = "HEALTHY"
                battery_level_label = f"Optimal ({voltage:.1f}V)"
        else:
            system_type = "12V Standard"
            if voltage < HEALTH_THRESHOLDS["BATTERY_12V_CRITICAL"]:
                battery_status = "CRITICAL"
                battery_level_label = f"Critical ({voltage:.1f}V)"
            elif voltage < HEALTH_THRESHOLDS["BATTERY_12V_WARNING"]:
                battery_status = "WARNING"
                battery_level_label = f"Low ({voltage:.1f}V)"
            else:
                battery_status = "HEALTHY"
                battery_level_label = f"Optimal ({voltage:.1f}V)"

    # Internal backup battery
    prms = ((raw_unit or {}).get("lmsg") or {}).get("p") or {}
    if not prms and calc_data:
        prms = calc_data.get("prms") or {}
    pwr_int = prms.get("pwr_int")
    battery_charge = prms.get("battery_charge")

    # GPS Quality
    loc = vehicle.get("location") or {}
    sats = loc.get("satellites")
    hdop = prms.get("hdop")
    pdop = prms.get("pdop")

    gps_status = "GOOD"
    if sats is None:
        gps_status = "UNKNOWN"
    elif sats >= HEALTH_THRESHOLDS["GPS_SATELLITES_EXCELLENT"] and (hdop is None or hdop <= HEALTH_THRESHOLDS["GPS_HDOP_EXCELLENT"]):
        gps_status = "EXCELLENT"
    elif sats >= HEALTH_THRESHOLDS["GPS_SATELLITES_GOOD"] and (hdop is None or hdop <= HEALTH_THRESHOLDS["GPS_HDOP_GOOD"]):
        gps_status = "GOOD"
    else:
        gps_status = "WEAK"

    # Connectivity
    pos_data = (calc_data or {}).get("pos") or (raw_unit or {}).get("pos") or {}
    last_msg_ts = ((raw_unit or {}).get("lmsg") or {}).get("t") or pos_data.get("t")
    now_ts = int(time.time())

    conn_status = "STABLE"
    seconds_since_update = 0
    if last_msg_ts:
        seconds_since_update = max(0, now_ts - int(last_msg_ts))
        if seconds_since_update > HEALTH_THRESHOLDS["CONN_DELAYED_SECONDS"]:
            conn_status = "OFFLINE"
        elif seconds_since_update > HEALTH_THRESHOLDS["CONN_STABLE_SECONDS"] and vehicle.get("last_known_status") != "STOPPED":
            # A unit last seen parked (STOPPED) reports less often: an older message alone is not a delay (no
            # warning, alert or penalty) until it is OFFLINE past CONN_DELAYED_SECONDS
            conn_status = "DELAYED"
        else:
            conn_status = "STABLE"
    else:
        conn_status = "OFFLINE"
        seconds_since_update = None

    # Transparent Health Score (0-100)
    score = 100
    if battery_status == "CRITICAL":
        score -= 40
    elif battery_status == "WARNING":
        score -= 20
    elif battery_status == "UNKNOWN":
        score -= 10

    if gps_status == "WEAK":
        score -= 25
    elif gps_status == "UNKNOWN":
        score -= 15
    elif gps_status == "GOOD":
        score -= 5

    if conn_status == "OFFLINE":
        score -= 35
    elif conn_status == "DELAYED":
        score -= 15

    health_score = max(0, min(100, score))

    return {
        "vehicle_id": vid,
        "vehicle_name": vname,
        "group": vehicle.get("group", "General Fleet"),
        "health_score": health_score,
        "battery": {
            "voltage": round(float(voltage), 2) if voltage is not None else None,
            "status": battery_status,
            "label": battery_level_label,
            "system_type": system_type,
            "internal_voltage": round(float(pwr_int), 2) if pwr_int is not None else None,
            "charge_percent": battery_charge if battery_charge is not None else None,
        },
        "gps": {
            "satellites": sats,
            "hdop": hdop,
            "pdop": pdop,
            "status": gps_status,
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
        },
        "connectivity": {
            "status": conn_status,
            "last_message_ts": last_msg_ts,
            "seconds_since_update": seconds_since_update,
            "last_seen_formatted": f"{seconds_since_update // 60}m ago" if (seconds_since_update is not None and seconds_since_update < 3600) else (f"{seconds_since_update // 3600}h ago" if seconds_since_update is not None else "—")
        },
        "ignition": vehicle.get("ignition", False),
        "speed": vehicle.get("speed", 0),
        "status": vehicle.get("status", "STOPPED")
    }


# =============================================================================
# 1. FLEET HEALTH SERVICE
# =============================================================================

def get_fleet_health_analytics(use_cache: bool = True) -> Dict[str, Any]:
    cached = get_cached("fleet_health_analytics", ttl_seconds=180) if use_cache else None
    log_cache_state("/api/fleet/health", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups = get_vehicle_groups(sid)
    vehicles = get_all_vehicles(sid)
    calc_data = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)

    calc_map = {item.get("i"): item for item in calc_data}
    raw_map  = {u.get("id"): u for u in vehicles}

    evaluations = []
    battery_warnings = 0
    gps_warnings = 0
    connectivity_warnings = 0
    healthy_vehicles = 0

    for v in fleet:
        vid = v.get("vehicle_id")
        ev = evaluate_vehicle_health(v, raw_unit=raw_map.get(vid), calc_data=calc_map.get(vid))
        evaluations.append(ev)

        has_issue = False
        if ev["battery"]["status"] in ["WARNING", "CRITICAL"]:
            battery_warnings += 1
            has_issue = True
        if ev["gps"]["status"] == "WEAK":
            gps_warnings += 1
            has_issue = True
        if ev["connectivity"]["status"] in ["DELAYED", "OFFLINE"]:
            connectivity_warnings += 1
            has_issue = True

        if not has_issue and ev["health_score"] >= 80:
            healthy_vehicles += 1

    result = {
        "total_vehicles": len(fleet),
        "healthy_vehicles": healthy_vehicles,
        "battery_warnings": battery_warnings,
        "gps_warnings": gps_warnings,
        "connectivity_warnings": connectivity_warnings,
        "average_health_score": round(sum(e["health_score"] for e in evaluations) / len(evaluations), 1) if evaluations else 0,
        "vehicles": sorted(evaluations, key=lambda x: x["health_score"])  # Most urgent first
    }

    set_cached("fleet_health_analytics", result)
    return result


# =============================================================================
# 2. FLEET UTILIZATION SERVICE
# =============================================================================

def get_fleet_utilization_analytics(hours: int = 24) -> Dict[str, Any]:
    window_to = int(time.time())
    from_time_utc = window_to - hours * 3600
    log_data_window("/api/fleet/utilization", from_time_utc, window_to)
    cache_key = f"fleet_utilization_{hours}"
    cached = get_cached(cache_key, ttl_seconds=180)
    log_cache_state("/api/fleet/utilization", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups = get_vehicle_groups(sid)
    vehicles = get_all_vehicles(sid)
    calc_data = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)

    # Primary: Fast authoritative Wialon Group Engine Report (Template 2)
    group_engine = None
    try:
        group_engine = execute_wialon_group_engine_idle_report(
            sid=sid,
            from_time_utc=from_time_utc,
            to_time_utc=window_to,
            group_id=GROUP_ID_ALL_UNITS,
            include_events=False
        )
    except Exception as e:
        logging.warning("Group engine report failed, using unit telemetry fallback: %s", e)
        group_engine = None

    if group_engine is not None and group_engine.get("vehicles"):
        engine_by_name = {uv["vehicle_name"]: uv for uv in group_engine.get("vehicles", [])}
        ambiguous_names = _ambiguous_report_names(fleet, group_engine.get("vehicles", []))
        # A row naming no fleet unit is left out of the totals (as /api/fleet/trips): partial and not cached
        fleet_names = {v["vehicle_name"] for v in fleet}
        unmatched_rows = sorted({name for name in engine_by_name if name not in fleet_names})
        if unmatched_rows:
            logging.warning("Engine report rows name no fleet unit, left out of utilization: %s", unmatched_rows)
        report_incomplete = bool(unmatched_rows) or bool(
            group_engine.get("column_warnings") or group_engine.get("unlocated_fields")) or any(
            uv.get("incomplete_fields") for uv in group_engine.get("vehicles", []))
        unit_results = []
        total_sec = hours * 3600
        for v_record in fleet:
            vname = v_record["vehicle_name"]
            vid = v_record["vehicle_id"]
            uv = engine_by_name.get(vname, {})
            unreadable = set(uv.get("incomplete_fields") or []) | set(group_engine.get("unlocated_fields") or [])
            # The group report emits a row (with explicit zeros) for every member even without activity,
            # so a vehicle with no row was not covered by the report
            missing_row = vname not in engine_by_name
            if missing_row or vname in ambiguous_names or unreadable & {"in_motion_seconds", "total_idle_seconds", "engine_hours_seconds"}:
                # Unattributable, missing or unreadable (or guessed-column) row: utilization is unknown, not 0
                unit_results.append({
                    "vehicle_id": vid,
                    "vehicle_name": vname,
                    "group": v_record.get("group", "General Fleet"),
                    "status": v_record.get("status", "STOPPED"),
                    "connection_status": v_record.get("connection_status", "STABLE"),
                    "is_offline": v_record.get("is_offline", False),
                    "driving_minutes": None, "idle_minutes": None, "stopped_minutes": None, "engine_on_minutes": None,
                    "driving_seconds": None, "idle_seconds": None, "stopped_seconds": None, "engine_on_seconds": None,
                    "utilization_percentage": None,
                    "driving_to_idle_ratio": None,
                    "trip_count": None
                })
                continue
            driving_sec = uv.get("in_motion_seconds", 0)
            idle_sec = uv.get("total_idle_seconds", 0)
            engine_on_sec = uv.get("engine_hours_seconds", 0) or (driving_sec + idle_sec)
            stopped_sec = max(0, total_sec - (driving_sec + idle_sec))
            util_pct = round((driving_sec / total_sec) * 100, 1) if total_sec > 0 else 0
            drive_idle_ratio = round(driving_sec / max(1, idle_sec), 2)

            unit_results.append({
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group": v_record.get("group", "General Fleet"),
                "status": v_record.get("status", "STOPPED"),
                "connection_status": v_record.get("connection_status", "STABLE"),
                "is_offline": v_record.get("is_offline", False),
                "driving_minutes": round(driving_sec / 60, 1),
                "idle_minutes": round(idle_sec / 60, 1),
                "stopped_minutes": round(stopped_sec / 60, 1),
                "engine_on_minutes": round(engine_on_sec / 60, 1),
                "driving_seconds": driving_sec,
                "idle_seconds": idle_sec,
                "stopped_seconds": stopped_sec,
                "engine_on_seconds": engine_on_sec,
                "utilization_percentage": util_pct,
                "driving_to_idle_ratio": drive_idle_ratio,
                # The group engine report has no trip count; unknown, never inferred from driving time
                "trip_count": None
            })
    else:
        report_incomplete = False
        unmatched_rows = []

        def _process_unit_utilization(v_record):
            vid = v_record["vehicle_id"]
            sens = next((u.get("sens") or {} for u in vehicles if u.get("id") == vid), {})
            try:
                raw_msgs = get_vehicle_history(sid, vid, hours=hours)
                telemetry = normalize_wialon_messages(raw_msgs, configured_sensors=sens)
                activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)

                trips = activity.get("trips", [])
                idles = activity.get("idle_periods", [])

                driving_sec = sum(t.get("duration", 0) for t in trips)
                idle_sec    = sum(i.get("duration", 0) for i in idles)
                total_sec   = hours * 3600
                stopped_sec = max(0, total_sec - (driving_sec + idle_sec))
                engine_on_sec = driving_sec + idle_sec

                util_pct = round((driving_sec / total_sec) * 100, 1) if total_sec > 0 else 0
                drive_idle_ratio = round(driving_sec / max(1, idle_sec), 2)

                return {
                    "vehicle_id": vid,
                    "vehicle_name": v_record["vehicle_name"],
                    "group": v_record.get("group", "General Fleet"),
                    "status": v_record.get("status", "STOPPED"),
                    "connection_status": v_record.get("connection_status", "STABLE"),
                    "is_offline": v_record.get("is_offline", False),
                    "driving_minutes": round(driving_sec / 60, 1),
                    "idle_minutes": round(idle_sec / 60, 1),
                    "stopped_minutes": round(stopped_sec / 60, 1),
                    "engine_on_minutes": round(engine_on_sec / 60, 1),
                    "driving_seconds": driving_sec,
                    "idle_seconds": idle_sec,
                    "stopped_seconds": stopped_sec,
                    "engine_on_seconds": engine_on_sec,
                    "utilization_percentage": util_pct,
                    "driving_to_idle_ratio": drive_idle_ratio,
                    "trip_count": len(trips)
                }
            except Exception as exc:
                # The route handler logs the traceback when this propagates; keep the vehicle context here
                logging.error("Wialon utilization data failed for vehicle %s: %s", vid, exc)
                raise

        with ThreadPoolExecutor(max_workers=8) as executor:
            unit_results = list(map_with_request_context(executor, _process_unit_utilization, fleet))

    # Fleet totals cover vehicles with known values only; unknown ones are listed, never counted as 0
    known_units = [u for u in unit_results if u["driving_seconds"] is not None]
    unknown_vehicles = sorted(u["vehicle_name"] for u in unit_results if u["driving_seconds"] is None)
    total_driving_sec = sum(u["driving_seconds"] for u in known_units)
    total_idle_sec    = sum(u["idle_seconds"] for u in known_units)
    total_engine_sec  = sum(u["engine_on_seconds"] for u in known_units)
    total_fleet_time  = len(known_units) * hours * 3600

    fleet_util_pct = round((total_driving_sec / total_fleet_time) * 100, 1) if total_fleet_time > 0 else 0
    fleet_ratio    = round(total_driving_sec / max(1, total_idle_sec), 2)
    # Every vehicle unknown: there is no known basis for any fleet total (None, not 0)
    all_unknown = bool(unknown_vehicles) and not known_units

    result = {
        "hours": hours,
        "fleet_utilization_pct": None if all_unknown else fleet_util_pct,
        "total_driving_hours": None if all_unknown else round(total_driving_sec / 3600, 1),
        "total_idle_hours": None if all_unknown else round(total_idle_sec / 3600, 1),
        "total_engine_on_hours": None if all_unknown else round(total_engine_sec / 3600, 1),
        "driving_to_idle_ratio": None if all_unknown else fleet_ratio,
        "partial": bool(unknown_vehicles or unmatched_rows),
        "unknown_vehicles": unknown_vehicles,
        # Unknown (None) utilization sorts after all known values
        "vehicles": sorted(unit_results, key=lambda x: (x["utilization_percentage"] is not None, x["utilization_percentage"] or 0), reverse=True)
    }

    # Do not cache a report with unreadable cells or unlocated columns; the next request re-reads it
    if not report_incomplete:
        set_cached(cache_key, result)
    return result


def _ambiguous_report_names(fleet: List[Dict[str, Any]], report_rows: List[Dict[str, Any]]) -> set:
    """Names that cannot be attributed to a single unit: shared by several fleet units or several report rows."""
    fleet_counts = Counter(v.get("vehicle_name") for v in fleet)
    row_counts = Counter(r.get("vehicle_name") for r in report_rows)
    return {n for n in set(fleet_counts) | set(row_counts) if n and (fleet_counts.get(n, 0) > 1 or row_counts.get(n, 0) > 1)}


# =============================================================================
# 3. TRIPS & MILEAGE SERVICE
# =============================================================================

def get_trips_mileage_analytics(
    hours: int = 24,
    from_time: int = None,
    to_time: int = None,
    timeframe: str = None,
    from_date: str = None,
    to_date: str = None
) -> Dict[str, Any]:
    from app.utils.datetime_helpers import get_timeframe_timestamps, ROLLING_TIMEFRAMES
    if timeframe or from_date or from_time is not None:
        eff_from, eff_to = get_timeframe_timestamps(
            timeframe=timeframe,
            from_time=from_time,
            to_time=to_time,
            from_date=from_date,
            to_date=to_date
        )
    else:
        now_ts = int(time.time())
        eff_from, eff_to = now_ts - hours * 3600, now_ts

    eff_hours = max(1, int((eff_to - eff_from) / 3600))
    log_data_window("/api/fleet/trips", eff_from, eff_to)

    # Same precedence as get_timeframe_timestamps: explicit bounds, then dates, then timeframe (default 'today')
    if from_time is not None and to_time is not None:
        window_spec = f"exact-{eff_from}-{eff_to}"
    elif from_date:
        window_spec = f"dates-{from_date}-{to_date or from_date}"
    elif timeframe or from_time is not None:
        tf = (timeframe or "today").lower().strip()
        # A calendar window's start moves at UAE midnight: a result cached before it never answers after it
        window_spec = f"tf-{tf}" if tf in ROLLING_TIMEFRAMES else f"tf-{tf}-{eff_from}"
    else:
        window_spec = f"hours-{hours or 24}"

    cache_key = f"trips_mileage_v3_{window_spec}_uae"
    cached = get_cached(cache_key, ttl_seconds=180)
    log_cache_state("/api/fleet/trips", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups = get_vehicle_groups(sid)
    vehicles = get_all_vehicles(sid)
    calc_data = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)
    calc_map = {item.get("i"): item for item in calc_data}

    # Primary: Fast authoritative Wialon Group Trips Report (Template 5)
    group_report = None
    try:
        group_report = execute_wialon_group_trips_report(
            sid=sid,
            from_time_utc=eff_from,
            to_time_utc=eff_to,
            group_id=GROUP_ID_ALL_UNITS
        )
    except Exception as e:
        logging.warning("Group trips report failed, falling back to unit analysis: %s", e)
        group_report = None

    report_incomplete = False
    unmatched_rows: List[str] = []
    if group_report is not None:
        rep_by_name = {item["vehicle_name"]: item for item in group_report}
        # Report rows carry only the unit name: a name shared by several units (or rows) cannot be attributed
        ambiguous_names = _ambiguous_report_names(fleet, group_report)
        # A row naming no fleet unit is left out of the totals and listed, so the total is flagged partial
        fleet_names = {v["vehicle_name"] for v in fleet}
        unmatched_rows = sorted({item["vehicle_name"] for item in group_report if item["vehicle_name"] not in fleet_names})
        report_incomplete = bool(unmatched_rows) or any(
            item.get("incomplete_fields") or item.get("column_warnings") or item.get("unlocated_fields")
            for item in group_report
        ) or any(
            # A fleet unit with no report row, or one whose name cannot be attributed, is unknown: not cached either
            v["vehicle_name"] in ambiguous_names or v["vehicle_name"] not in rep_by_name
            for v in fleet
        )
        vehicle_trips = []
        for v_record in fleet:
            vname = v_record["vehicle_name"]
            vid = v_record["vehicle_id"]
            calc = calc_map.get(vid, {})
            curr_hw_mileage = (calc.get("mileage") or {}).get("value")
            rep_item = {} if vname in ambiguous_names else rep_by_name.get(vname, {})
            # An unreadable or guessed-column mileage cell, an unattributable row, or no row at all is unknown
            # (None), never 0 km: the group report emits a row with "0.00 km" for every member without activity
            mileage_unknown = (
                vname in ambiguous_names
                or vname not in rep_by_name
                or "mileage_km" in ((rep_item.get("incomplete_fields") or []) + (rep_item.get("unlocated_fields") or []))
            )
            raw_mileage = float(rep_item.get("mileage_km", 0.0)) if rep_item else 0.0
            avg_spd = _parse_speed_kmh(rep_item.get("avg_speed")) if rep_item else None
            max_spd = _parse_speed_kmh(rep_item.get("max_speed")) if rep_item else 0.0

            vehicle_trips.append({
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group": v_record.get("group", "General Fleet"),
                # Wialon's own trip count for the unit (its detailed sub-rows); unknown (None) when the row is
                # missing, unattributable or its count unreadable, never inferred from mileage
                "trip_count": rep_item.get("trip_count"),
                "gps_mileage_km": None if mileage_unknown else round(raw_mileage, 2),
                "hardware_mileage_km": None,
                "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage else None,
                "max_speed_kmh": round(max_spd or 0.0, 1),
                "avg_speed_kmh": round(avg_spd, 1) if avg_spd is not None else None,
                "trips": []
            })
    else:
        def _process_trips(v_record):
            vid = v_record["vehicle_id"]
            calc = calc_map.get(vid, {})
            curr_hw_mileage = (calc.get("mileage") or {}).get("value")

            try:
                native_rep = execute_wialon_unit_trips_report(sid, vid, eff_from, eff_to)
            except WialonAPIError as exc:
                # The Trips Report is the only mileage source: without it mileage and trip count are unknown
                # (None), never re-derived from raw messages, which measure differently
                logging.warning("Wialon trips report failed for vehicle %s; mileage unknown: %s", vid, exc)
                return {
                    "vehicle_id": vid,
                    "vehicle_name": v_record["vehicle_name"],
                    "group": v_record.get("group", "General Fleet"),
                    "trip_count": None,
                    "gps_mileage_km": None,
                    "hardware_mileage_km": None,
                    "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage else None,
                    "max_speed_kmh": 0.0,
                    "avg_speed_kmh": None,
                    "trips": [],
                }

            w_trips = native_rep["trips"]
            # Unknown when any trip distance is unreadable or the Mileage column was not identified
            dist_unknown = "distance_km" in (
                (native_rep.get("unlocated_fields") or []) + (native_rep.get("incomplete_fields") or [])
            )
            gps_dist = None if dist_unknown else round(sum(t.get("distance_km", 0.0) for t in w_trips), 2)
            max_spd = 0.0
            for t in w_trips:
                ms_str = str(t.get("max_speed", "")).replace("km/h", "").strip()
                try:
                    ms_val = float(ms_str)
                    if ms_val > max_spd:
                        max_spd = ms_val
                except ValueError:
                    pass

            speed_samples = []
            for trip in w_trips:
                trip_speed = _parse_speed_kmh(trip.get("average_speed"))
                if trip_speed is not None:
                    moving_seconds = parse_duration_to_seconds(trip.get("moving_duration"))
                    speed_samples.append((trip_speed, moving_seconds))
            weighted_seconds = sum(seconds for _, seconds in speed_samples)
            avg_spd = (
                round(sum(speed * seconds for speed, seconds in speed_samples) / weighted_seconds, 1)
                if weighted_seconds > 0
                else round(sum(speed for speed, _ in speed_samples) / len(speed_samples), 1)
                if speed_samples
                else None
            )

            return {
                "vehicle_id": vid,
                "vehicle_name": v_record["vehicle_name"],
                "group": v_record.get("group", "General Fleet"),
                "trip_count": len(w_trips),
                "gps_mileage_km": gps_dist,
                "hardware_mileage_km": None,
                "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage else None,
                "max_speed_kmh": round(max_spd, 1),
                "avg_speed_kmh": avg_spd,
                "trips": [
                    {
                        "start_time": t.get("start_time"),
                        "end_time": t.get("end_time"),
                        "duration_minutes": t.get("duration_minutes"),
                        "moving_duration": t.get("moving_duration_str"),
                        "distance_km": t.get("distance_km"),
                        "max_speed": t.get("max_speed"),
                        "average_speed": t.get("average_speed"),
                        "start_location": t.get("start_location"),
                        "end_location": t.get("end_location"),
                    }
                    for t in w_trips
                ]
            }

        with ThreadPoolExecutor(max_workers=3) as executor:
            vehicle_trips = list(map_with_request_context(executor, _process_trips, fleet))
        # A failed or partial unit report leaves that unit unknown: not cached, so the next request re-reads it
        report_incomplete = any(v["gps_mileage_km"] is None or v["trip_count"] is None for v in vehicle_trips)

    # Trip metrics need every vehicle's real trip list; one unknown count makes the fleet totals unknown.
    trips_known = all(v["trip_count"] is not None for v in vehicle_trips)
    total_trips = sum(v["trip_count"] for v in vehicle_trips) if trips_known else None
    # Unknown (None) distances are left out of the total and listed, never counted as 0 km
    known_dists = [v["gps_mileage_km"] for v in vehicle_trips if v["gps_mileage_km"] is not None]
    distance_unknown_vehicles = sorted(v["vehicle_name"] for v in vehicle_trips if v["gps_mileage_km"] is None)
    # No known vehicle distance at all (reports failed or gave no usable mileage) is unknown, not 0 km
    total_gps_dist = round(sum(known_dists), 2) if known_dists or not vehicle_trips else None
    available_hw_distances = [v["hardware_mileage_km"] for v in vehicle_trips if v.get("hardware_mileage_km") is not None]
    total_hw_dist = round(sum(available_hw_distances), 2) if available_hw_distances else None

    def _to_float(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    all_single_trips = [t for v in vehicle_trips for t in v.get("trips", [])] if trips_known else []
    # No trips (or unknown trips) means no average or longest trip: None, never 0 or a vehicle's daily mileage
    # A distance unknown for any vehicle makes the fleet average and longest trip unknown, not a partial figure
    avg_trip_dist = round(total_gps_dist / total_trips, 2) if total_trips and not distance_unknown_vehicles else None
    avg_trip_dur  = round(sum(_to_float(t.get("duration_minutes")) for t in all_single_trips) / len(all_single_trips), 1) if all_single_trips else None
    longest_trip_dist = None if distance_unknown_vehicles else max((t["distance_km"] for t in all_single_trips), default=None)
    fleet_max_spd = max((v["max_speed_kmh"] for v in vehicle_trips), default=0)

    # Mileage by group
    by_group: Dict[str, Dict[str, Any]] = {}
    for vt in vehicle_trips:
        g = vt["group"]
        if g not in by_group:
            by_group[g] = {"group": g, "trips": 0, "distance_km": 0.0, "vehicle_count": 0}
        if by_group[g]["trips"] is not None:
            by_group[g]["trips"] = None if vt["trip_count"] is None else by_group[g]["trips"] + vt["trip_count"]
        if by_group[g]["distance_km"] is not None:
            by_group[g]["distance_km"] = None if vt["gps_mileage_km"] is None else round(by_group[g]["distance_km"] + vt["gps_mileage_km"], 2)
        by_group[g]["vehicle_count"] += 1

    result = {
        "period_hours": eff_hours,
        "from_time": eff_from,
        "to_time": eff_to,
        "summary": {
            "total_trips": total_trips,
            "total_distance_km": total_gps_dist,
            "total_distance_partial": bool(distance_unknown_vehicles or unmatched_rows),
            "distance_unknown_vehicles": distance_unknown_vehicles,
            "unmatched_report_rows": unmatched_rows,
            "total_hardware_distance_km": total_hw_dist,
            "average_trip_distance_km": avg_trip_dist,
            "average_trip_duration_minutes": avg_trip_dur,
            "longest_trip_km": longest_trip_dist,
            "fleet_max_speed_kmh": fleet_max_spd,
        },
        "by_group": list(by_group.values()),
        # Unknown (None) distances sort after all known ones
        "vehicles": sorted(vehicle_trips, key=lambda x: (x["gps_mileage_km"] is not None, x["gps_mileage_km"] or 0), reverse=True)
    }

    # Do not cache a report with unreadable cells or unlocated columns; the next request re-reads it
    if not report_incomplete:
        set_cached(cache_key, result)
    return result


# =============================================================================
# 4. SAFETY ANALYTICS SERVICE
# =============================================================================

def get_safety_analytics(hours: int = 168, use_cache: bool = True) -> Dict[str, Any]:
    window_to = int(time.time())
    log_data_window("/api/fleet/safety", window_to - hours * 3600, window_to)
    cache_key = f"safety_analytics_{hours}"
    cached = get_cached(cache_key, ttl_seconds=180) if use_cache else None
    log_cache_state("/api/fleet/safety", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups = get_vehicle_groups(sid)
    vehicles = get_all_vehicles(sid)
    calc_data = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)

    def _fetch_safety_events(v_record):
        vid = v_record["vehicle_id"]
        vname = v_record["vehicle_name"]
        grp = v_record.get("group", "General Fleet")
        events = []

        sens_cfg = next((u.get("sens") or {} for u in vehicles if u.get("id") == vid), {})
        accel_sid = next((str(k) for k, sc in sens_cfg.items() if sc and "accel" in str(sc.get("n", "")).lower() and ("event" in str(sc.get("n", "")).lower() or sc.get("p") == "io_253")), None)
        # Brake needs an event qualifier like accel/corner: a plain brake-pedal input is not harsh braking
        brake_sid = next((str(k) for k, sc in sens_cfg.items() if sc and ("brake" in str(sc.get("n", "")).lower() or "braking" in str(sc.get("n", "")).lower()) and ("event" in str(sc.get("n", "")).lower() or "harsh" in str(sc.get("n", "")).lower())), None)
        corner_sid = next((str(k) for k, sc in sens_cfg.items() if sc and "corner" in str(sc.get("n", "")).lower() and ("event" in str(sc.get("n", "")).lower() or sc.get("p") == "io_253")), None)
        gforce_sid = next((str(k) for k, sc in sens_cfg.items() if sc and ("g-force" in str(sc.get("n", "")).lower() or "green value" in str(sc.get("n", "")).lower() or sc.get("p") == "io_254")), None)

        try:
            raw_msgs = get_vehicle_history(sid, vid, hours=hours)
            raw_msgs_sorted = sorted(raw_msgs, key=lambda m: m.get("t", 0))

            prev_active_event = None

            for m in raw_msgs_sorted:
                p = m.get("p") or {}
                sens = m.get("sensors") or {}
                pos = m.get("pos") or {}
                ts = m.get("t")

                # Check parameter or dynamic sensor flags
                raw_io_253 = p.get("io_253")
                accel_flag = (sens.get(accel_sid, {}).get("value") == 1) if is_valid_sensor_value(sens.get(accel_sid, {}).get("value")) else False
                brake_flag = (sens.get(brake_sid, {}).get("value") == 1) if is_valid_sensor_value(sens.get(brake_sid, {}).get("value")) else False
                corner_flag = (sens.get(corner_sid, {}).get("value") == 1) if is_valid_sensor_value(sens.get(corner_sid, {}).get("value")) else False

                detected_type = None
                if raw_io_253 == 1 or accel_flag:
                    detected_type = "Harsh Acceleration"
                elif raw_io_253 == 2 or brake_flag:
                    detected_type = "Harsh Braking"
                elif raw_io_253 == 3 or corner_flag:
                    detected_type = "Harsh Cornering"

                if detected_type:
                    # Rising-edge transition: only emit event when moving from None/diff to active
                    if prev_active_event != detected_type:
                        raw_g = p.get("io_254") or sens.get(gforce_sid, {}).get("value")
                        g_val = None
                        if is_valid_sensor_value(raw_g) and isinstance(raw_g, (int, float)):
                            parsed_g = float(raw_g) / 100.0 if float(raw_g) > 5 else float(raw_g)
                            if 0.15 <= parsed_g <= 3.0:
                                g_val = round(parsed_g, 2)

                        events.append({
                            "id": f"{vid}-{ts}-{detected_type[:4]}",
                            "vehicle_id": vid,
                            "vehicle_name": vname,
                            "group": grp,
                            "event_type": detected_type,
                            "timestamp": ts,
                            "datetime_str": to_iso_uae(ts) if ts else "—",
                            "speed_kmh": (pos.get("s") or 0),
                            "g_force": g_val,
                            "location": {
                                "latitude": pos.get("y"),
                                "longitude": pos.get("x")
                            }
                        })
                    prev_active_event = detected_type
                else:
                    prev_active_event = None

            return events
        except Exception as exc:
            logging.error("Wialon safety data failed for vehicle %s: %s", vid, exc)
            raise

    with ThreadPoolExecutor(max_workers=3) as executor:
        nested_events = list(map_with_request_context(executor, _fetch_safety_events, fleet))

    all_events = [ev for sub in nested_events for ev in sub]
    all_events.sort(key=lambda x: x["timestamp"], reverse=True)

    braking_count = sum(1 for e in all_events if e["event_type"] == "Harsh Braking")
    accel_count   = sum(1 for e in all_events if e["event_type"] == "Harsh Acceleration")
    corner_count  = sum(1 for e in all_events if e["event_type"] == "Harsh Cornering")

    # Group & Vehicle distribution
    by_veh: Dict[str, Dict[str, Any]] = {}
    by_grp: Dict[str, Dict[str, Any]] = {}

    for ev in all_events:
        v = ev["vehicle_name"]
        g = ev["group"]
        if v not in by_veh:
            by_veh[v] = {"vehicle_name": v, "vehicle_id": ev["vehicle_id"], "group": g, "total": 0, "braking": 0, "accel": 0, "cornering": 0}
        by_veh[v]["total"] += 1
        if ev["event_type"] == "Harsh Braking": by_veh[v]["braking"] += 1
        elif ev["event_type"] == "Harsh Acceleration": by_veh[v]["accel"] += 1
        elif ev["event_type"] == "Harsh Cornering": by_veh[v]["cornering"] += 1

        if g not in by_grp:
            by_grp[g] = {"group": g, "total": 0, "braking": 0, "accel": 0, "cornering": 0}
        by_grp[g]["total"] += 1
        if ev["event_type"] == "Harsh Braking": by_grp[g]["braking"] += 1
        elif ev["event_type"] == "Harsh Acceleration": by_grp[g]["accel"] += 1
        elif ev["event_type"] == "Harsh Cornering": by_grp[g]["cornering"] += 1

    result = {
        "period_hours": hours,
        "summary": {
            "total_events": len(all_events),
            "harsh_braking": braking_count,
            "harsh_acceleration": accel_count,
            "harsh_cornering": corner_count,
            "vehicles_affected": len(by_veh)
        },
        "by_vehicle": sorted(list(by_veh.values()), key=lambda x: x["total"], reverse=True),
        "by_group": sorted(list(by_grp.values()), key=lambda x: x["total"], reverse=True),
        "events": all_events[:100]  # Cap for snappy JSON response
    }

    set_cached(cache_key, result)
    return result


# =============================================================================
# 5. FLEET ALERTS SERVICE
# =============================================================================

# A fresh entry is served for _ALERTS_TTL_SECONDS (unchanged). Once it is _ALERTS_REFRESH_AHEAD_SECONDS
# old, a cache hit starts one background rebuild so polling clients get a new entry before the old one
# expires instead of a cold scan. Foreground and background builds share _alerts_build_lock, so at most
# one alerts scan (health + 3-worker safety fan-out) runs at a time. A failed rebuild caches nothing:
# the last good entry stays until its own 180 s freshness runs out.
_ALERTS_CACHE_KEY = "fleet_alerts_analytics"
_ALERTS_TTL_SECONDS = 180
_ALERTS_REFRESH_AHEAD_SECONDS = 120
_ALERTS_REFRESH_RETRY_SECONDS = 30
_alerts_build_lock = threading.Lock()
_alerts_refresh_failed_at = 0.0
_alerts_build_seq = 0  # foreground builds finished, success or failure
_alerts_last_error: Optional[BaseException] = None


def get_fleet_alerts_analytics() -> Dict[str, Any]:
    entry = _analytics_cache.get(_ALERTS_CACHE_KEY)
    age = time.time() - entry["time"] if entry else None
    fresh = age is not None and age < _ALERTS_TTL_SECONDS
    log_cache_state("/api/fleet/alerts", fresh)
    if fresh:
        if age >= _ALERTS_REFRESH_AHEAD_SECONDS:
            _start_alerts_background_refresh()
        return entry["data"]

    # Cold: wait for any build already running (foreground or background) rather than starting another
    global _alerts_build_seq, _alerts_last_error
    seen_seq = _alerts_build_seq
    with _alerts_build_lock:
        cached = get_cached(_ALERTS_CACHE_KEY, ttl_seconds=_ALERTS_TTL_SECONDS)
        if cached:
            return cached
        # Callers queued behind a foreground build that failed share its error instead of repeating the scan
        if _alerts_last_error is not None and _alerts_build_seq != seen_seq:
            raise _alerts_last_error
        try:
            result = _build_fleet_alerts(use_cache=True)
        except BaseException as exc:
            _alerts_last_error = exc
            raise
        else:
            _alerts_last_error = None
        finally:
            _alerts_build_seq += 1
        return result


def prewarm_fleet_alerts() -> bool:
    """
    Start the alerts build in the background (e.g. at startup) so the first visitor does not wait for the
    cold scan (~30 s measured: 24 h of sensor-calculated messages for every unit). Same build, lock and
    TTL as a refresh; a request arriving meanwhile waits for it, and a failure caches nothing.
    """
    return _start_alerts_background_refresh()


def _start_alerts_background_refresh() -> bool:
    """Start one background rebuild unless a build is running or the last refresh failed recently."""
    if time.monotonic() - _alerts_refresh_failed_at < _ALERTS_REFRESH_RETRY_SECONDS:
        return False
    if not _alerts_build_lock.acquire(blocking=False):
        return False
    try:
        threading.Thread(target=_run_alerts_background_refresh, name="fleet-alerts-refresh", daemon=True).start()
    except BaseException:
        _alerts_build_lock.release()
        raise
    return True


def _run_alerts_background_refresh() -> None:
    """Runs with _alerts_build_lock held; releases it when done."""
    global _alerts_refresh_failed_at
    request_trace_id.set(f"bg-{new_trace_id()}")
    started = time.perf_counter()
    try:
        # Bypass the health/safety caches so the new entry's timestamp reflects data read now
        _build_fleet_alerts(use_cache=False)
        _alerts_refresh_failed_at = 0.0
        logging.debug("PERF alerts background refresh request_id=%s state=ok duration_ms=%.1f",
                     request_trace_id.get(), (time.perf_counter() - started) * 1000)
    except Exception:
        _alerts_refresh_failed_at = time.monotonic()
        logging.exception("PERF alerts background refresh request_id=%s state=failed duration_ms=%.1f; "
                          "keeping the last cached alerts", request_trace_id.get(), (time.perf_counter() - started) * 1000)
    finally:
        _alerts_build_lock.release()


def _build_fleet_alerts(use_cache: bool) -> Dict[str, Any]:
    # Without the cache bypass the call signatures stay exactly as before
    part_kwargs = {} if use_cache else {"use_cache": False}

    # Health makes only sequential Wialon calls, so overlapping it with the safety scan adds one
    # concurrent request at most; the safety fan-out stays at 3 workers (5+ hits LIMIT api_concurrent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        health_future = executor.submit(copy_context().run, get_fleet_health_analytics, **part_kwargs)
        safety_future = executor.submit(copy_context().run, get_safety_analytics, 24, **part_kwargs)
        health = health_future.result()
        safety = safety_future.result()

    alerts = []
    alert_counter = 1

    # 1. Critical & Warning Battery Alerts
    for v in health["vehicles"]:
        b_status = v["battery"]["status"]
        v_volt = v["battery"]["voltage"]
        if b_status == "CRITICAL":
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "CRITICAL",
                "alert_type": "BATTERY_CRITICAL",
                "title": f"Critical Battery Voltage: {v_volt}V",
                "message": f"{v['vehicle_name']} has dangerously low battery level ({v_volt}V). Risk of vehicle shutdown.",
                "value": f"{v_volt}V",
                "timestamp": v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1
        elif b_status == "WARNING":
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "WARNING",
                "alert_type": "BATTERY_LOW",
                "title": f"Low Battery Warning: {v_volt}V",
                "message": f"{v['vehicle_name']} battery voltage is below recommended threshold ({v_volt}V).",
                "value": f"{v_volt}V",
                "timestamp": v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1

    # 2. Connectivity & Stale Telemetry Alerts
    for v in health["vehicles"]:
        c_status = v["connectivity"]["status"]
        sec = v["connectivity"]["seconds_since_update"]
        if c_status == "OFFLINE":
            hours_off = (sec // 3600) if sec is not None else "—"
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "CRITICAL",
                "alert_type": "DEVICE_OFFLINE",
                "title": f"Device Offline ({hours_off}h ago)",
                "message": f"No telemetry received from {v['vehicle_name']} for {hours_off} hours.",
                "value": f"{hours_off}h offline",
                "timestamp": v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1
        elif c_status == "DELAYED":
            mins_off = (sec // 60) if sec is not None else "—"
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "WARNING",
                "alert_type": "TELEMETRY_DELAYED",
                "title": f"Communication Delayed ({mins_off}m ago)",
                "message": f"Tracking data for {v['vehicle_name']} is delayed by {mins_off} minutes.",
                "value": f"{mins_off}m delay",
                "timestamp": v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1

    # 3. GPS Weak Signal Alerts
    for v in health["vehicles"]:
        if v["gps"]["status"] == "WEAK":
            sats = v["gps"]["satellites"] or 0
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "WARNING",
                "alert_type": "GPS_WEAK",
                "title": f"Weak GPS Fix ({sats} satellites)",
                "message": f"{v['vehicle_name']} has degraded GPS accuracy with only {sats} satellites locked.",
                "value": f"{sats} sats",
                "timestamp": v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1

    # 4. Harsh Driving Safety Alerts
    for ev in safety.get("events", [])[:15]:
        g_force = ev.get("g_force")
        sev = "CRITICAL" if (g_force and g_force >= 0.7) else "WARNING"
        alerts.append({
            "id": f"alt-{alert_counter}",
            "vehicle_id": ev["vehicle_id"],
            "vehicle_name": ev["vehicle_name"],
            "group": ev["group"],
            "severity": sev,
            "alert_type": "HARSH_DRIVING",
            "title": f"{ev['event_type']} Detected",
            "message": f"{ev['vehicle_name']} registered a {ev['event_type']} event at {ev['speed_kmh']} km/h{f' ({g_force}G)' if g_force else ''}.",
            "value": f"{g_force}G" if g_force else f"{ev['speed_kmh']} km/h",
            "timestamp": ev["timestamp"],
            "location": ev["location"]
        })
        alert_counter += 1

    # Sort: CRITICAL first, then WARNING, then by timestamp
    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    alerts.sort(key=lambda a: (order.get(a["severity"], 9), -(a.get("timestamp") or 0)))

    critical_count = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    warning_count  = sum(1 for a in alerts if a["severity"] == "WARNING")
    info_count     = sum(1 for a in alerts if a["severity"] == "INFO")

    by_vehicle: Dict[str, int] = defaultdict(int)
    by_group: Dict[str, int] = defaultdict(int)
    for a in alerts:
        vid = str(a.get("vehicle_id") or "")
        if vid:
            by_vehicle[vid] += 1
        grp = str(a.get("group") or "").strip()
        if grp:
            by_group[grp] += 1

    result = {
        "total_alerts": len(alerts),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "by_vehicle": dict(by_vehicle),
        "by_group": dict(by_group),
        "alerts": alerts
    }

    set_cached(_ALERTS_CACHE_KEY, result)
    return result


# =============================================================================
# 6. DYNAMIC GROUP ANALYTICS SERVICE
# =============================================================================

def get_fleet_groups_analytics() -> Dict[str, Any]:
    cached = get_cached("fleet_groups_analytics", ttl_seconds=180)
    log_cache_state("/api/fleet/groups", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups = get_vehicle_groups(sid)
    vehicles = get_all_vehicles(sid)
    calc_data = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)

    fleet_by_id = {v["vehicle_id"]: v for v in fleet}
    trips_data = get_trips_mileage_analytics(hours=24)
    trips_by_veh = {v["vehicle_id"]: v for v in trips_data.get("vehicles", [])}
    safety_data = get_safety_analytics(hours=24)
    safety_by_grp = {g["group"]: g["total"] for g in safety_data.get("by_group", [])}

    groups_summary = []
    for g in raw_groups:
        gname = g.get("nm", "").strip()
        gid = g.get("id")
        if not gname or gname.lower() == "all unit":
            continue

        u_ids = g.get("u", [])
        g_vehicles = [fleet_by_id[uid] for uid in u_ids if uid in fleet_by_id]

        moving = sum(1 for v in g_vehicles if v.get("status") == "MOVING")
        idling = sum(1 for v in g_vehicles if v.get("status") == "IDLING")
        stopped = sum(1 for v in g_vehicles if v.get("status") == "STOPPED")
        delayed = sum(1 for v in g_vehicles if v.get("status") == "DELAYED")  # stale telemetry, not live
        offline = sum(1 for v in g_vehicles if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))

        distances = [trips_by_veh.get(v["vehicle_id"], {}).get("gps_mileage_km") for v in g_vehicles]
        # One unknown vehicle distance makes the group distance unknown (None), never a silent 0
        total_distance = None if any(d is None for d in distances) else sum(distances)
        trip_counts = [trips_by_veh.get(v["vehicle_id"], {}).get("trip_count") for v in g_vehicles]
        total_trips = None if any(c is None for c in trip_counts) else sum(trip_counts)
        total_safety = safety_by_grp.get(gname, 0)

        groups_summary.append({
            "group_id": gid,
            "group_name": gname,
            "total_vehicles": len(g_vehicles),
            "moving": moving,
            "idling": idling,
            "stopped": stopped,
            "delayed": delayed,
            "offline": offline,
            "distance_24h_km": round(total_distance, 1) if total_distance is not None else None,
            "trips_24h": total_trips,
            "safety_events_24h": total_safety,
            "vehicles": [
                {
                    "vehicle_id": v["vehicle_id"],
                    "vehicle_name": v["vehicle_name"],
                    "status": v["status"],
                    "connection_status": v.get("connection_status", "STABLE"),
                    "is_offline": v.get("is_offline", False),
                    "speed": v["speed"],
                    "ignition": v["ignition"],
                    "latitude": v["latitude"],
                    "longitude": v["longitude"]
                }
                for v in g_vehicles
            ]
        })

    result = {
        "count": len(groups_summary),
        "groups": sorted(groups_summary, key=lambda x: x["total_vehicles"], reverse=True)
    }

    # Built on a partial trips report: not cached, so the unknown distances are re-read on the next request
    if not trips_data.get("summary", {}).get("total_distance_partial"):
        set_cached("fleet_groups_analytics", result)
    return result


# =============================================================================
# 7. SINGLE VEHICLE DETAILS & ROUTE REPLAY
# =============================================================================

def get_single_vehicle_deep_analytics(vehicle_id: int, hours: int = 24) -> Dict[str, Any]:
    cache_key = f"vehicle_deep_{vehicle_id}_{hours}"
    cached = get_cached(cache_key, ttl_seconds=60)
    if cached:
        return cached

    sid = login()
    vehicles = get_all_vehicles(sid)
    target = next((u for u in vehicles if u.get("id") == vehicle_id), None)
    if not target:
        return {"found": False, "vehicle_id": vehicle_id}

    calc_data = get_calculated_data(sid, [target])
    fleet = get_vehicle_live_data(sid, [target], calc_data)
    v_record = fleet[0] if fleet else {}

    health = evaluate_vehicle_health(v_record, raw_unit=target, calc_data=calc_data[0] if calc_data else None)

    # Historical messages for trips and route replay points
    sens = target.get("sens") or {}
    raw_msgs = get_vehicle_history(sid, vehicle_id, hours=hours)
    telemetry = normalize_wialon_messages(raw_msgs, configured_sensors=sens)
    activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)

    trips = activity.get("trips", [])
    idles = activity.get("idle_periods", [])

    total_dist = sum(t.get("distance", 0) for t in trips)
    total_drive_sec = sum(t.get("duration", 0) for t in trips)
    total_idle_sec = sum(i.get("duration", 0) for i in idles)

    # Route points for map playback (downsampled if over 300 points for smooth performance)
    route_points = []
    step = max(1, len(telemetry) // 250)
    for idx, p in enumerate(telemetry):
        if idx % step == 0 or idx == len(telemetry) - 1:
            loc = p.get("location") or {}
            st = p.get("status") or {}
            route_points.append({
                "timestamp": p.get("timestamp"),
                "datetime": to_iso_uae(p.get("timestamp", 0)) if p.get("timestamp") else "",
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
                "speed": round(float(st.get("speed") or 0), 1),
                "state": st.get("state", "STOPPED"),
            })

    result = {
        "found": True,
        "vehicle_id": vehicle_id,
        "vehicle_name": target.get("nm", f"Vehicle {vehicle_id}"),
        "group": v_record.get("group", "General Fleet"),
        "status": v_record.get("status", "STOPPED"),
        "connection_status": v_record.get("connection_status", "STABLE"),
        "is_offline": v_record.get("is_offline", False),
        "speed": v_record.get("speed", 0),
        "ignition": v_record.get("ignition", False),
        "location": v_record.get("location", {}),
        "health": health,
        "metrics_24h": {
            # Segmented from raw messages for the route view; not the Wialon Trips Report mileage used elsewhere
            "distance_km": round(total_dist, 2),
            "distance_source": "raw_message_segmentation",
            "driving_minutes": round(total_drive_sec / 60, 1),
            "idle_minutes": round(total_idle_sec / 60, 1),
            "trips_count": len(trips),
            "idle_episodes": len(idles),
            "engine_hours": round((calc_data[0].get("engine_hours") or {}).get("value", 0), 1) if calc_data else None,
            "cumulative_odometer_km": round((calc_data[0].get("mileage") or {}).get("value", 0), 1) if calc_data else None,
        },
        "trips": [
            {
                "start_time": t.get("start_time"),
                "end_time": t.get("end_time"),
                "duration_minutes": round(t.get("duration", 0) / 60, 1),
                "distance_km": round(t.get("distance", 0), 2),
                "max_speed": t.get("max_speed", 0),
                "start_location": t.get("start_location"),
                "end_location": t.get("end_location"),
            }
            for t in trips
        ],
        "route_points": route_points
    }

    set_cached(cache_key, result)
    return result
