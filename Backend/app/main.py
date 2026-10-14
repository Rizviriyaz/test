from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from app.utils.datetime_helpers import now_timestamp, to_iso_utc, get_timeframe_timestamps, UAE_TZ
import logging
import os
import time
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import List, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from app.auth import authenticate_access_token
from app.performance_diagnostics import log_cache_state, log_data_window, new_trace_id, request_trace_id, timed_calculation
from app.utils.status_config import CONN_DELAYED_SECONDS

from app.wialon import WialonAPIError, login, get_vehicle_history, get_vehicle_groups, audit_wialon_fleet
from app.wialon_reports import (
    execute_wialon_unit_trips_report,
    execute_wialon_group_trips_report,
    execute_wialon_unit_engine_report,
    execute_wialon_group_engine_idle_report,
    GROUP_ID_ALL_UNITS,
)
from app.normalize import (
    normalize_wialon_messages,
    segment_vehicle_activity,
    is_valid_sensor_value,
    INVALID_WIALON_VALUE
)

from app.vehicles import (
    get_all_vehicles,
    get_calculated_data,
    get_vehicle_live_data,
    get_sensor_by_name,
    idle_start_times,
)
from app.status import calculate_idle_duration
from app.fuel.routes import router as fuel_router



@asynccontextmanager
async def lifespan(app):
    # Login to Wialon on startup
    login(force=True)
    print("[OK] Wialon session established at startup")
    yield


app = FastAPI(
    title="Fleet Management API",
    description="Vehicle fleet monitoring backend",
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(fuel_router)

cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env.strip():
    allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
elif os.getenv("ENVIRONMENT", "development").lower() != "production":
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
else:
    allowed_origins = []

if "*" in allowed_origins:
    raise RuntimeError("CORS_ORIGINS must list explicit origins when credentialed authentication is enabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(WialonAPIError)
async def wialon_upstream_error_handler(_request: Request, exc: WialonAPIError):
    return JSONResponse(status_code=502, content={"detail": "Wialon upstream request failed", "reason": str(exc)})


@app.middleware("http")
async def require_supabase_auth(request: Request, call_next):
    """Protect every operational API route at the backend boundary."""
    tracked_path = request.url.path in {
        "/api/vehicles",
        "/api/dashboard/mileage",
        "/api/idle/fleet-analytics",
        "/api/fleet/health",
        "/api/fleet/safety",
        "/api/fleet/utilization",
        "/api/fleet/trips",
        "/api/fleet/groups",
        "/api/wialon/audit",
    }
    trace_id = new_trace_id() if tracked_path else "-"
    trace_token = request_trace_id.set(trace_id)
    request_started = time.perf_counter()
    if tracked_path:
        logging.info("PERF request start request_id=%s method=%s path=%s", trace_id, request.method, request.url.path)
    response = None
    try:
        response = await _authenticate_and_dispatch(request, call_next)
        return response
    finally:
        if tracked_path:
            status_code = response.status_code if response is not None else "exception"
            logging.info(
                "PERF request end request_id=%s path=%s status=%s total_ms=%.1f",
                trace_id, request.url.path, status_code, (time.perf_counter() - request_started) * 1000,
            )
        request_trace_id.reset(trace_token)


async def _authenticate_and_dispatch(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in {"/", "/health", "/api/health"}:
        return await call_next(request)
    if request.url.path.startswith("/api/"):
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return JSONResponse(status_code=401, content={"detail": "Bearer access token required"})
        try:
            request.state.user = await run_in_threadpool(authenticate_access_token, token.strip())
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


# ─── Simple in-memory cache ───
_cache = {}

def cached(key, ttl_seconds=300):
    """Return cached data if still fresh, otherwise None."""
    entry = _cache.get(key)
    if entry and (time.time() - entry["time"]) < ttl_seconds:
        return entry["data"]
    return None

def set_cache(key, data):
    _cache[key] = {"data": data, "time": time.time()}


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "ATS Fleet Management API",
        "version": "1.0.0"
    }


@app.get("/health")
@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": int(time.time()),
        "wialon_configured": bool(os.getenv("WIALON_TOKEN") and os.getenv("WIALON_API"))
    }


@app.get("/api/vehicles")
@timed_calculation("/api/vehicles")
def get_vehicles():

    try:

        # Login to Wialon
        sid = login()

        # Get all vehicles
        vehicles = get_all_vehicles(
            sid
        )

        # Get latest calculated data
        calculated_data = get_calculated_data(
            sid,
            vehicles
        )

        # Build final vehicle data
        fleet = get_vehicle_live_data(
            sid,
            vehicles,
            calculated_data
        )

        return {
            "count": len(fleet),
            "vehicles": fleet
        }

    except Exception as e:
        print("ERROR IN /api/vehicles:", repr(e))
        traceback.print_exc()
        raise HTTPException(
            status_code=502 if isinstance(e, WialonAPIError) else 500,
            detail=str(e)
        )


@app.get("/api/vehicles/{vehicle_id}")
def get_vehicle_details(vehicle_id: int):
    sid = login()

    vehicles = get_all_vehicles(sid)
    calculated_data = get_calculated_data(sid, vehicles)

    fleet = get_vehicle_live_data(
        sid,
        vehicles,
        calculated_data
    )

    vehicle = next(
        (
            item
            for item in fleet
            if item["vehicle_id"] == vehicle_id
        ),
        None
    )

    if not vehicle:
        return {
            "vehicle_id": vehicle_id,
            "found": False,
            "vehicle": None
        }

    return {
        "vehicle_id": vehicle_id,
        "found": True,
        "vehicle": vehicle
    }


@app.get("/api/wialon/audit")
@timed_calculation("/api/wialon/audit")
def get_wialon_audit():
    try:
        sid = login()
        audit_data = audit_wialon_fleet(sid)
        return audit_data
    except Exception as e:
        print("ERROR IN /api/wialon/audit:", repr(e))
        traceback.print_exc()
        raise HTTPException(
            status_code=502 if isinstance(e, WialonAPIError) else 500,
            detail=str(e)
        )




@app.get("/api/groups")
def get_groups():
    try:
        sid = login()
        raw_groups = get_vehicle_groups(sid)
        vehicles = get_all_vehicles(sid)
        calculated_data = get_calculated_data(sid, vehicles)
        fleet = get_vehicle_live_data(
            sid,
            vehicles,
            calculated_data,
            groups=raw_groups
        )

        fleet_by_id = {v["vehicle_id"]: v for v in fleet}

        group_summaries = []
        for g in raw_groups:
            name = g.get("nm", "").strip()
            if not name or name.lower() == "all unit":
                continue
            unit_ids = g.get("u", [])
            group_vehicles = [fleet_by_id[uid] for uid in unit_ids if uid in fleet_by_id]
            moving = sum(1 for v in group_vehicles if v["status"] == "MOVING")
            idling = sum(1 for v in group_vehicles if v["status"] == "IDLING")
            stopped = sum(1 for v in group_vehicles if v["status"] == "STOPPED")
            offline = sum(1 for v in group_vehicles if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))
            group_summaries.append({
                "id": g.get("id"),
                "name": name,
                "total_vehicles": len(group_vehicles),
                "moving": moving,
                "idling": idling,
                "stopped": stopped,
                "offline": offline,
                "vehicles": group_vehicles
            })

        return {
            "count": len(group_summaries),
            "groups": group_summaries
        }
    except Exception as e:
        print("ERROR IN /api/groups:", repr(e))
        traceback.print_exc()
        raise HTTPException(
            status_code=502 if isinstance(e, WialonAPIError) else 500,
            detail=str(e)
        )


