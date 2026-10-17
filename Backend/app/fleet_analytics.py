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
import time
from concurrent.futures import ThreadPoolExecutor
from app.utils.datetime_helpers import to_iso_uae, now_timestamp
from app.performance_diagnostics import log_cache_state, log_data_window, map_with_request_context, request_trace_id
from collections import defaultdict
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
from app.utils.status_config import CONN_DELAYED_SECONDS

# ── Simple in-memory cache with TTL ──────────────────────────────────────────
_analytics_cache: Dict[str, Dict[str, Any]] = {}

def get_cached(key: str, ttl_seconds: int = 120) -> Optional[Any]:
    entry = _analytics_cache.get(key)
    if entry and (time.time() - entry["time"]) < ttl_seconds:
        return entry["data"]
    return None

def set_cached(key: str, data: Any):
    _analytics_cache[key] = {"data": data, "time": time.time()}


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
    "CONN_STABLE_SECONDS": int(_env_float("CONN_STABLE_SECONDS", 600)),
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
    prms = (raw_unit or {}).get("lmsg", {}).get("p") or {}
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
    last_msg_ts = (raw_unit or {}).get("lmsg", {}).get("t") or pos_data.get("t")
    now_ts = int(time.time())

    conn_status = "STABLE"
    seconds_since_update = 0
    if last_msg_ts:
        seconds_since_update = max(0, now_ts - int(last_msg_ts))
        if seconds_since_update > HEALTH_THRESHOLDS["CONN_DELAYED_SECONDS"]:
            conn_status = "OFFLINE"
        elif seconds_since_update > HEALTH_THRESHOLDS["CONN_STABLE_SECONDS"]:
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

