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
import functools
from app.utils.datetime_helpers import to_iso_uae, now_timestamp, closed_window_ttl
from app.performance_diagnostics import log_cache_state, log_data_window, map_with_request_context, new_trace_id, request_trace_id
from collections import Counter, defaultdict
from typing import Dict, List, Any, Optional

from app.wialon import (
    WIALON_LIMIT_MSGS_ACTIVITY, WialonAPIError, login, get_vehicle_history, get_vehicle_groups, history_truncated,
    seconds_since_limit_hit, wialon_error_code,
)
from app.wialon_reports import (
    execute_wialon_unit_trips_report,
    execute_wialon_group_trips_report,
    execute_wialon_group_engine_idle_report,
    parse_duration_to_seconds,
    GROUP_ID_ALL_UNITS,
    resolve_all_units_group_id,
)
from app.vehicles import get_all_vehicles, get_calculated_data, get_vehicle_live_data, get_sensor_by_name, parse_wialon_epoch
from app.normalize import normalize_wialon_messages, segment_vehicle_activity, is_valid_sensor_value, INVALID_WIALON_VALUE
from app.utils.status_config import CONN_DELAYED_SECONDS, CONN_STABLE_SECONDS
from app.utils.ttl_cache import InFlight, TTLCache

# ── Simple in-memory cache with TTL ──────────────────────────────────────────
# Bounded LRU (keys include client-chosen hours and windows). Entries are evicted after the longest
# ttl_seconds any caller passes to get_cached(); freshness is still checked per call.
_ANALYTICS_CACHE_RETENTION_SECONDS = 600
_analytics_cache = TTLCache(max_entries=128)

def get_cached(key: str, ttl_seconds: int = 120) -> Optional[Any]:
    entry = _analytics_cache.get(key)
    # An entry stored with its own (shorter) ttl is fresh only for that long, whatever the caller passes
    if entry and (time.time() - entry["time"]) < min(ttl_seconds, entry.get("ttl") or ttl_seconds):
        return entry["data"]
    return None

def set_cached(key: str, data: Any, ttl_seconds: Optional[int] = None):
    # Retained at least as long as the entry's own TTL (closed report windows outlive the default retention)
    _analytics_cache.set(key, {"data": data, "time": time.time(), "ttl": ttl_seconds},
                         max(_ANALYTICS_CACHE_RETENTION_SECONDS, ttl_seconds or 0))


# Concurrent calls of one analytics builder with the same arguments share a single run (cache check included) and
# its result or exception, so a cold cache is filled by one Wialon read instead of one per caller. Nothing is
# retained: caching stays with each builder's own rules.
_builds = InFlight()