@app.get("/api/vehicles/{vehicle_id}/trips")
def get_vehicle_trips(
    vehicle_id: int,
    hours: int = 24,
    from_time: int = None,
    to_time: int = None
):
    if from_time is not None and to_time is not None:
        if from_time > to_time:
            raise HTTPException(status_code=400, detail="End timestamp must be on or after start timestamp")
        eff_from, eff_to = from_time, to_time
    else:
        now_ts = int(time.time())
        eff_from, eff_to = now_ts - hours * 3600, now_ts

    sid = login()

    native_rep = execute_wialon_unit_trips_report(sid, vehicle_id, eff_from, eff_to)
    if native_rep is not None and "trips" in native_rep:
        w_trips = native_rep["trips"]
        return {
            "vehicle_id": vehicle_id,
            "hours": hours,
            "from_time": eff_from,
            "to_time": eff_to,
            "trip_count": len(w_trips),
            "trips": w_trips
        }

    vehicles = get_all_vehicles(sid)
    target_vehicle = next((v for v in vehicles if v.get("id") == vehicle_id), None)
    sens = (target_vehicle.get("sens") or {}) if target_vehicle else None

    raw_messages = get_vehicle_history(
        sid,
        vehicle_id,
        hours=hours,
        from_time=eff_from,
        to_time=eff_to
    )

    telemetry = normalize_wialon_messages(raw_messages, configured_sensors=sens)

    activity = segment_vehicle_activity(
        telemetry,
        stop_tolerance_seconds=60
    )

    trips = []

    for trip in activity["trips"]:
        trips.append({
            "start_time": trip["start_time"],
            "end_time": trip["end_time"],
            "duration": trip["duration"],
            "distance_km": round(trip["distance"], 2),
            "max_speed": trip["max_speed"],
            "points": trip["points"],
            "start_location": trip["start_location"],
            "end_location": trip["end_location"]
        })

    return {
        "vehicle_id": vehicle_id,
        "hours": hours,
        "from_time": eff_from,
        "to_time": eff_to,
        "trip_count": len(trips),
        "trips": trips
    }


@app.get("/api/vehicles/{vehicle_id}/history")
def get_vehicle_history_data(
    vehicle_id: int,
    hours: int = 24,
    from_time: int = None,
    to_time: int = None
):
    if from_time is not None and to_time is not None and from_time > to_time:
        raise HTTPException(status_code=400, detail="End timestamp must be on or after start timestamp")
    sid = login()

    vehicles = get_all_vehicles(sid)
    target_vehicle = next((v for v in vehicles if v.get("id") == vehicle_id), None)
    sens = (target_vehicle.get("sens") or {}) if target_vehicle else None

    raw_messages = get_vehicle_history(
        sid,
        vehicle_id,
        hours=hours,
        from_time=from_time,
        to_time=to_time
    )

    telemetry = normalize_wialon_messages(raw_messages, configured_sensors=sens)

    return {
        "vehicle_id": vehicle_id,
        "hours": hours,
        "from_time": from_time,
        "to_time": to_time,
        "point_count": len(telemetry),
        "telemetry": telemetry
    }


@app.get("/api/vehicles/{vehicle_id}/idle")
def get_vehicle_idle(
    vehicle_id: int,
    hours: int = 24,
    timeframe: str = None,
    from_date: str = None,
    to_date: str = None,
    from_time: int = None,
    to_time: int = None
):
    """
    Vehicle-specific idle details using Wialon's authoritative Engine Hours Report (Template 1).
    """
    try:
        eff_from, eff_to = get_timeframe_timestamps(
            timeframe=timeframe,
            from_time=from_time,
            to_time=to_time,
            from_date=from_date,
            to_date=to_date,
            hours=hours if (hours and not timeframe and not from_date and from_time is None) else None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cache_key = f"vehicle-idle-v5-{vehicle_id}-{timeframe}-{hours}-{eff_from}-{eff_to}-uae"
    hit = cached(cache_key, ttl_seconds=60)
    if hit:
        return hit

    sid = login()
    rep_res = execute_wialon_unit_engine_report(sid, vehicle_id, eff_from, eff_to)

    if rep_res is not None:
        idle_periods = rep_res.get("idle_events", [])
        total_idle_sec = rep_res.get("total_idle_seconds", 0)
        total_engine_sec = rep_res.get("total_engine_seconds", 0)
        longest_idle_sec = max((p["duration"] for p in idle_periods), default=0)
        avg_idle_sec = (total_idle_sec / len(idle_periods)) if idle_periods else 0

        result = {
            "vehicle_id": vehicle_id,
            "hours": hours,
            "timeframe": timeframe,
            "from_time": eff_from,
            "to_time": eff_to,
            "summary": {
                "idle_periods": len(idle_periods),
                "total_idle_seconds": total_idle_sec,
                "total_idle_minutes": round(total_idle_sec / 60.0, 2),
                "total_engine_seconds": total_engine_sec,
                "total_engine_hours": round(total_engine_sec / 3600.0, 2),
                "longest_idle_seconds": longest_idle_sec,
                "average_idle_seconds": round(avg_idle_sec, 2)
            },
            "periods": idle_periods
        }
    else:
        # Fallback if Wialon report template execution is unavailable
        vehicles = get_all_vehicles(sid)
        target_vehicle = next((v for v in vehicles if v.get("id") == vehicle_id), None)
        sens = (target_vehicle.get("sens") or {}) if target_vehicle else None

        raw_messages = get_vehicle_history(
            sid,
            vehicle_id,
            hours=max(1, int((eff_to - eff_from) / 3600)),
            from_time=eff_from,
            to_time=eff_to
        )
        telemetry = normalize_wialon_messages(raw_messages, configured_sensors=sens)
        activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)

        idle_periods = []
        for period in activity.get("idle_periods", []):
            idle_periods.append({
                "start_time": period["start_time"],
                "end_time": period["end_time"],
                "duration": period["duration"],
                "location": period["location"]
            })

        total_idle_sec = sum(period["duration"] for period in idle_periods)
        longest_idle_sec = max((period["duration"] for period in idle_periods), default=0)
        avg_idle_sec = (total_idle_sec / len(idle_periods)) if idle_periods else 0

        result = {
            "vehicle_id": vehicle_id,
            "hours": hours,
            "timeframe": timeframe,
            "from_time": eff_from,
            "to_time": eff_to,
            "summary": {
                "idle_periods": len(idle_periods),
                "total_idle_seconds": total_idle_sec,
                "total_idle_minutes": round(total_idle_sec / 60.0, 2),
                "total_engine_seconds": 0,
                "total_engine_hours": 0,
                "longest_idle_seconds": longest_idle_sec,
                "average_idle_seconds": round(avg_idle_sec, 2)
            },
            "periods": idle_periods
        }

    set_cache(cache_key, result)
    return result