def get_fleet_health_analytics() -> Dict[str, Any]:
    cached = get_cached("fleet_health_analytics", ttl_seconds=180)
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
        unit_results = []
        total_sec = hours * 3600
        for v_record in fleet:
            vname = v_record["vehicle_name"]
            vid = v_record["vehicle_id"]
            uv = engine_by_name.get(vname, {})
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
                "trip_count": 1 if driving_sec > 0 else 0
            })
    else:
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
            except Exception:
                logging.exception("Wialon utilization data failed for vehicle %s", vid)
                raise

        with ThreadPoolExecutor(max_workers=8) as executor:
            unit_results = list(map_with_request_context(executor, _process_unit_utilization, fleet))

    total_driving_sec = sum(u["driving_seconds"] for u in unit_results)
    total_idle_sec    = sum(u["idle_seconds"] for u in unit_results)
    total_engine_sec  = sum(u["engine_on_seconds"] for u in unit_results)
    total_fleet_time  = len(unit_results) * hours * 3600

    fleet_util_pct = round((total_driving_sec / total_fleet_time) * 100, 1) if total_fleet_time > 0 else 0
    fleet_ratio    = round(total_driving_sec / max(1, total_idle_sec), 2)

    result = {
        "hours": hours,
        "fleet_utilization_pct": fleet_util_pct,
        "total_driving_hours": round(total_driving_sec / 3600, 1),
        "total_idle_hours": round(total_idle_sec / 3600, 1),
        "total_engine_on_hours": round(total_engine_sec / 3600, 1),
        "driving_to_idle_ratio": fleet_ratio,
        "vehicles": sorted(unit_results, key=lambda x: x["utilization_percentage"], reverse=True)
    }

    set_cached(cache_key, result)
    return result


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
    from app.utils.datetime_helpers import get_timeframe_timestamps
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

    if from_date:
        window_spec = f"dates-{from_date}-{to_date or from_date}"
    elif timeframe:
        window_spec = f"tf-{timeframe.lower().strip()}"
    elif from_time is not None and to_time is not None:
        window_spec = f"exact-{from_time}-{to_time}"
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

    if group_report is not None:
        rep_by_name = {item["vehicle_name"]: item for item in group_report}
        vehicle_trips = []
        for v_record in fleet:
            vname = v_record["vehicle_name"]
            vid = v_record["vehicle_id"]
            calc = calc_map.get(vid, {})
            curr_hw_mileage = (calc.get("mileage") or {}).get("value")
            rep_item = rep_by_name.get(vname, {})
            raw_mileage = float(rep_item.get("mileage_km", 0.0)) if rep_item else 0.0
            avg_spd = _parse_speed_kmh(rep_item.get("avg_speed")) if rep_item else None
            max_spd = _parse_speed_kmh(rep_item.get("max_speed")) if rep_item else 0.0

            vehicle_trips.append({
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group": v_record.get("group", "General Fleet"),
                "trip_count": 1 if raw_mileage > 0 else 0,
                "gps_mileage_km": round(raw_mileage, 2),
                "hardware_mileage_km": None,
                "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage else None,
                "max_speed_kmh": round(max_spd or 0.0, 1),
                "avg_speed_kmh": round(avg_spd, 1) if avg_spd is not None else None,
                "trips": []
            })
    else:
        def _process_trips(v_record):
            vid = v_record["vehicle_id"]
            sens = next((u.get("sens") or {} for u in vehicles if u.get("id") == vid), {})
            calc = calc_map.get(vid, {})
            curr_hw_mileage = (calc.get("mileage") or {}).get("value")
            fallback_started = None
            fallback_reason = "native_report_empty_or_missing"

            try:
                try:
                    native_rep = execute_wialon_unit_trips_report(sid, vid, eff_from, eff_to)
                except WialonAPIError:
                    fallback_reason = "native_report_error"
                    native_rep = None
                if native_rep and "trips" in native_rep:
                    w_trips = native_rep["trips"]
                    gps_dist = round(sum(t.get("distance_km", 0.0) for t in w_trips), 2)
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

                fallback_started = time.perf_counter()
                logging.info(
                    "PERF trips fallback start request_id=%s reason=%s",
                    request_trace_id.get(), fallback_reason,
                )
                raw_msgs = get_vehicle_history(sid, vid, hours=eff_hours, from_time=eff_from, to_time=eff_to)
                telemetry = normalize_wialon_messages(raw_msgs, configured_sensors=sens)
                activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)

                trips = activity.get("trips", [])
                gps_dist = round(sum(t.get("distance", 0) for t in trips), 2)

                max_spd = max((t.get("max_speed", 0) for t in trips), default=0)
                trip_duration_seconds = sum(t.get("duration", 0) for t in trips)
                avg_spd = round(gps_dist * 3600 / trip_duration_seconds, 1) if trip_duration_seconds > 0 else None

                logging.info(
                    "PERF trips fallback end request_id=%s duration_ms=%.1f history_messages=%s",
                    request_trace_id.get(), (time.perf_counter() - fallback_started) * 1000, len(raw_msgs),
                )

                return {
                    "vehicle_id": vid,
                    "vehicle_name": v_record["vehicle_name"],
                    "group": v_record.get("group", "General Fleet"),
                    "trip_count": len(trips),
                    "gps_mileage_km": gps_dist,
                    "hardware_mileage_km": None,
                    "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage else None,
                    "max_speed_kmh": max_spd,
                    "avg_speed_kmh": avg_spd,
                    "trips": [
                        {
                            "start_time": t.get("start_time"),
                            "end_time": t.get("end_time"),
                            "duration_minutes": round(t.get("duration", 0) / 60, 1),
                            "distance_km": round(t.get("distance", 0), 2),
                            "max_speed": t.get("max_speed"),
                            "start_location": t.get("start_location"),
                            "end_location": t.get("end_location"),
                        }
                        for t in trips
                    ]
                }
            except Exception:
                if fallback_started is not None:
                    logging.info(
                        "PERF trips fallback failed request_id=%s duration_ms=%.1f",
                        request_trace_id.get(), (time.perf_counter() - fallback_started) * 1000,
                    )
                logging.exception("Wialon trip data failed for vehicle %s", vid)
                raise

        with ThreadPoolExecutor(max_workers=3) as executor:
            vehicle_trips = list(map_with_request_context(executor, _process_trips, fleet))

    total_trips = sum(v["trip_count"] for v in vehicle_trips)
    total_gps_dist = round(sum(v["gps_mileage_km"] for v in vehicle_trips), 2)
    available_hw_distances = [v["hardware_mileage_km"] for v in vehicle_trips if v.get("hardware_mileage_km") is not None]
    total_hw_dist = round(sum(available_hw_distances), 2) if available_hw_distances else None

    def _to_float(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    all_single_trips = [t for v in vehicle_trips for t in v.get("trips", [])]
    avg_trip_dist = round(total_gps_dist / total_trips, 2) if total_trips > 0 else 0
    avg_trip_dur  = round(sum(_to_float(t.get("duration_minutes")) for t in all_single_trips) / len(all_single_trips), 1) if all_single_trips else 0
    longest_trip_dist = max((t["distance_km"] for t in all_single_trips), default=(max((v["gps_mileage_km"] for v in vehicle_trips), default=0)))
    fleet_max_spd = max((v["max_speed_kmh"] for v in vehicle_trips), default=0)

    # Mileage by group
    by_group: Dict[str, Dict[str, Any]] = {}
    for vt in vehicle_trips:
        g = vt["group"]
        if g not in by_group:
            by_group[g] = {"group": g, "trips": 0, "distance_km": 0.0, "vehicle_count": 0}
        by_group[g]["trips"] += vt["trip_count"]
        by_group[g]["distance_km"] = round(by_group[g]["distance_km"] + vt["gps_mileage_km"], 2)
        by_group[g]["vehicle_count"] += 1

    result = {
        "period_hours": eff_hours,
        "from_time": eff_from,
        "to_time": eff_to,
        "summary": {
            "total_trips": total_trips,
            "total_distance_km": total_gps_dist,
            "total_hardware_distance_km": total_hw_dist,
            "average_trip_distance_km": avg_trip_dist,
            "average_trip_duration_minutes": avg_trip_dur,
            "longest_trip_km": longest_trip_dist,
            "fleet_max_speed_kmh": fleet_max_spd,
        },
        "by_group": list(by_group.values()),
        "vehicles": sorted(vehicle_trips, key=lambda x: x["gps_mileage_km"], reverse=True)
    }

    set_cached(cache_key, result)
    return result


# =============================================================================
# 4. SAFETY ANALYTICS SERVICE
# =============================================================================

def get_safety_analytics(hours: int = 168) -> Dict[str, Any]:
    window_to = int(time.time())
    log_data_window("/api/fleet/safety", window_to - hours * 3600, window_to)
    cache_key = f"safety_analytics_{hours}"
    cached = get_cached(cache_key, ttl_seconds=180)
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
        brake_sid = next((str(k) for k, sc in sens_cfg.items() if sc and ("brake" in str(sc.get("n", "")).lower() or "braking" in str(sc.get("n", "")).lower())), None)
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
        except Exception:
            logging.exception("Wialon safety data failed for vehicle %s", vid)
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

def get_fleet_alerts_analytics() -> Dict[str, Any]:
    cached = get_cached("fleet_alerts_analytics", ttl_seconds=180)
    if cached:
        return cached

    health = get_fleet_health_analytics()
    safety = get_safety_analytics(hours=24)

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

    set_cached("fleet_alerts_analytics", result)
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
        offline = sum(1 for v in g_vehicles if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))

        total_distance = sum(trips_by_veh.get(v["vehicle_id"], {}).get("gps_mileage_km", 0.0) for v in g_vehicles)
        total_trips = sum(trips_by_veh.get(v["vehicle_id"], {}).get("trip_count", 0) for v in g_vehicles)
        total_safety = safety_by_grp.get(gname, 0)

        groups_summary.append({
            "group_id": gid,
            "group_name": gname,
            "total_vehicles": len(g_vehicles),
            "moving": moving,
            "idling": idling,
            "stopped": stopped,
            "offline": offline,
            "distance_24h_km": round(total_distance, 1),
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
            "distance_km": round(total_dist, 2),
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