def _coalesced(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        key = (func.__name__, args, tuple(sorted(kwargs.items())))
        return _builds.run(key, lambda: func(*args, **kwargs))
    return wrapper


def _remaining_ttl(key: str) -> float:
    """Seconds until the cached entry for key stops being fresh under its own TTL (0 when absent)."""
    entry = _analytics_cache.get(key)
    if not entry or not entry.get("ttl"):
        return 0.0
    return max(0.0, entry["ttl"] - (time.time() - entry["time"]))


# ── Shared fleet discovery ───────────────────────────────────────────────────
# Groups, unit list and calc_last read once for every analytics builder running within this many seconds (the
# alerts, groups and summary builds call several builders back to back). Callers treat the data as read-only.
FLEET_DISCOVERY_TTL_SECONDS = 5


def _fleet_discovery(sid: str):
    """(raw_groups, vehicles, calc_data), read from Wialon at most once per FLEET_DISCOVERY_TTL_SECONDS."""
    def read():
        hit = get_cached("fleet_discovery", ttl_seconds=FLEET_DISCOVERY_TTL_SECONDS)
        if hit:
            return hit
        raw_groups = get_vehicle_groups(sid)
        vehicles = get_all_vehicles(sid)
        calc_data = get_calculated_data(sid, vehicles)
        value = (raw_groups, vehicles, calc_data)
        set_cached("fleet_discovery", value, ttl_seconds=FLEET_DISCOVERY_TTL_SECONDS)
        return value
    return get_cached("fleet_discovery", ttl_seconds=FLEET_DISCOVERY_TTL_SECONDS) or _builds.run("fleet_discovery", read)


# ── Shared Group Trips snapshot ──────────────────────────────────────────────
# /api/dashboard/mileage (Distance) and /api/fleet/trips (Trips) read the same Wialon Group Trips report. For one
# report group and window they share one report execution: both results carry its window and retrieval time and
# expire with it, so the two KPIs always come from the same report run. Open windows: GROUP_TRIPS_SNAPSHOT_TTL_SECONDS
# (the Dashboard mileage freshness); closed windows: the closed-window TTL; rows with unreadable cells, guessed
# columns or column warnings: TRANSIENT_PARTIAL_TTL_SECONDS. A failure is never cached.
GROUP_TRIPS_SNAPSHOT_TTL_SECONDS = 60
# get_cached() bound for snapshots: each entry's own TTL (set below) decides its freshness
_ENTRY_TTL_DECIDES = 10 ** 9


def group_trips_snapshot(report_fn, sid: str, group_id, window_spec: str, from_ts: int, to_ts: int):
    """
    The shared Group Trips result for (group_id, window_spec) and the seconds it stays fresh:
    ({"rows", "from_time", "to_time", "retrieved_at"}, remaining_seconds). `report_fn` is
    execute_wialon_group_trips_report as imported by the caller; a falsy group_id is the account's 'All Unit' group,
    resolved by the report. Concurrent callers share one execution; raises what report_fn raises.
    """
    key = _group_trips_snapshot_key(group_id, window_spec)

    def read():
        snapshot = get_cached(key, ttl_seconds=_ENTRY_TTL_DECIDES)
        if snapshot is None:
            rows = report_fn(sid=sid, from_time_utc=from_ts, to_time_utc=to_ts, group_id=group_id)
            transient = any(r.get("incomplete_fields") or r.get("column_warnings") or r.get("unlocated_fields")
                            for r in rows or [])
            ttl = (TRANSIENT_PARTIAL_TTL_SECONDS if transient
                   else closed_window_ttl(to_ts, GROUP_TRIPS_SNAPSHOT_TTL_SECONDS))
            snapshot = {"rows": rows, "from_time": from_ts, "to_time": to_ts, "retrieved_at": now_timestamp()}
            set_cached(key, snapshot, ttl_seconds=ttl)
        return snapshot, _remaining_ttl(key)

    hit = get_cached(key, ttl_seconds=_ENTRY_TTL_DECIDES)
    return (hit, _remaining_ttl(key)) if hit else _builds.run(key, read)


def _group_trips_snapshot_key(group_id, window_spec: str) -> str:
    return f"group_trips_snapshot_{group_id or 'all'}_{window_spec}"


def shorten_group_trips_snapshot(group_id, window_spec: str) -> float:
    """
    A consumer found a possibly transient gap the rows alone do not show (a report-group member without a row): the
    snapshot is re-read TRANSIENT_PARTIAL_TTL_SECONDS after it was read, like that consumer's result. Returns the
    seconds it stays fresh.
    """
    key = _group_trips_snapshot_key(group_id, window_spec)
    entry = _analytics_cache.get(key)
    if entry and (entry.get("ttl") or 0) > TRANSIENT_PARTIAL_TTL_SECONDS:
        entry["ttl"] = TRANSIENT_PARTIAL_TTL_SECONDS
    return _remaining_ttl(key)


# A report result partial only through permanent gaps (a unit outside the report group, a name shared by several
# units, a row naming no unit) is cached like a complete one and keeps its partial flags; one partial through a
# possibly transient read problem (unreadable cell, guessed column, failed group report) only this long
TRANSIENT_PARTIAL_TTL_SECONDS = 30


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

    # Readings from this voltage up to the 24V floor below fit neither system: a drained 24V battery or an
    # overcharging 12V one. The system type is unknown, so the reading is never classified healthy.
    "BATTERY_UNCLASSIFIED_MIN": _env_float("BATTERY_UNCLASSIFIED_MIN", 15.0),
    "BATTERY_24V_MIN": _env_float("BATTERY_24V_MIN", 18.0),

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


def _finite_number(value: Any) -> Optional[float]:
    """A finite int/float (numeric strings accepted), else None; bools are not numbers here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


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
        if HEALTH_THRESHOLDS["BATTERY_UNCLASSIFIED_MIN"] <= voltage <= HEALTH_THRESHOLDS["BATTERY_24V_MIN"]:
            # Neither a healthy 12V nor a healthy 24V reading: system type unknown, flagged for review (never Optimal)
            system_type = "Unknown"
            battery_status = "WARNING"
            battery_level_label = f"Unclassified ({voltage:.1f}V, system voltage unknown)"
        elif voltage > HEALTH_THRESHOLDS["BATTERY_24V_MIN"]:
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
    # A malformed (non-dict) last message is treated as absent, never a crash
    raw_lmsg = (raw_unit or {}).get("lmsg")
    raw_lmsg = raw_lmsg if isinstance(raw_lmsg, dict) else {}
    prms = raw_lmsg.get("p") or {}
    if not prms and calc_data:
        prms = calc_data.get("prms") or {}
    pwr_int = prms.get("pwr_int")
    battery_charge = prms.get("battery_charge")

    # GPS Quality. A non-numeric satellite count or DOP is unknown, never a crash of the fleet response.
    loc = vehicle.get("location") or {}
    sats = _finite_number(loc.get("satellites"))
    hdop = _finite_number(prms.get("hdop"))
    pdop = _finite_number(prms.get("pdop"))

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
    # Last message time, else the position time (as before), parsed like the live view: missing or malformed is None
    # (OFFLINE, unknown age), never a crash of the fleet health, alerts or deep-analytics response
    last_msg_ts = parse_wialon_epoch(raw_lmsg.get("t"))
    if last_msg_ts is None and isinstance(pos_data, dict):
        last_msg_ts = parse_wialon_epoch(pos_data.get("t"))
    now_ts = int(time.time())

    conn_status = "STABLE"
    seconds_since_update = 0
    if last_msg_ts is not None:
        seconds_since_update = max(0, now_ts - last_msg_ts)
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

    # R2: last seen MOVING with a GPS fix older than CONN_STABLE_SECONDS although messages are recent (the live
    # record's position_stale). The satellites describe that old fix, so GPS quality is STALE, with the same -15 as a
    # delayed connection. Not applied when the connection is already DELAYED/OFFLINE (no double penalty); a unit last
    # seen STOPPED or IDLING is never position_stale (D3).
    if vehicle.get("position_stale") is True and conn_status == "STABLE":
        gps_status = "STALE"
        score -= 15

    health_score = max(0, min(100, score))
    # The battery value is the last one reported: not current once telemetry is delayed or offline
    battery_stale = voltage is not None and conn_status in ("DELAYED", "OFFLINE")

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
            "stale": battery_stale,
            "internal_voltage": round(_finite_number(pwr_int), 2) if _finite_number(pwr_int) is not None else None,
            "charge_percent": battery_charge if battery_charge is not None else None,
        },
        "gps": {
            "satellites": sats,
            "hdop": hdop,
            "pdop": pdop,
            "status": gps_status,
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "position_timestamp": vehicle.get("position_timestamp"),
            "seconds_since_position": vehicle.get("seconds_since_position"),
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

@_coalesced
def get_fleet_health_analytics(use_cache: bool = True) -> Dict[str, Any]:
    cached = get_cached("fleet_health_analytics", ttl_seconds=180) if use_cache else None
    log_cache_state("/api/fleet/health", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups, vehicles, calc_data = _fleet_discovery(sid)
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
        if ev["gps"]["status"] in ("WEAK", "STALE"):
            gps_warnings += 1
            has_issue = True
        if ev["connectivity"]["status"] in ["DELAYED", "OFFLINE"]:
            connectivity_warnings += 1
            has_issue = True

        if not has_issue and ev["health_score"] >= 80:
            healthy_vehicles += 1

    result = {
        # When this data was read from Wialon (UTC epoch seconds); a cached response keeps its original time
        "generated_at": now_timestamp(),
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

@_coalesced
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
    raw_groups, vehicles, calc_data = _fleet_discovery(sid)
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
        source = "wialon_group_engine_report"
        engine_by_name = {uv["vehicle_name"]: uv for uv in group_engine.get("vehicles", [])}
        ambiguous_names = _ambiguous_report_names(fleet, group_engine.get("vehicles", []))
        # A row naming no fleet unit is left out of the totals (as /api/fleet/trips): partial and not cached
        fleet_names = {v["vehicle_name"] for v in fleet}
        unmatched_rows = sorted({name for name in engine_by_name if name not in fleet_names})
        if unmatched_rows:
            logging.warning("Engine report rows name no fleet unit, left out of utilization: %s", unmatched_rows)
        # Unmatched rows are a permanent gap (cached, still partial); unreadable cells or guessed columns may be
        # transient and are re-read after TRANSIENT_PARTIAL_TTL_SECONDS
        transient_incomplete = bool(
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
            if missing_row or vname in ambiguous_names:
                # Unattributable or missing row: utilization is unknown, not 0
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
            # Each field is decided on its own: an unreadable (or guessed-column) cell makes only the values that
            # depend on it unknown (None), so one bad cell does not erase the unit's other valid measurements
            driving_sec = None if "in_motion_seconds" in unreadable else uv.get("in_motion_seconds", 0)
            idle_sec = None if "total_idle_seconds" in unreadable else uv.get("total_idle_seconds", 0)
            both_known = driving_sec is not None and idle_sec is not None
            if "engine_hours_seconds" in unreadable:
                engine_on_sec = None
            else:
                engine_on_sec = uv.get("engine_hours_seconds", 0) or ((driving_sec + idle_sec) if both_known else 0)
            stopped_sec = max(0, total_sec - (driving_sec + idle_sec)) if both_known else None
            util_pct = (round((driving_sec / total_sec) * 100, 1) if total_sec > 0 else 0) if driving_sec is not None else None
            drive_idle_ratio = round(driving_sec / max(1, idle_sec), 2) if both_known else None

            def _mins(sec):
                return round(sec / 60, 1) if sec is not None else None

            unit_results.append({
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group": v_record.get("group", "General Fleet"),
                "status": v_record.get("status", "STOPPED"),
                "connection_status": v_record.get("connection_status", "STABLE"),
                "is_offline": v_record.get("is_offline", False),
                "driving_minutes": _mins(driving_sec),
                "idle_minutes": _mins(idle_sec),
                "stopped_minutes": _mins(stopped_sec),
                "engine_on_minutes": _mins(engine_on_sec),
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
        # Fallback: segmented from raw messages, a different method from the engine report. Labelled as such and
        # never cached, so it cannot stand in for the authoritative report on later requests.
        source = "raw_message_fallback"
        transient_incomplete = True
        unmatched_rows = []

        def _unknown_utilization(v_record):
            return {
                "vehicle_id": v_record["vehicle_id"],
                "vehicle_name": v_record["vehicle_name"],
                "group": v_record.get("group", "General Fleet"),
                "status": v_record.get("status", "STOPPED"),
                "connection_status": v_record.get("connection_status", "STABLE"),
                "is_offline": v_record.get("is_offline", False),
                "driving_minutes": None, "idle_minutes": None, "stopped_minutes": None, "engine_on_minutes": None,
                "driving_seconds": None, "idle_seconds": None, "stopped_seconds": None, "engine_on_seconds": None,
                "utilization_percentage": None,
                "driving_to_idle_ratio": None,
                "trip_count": None,
            }

        def _process_unit_utilization(v_record):
            vid = v_record["vehicle_id"]
            sens = next((u.get("sens") or {} for u in vehicles if u.get("id") == vid), {})
            try:
                raw_msgs = get_vehicle_history(sid, vid, hours=hours)
                if history_truncated(raw_msgs):
                    # A partial message list would understate driving and idle time: unknown, not a lower bound
                    return _unknown_utilization(v_record)
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
                # One unit's failure leaves that unit unknown; the fleet response still loads (and is not cached)
                logging.warning("Wialon utilization data failed for vehicle %s; unknown: %s", vid, exc)
                return _unknown_utilization(v_record)

        with ThreadPoolExecutor(max_workers=8) as executor:
            unit_results = list(map_with_request_context(executor, _process_unit_utilization, fleet))

    # Fleet totals cover vehicles with a known value for that field only; unknown ones are listed, never counted
    # as 0. Every vehicle unknown for a field: there is no known basis for that total (None, not 0).
    def _known(field):
        return [u[field] for u in unit_results if u[field] is not None]

    def _field_unknown(field):
        return sorted(u["vehicle_name"] for u in unit_results if u[field] is None)

    def _hours_total(field):
        values = _known(field)
        return round(sum(values) / 3600, 1) if values or not unit_results else None

    driving_known = _known("driving_seconds")
    total_driving_sec = sum(driving_known)
    total_fleet_time = len(driving_known) * hours * 3600
    fleet_util_pct = (round((total_driving_sec / total_fleet_time) * 100, 1) if total_fleet_time > 0 else 0) \
        if driving_known or not unit_results else None
    ratio_units = [u for u in unit_results if u["driving_seconds"] is not None and u["idle_seconds"] is not None]
    fleet_ratio = round(sum(u["driving_seconds"] for u in ratio_units) / max(1, sum(u["idle_seconds"] for u in ratio_units)), 2) \
        if ratio_units or not unit_results else None
    unknown_vehicles = sorted({u["vehicle_name"] for u in unit_results
                               if u["driving_seconds"] is None or u["idle_seconds"] is None or u["engine_on_seconds"] is None})

    result = {
        "hours": hours,
        # wialon_group_engine_report (authoritative) or raw_message_fallback (segmented messages; partial, uncached)
        "source": source,
        "fleet_utilization_pct": fleet_util_pct,
        "total_driving_hours": _hours_total("driving_seconds"),
        "total_idle_hours": _hours_total("idle_seconds"),
        "total_engine_on_hours": _hours_total("engine_on_seconds"),
        "driving_to_idle_ratio": fleet_ratio,
        "partial": bool(unknown_vehicles or unmatched_rows or source != "wialon_group_engine_report"),
        # Vehicles with any unknown field; the per-field lists say which totals leave them out
        "unknown_vehicles": unknown_vehicles,
        "driving_unknown_vehicles": _field_unknown("driving_seconds"),
        "idle_unknown_vehicles": _field_unknown("idle_seconds"),
        "engine_on_unknown_vehicles": _field_unknown("engine_on_seconds"),
        # Unknown (None) utilization sorts after all known values
        "vehicles": sorted(unit_results, key=lambda x: (x["utilization_percentage"] is not None, x["utilization_percentage"] or 0), reverse=True)
    }

    # The raw-message fallback is never cached. From the report: permanently partial (unmatched rows) uses the normal
    # TTL, transiently partial (unreadable cells, guessed columns) TRANSIENT_PARTIAL_TTL_SECONDS; flags are kept.
    if source == "wialon_group_engine_report":
        set_cached(cache_key, result, ttl_seconds=TRANSIENT_PARTIAL_TTL_SECONDS if transient_incomplete else None)
    return result


# Harsh-driving alerts listed on the Alerts page (newest first); the alert totals count every event
HARSH_ALERTS_DISPLAY_LIMIT = 15


def harsh_event_severity(ev: Dict[str, Any]) -> str:
    """Alert severity of a harsh-driving event: CRITICAL at 0.7 G or more, else WARNING."""
    g_force = ev.get("g_force")
    return "CRITICAL" if (g_force and g_force >= 0.7) else "WARNING"


def _ambiguous_report_names(fleet: List[Dict[str, Any]], report_rows: List[Dict[str, Any]]) -> set:
    """Names that cannot be attributed to a single unit: shared by several fleet units or several report rows."""
    fleet_counts = Counter(v.get("vehicle_name") for v in fleet)
    row_counts = Counter(r.get("vehicle_name") for r in report_rows)
    return {n for n in set(fleet_counts) | set(row_counts) if n and (fleet_counts.get(n, 0) > 1 or row_counts.get(n, 0) > 1)}


# =============================================================================
# 3. TRIPS & MILEAGE SERVICE
# =============================================================================

@_coalesced
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
    # A window that ended before today (UAE), e.g. yesterday, is a closed report: cached longer
    trips_ttl = closed_window_ttl(eff_to, 180)
    cached = get_cached(cache_key, ttl_seconds=trips_ttl)
    log_cache_state("/api/fleet/trips", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups, vehicles, calc_data = _fleet_discovery(sid)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)
    calc_map = {item.get("i"): item for item in calc_data}

    # Primary: Fast authoritative Wialon Group Trips Report (Template 5), shared with /api/dashboard/mileage for the
    # same report group and window (group_trips_snapshot): the result then reports the snapshot's window and retrieval
    # time and expires with it. An hours-N window has no mileage counterpart (mileage takes timeframes, dates and
    # exact bounds only), so it runs its own report and keeps the normal trips TTL.
    group_report = None
    retrieved_at = None
    snapshot_fresh_for = None
    try:
        if window_spec.startswith("hours-"):
            group_report = execute_wialon_group_trips_report(
                sid=sid,
                from_time_utc=eff_from,
                to_time_utc=eff_to,
                group_id=GROUP_ID_ALL_UNITS
            )
        else:
            try:
                snapshot_group_id = resolve_all_units_group_id(sid, raw_groups)
            except WialonAPIError:
                snapshot_group_id = GROUP_ID_ALL_UNITS  # resolved by the report itself, as before
            snapshot, snapshot_fresh_for = group_trips_snapshot(
                execute_wialon_group_trips_report, sid, snapshot_group_id, window_spec, eff_from, eff_to)
            group_report = snapshot["rows"]
            eff_from, eff_to, retrieved_at = snapshot["from_time"], snapshot["to_time"], snapshot["retrieved_at"]
    except Exception as e:
        logging.warning("Group trips report failed, falling back to unit analysis: %s", e)
        group_report = None

    report_incomplete = False
    unmatched_rows: List[str] = []
    # wialon_group_trips_report, or unit_trips_reports (one Trips Report per unit when the group report failed)
    source = "wialon_group_trips_report" if group_report is not None else "unit_trips_reports"
    if group_report is not None:
        rep_by_name = {item["vehicle_name"]: item for item in group_report}
        # Report rows carry only the unit name: a name shared by several units (or rows) cannot be attributed
        ambiguous_names = _ambiguous_report_names(fleet, group_report)
        # A row naming no fleet unit is left out of the totals and listed, so the total is flagged partial
        fleet_names = {v["vehicle_name"] for v in fleet}
        unmatched_rows = sorted({item["vehicle_name"] for item in group_report if item["vehicle_name"] not in fleet_names})
        # Unmatched rows, ambiguous names and fleet units outside the report group are permanent gaps (cached, still
        # partial); unreadable cells, guessed columns or a report-group member without a row are re-read sooner
        try:
            report_group_id = resolve_all_units_group_id(sid, raw_groups)
            report_group = next((g for g in raw_groups if g.get("id") == report_group_id), None)
        except WialonAPIError:
            report_group = None
        members = set(report_group.get("u") or []) if report_group is not None else set()
        report_incomplete = any(
            item.get("incomplete_fields") or item.get("column_warnings") or item.get("unlocated_fields")
            for item in group_report
        ) or any(v["vehicle_id"] in members and v["vehicle_name"] not in rep_by_name and v["vehicle_name"] not in ambiguous_names
                 for v in fleet)
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
            # Unknown (unreadable or no row) maximum speed stays None, never 0
            max_spd = _parse_speed_kmh(rep_item.get("max_speed")) if rep_item else None

            vehicle_trips.append({
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group": v_record.get("group", "General Fleet"),
                # Wialon's own trip count for the unit (its detailed sub-rows); unknown (None) when the row is
                # missing, unattributable or its count unreadable, never inferred from mileage
                "trip_count": rep_item.get("trip_count"),
                "gps_mileage_km": None if mileage_unknown else round(raw_mileage, 2),
                "hardware_mileage_km": None,
                # A genuine 0 km odometer reading is kept; only a missing reading is None
                "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage is not None else None,
                "max_speed_kmh": round(max_spd, 1) if max_spd is not None else None,
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
                    "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage is not None else None,
                    "max_speed_kmh": None,
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
                "cumulative_odometer_km": round(float(curr_hw_mileage), 1) if curr_hw_mileage is not None else None,
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

    # The trip total sums the known counts and lists the vehicles whose count is unknown (as distance does), never
    # counting them as 0; no known count at all is unknown (None). Per-trip metrics still need every trip list.
    trips_known = all(v["trip_count"] is not None for v in vehicle_trips)
    known_trip_counts = [v["trip_count"] for v in vehicle_trips if v["trip_count"] is not None]
    trip_count_unknown_vehicles = sorted(v["vehicle_name"] for v in vehicle_trips if v["trip_count"] is None)
    total_trips = sum(known_trip_counts) if known_trip_counts or not vehicle_trips else None
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
    avg_trip_dist = round(total_gps_dist / total_trips, 2) if total_trips and trips_known and not distance_unknown_vehicles else None
    avg_trip_dur  = round(sum(_to_float(t.get("duration_minutes")) for t in all_single_trips) / len(all_single_trips), 1) if all_single_trips else None
    longest_trip_dist = None if distance_unknown_vehicles else max((t["distance_km"] for t in all_single_trips), default=None)
    # Maximum over the known values; None when no vehicle's maximum speed is known
    fleet_max_spd = max((v["max_speed_kmh"] for v in vehicle_trips if v["max_speed_kmh"] is not None), default=None)

    # Mileage by group (primary group): known values are summed and the vehicles with an unknown value counted, as for
    # the fleet totals. A group whose every vehicle is unknown for a field has an unknown (None) total.
    by_group: Dict[str, Dict[str, Any]] = {}
    for vt in vehicle_trips:
        g = vt["group"]
        if g not in by_group:
            by_group[g] = {"group": g, "trips": 0, "distance_km": 0.0, "vehicle_count": 0,
                           "trips_unknown_vehicles": 0, "distance_unknown_vehicles": 0}
        bg = by_group[g]
        if vt["trip_count"] is None:
            bg["trips_unknown_vehicles"] += 1
        else:
            bg["trips"] += vt["trip_count"]
        if vt["gps_mileage_km"] is None:
            bg["distance_unknown_vehicles"] += 1
        else:
            bg["distance_km"] = round(bg["distance_km"] + vt["gps_mileage_km"], 2)
        bg["vehicle_count"] += 1
    for bg in by_group.values():
        bg["trips_partial"] = bg["trips_unknown_vehicles"] > 0
        bg["distance_partial"] = bg["distance_unknown_vehicles"] > 0
        if bg["trips_unknown_vehicles"] == bg["vehicle_count"]:
            bg["trips"] = None
        if bg["distance_unknown_vehicles"] == bg["vehicle_count"]:
            bg["distance_km"] = None

    result = {
        "period_hours": eff_hours,
        "from_time": eff_from,
        "to_time": eff_to,
        # When this result was read from Wialon (UTC epoch seconds); a cached response keeps its original time
        "retrieved_at": retrieved_at or now_timestamp(),
        "source": source,
        "summary": {
            "total_trips": total_trips,
            "total_trips_partial": bool(trip_count_unknown_vehicles),
            "trip_count_unknown_vehicles": trip_count_unknown_vehicles,
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

    result_ttl = TRANSIENT_PARTIAL_TTL_SECONDS if report_incomplete else trips_ttl
    if snapshot_fresh_for is not None and report_incomplete:
        snapshot_fresh_for = shorten_group_trips_snapshot(snapshot_group_id, window_spec)
    if snapshot_fresh_for is not None:
        # From the shared snapshot: never outlives it, so Trips and Distance move to the next report run together
        result_ttl = min(result_ttl, snapshot_fresh_for)
    if result_ttl > 0:
        set_cached(cache_key, result, ttl_seconds=result_ttl)
    return result


# =============================================================================
# 4. SAFETY ANALYTICS SERVICE
# =============================================================================

@_coalesced
def get_safety_analytics(hours: int = 168, use_cache: bool = True) -> Dict[str, Any]:
    window_to = int(time.time())
    log_data_window("/api/fleet/safety", window_to - hours * 3600, window_to)
    cache_key = f"safety_analytics_{hours}"
    cached = get_cached(cache_key, ttl_seconds=180) if use_cache else None
    log_cache_state("/api/fleet/safety", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups, vehicles, calc_data = _fleet_discovery(sid)
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
        # A harsh-driving source: a configured harsh sensor, or the io_253 parameter in the unit's messages
        has_source = bool(accel_sid or brake_sid or corner_sid)

        if limit_hit.is_set():
            # Wialon already answered 1004 (LIMIT msgs_activity) in this scan: the remaining units are unknown, not
            # loaded one by one into the same limit
            return {"vehicle": v_record, "events": [], "failed": True, "truncated": False, "has_source": has_source}
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
                if "io_253" in p:
                    has_source = True
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

            return {"vehicle": v_record, "events": events, "failed": False,
                    "truncated": history_truncated(raw_msgs), "has_source": has_source}
        except Exception as exc:
            # One unit's history failure leaves that unit's events unknown; the fleet result is partial, not failed
            logging.warning("Wialon safety data failed for vehicle %s; its events are unknown: %s", vid, exc)
            if wialon_error_code(exc) == WIALON_LIMIT_MSGS_ACTIVITY:
                limit_hit.set()
            return {"vehicle": v_record, "events": [], "failed": True, "truncated": False, "has_source": has_source}

    limit_hit = threading.Event()
    with ThreadPoolExecutor(max_workers=3) as executor:
        per_vehicle = list(map_with_request_context(executor, _fetch_safety_events, fleet))

    all_events = [ev for res in per_vehicle for ev in res["events"]]
    failed_vehicles = sorted(res["vehicle"]["vehicle_name"] for res in per_vehicle if res["failed"])
    truncated_vehicles = sorted(res["vehicle"]["vehicle_name"] for res in per_vehicle if res["truncated"])
    # Units without any harsh-driving source report no events because they cannot, not because they drove safely
    without_source = sorted(res["vehicle"]["vehicle_name"] for res in per_vehicle
                            if not res["failed"] and not res["has_source"])
    partial = bool(failed_vehicles or truncated_vehicles)
    all_events.sort(key=lambda x: x["timestamp"], reverse=True)

    severity_counts = Counter(harsh_event_severity(e) for e in all_events)
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
            # Counts cover the units that could be read; `partial` lists the ones that could not
            "total_events": len(all_events),
            "harsh_braking": braking_count,
            "harsh_acceleration": accel_count,
            "harsh_cornering": corner_count,
            "vehicles_affected": len(by_veh)
        },
        "generated_at": now_timestamp(),
        "partial": partial,
        "failed_vehicles": failed_vehicles,
        "truncated_vehicles": truncated_vehicles,
        "vehicles_without_safety_source": len(without_source),
        "vehicles_without_safety_source_names": without_source[:50],
        "by_vehicle": sorted(list(by_veh.values()), key=lambda x: x["total"], reverse=True),
        "by_group": sorted(list(by_grp.values()), key=lambda x: x["total"], reverse=True),
        # Alert severity of every event (independent of the `events` cap below)
        "event_severity_counts": {"CRITICAL": severity_counts.get("CRITICAL", 0), "WARNING": severity_counts.get("WARNING", 0)},
        "events": all_events[:100]  # Cap for snappy JSON response
    }

    # A partial result (failed or truncated units, still flagged) is kept only TRANSIENT_PARTIAL_TTL_SECONDS, so
    # concurrent and back-to-back callers do not each rescan every unit's history
    set_cached(cache_key, result, ttl_seconds=TRANSIENT_PARTIAL_TTL_SECONDS if partial else None)
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
# No background rebuild for this long after any Wialon 1004 (LIMIT msgs_activity) reply
_ALERTS_LIMIT_BACKOFF_SECONDS = 300
# A build whose safety scan was partial is served for at most this long (see _build_fleet_alerts)
_ALERTS_PARTIAL_TTL_SECONDS = 30
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
    """Start one background rebuild unless a build is running, the last refresh failed recently, or Wialon recently
    answered 1004 (LIMIT msgs_activity) to any caller: the scan loads 24 h of messages for every unit, so it waits
    rather than competing with the reports that hit the limit. Foreground (cold) loads are not affected."""
    if time.monotonic() - _alerts_refresh_failed_at < _ALERTS_REFRESH_RETRY_SECONDS:
        return False
    since_limit = seconds_since_limit_hit()
    if since_limit is not None and since_limit < _ALERTS_LIMIT_BACKOFF_SECONDS:
        logging.info("Fleet alerts background refresh skipped: Wialon message limit (1004) hit %.0f s ago", since_limit)
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
        last_reported = " (last reported)" if v["battery"].get("stale") else ""
        if b_status == "WARNING" and v["battery"].get("system_type") == "Unknown":
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "WARNING",
                "alert_type": "BATTERY_UNCLASSIFIED",
                "title": f"Unclassified Battery Voltage: {v_volt}V{last_reported}",
                "message": f"{v['vehicle_name']} reports {v_volt}V{last_reported}, which fits neither a healthy 12V nor a "
                           f"healthy 24V system. Check the battery and the vehicle's system voltage.",
                "value": f"{v_volt}V",
                "timestamp": v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1
        elif b_status == "CRITICAL":
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "CRITICAL",
                "alert_type": "BATTERY_CRITICAL",
                "title": f"Critical Battery Voltage: {v_volt}V{last_reported}",
                "message": f"{v['vehicle_name']} has dangerously low battery level ({v_volt}V{last_reported}). Risk of vehicle shutdown.",
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
                "title": f"Low Battery Warning: {v_volt}V{last_reported}",
                "message": f"{v['vehicle_name']} battery voltage is below recommended threshold ({v_volt}V{last_reported}).",
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
        elif v["gps"]["status"] == "STALE":
            pos_sec = v["gps"].get("seconds_since_position")
            mins_old = (pos_sec // 60) if pos_sec is not None else "—"
            alerts.append({
                "id": f"alt-{alert_counter}",
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v["group"],
                "severity": "WARNING",
                "alert_type": "GPS_POSITION_STALE",
                "title": f"GPS Position Stale ({mins_old}m old)",
                "message": f"{v['vehicle_name']} was last seen moving, but its last GPS fix is {mins_old} minutes old "
                           f"although messages are recent. Its position and speed are not current.",
                "value": f"{mins_old}m old fix",
                "timestamp": v["gps"].get("position_timestamp") or v["connectivity"]["last_message_ts"],
                "location": {"latitude": v["gps"]["latitude"], "longitude": v["gps"]["longitude"]}
            })
            alert_counter += 1

    # 4. Harsh Driving Safety Alerts: only the newest HARSH_ALERTS_DISPLAY_LIMIT are listed; the totals below count
    # every event of the safety scan
    for ev in safety.get("events", [])[:HARSH_ALERTS_DISPLAY_LIMIT]:
        g_force = ev.get("g_force")
        sev = harsh_event_severity(ev)
        alerts.append({
            "id": f"alt-{alert_counter}",
            "vehicle_id": ev["vehicle_id"],
            "vehicle_name": ev["vehicle_name"],
            "group": ev["group"],
            "severity": sev,
            "alert_type": "HARSH_DRIVING",
            "event_id": ev.get("id"),
            "title": f"{ev['event_type']} Detected",
            "message": f"{ev['vehicle_name']} registered a {ev['event_type']} event at {ev['speed_kmh']} km/h{f' ({g_force}G)' if g_force else ''}.",
            "value": f"{g_force}G" if g_force else f"{ev['speed_kmh']} km/h",
            "timestamp": ev["timestamp"],
            "location": ev["location"]
        })
        alert_counter += 1

    # Stable ids (the sequential alt-N ids changed on every rebuild): one alert per condition and vehicle, plus the
    # event id for harsh driving. A repeated key gets a numeric suffix, so ids stay unique in every response.
    seen_ids: Dict[str, int] = {}
    for a in alerts:
        base = f"{a['alert_type']}-{a.get('vehicle_id')}"
        if a.get("event_id"):
            base = f"{base}-{a['event_id']}"
        n = seen_ids.get(base, 0)
        seen_ids[base] = n + 1
        a["id"] = base if n == 0 else f"{base}-{n + 1}"

    # Sort: CRITICAL first, then WARNING, then by timestamp
    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    alerts.sort(key=lambda a: (order.get(a["severity"], 9), -(a.get("timestamp") or 0)))

    # Counts: every listed non-harsh alert plus every harsh-driving event of the scan, not only the listed ones.
    # A cached safety result without the severity counts falls back to the events it carries.
    other_alerts = [a for a in alerts if a["alert_type"] != "HARSH_DRIVING"]
    harsh_listed = len(alerts) - len(other_alerts)
    safety_events = safety.get("events", [])
    harsh_total = (safety.get("summary") or {}).get("total_events", len(safety_events))
    harsh_severity = safety.get("event_severity_counts") or dict(Counter(harsh_event_severity(e) for e in safety_events))
    critical_count = sum(1 for a in other_alerts if a["severity"] == "CRITICAL") + harsh_severity.get("CRITICAL", 0)
    warning_count  = sum(1 for a in other_alerts if a["severity"] == "WARNING") + harsh_severity.get("WARNING", 0)
    info_count     = sum(1 for a in other_alerts if a["severity"] == "INFO")

    by_vehicle: Dict[str, int] = defaultdict(int)
    by_group: Dict[str, int] = defaultdict(int)
    for a in other_alerts:
        vid = str(a.get("vehicle_id") or "")
        if vid:
            by_vehicle[vid] += 1
        grp = str(a.get("group") or "").strip()
        if grp:
            by_group[grp] += 1
    # Harsh-driving counts per vehicle and group over every event (safety by_vehicle / by_group); a safety result
    # without them falls back to the events it carries
    if "by_vehicle" in safety or "by_group" in safety:
        harsh_by_vehicle = [(sv.get("vehicle_id"), sv.get("total", 0)) for sv in safety.get("by_vehicle") or []]
        harsh_by_group = [(sg.get("group"), sg.get("total", 0)) for sg in safety.get("by_group") or []]
    else:
        harsh_by_vehicle = [(e.get("vehicle_id"), 1) for e in safety_events]
        harsh_by_group = [(e.get("group"), 1) for e in safety_events]
    for vid, n in harsh_by_vehicle:
        if str(vid or ""):
            by_vehicle[str(vid)] += n
    for grp, n in harsh_by_group:
        if str(grp or "").strip():
            by_group[str(grp).strip()] += n

    safety_partial = bool(safety.get("partial"))
    result = {
        # When the underlying health/safety data was read (UTC epoch seconds); `timestamp` on an alert is the
        # vehicle's last reported message (or GPS fix), not when the condition began
        "generated_at": min(filter(None, [health.get("generated_at"), safety.get("generated_at")]), default=now_timestamp()),
        "total_alerts": len(other_alerts) + harsh_total,
        # The list below holds displayed_alerts of total_alerts: harsh-driving events beyond the newest
        # HARSH_ALERTS_DISPLAY_LIMIT are counted but not listed
        "displayed_alerts": len(alerts),
        "alerts_capped": harsh_total > harsh_listed,
        "harsh_driving_total": harsh_total,
        "harsh_driving_displayed": harsh_listed,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "by_vehicle": dict(by_vehicle),
        "by_group": dict(by_group),
        # Harsh-driving alerts are missing for these units (their history could not be read completely)
        "safety_partial": safety_partial,
        "safety_unavailable_vehicles": sorted(set(safety.get("failed_vehicles") or []) | set(safety.get("truncated_vehicles") or [])),
        "alerts": alerts
    }

    if safety_partial:
        # Kept only briefly: the entry counts as old enough that the next hit starts a background rebuild, and it
        # stops being served after _ALERTS_PARTIAL_TTL_SECONDS
        _analytics_cache.set(
            _ALERTS_CACHE_KEY,
            {"data": result, "time": time.time() - (_ALERTS_TTL_SECONDS - _ALERTS_PARTIAL_TTL_SECONDS)},
            _ANALYTICS_CACHE_RETENTION_SECONDS,
        )
    else:
        set_cached(_ALERTS_CACHE_KEY, result)
    return result


# =============================================================================
# 6. DYNAMIC GROUP ANALYTICS SERVICE
# =============================================================================

@_coalesced
def get_fleet_groups_analytics() -> Dict[str, Any]:
    cached = get_cached("fleet_groups_analytics", ttl_seconds=180)
    log_cache_state("/api/fleet/groups", cached is not None)
    if cached:
        return cached

    sid = login()
    raw_groups, vehicles, calc_data = _fleet_discovery(sid)
    fleet = get_vehicle_live_data(sid, vehicles, calc_data, groups=raw_groups)

    fleet_by_id = {v["vehicle_id"]: v for v in fleet}
    trips_data = get_trips_mileage_analytics(hours=24)
    trips_by_veh = {v["vehicle_id"]: v for v in trips_data.get("vehicles", [])}
    safety_data = get_safety_analytics(hours=24)
    safety_by_vehicle = {v["vehicle_id"]: v["total"] for v in safety_data.get("by_vehicle", [])}
    safety_unknown = set(safety_data.get("failed_vehicles") or []) | set(safety_data.get("truncated_vehicles") or [])

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
        delayed = sum(1 for v in g_vehicles if v.get("telemetry_stale"))  # last known status shown, telemetry not live (overlaps the counts above)
        offline = sum(1 for v in g_vehicles if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))

        # Known values are summed and the members with an unknown value counted (as for the fleet totals), never
        # counted as 0; a group whose every member is unknown has an unknown (None) total. Every member counts (a
        # vehicle in several groups counts in each).
        distances = [trips_by_veh.get(v["vehicle_id"], {}).get("gps_mileage_km") for v in g_vehicles]
        known_distances = [d for d in distances if d is not None]
        total_distance = sum(known_distances) if known_distances or not g_vehicles else None
        trip_counts = [trips_by_veh.get(v["vehicle_id"], {}).get("trip_count") for v in g_vehicles]
        known_trips = [c for c in trip_counts if c is not None]
        total_trips = sum(known_trips) if known_trips or not g_vehicles else None
        # A member's safety events are unknown when its history could not be read
        safety_known = [v for v in g_vehicles if v["vehicle_name"] not in safety_unknown]
        total_safety = sum(safety_by_vehicle.get(v["vehicle_id"], 0) for v in safety_known) \
            if safety_known or not g_vehicles else None

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
            "distance_unknown_vehicles": len(distances) - len(known_distances),
            "trips_24h": total_trips,
            "trips_unknown_vehicles": len(trip_counts) - len(known_trips),
            "safety_events_24h": total_safety,
            "safety_unknown_vehicles": len(g_vehicles) - len(safety_known),
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
        # Group-membership totals: a vehicle in several groups counts in each, so they do not add up to the fleet
        "totals_basis": "group_membership",
        "groups": sorted(groups_summary, key=lambda x: x["total_vehicles"], reverse=True)
    }

    # Built on a partial trips report or safety scan: not cached, so the unknown values are re-read next time
    if not trips_data.get("summary", {}).get("total_distance_partial") and not safety_data.get("partial"):
        set_cached("fleet_groups_analytics", result)
    return result


# =============================================================================
# 7. SINGLE VEHICLE DETAILS & ROUTE REPLAY
# =============================================================================

def _rounded_counter(calc_data: List[Dict[str, Any]], key: str) -> Optional[float]:
    if not calc_data or not isinstance(calc_data[0], dict):
        return None
    value = _finite_number((calc_data[0].get(key) or {}).get("value"))
    return round(value, 1) if value is not None else None


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
    history_partial = history_truncated(raw_msgs)
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
        # True when Wialon history could not be read completely: the segmented metrics and route are lower bounds
        "history_partial": history_partial,
        "metrics_24h": {
            # Segmented from raw messages for the route view; not the Wialon Trips Report mileage used elsewhere
            "distance_km": round(total_dist, 2),
            "distance_source": "raw_message_segmentation",
            "driving_minutes": round(total_drive_sec / 60, 1),
            "idle_minutes": round(total_idle_sec / 60, 1),
            "trips_count": len(trips),
            "idle_episodes": len(idles),
            # Unknown (None) when calc_last reports no counter or a non-numeric value, never 0
            "engine_hours": _rounded_counter(calc_data, "engine_hours"),
            "cumulative_odometer_km": _rounded_counter(calc_data, "mileage"),
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

    if not history_partial:
        set_cached(cache_key, result)
    return result