@app.get("/api/vehicles/{vehicle_id}/efficiency")
def get_vehicle_efficiency(vehicle_id: int, hours: int = 24):
    sid = login()

    vehicles = get_all_vehicles(sid)
    target_vehicle = next((v for v in vehicles if v.get("id") == vehicle_id), None)
    sens = (target_vehicle.get("sens") or {}) if target_vehicle else None

    raw_messages = get_vehicle_history(
        sid,
        vehicle_id,
        hours=hours
    )

    telemetry = normalize_wialon_messages(raw_messages, configured_sensors=sens)

    activity = segment_vehicle_activity(
        telemetry,
        stop_tolerance_seconds=60
    )

    trips = activity["trips"]
    idle_periods = activity["idle_periods"]

    # Distance
    total_distance_km = sum(
        trip["distance"]
        for trip in trips
    )

    # Driving time
    total_driving_seconds = sum(
        trip["duration"]
        for trip in trips
    )

    # Idle time
    total_idle_seconds = sum(
        period["duration"]
        for period in idle_periods
    )

    # Speed statistics from moving telemetry
    moving_points = [
        point
        for point in telemetry
        if point["status"]["state"] == "MOVING"
    ]

    speeds = [
        point["status"]["speed"]
        for point in moving_points
        if point["status"].get("speed") is not None
    ]

    average_speed = (
        sum(speeds) / len(speeds)
        if speeds
        else 0
    )

    max_speed = max(speeds, default=0)

    # Idle percentage
    total_active_seconds = (
        total_driving_seconds + total_idle_seconds
    )

    idle_percentage = (
        (total_idle_seconds / total_active_seconds) * 100
        if total_active_seconds > 0
        else 0
    )

    return {
        "vehicle_id": vehicle_id,
        "hours": hours,
        "efficiency": {
            "distance_km": round(total_distance_km, 2),
            "driving_time_minutes": round(
                total_driving_seconds / 60,
                2
            ),
            "idle_time_minutes": round(
                total_idle_seconds / 60,
                2
            ),
            "idle_percentage": round(
                idle_percentage,
                2
            ),
            "average_speed": round(
                average_speed,
                2
            ),
            "max_speed": max_speed,
            "trips": len(trips),
            "idle_periods": len(idle_periods)
        }
    }


@app.get("/api/vehicles/{vehicle_id}/safety")
def get_vehicle_safety(vehicle_id: int):
    sid = login()

    vehicles = get_all_vehicles(sid)
    calculated_data = get_calculated_data(sid, vehicles)

    fleet = get_vehicle_live_data(
        sid,
        vehicles,
        calculated_data
    )

    vehicle = next(
        (
            item
            for item in fleet
            if item["vehicle_id"] == vehicle_id
        ),
        None
    )

    if not vehicle:
        return {
            "vehicle_id": vehicle_id,
            "found": False,
            "safety": None
        }

    return {
        "vehicle_id": vehicle_id,
        "vehicle_name": vehicle["vehicle_name"],
        "found": True,
        "safety": vehicle["safety"]
    }


@app.get("/api/drivers")
def get_drivers():
    sid = login()

    vehicles = get_all_vehicles(sid)
    calculated_data = get_calculated_data(sid, vehicles)

    fleet = get_vehicle_live_data(
        sid,
        vehicles,
        calculated_data
    )

    drivers = {}

    invalid_driver_ids = {
        "",
        "---",
        "0000000000000000",
        "0",
        "none",
        "null"
    }

    for vehicle in fleet:
        driver = vehicle.get("driver") or {}
        driver_id = driver.get("id")

        if driver_id is None:
            continue

        driver_id_string = str(driver_id).strip().lower()

        if driver_id_string in invalid_driver_ids:
            continue

        driver_key = driver_id_string

        if driver_key not in drivers:
            drivers[driver_key] = {
                "driver_id": driver_id,
                "vehicle_id": vehicle["vehicle_id"],
                "vehicle_name": vehicle["vehicle_name"]
            }

    driver_list = list(drivers.values())

    return {
        "count": len(driver_list),
        "drivers": driver_list
    }


@app.get("/api/dashboard/summary")
def get_dashboard_summary(hours: int = 24, timeframe: str = None):
    """
    Authoritative single-source-of-truth dashboard summary.
    Reconciles live fleet state with validated historical analytics.
    """
    cache_key = f"summary-{timeframe}-{hours}"
    hit = cached(cache_key, ttl_seconds=60)
    if hit:
        return hit

    try:
        from app.fleet_analytics import (
            get_fleet_utilization_analytics,
            get_trips_mileage_analytics,
            get_safety_analytics
        )

        sid = login()
        vehicles = get_all_vehicles(sid)
        vehicles_by_id = {vehicle.get("id"): vehicle for vehicle in vehicles}
        calculated_data = get_calculated_data(sid, vehicles)
        fleet = get_vehicle_live_data(sid, vehicles, calculated_data)

        # Authoritative live counts
        moving = sum(1 for v in fleet if v.get("status") == "MOVING")
        idling = sum(1 for v in fleet if v.get("status") == "IDLING")
        stopped = sum(1 for v in fleet if v.get("status") == "STOPPED")
        offline = sum(1 for v in fleet if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))

        # Authoritative analytics
        trips_data = get_trips_mileage_analytics(hours=hours, timeframe=timeframe)
        safety_data = get_safety_analytics(hours=hours)
        util_data = get_fleet_utilization_analytics(hours=hours)

        result = {
            "hours": hours,
            "summary": {
                "total_vehicles": len(fleet),
                "moving": moving,
                "idling": idling,
                "stopped": stopped,
                "offline": offline,
                "total_distance_km": trips_data.get("summary", {}).get("total_distance_km", 0.0),
                "total_idle_minutes": round(util_data.get("total_idle_hours", 0.0) * 60, 1),
                "total_trips": trips_data.get("summary", {}).get("total_trips", 0),
                "total_safety_events": safety_data.get("summary", {}).get("total_events", 0)
            }
        }

        set_cache(cache_key, result)
        return result
    except Exception as e:
        print("ERROR IN /api/dashboard/summary:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/dashboard/fleet-health")
def get_fleet_health():
    hit = cached("fleet-health", ttl_seconds=300)  # 5 min cache
    if hit:
        return hit

    sid = login()
    raw_groups = get_vehicle_groups(sid)
    vehicles = get_all_vehicles(sid)
    calculated_data = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calculated_data, groups=raw_groups)

    total = len(fleet)
    moving = sum(1 for v in fleet if v["status"] == "MOVING")
    idling = sum(1 for v in fleet if v["status"] == "IDLING")
    stopped = sum(1 for v in fleet if v["status"] == "STOPPED")
    offline = sum(1 for v in fleet if v.get("connection_status") == "OFFLINE" or v.get("is_offline"))

    gps_available = sum(1 for v in fleet if v.get("latitude") is not None and v.get("longitude") is not None)
    battery_available = sum(1 for v in fleet if v.get("sensors", {}).get("battery_voltage") is not None)
    ignition_available = sum(1 for v in fleet if v.get("sensors", {}).get("ignition") is not None)
    driver_sensor_available = sum(1 for v in fleet if v.get("sensors", {}).get("driver_id") is not None)
    driver_assigned = sum(1 for v in fleet if v.get("driver", {}).get("id") and str(v.get("driver", {}).get("id")) not in ["0000000000000000", "0", "---", "none", "null"])

    mileage_units = []
    total_mileage = 0.0
    total_engine_hours = 0.0

    for v in fleet:
        m = v.get("mileage")
        if m is not None:
            m_val = float(m)
            total_mileage += m_val
            mileage_units.append({
                "vehicle_id": v["vehicle_id"],
                "vehicle_name": v["vehicle_name"],
                "group": v.get("group", "General Fleet"),
                "groups": v.get("groups", ["General Fleet"]),
                "mileage": round(m_val, 2)
            })
        eh = v.get("engine_hours")
        if eh is not None:
            total_engine_hours += float(eh)

    mileage_units.sort(key=lambda item: item["mileage"], reverse=True)
    mileage_available = len(mileage_units)
    average_mileage = (total_mileage / mileage_available if mileage_available else 0)
    average_engine_hours = (total_engine_hours / total if total else 0)

    result = {
        "fleet": {
            "total": total,
            "moving": moving,
            "idling": idling,
            "stopped": stopped,
            "offline": offline
        },
        "health": {
            "gps_available": gps_available,
            "battery_available": battery_available,
            "ignition_available": ignition_available,
            "driver_sensor_available": driver_sensor_available,
            "driver_assigned": driver_assigned
        },
        "mileage": {
            "total_km": round(total_mileage, 2),
            "average_km": round(average_mileage, 2),
            "available_units": mileage_available,
            "top_units": mileage_units[:5],
            "all_units": mileage_units
        },
        "engine_hours": {
            "total_hours": round(total_engine_hours, 2),
            "average_hours": round(average_engine_hours, 2)
        }
    }

    set_cache("fleet-health", result)
    return result


@app.get("/api/dashboard/mileage")
@timed_calculation("/api/dashboard/mileage")
def get_dashboard_mileage(
    timeframe: str = "today",
    from_time: int = None,
    to_time: int = None,
    from_date: str = None,
    to_date: str = None,
    group_id: int = None
):
    """
    Get vehicles ranked by mileage for the specified UAE timeframe using authoritative Wialon data.
    - 'today': UAE calendar day so far (00:00:00 Asia/Dubai -> current time)
    - 'yesterday': Previous UAE calendar day (00:00:00 -> 23:59:59.999 Asia/Dubai)
    - 'week': Start of current week (Monday 00:00:00 Asia/Dubai -> current time)
    - 'month': Start of current month (1st 00:00:00 Asia/Dubai -> current time)
    - 'year': Start of current year (Jan 1st 00:00:00 Asia/Dubai -> current time)
    - 'last_24_hours' / '24h': Rolling 24 hours
    - 'all': Cumulative odometer from tracker telemetry
    """
    timeframe_norm = (timeframe or "today").lower().strip()

    # Resolve exact effective time range in UAE timezone
    try:
        if timeframe_norm in ["all", "all_time", "odometer"]:
            eff_from, eff_to = 0, now_timestamp()
        else:
            eff_from, eff_to = get_timeframe_timestamps(
                timeframe=timeframe_norm,
                from_time=from_time,
                to_time=to_time,
                from_date=from_date,
                to_date=to_date
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_data_window("/api/dashboard/mileage", eff_from, eff_to)

    # Cache key uniquely includes timeframe, effective UTC timestamps, group, and timezone
    cache_key = f"dashboard-mileage-v2-{timeframe_norm}-{eff_from}-{eff_to}-{group_id}-uae"
    hit = cached(cache_key, ttl_seconds=60)
    log_cache_state("/api/dashboard/mileage", hit is not None)
    if hit:
        return hit

    try:
        sid = login()
        raw_groups = get_vehicle_groups(sid)
        unit_groups_map = {}
        for group in raw_groups:
            group_name = group.get("nm", "").strip()
            if not group_name or group_name.lower() == "all unit":
                continue
            for uid in group.get("u", []):
                if uid not in unit_groups_map:
                    unit_groups_map[uid] = []
                if group_name not in unit_groups_map[uid]:
                    unit_groups_map[uid].append(group_name)

        vehicles = get_all_vehicles(sid)
        vehicles_by_name = {str(v.get("nm", "")).strip(): v for v in vehicles}
        calculated_data = get_calculated_data(sid, vehicles)
        calculated_by_vehicle = {item.get("i"): item for item in calculated_data}

        mileage_units = []

        if timeframe_norm in ["all", "all_time", "odometer"]:
            for vehicle in vehicles:
                vid = vehicle.get("id")
                calc = calculated_by_vehicle.get(vid, {})
                mileage_data = calc.get("mileage") or {}
                mileage = mileage_data.get("value")
                v_groups = unit_groups_map.get(vid, ["General Fleet"])
                primary_group = v_groups[0] if v_groups else "General Fleet"
                if mileage is not None:
                    mileage_units.append({
                        "vehicle_id": vid,
                        "vehicle_name": vehicle.get("nm", f"Vehicle {vid}"),
                        "group": primary_group,
                        "groups": v_groups,
                        "mileage": round(float(mileage), 2),
                        "source": "wialon_hardware_odometer"
                    })
        else:
            # Authoritative source: Wialon Group Trips Report (Template 5)
            report_group_id = int(group_id) if group_id else GROUP_ID_ALL_UNITS
            group_report = execute_wialon_group_trips_report(
                sid=sid,
                from_time_utc=eff_from,
                to_time_utc=eff_to,
                group_id=report_group_id
            )

            if group_report is not None:
                for rep_item in group_report:
                    v_name = rep_item["vehicle_name"]
                    v_obj = vehicles_by_name.get(v_name)
                    vid = v_obj.get("id") if v_obj else None
                    v_groups = unit_groups_map.get(vid, ["General Fleet"]) if vid else ["General Fleet"]
                    primary_group = v_groups[0] if v_groups else "General Fleet"
                    raw_mileage = float(rep_item["mileage_km"])

                    # Diagnostic logging for high mileage, NEVER capping or zeroing legitimate Wialon values
                    if raw_mileage > 1000.0:
                        logging.info(
                            f"Diagnostic: High Wialon mileage {raw_mileage:.2f} km reported for vehicle {v_name} (ID: {vid}) in window {eff_from}..{eff_to}"
                        )

                    mileage_units.append({
                        "vehicle_id": vid,
                        "vehicle_name": v_name,
                        "group": primary_group,
                        "groups": v_groups,
                        "mileage": round(raw_mileage, 2),
                        "total_time": rep_item.get("total_time"),
                        "engine_hours": rep_item.get("engine_hours"),
                        "avg_speed": rep_item.get("avg_speed"),
                        "max_speed": rep_item.get("max_speed"),
                        "driver": rep_item.get("driver"),
                        "source": "wialon_native_report"
                    })
            else:
                # Fallback to get_trips_mileage_analytics if report template failed
                from app.fleet_analytics import get_trips_mileage_analytics
                trips_res = get_trips_mileage_analytics(from_time=eff_from, to_time=eff_to)
                for v_trips in trips_res.get("vehicles", []):
                    vid = v_trips["vehicle_id"]
                    v_groups = unit_groups_map.get(vid, ["General Fleet"])
                    primary_group = v_groups[0] if v_groups else "General Fleet"
                    raw_mileage = float(v_trips["gps_mileage_km"])
                    mileage_units.append({
                        "vehicle_id": vid,
                        "vehicle_name": v_trips["vehicle_name"],
                        "group": primary_group,
                        "groups": v_groups,
                        "mileage": round(raw_mileage, 2),
                        "source": "wialon_unit_trips"
                    })

        mileage_units.sort(key=lambda item: item["mileage"], reverse=True)
        total_km = sum(item["mileage"] for item in mileage_units)
        avg_km = round(total_km / len(mileage_units), 2) if mileage_units else 0

        result = {
            "timeframe": timeframe_norm,
            "timezone": "Asia/Dubai",
            "effective_from": eff_from,
            "effective_to": eff_to,
            "total_km": round(total_km, 2),
            "average_km": avg_km,
            "available_units": len(mileage_units),
            "top_units": mileage_units[:5],
            "all_units": mileage_units
        }

        set_cache(cache_key, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        print("ERROR IN /api/dashboard/mileage:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/dashboard/trends")
def get_dashboard_trends(hours: int = 24):
    if hours < 1:
        hours = 1

    if hours > 72:
        hours = 72

    cache_key = f"trends-{hours}"
    hit = cached(cache_key, ttl_seconds=600)  # 10 min cache
    if hit:
        return hit

    sid = login()

    vehicles = get_all_vehicles(sid)

    if not vehicles:
        return {
            "hours": hours,
            "interval": "hour",
            "points": []
        }

    buckets = {}

    now = now_timestamp()
    start_time = now - (hours * 3600)

    for hour_offset in range(hours):
        bucket_start = start_time + (hour_offset * 3600)

        buckets[bucket_start] = {
            "timestamp": bucket_start,
            "mileage_km": 0,
            "idle_seconds": 0,
            "moving_seconds": 0,
            "stopped_seconds": 0
        }

    def _fetch_trend_telemetry(v):
        vid = v.get("id")
        if not vid:
            return []
        sens = v.get("sens") or {}
        try:
            messages = get_vehicle_history(
                sid,
                vid,
                hours=hours
            )

            telemetry = normalize_wialon_messages(
                messages,
                configured_sensors=sens
            )

            if not telemetry:
                return []

            return sorted(
                telemetry,
                key=lambda point: point.get("timestamp", 0)
            )
        except Exception as exc:
            print(f"Trend calculation failed for vehicle {vid}: {exc}")
            raise

    with ThreadPoolExecutor(max_workers=4) as executor:
        all_telemetries = list(executor.map(_fetch_trend_telemetry, vehicles))

    for telemetry in all_telemetries:
        if not telemetry:
            continue

        previous = None

        for point in telemetry:
            timestamp = point.get("timestamp")

            if not timestamp:
                continue

            if timestamp < start_time:
                continue

            bucket_start = (
                start_time
                + ((timestamp - start_time) // 3600) * 3600
            )

            if bucket_start not in buckets:
                continue

            bucket = buckets[bucket_start]

            if previous:
                previous_timestamp = previous.get("timestamp")

                elapsed = timestamp - previous_timestamp

                # Prevent unrealistic gaps from inflating the trend.
                if elapsed > 300:
                    elapsed = 300

                previous_metrics = previous.get("metrics") or {}
                current_metrics = point.get("metrics") or {}

                previous_odometer = previous_metrics.get(
                    "odometer"
                )

                current_odometer = current_metrics.get(
                    "odometer"
                )

                if (
                    previous_odometer is not None
                    and current_odometer is not None
                ):
                    try:
                        distance = (
                            float(current_odometer)
                            - float(previous_odometer)
                        )

                        # Odometer is explicitly normalized in kilometers
                        if 0 <= distance <= 120.0:
                            bucket["mileage_km"] += distance
                    except (ValueError, TypeError):
                        pass

                status = point.get("status") or {}
                state = str(
                    status.get("state", "")
                ).upper()

                if state == "MOVING":
                    bucket["moving_seconds"] += elapsed

                elif state == "IDLING":
                    bucket["idle_seconds"] += elapsed

                elif state == "STOPPED":
                    bucket["stopped_seconds"] += elapsed

            previous = point

    points = []

    for bucket in buckets.values():
        points.append({
            "timestamp": bucket["timestamp"],
            "label": datetime.fromtimestamp(
                bucket["timestamp"],
                tz=UAE_TZ
            ).strftime("%H:%M"),
            "mileage_km": round(
                bucket["mileage_km"],
                2
            ),
            "idle_minutes": round(
                bucket["idle_seconds"] / 60,
                2
            ),
            "moving_minutes": round(
                bucket["moving_seconds"] / 60,
                2
            ),
            "stopped_minutes": round(
                bucket["stopped_seconds"] / 60,
                2
            )
        })

    result = {
        "hours": hours,
        "interval": "hour",
        "points": points
    }

    set_cache(cache_key, result)
    return result


@app.get("/api/idle/fleet-analytics")
@timed_calculation("/api/idle/fleet-analytics")
def get_fleet_idle_analytics(
    hours: int = 24,
    timeframe: str = None,
    from_date: str = None,
    to_date: str = None,
    from_time: int = None,
    to_time: int = None,
    group_id: int = None,
    vehicle_id: int = None
):
    """
    Authoritative Fleet-wide Idle Analytics endpoint backed directly by Wialon Engine Hours Report (Template 2).
    Computes exact Wialon idle durations, discrete idle events with coordinates and addresses,
    UAE-local trends, location hotspots, and customer group breakdowns.
    """
    try:
        eff_from, eff_to = get_timeframe_timestamps(
            timeframe=timeframe,
            from_time=from_time,
            to_time=to_time,
            from_date=from_date,
            to_date=to_date,
            hours=hours if (hours and not timeframe and not from_date and from_time is None) else None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_data_window("/api/idle/fleet-analytics", eff_from, eff_to)

    cache_key = f"fleet-idle-v5-{timeframe}-{hours}-{eff_from}-{eff_to}-{group_id}-{vehicle_id}-uae"
    hit = cached(cache_key, ttl_seconds=60)
    log_cache_state("/api/idle/fleet-analytics", hit is not None)
    if hit:
        return hit

    sid = login()

    # Dynamic Customer Groups Discovery
    raw_groups = get_vehicle_groups(sid)
    group_map = {}
    unit_groups_map = {}
    valid_groups = []

    for g in raw_groups:
        gid = g.get("id")
        name = g.get("nm", "").strip()
        if not name or name.lower() == "all unit":
            continue
        group_map[gid] = name
        u_ids = g.get("u", [])
        valid_groups.append({
            "id": gid,
            "name": name,
            "vehicle_count": len(u_ids)
        })
        for uid in u_ids:
            if uid not in unit_groups_map:
                unit_groups_map[uid] = []
            if name not in unit_groups_map[uid]:
                unit_groups_map[uid].append({"id": gid, "name": name})

    # Vehicles & Live Calculations
    vehicles = get_all_vehicles(sid)
    calculated_data = get_calculated_data(sid, vehicles)
    live_vehicles = get_vehicle_live_data(sid, vehicles, calculated_data, groups=raw_groups)
    live_map = {v["vehicle_id"]: v for v in live_vehicles}

    current_ts = now_timestamp()

    # Execute authoritative Wialon Engine Hours Report (Template 2)
    rep_res = execute_wialon_group_engine_idle_report(
        sid,
        eff_from,
        eff_to,
        group_id=GROUP_ID_ALL_UNITS,
        include_events=True
    )

    if rep_res is None:
        raise HTTPException(
            status_code=502,
            detail="Wialon idle report is unavailable; analytics could not be calculated."
        )

    wialon_vehicle_map = {}
    wialon_events_by_veh = defaultdict(list)
    if rep_res:
        for uv in rep_res.get("vehicles", []):
            wialon_vehicle_map[uv["vehicle_name"]] = uv
        for ev in rep_res.get("events", []):
            wialon_events_by_veh[ev["vehicle_name"]].append(ev)

    # Filter target vehicles based on query parameters
    target_vehicles = live_vehicles
    if vehicle_id:
        target_vehicles = [v for v in live_vehicles if v.get("vehicle_id") == vehicle_id]
    elif group_id:
        target_group = next((g for g in raw_groups if g.get("id") == group_id), None)
        if target_group:
            target_unit_ids = set(target_group.get("u", []))
            target_vehicles = [v for v in live_vehicles if v.get("vehicle_id") in target_unit_ids]

    all_events = []
    by_vehicle = []
    currently_idling = 0
    total_idle_vehicles = 0
    total_idle_seconds = 0
    longest_idle_seconds = 0

    group_agg = {}
    for g in valid_groups:
        group_agg[g["id"]] = {
            "group_id": g["id"],
            "group_name": g["name"],
            "vehicle_count": g["vehicle_count"],
            "total_idle_seconds": 0,
            "idle_events": 0,
            "currently_idling": 0
        }

    if 0 not in group_agg:
        group_agg[0] = {
            "group_id": 0,
            "group_name": "General Fleet",
            "vehicle_count": 0,
            "total_idle_seconds": 0,
            "idle_events": 0,
            "currently_idling": 0
        }

    for v in target_vehicles:
        vid = v.get("vehicle_id") or v.get("id")
        vname = v.get("vehicle_name") or v.get("nm") or f"Vehicle {vid}"
        v_grps = unit_groups_map.get(vid, [{"id": 0, "name": "General Fleet"}])
        primary_grp = v_grps[0]
        live_st = v.get("status", "STOPPED")
        v_idle_dur = v.get("idle_duration", 0)
        v_idle_st = v.get("idle_start")
        v_lat = v.get("latitude")
        v_lng = v.get("longitude")
        v_addr = v.get("address")
        if not isinstance(v_addr, str) or not v_addr.strip():
            v_addr = "Current Location"

        w_veh = wialon_vehicle_map.get(vname, {})
        v_idle_sec = w_veh.get("total_idle_seconds", 0)
        v_engine_sec = w_veh.get("engine_hours_seconds", 0)
        v_total_sec = w_veh.get("total_time_seconds", 0)
        v_longest_sec = w_veh.get("longest_idle_seconds", 0)

        # Discrete events for this vehicle from Wialon
        raw_v_events = wialon_events_by_veh.get(vname, [])
        processed_v_events = []
        for idx, ev in enumerate(raw_v_events):
            ev_item = {
                "id": f"{vid}-{ev.get('start_time', idx)}",
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group_id": primary_grp["id"],
                "group_name": primary_grp["name"],
                "start_time": ev.get("start_time", 0),
                "end_time": ev.get("end_time", 0),
                "duration": ev.get("duration", 0),
                "duration_minutes": ev.get("duration_minutes", round(ev.get("duration", 0) / 60.0, 2)),
                "location": ev.get("location") or {"latitude": None, "longitude": None},
                "address": ev.get("address") or "Unknown Location",
            }
            processed_v_events.append(ev_item)

        # Handle active idling if currently idling and timeframe covers now
        if live_st == "IDLING" and v_idle_dur > 0 and eff_to >= (current_ts - 300):
            act_start = v_idle_st or (current_ts - v_idle_dur)
            last_ev_end = max((e["end_time"] for e in processed_v_events), default=0)
            if act_start >= last_ev_end:
                act_event = {
                    "id": f"{vid}-active",
                    "vehicle_id": vid,
                    "vehicle_name": vname,
                    "group_id": primary_grp["id"],
                    "group_name": primary_grp["name"],
                    "start_time": act_start,
                    "end_time": current_ts,
                    "duration": v_idle_dur,
                    "duration_minutes": round(v_idle_dur / 60.0, 2),
                    "location": {
                        "latitude": v_lat,
                        "longitude": v_lng
                    },
                    "address": v_addr,
                    "is_active": True
                }
                processed_v_events.append(act_event)
                v_idle_sec += v_idle_dur
                if v_idle_dur > v_longest_sec:
                    v_longest_sec = v_idle_dur

        all_events.extend(processed_v_events)
        v_event_count = len(processed_v_events) if processed_v_events else (1 if v_idle_sec > 0 else 0)

        by_vehicle.append({
            "vehicle_id": vid,
            "vehicle_name": vname,
            "group_id": primary_grp["id"],
            "group_name": primary_grp["name"],
            "total_idle_seconds": v_idle_sec,
            "idle_minutes": round(v_idle_sec / 60.0, 2),
            "idle_events": v_event_count,
            "longest_idle_seconds": v_longest_sec,
            "engine_hours_seconds": v_engine_sec,
            "total_time_seconds": v_total_sec,
            "status": live_st
        })

        if live_st == "IDLING":
            currently_idling += 1
        if v_idle_sec > 0:
            total_idle_vehicles += 1

        total_idle_seconds += v_idle_sec
        if v_longest_sec > longest_idle_seconds:
            longest_idle_seconds = v_longest_sec

        # Aggregate by group
        gid = primary_grp["id"]
        if gid in group_agg:
            group_agg[gid]["total_idle_seconds"] += v_idle_sec
            group_agg[gid]["idle_events"] += v_event_count
            if live_st == "IDLING":
                group_agg[gid]["currently_idling"] += 1

    # Format by_group list
    by_group = []
    for gid, gdata in group_agg.items():
        if gdata["vehicle_count"] > 0 or gdata["total_idle_seconds"] > 0:
            avg_min = round((gdata["total_idle_seconds"] / gdata["idle_events"] / 60.0), 2) if gdata["idle_events"] > 0 else 0
            by_group.append({
                "group_id": gdata["group_id"],
                "group_name": gdata["group_name"],
                "vehicle_count": gdata["vehicle_count"],
                "total_idle_seconds": gdata["total_idle_seconds"],
                "idle_minutes": round(gdata["total_idle_seconds"] / 60.0, 2),
                "idle_events": gdata["idle_events"],
                "currently_idling": gdata["currently_idling"],
                "average_idle_minutes": avg_min
            })

    by_group.sort(key=lambda x: x["total_idle_seconds"], reverse=True)
    by_vehicle.sort(key=lambda x: x["total_idle_seconds"], reverse=True)

    # Location clustering (hotspots)
    location_clusters = defaultdict(lambda: {"total_idle_seconds": 0, "event_count": 0, "vehicles": set(), "lat": 0, "lng": 0, "address": ""})
    for ev in all_events:
        loc = ev.get("location") or {}
        lat = loc.get("latitude")
        lng = loc.get("longitude")
        if lat is not None and lng is not None:
            cluster_key = f"{round(lat, 3)},{round(lng, 3)}"
            c = location_clusters[cluster_key]
            c["total_idle_seconds"] += ev["duration"]
            c["event_count"] += 1
            c["vehicles"].add(ev["vehicle_name"])
            c["lat"] = lat
            c["lng"] = lng
            if not c["address"] and ev.get("address"):
                c["address"] = ev["address"]

    by_location = []
    for key, c in location_clusters.items():
        by_location.append({
            "location_key": key,
            "latitude": c["lat"],
            "longitude": c["lng"],
            "label": c["address"] if c["address"] and c["address"] != "Unknown Location" and not c["address"].startswith("{") else f"Coordinates ({key})",
            "total_idle_seconds": c["total_idle_seconds"],
            "idle_minutes": round(c["total_idle_seconds"] / 60.0, 2),
            "event_count": c["event_count"],
            "vehicle_count": len(c["vehicles"]),
            "vehicles": list(c["vehicles"])
        })
    by_location.sort(key=lambda x: x["total_idle_seconds"], reverse=True)

    # Trend timeline generation (using UAE local timezone for bucket labels)
    total_events_count = len(all_events)
    span_seconds = eff_to - eff_from
    use_daily_interval = span_seconds > (48 * 3600)
    bucket_step = 86400 if use_daily_interval else 3600
    date_format = "%b %d" if use_daily_interval else "%H:%M"

    trend_buckets = {}
    curr_b = eff_from
    while curr_b <= eff_to:
        b_dt = datetime.fromtimestamp(curr_b, tz=UAE_TZ)
        trend_buckets[curr_b] = {
            "timestamp": curr_b,
            "label": b_dt.strftime(date_format),
            "idle_seconds": 0,
            "idle_minutes": 0,
            "event_count": 0
        }
        curr_b += bucket_step

    for ev in all_events:
        ev_start = ev.get("start_time", 0)
        ev_dur = ev.get("duration", 0)
        if eff_from <= ev_start <= eff_to:
            bucket_key = eff_from + ((ev_start - eff_from) // bucket_step) * bucket_step
            if bucket_key in trend_buckets:
                trend_buckets[bucket_key]["idle_seconds"] += ev_dur
                trend_buckets[bucket_key]["event_count"] += 1

    trend_points = []
    for b in sorted(trend_buckets.values(), key=lambda x: x["timestamp"]):
        trend_points.append({
            "timestamp": b["timestamp"],
            "label": b["label"],
            "idle_seconds": b["idle_seconds"],
            "idle_minutes": round(b["idle_seconds"] / 60.0, 2),
            "event_count": b["event_count"]
        })

    # Sort all events chronologically newest first
    all_events.sort(key=lambda x: x["start_time"], reverse=True)

    total_fleet_vehicles = len(target_vehicles)
    avg_idle_sec = round(total_idle_seconds / total_events_count, 2) if total_events_count > 0 else 0

    response_payload = {
        "timeframe": {
            "hours": hours,
            "timeframe": timeframe,
            "from_time": eff_from,
            "to_time": eff_to
        },
        "kpis": {
            "total_vehicles": total_fleet_vehicles,
            "total_idle_vehicles": total_idle_vehicles,
            "currently_idling_vehicles": currently_idling,
            "total_idle_seconds": total_idle_seconds,
            "total_idle_minutes": round(total_idle_seconds / 60.0, 2),
            "total_idle_hours": round(total_idle_seconds / 3600.0, 2),
            "average_idle_seconds": avg_idle_sec,
            "longest_idle_seconds": longest_idle_seconds,
            "idle_event_count": total_events_count,
            "idling_fleet_ratio": round((currently_idling / total_fleet_vehicles) * 100, 1) if total_fleet_vehicles > 0 else 0
        },
        "by_group": by_group,
        "by_vehicle": by_vehicle,
        "by_location": by_location[:10],
        "trend": trend_points,
        "events": all_events,
        "groups": valid_groups
    }

    set_cache(cache_key, response_payload)
    return response_payload


# ─── AI Fleet Assistant ────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

@app.post("/api/ai/chat")
async def ai_chat(request: ChatRequest):
    """
    Groq-powered Fleet AI chat endpoint.
    Requires the Supabase bearer token enforced by the API authentication middleware.
    """
    if not request.messages:
        raise HTTPException(status_code=400, detail="No messages provided.")

    message_dicts = [
        {"role": m.role, "content": m.content}
        for m in request.messages
    ]

    try:
        from app.ai.assistant import run_assistant
        reply = run_assistant(message_dicts)
        return {"reply": reply}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        print("AI chat error:", repr(e))
        raise HTTPException(
            status_code=502 if isinstance(e, WialonAPIError) else 500,
            detail="Fleet AI is temporarily unavailable. Please try again."
        )


# ─── Extended Fleet Analytics Endpoints ────────────────────────────────────────

from app.fleet_analytics import (
    get_fleet_health_analytics,
    get_fleet_utilization_analytics,
    get_trips_mileage_analytics,
    get_safety_analytics,
    get_fleet_alerts_analytics,
    get_fleet_groups_analytics,
    get_single_vehicle_deep_analytics,
)


@app.get("/api/fleet/health")
@timed_calculation("/api/fleet/health")
def api_fleet_health():
    """Vehicle Health & Diagnostics across the fleet."""
    try:
        return get_fleet_health_analytics()
    except Exception as e:
        print("ERROR in /api/fleet/health:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/fleet/utilization")
@timed_calculation("/api/fleet/utilization")
def api_fleet_utilization(hours: int = 24):
    """Fleet utilization breakdown: driving, idle, stopped, engine-on."""
    try:
        return get_fleet_utilization_analytics(hours=hours)
    except Exception as e:
        print("ERROR in /api/fleet/utilization:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/fleet/trips")
@timed_calculation("/api/fleet/trips")
def api_fleet_trips(
    hours: int = 24,
    from_time: int = None,
    to_time: int = None,
    timeframe: str = None,
    from_date: str = None,
    to_date: str = None
):
    """Fleet trips and mileage (authoritative Wialon native report)."""
    try:
        if timeframe or from_date:
            eff_from, eff_to = get_timeframe_timestamps(
                timeframe=timeframe,
                from_time=from_time,
                to_time=to_time,
                from_date=from_date,
                to_date=to_date
            )
            return get_trips_mileage_analytics(from_time=eff_from, to_time=eff_to, timeframe=timeframe)
        return get_trips_mileage_analytics(hours=hours, from_time=from_time, to_time=to_time)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print("ERROR in /api/fleet/trips:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/fleet/safety")
@timed_calculation("/api/fleet/safety")
def api_fleet_safety(hours: int = 168):
    """Safety analytics: harsh braking, harsh acceleration, harsh cornering."""
    try:
        return get_safety_analytics(hours=hours)
    except Exception as e:
        print("ERROR in /api/fleet/safety:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/fleet/alerts")
def api_fleet_alerts():
    """Derived fleet alerts categorized by CRITICAL, WARNING, INFO."""
    try:
        return get_fleet_alerts_analytics()
    except Exception as e:
        print("ERROR in /api/fleet/alerts:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/fleet/groups")
@timed_calculation("/api/fleet/groups")
def api_fleet_groups():
    """Dynamic group rollups (distance, trips, idle, safety)."""
    try:
        return get_fleet_groups_analytics()
    except Exception as e:
        print("ERROR in /api/fleet/groups:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/vehicles/{vehicle_id}/analytics")
def api_vehicle_deep_analytics(vehicle_id: int, hours: int = 24):
    """Comprehensive single vehicle drawer analytics & route points."""
    try:
        res = get_single_vehicle_deep_analytics(vehicle_id=vehicle_id, hours=hours)
        if not res.get("found"):
            raise HTTPException(status_code=404, detail="Vehicle not found")
        return res
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR in /api/vehicles/{vehicle_id}/analytics:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))


@app.get("/api/vehicles/{vehicle_id}/route")
def api_vehicle_route(vehicle_id: int, hours: int = 24):
    """Route history points for map replay and safety event overlays."""
    try:
        res = get_single_vehicle_deep_analytics(vehicle_id=vehicle_id, hours=hours)
        if not res.get("found"):
            raise HTTPException(status_code=404, detail="Vehicle not found")
        return {
            "vehicle_id": vehicle_id,
            "vehicle_name": res.get("vehicle_name"),
            "hours": hours,
            "route_points": res.get("route_points", []),
            "trips": res.get("trips", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR in /api/vehicles/{vehicle_id}/route:", repr(e))
        traceback.print_exc()
        raise HTTPException(status_code=502 if isinstance(e, WialonAPIError) else 500, detail=str(e))
