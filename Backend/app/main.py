from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from app.utils.datetime_helpers import now_timestamp, to_iso_utc, get_timeframe_timestamps, UAE_TZ, ROLLING_TIMEFRAMES
import logging
import os
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Annotated, List, Dict, Any
from fastapi.middleware.cors import CORSMiddleware
from app.auth import authenticate_access_token
from app.performance_diagnostics import log_cache_state, log_data_window, new_trace_id, request_trace_id, timed_calculation
from app.utils.status_config import CONN_DELAYED_SECONDS
from app.utils.ttl_cache import TTLCache

from app.wialon import EVENTS_SESSION, WialonAPIError, login, get_vehicle_history, get_vehicle_groups, audit_wialon_fleet
from app.wialon_reports import (
    execute_wialon_unit_trips_report,
    execute_wialon_group_trips_report,
    execute_wialon_unit_engine_report,
    execute_wialon_group_engine_idle_report,
    resolve_all_units_group_id,
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

# Routine PERF timings, cache state and calculation traces log at DEBUG; set LOG_LEVEL=DEBUG to see them.
# INFO keeps startup events and concise request failures; warnings and errors are always shown.
_log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
logging.basicConfig(
    level=_log_level if isinstance(logging.getLevelName(_log_level), int) else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# The Groq SDK's httpx client logs every HTTP call at INFO; keep its warnings and errors only
if logging.getLogger().getEffectiveLevel() > logging.DEBUG:
    logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app):
    # Warm the Wialon session, but never block startup on it: login() is lazy on every request path,
    # so health and fuel-only endpoints stay available during a Wialon outage.
    try:
        login(force=True)
        logging.info("Wialon session established at startup")
    except Exception as exc:
        logging.warning("Wialon login failed at startup; it will be retried on the next request: %s", exc)
    else:
        # The alerts scan is the slowest cold load; start it now in the background (never blocks startup)
        from app.fleet_analytics import prewarm_fleet_alerts
        prewarm_fleet_alerts()
    yield


# Interactive API docs and the OpenAPI schema are public routes, so serve them only when
# ENVIRONMENT is explicitly "development"; an unset or unknown value keeps them off
_docs_enabled = os.getenv("ENVIRONMENT", "").strip().lower() == "development"

app = FastAPI(
    title="Fleet Management API",
    description="Vehicle fleet monitoring backend",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
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

# Deployed Vercel frontend; always allowed in addition to the env-configured origins.
VERCEL_FRONTEND_ORIGIN = "https://test-eta-pink-edg2397shy.vercel.app"
if VERCEL_FRONTEND_ORIGIN not in allowed_origins:
    allowed_origins.append(VERCEL_FRONTEND_ORIGIN)

if "*" in allowed_origins:
    raise RuntimeError("CORS_ORIGINS must list explicit origins when credentialed authentication is enabled")

# Vercel gives every deployment its own hashed URL (test-<hash>-ats-5a9e.vercel.app); match only this
# project's deployments in its team scope. Starlette full-matches the pattern against the Origin header.
allowed_origin_regex = os.getenv("CORS_ORIGIN_REGEX", "").strip() or r"https://test-[a-z0-9]+-ats-5a9e\.vercel\.app"


@app.exception_handler(WialonAPIError)
async def wialon_upstream_error_handler(_request: Request, exc: WialonAPIError):
    # Wialon error text can include raw response payloads; it is logged here and not sent to the client.
    logging.warning("Wialon upstream error on %s: %s", _request.url.path, exc)
    return JSONResponse(status_code=502, content={"detail": "Wialon upstream request failed"})


@app.middleware("http")
async def require_supabase_auth(request: Request, call_next):
    """Protect every operational API route at the backend boundary."""
    tracked_path = request.url.path in {
        "/api/vehicles",
        "/api/dashboard/mileage",
        "/api/idle/fleet-analytics",
        "/api/fleet/health",
        "/api/fleet/safety",
        "/api/fleet/alerts",
        "/api/fleet/utilization",
        "/api/fleet/trips",
        "/api/fleet/groups",
        "/api/wialon/audit",
        "/api/dashboard/summary",
    } or request.url.path.startswith("/api/fuel/analytics/")
    trace_id = new_trace_id() if tracked_path else "-"
    trace_token = request_trace_id.set(trace_id)
    request_started = time.perf_counter()
    # Request entry for time budgets (the fuel import counts upload parsing and its lock wait against it)
    request.state.started = request_started
    if tracked_path:
        logging.debug("PERF request start request_id=%s method=%s path=%s", trace_id, request.method, request.url.path)
    response = None
    try:
        response = await _authenticate_and_dispatch(request, call_next)
        return response
    finally:
        if tracked_path:
            status_code = response.status_code if response is not None else "exception"
            # Successful timings are routine (DEBUG); client errors stay at INFO, 5xx and exceptions at WARNING
            if response is None or status_code >= 500:
                end_level = logging.WARNING
            elif status_code >= 400:
                end_level = logging.INFO
            else:
                end_level = logging.DEBUG
            logging.log(
                end_level,
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


# Registered after the auth middleware so CORS is the outermost layer: auth 401/403/503 responses
# then carry CORS headers and the browser can read them instead of reporting "Failed to fetch".
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _internal_error(endpoint: str) -> HTTPException:
    """Log the exception being handled and build a client-safe error that carries no exception text."""
    exc = sys.exc_info()[1]
    logging.exception("Request failed in %s", endpoint)
    if isinstance(exc, WialonAPIError):
        return HTTPException(status_code=502, detail="Wialon upstream request failed")
    return HTTPException(status_code=500, detail="Internal server error")


# Upper bounds on client-chosen time windows. Raw message history (per-vehicle message loads) is far heavier
# than Wialon report rows, so it gets the tighter limit; the UI's longest preset is 720 h (30 days).
MAX_HISTORY_HOURS = 31 * 24
MAX_REPORT_HOURS = 366 * 24
# Rolling timeframe aliases accepted by /api/dashboard/summary (all within MAX_HISTORY_HOURS)
SUMMARY_ROLLING_TIMEFRAME_HOURS = {
    "24h": 24, "24_hours": 24, "last_24_hours": 24,
    "7d": 7 * 24, "1w": 7 * 24, "last_1_week": 7 * 24,
    "30d": 30 * 24, "1m": 30 * 24, "last_1_month": 30 * 24,
}


def _require_window_within(eff_from: int, eff_to: int, max_hours: int) -> None:
    """Reject a resolved window longer than max_hours (400) before any Wialon work starts."""
    if eff_to - eff_from > max_hours * 3600:
        raise HTTPException(
            status_code=400,
            detail=f"Requested time range is too long; the maximum is {max_hours // 24} days",
        )


# ─── Simple in-memory cache ───
# Bounded LRU: keys include client-chosen windows and vehicle ids, so an unbounded dict would grow forever.
# Entries are evicted after the longest ttl_seconds any caller passes to cached(); freshness is checked per call.
_CACHE_RETENTION_SECONDS = 600
_cache = TTLCache(max_entries=128)

def cached(key, ttl_seconds=300):
    """Return cached data if still fresh, otherwise None."""
    entry = _cache.get(key)
    if entry and (time.time() - entry["time"]) < ttl_seconds:
        return entry["data"]
    return None

def set_cache(key, data):
    _cache.set(key, {"data": data, "time": time.time()}, _CACHE_RETENTION_SECONDS)


_flight_locks: Dict[str, list] = {}
_flight_guard = threading.Lock()


@contextmanager
def _single_flight(key):
    """Serialize work on one cache key; callers re-check the cache inside, so concurrent duplicates compute once."""
    with _flight_guard:
        entry = _flight_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _flight_guard:
            entry[1] -= 1
            if entry[1] == 0 and _flight_locks.get(key) is entry:
                del _flight_locks[key]


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
    hit = cached("fleet-vehicles-live", ttl_seconds=5)
    if hit:
        return hit

    try:
        # Login to Wialon
        sid = login()

        # Get all vehicles
        vehicles = get_all_vehicles(sid)

        # Get latest calculated data
        calculated_data = get_calculated_data(sid, vehicles)

        # Build final vehicle data
        fleet = get_vehicle_live_data(sid, vehicles, calculated_data)

        result = {
            "count": len(fleet),
            "vehicles": fleet
        }
        set_cache("fleet-vehicles-live", result)
        return result

    except Exception as e:
        raise _internal_error("/api/vehicles")


@app.get("/api/vehicles/{vehicle_id}")
def get_vehicle_details(vehicle_id: int):
    cached_fleet_res = cached("fleet-vehicles-live", ttl_seconds=5)
    fleet = cached_fleet_res.get("vehicles") if cached_fleet_res else None

    if not fleet:
        sid = login()
        vehicles = get_all_vehicles(sid)
        calculated_data = get_calculated_data(sid, vehicles)
        fleet = get_vehicle_live_data(sid, vehicles, calculated_data)
        set_cache("fleet-vehicles-live", {"count": len(fleet), "vehicles": fleet})

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
        raise _internal_error("/api/wialon/audit")




@app.get("/api/groups")
def get_groups():
    hit = cached("fleet-groups-live", ttl_seconds=15)
    if hit:
        return hit

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

        result = {
            "count": len(group_summaries),
            "groups": group_summaries
        }
        set_cache("fleet-groups-live", result)
        return result
    except Exception as e:
        raise _internal_error("/api/groups")


@app.get("/api/vehicles/{vehicle_id}/trips")
def get_vehicle_trips(
    vehicle_id: int,
    hours: Annotated[int, Query(ge=1, le=MAX_REPORT_HOURS)] = 24,
    from_time: int = None,
    to_time: int = None
):
    if from_time is not None and to_time is not None:
        if from_time > to_time:
            raise HTTPException(status_code=400, detail="End timestamp must be on or after start timestamp")
        _require_window_within(from_time, to_time, MAX_REPORT_HOURS)
        eff_from, eff_to = from_time, to_time
        window_spec = f"exact-{from_time}-{to_time}"
    else:
        now_ts = int(time.time())
        eff_from, eff_to = now_ts - hours * 3600, now_ts
        window_spec = f"hours-{hours or 24}"

    cache_key = f"vehicle-trips-v3-{vehicle_id}-{window_spec}-uae"
    hit = cached(cache_key, ttl_seconds=60)
    if hit:
        return hit

    sid = login()

    # The report returns {"trips": [...]} or raises WialonAPIError (mapped to 502); it never returns None.
    native_rep = execute_wialon_unit_trips_report(sid, vehicle_id, eff_from, eff_to)
    w_trips = native_rep["trips"]
    unlocated = native_rep.get("unlocated_fields") or []
    if "distance_km" in unlocated:
        # The Mileage column could not be identified: every trip distance is unknown, not a guessed column
        w_trips = [
            dict(t, incomplete_fields=sorted(set(t.get("incomplete_fields") or []) | {"distance_km"}))
            for t in w_trips
        ]
    res = {
        "vehicle_id": vehicle_id,
        "hours": hours,
        "from_time": eff_from,
        "to_time": eff_to,
        "trip_count": len(w_trips),
        "trips": w_trips,
        "incomplete_fields": sorted(set(native_rep.get("incomplete_fields") or []) | set(unlocated)),
        "column_warnings": native_rep.get("column_warnings") or [],
    }
    # Do not cache a report with unreadable cells or unlocated columns; the next request re-reads it
    if not (res["incomplete_fields"] or res["column_warnings"]):
        set_cache(cache_key, res)
    return res


@app.get("/api/vehicles/{vehicle_id}/history")
def get_vehicle_history_data(
    vehicle_id: int,
    hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 24,
    from_time: int = None,
    to_time: int = None
):
    if from_time is not None and to_time is not None:
        if from_time > to_time:
            raise HTTPException(status_code=400, detail="End timestamp must be on or after start timestamp")
        _require_window_within(from_time, to_time, MAX_HISTORY_HOURS)
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
    hours: Annotated[int, Query(ge=1, le=MAX_REPORT_HOURS)] = 24,
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
    _require_window_within(eff_from, eff_to, MAX_REPORT_HOURS)

    if from_time is not None and to_time is not None:
        window_spec = f"exact-{from_time}-{to_time}"
    elif from_date and to_date:
        window_spec = f"dates-{from_date}-{to_date}"
    elif timeframe:
        window_spec = f"tf-{timeframe.lower().strip()}"
    else:
        window_spec = f"hours-{hours or 24}"

    cache_key = f"vehicle-idle-v6-{vehicle_id}-{window_spec}-uae"
    hit = cached(cache_key, ttl_seconds=60)
    if hit:
        return hit

    sid = login()
    # The report returns its totals or raises WialonAPIError (mapped to 502); it never returns None.
    rep_res = execute_wialon_unit_engine_report(sid, vehicle_id, eff_from, eff_to)

    idle_periods = rep_res.get("idle_events", [])
    # A cell that could not be read, or was read from a guessed column position, is unknown
    incomplete_fields = sorted(set(rep_res.get("incomplete_fields") or []) | set(rep_res.get("unlocated_fields") or []))
    column_warnings = rep_res.get("column_warnings") or []
    # An unreadable report cell is unknown (None), never reported as 0
    idle_unknown = "idle_seconds" in incomplete_fields
    engine_unknown = "engine_hours" in incomplete_fields
    total_idle_sec = rep_res.get("total_idle_seconds", 0)
    total_engine_sec = rep_res.get("total_engine_seconds", 0)
    longest_idle_sec = max((p["duration"] for p in idle_periods), default=0)
    avg_idle_sec = (total_idle_sec / len(idle_periods)) if idle_periods else 0
    # The period list is complete only when Wialon delivered every interval and the idle cell was read (an
    # unreadable one parses as 0, so no periods were fetched). Otherwise period-derived metrics are unknown
    # (None), not a lower bound; `periods` keeps what was read.
    events_complete = "idle_events" not in incomplete_fields and not idle_unknown

    result = {
        "vehicle_id": vehicle_id,
        "hours": hours,
        "timeframe": timeframe,
        "from_time": eff_from,
        "to_time": eff_to,
        "summary": {
            "idle_periods": len(idle_periods) if events_complete else None,
            "total_idle_seconds": None if idle_unknown else total_idle_sec,
            "total_idle_minutes": None if idle_unknown else round(total_idle_sec / 60.0, 2),
            "total_engine_seconds": None if engine_unknown else total_engine_sec,
            "total_engine_hours": None if engine_unknown else round(total_engine_sec / 3600.0, 2),
            "longest_idle_seconds": longest_idle_sec if events_complete else None,
            "average_idle_seconds": round(avg_idle_sec, 2) if events_complete else None
        },
        "events_complete": events_complete,
        "periods": idle_periods,
        "incomplete_fields": incomplete_fields,
        "column_warnings": column_warnings,
    }

    # Do not cache a report with unreadable cells or unlocated columns; the next request re-reads it
    if not (incomplete_fields or column_warnings):
        set_cache(cache_key, result)
    return result



@app.get("/api/vehicles/{vehicle_id}/efficiency")
def get_vehicle_efficiency(vehicle_id: int, hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 24):
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
def get_dashboard_summary(hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 24, timeframe: str = None):
    """
    Authoritative single-source-of-truth dashboard summary.
    Reconciles live fleet state with validated historical analytics.
    """
    # Trips, idle and safety must cover the same window, and safety and utilization only take a rolling
    # `hours` window. So only rolling timeframes are accepted: a calendar one (today, week, month...)
    # cannot be expressed in whole rolling hours without including time before its calendar start.
    if timeframe:
        tf_hours = SUMMARY_ROLLING_TIMEFRAME_HOURS.get(timeframe.lower().strip())
        if tf_hours is None:
            raise HTTPException(
                status_code=400,
                detail="Unsupported timeframe for the dashboard summary. Use a rolling window "
                       f"({', '.join(sorted(SUMMARY_ROLLING_TIMEFRAME_HOURS))}) or the hours parameter.",
            )
        hours = tf_hours

    cache_key = f"summary-{hours}"
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
        trips_data = get_trips_mileage_analytics(hours=hours)
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
                # The totals above cover vehicles with known values only; these flag when some were unknown
                "total_distance_partial": bool(trips_data.get("summary", {}).get("total_distance_partial")),
                "total_idle_minutes": round(util_data["total_idle_hours"] * 60, 1) if util_data.get("total_idle_hours") is not None else None,
                "total_idle_partial": bool(util_data.get("partial")),
                "total_trips": trips_data.get("summary", {}).get("total_trips", 0),
                "total_safety_events": safety_data.get("summary", {}).get("total_events", 0)
            }
        }

        # A partial total is not cached; the next request re-reads the reports
        if not (result["summary"]["total_distance_partial"] or result["summary"]["total_idle_partial"]):
            set_cache(cache_key, result)
        return result
    except Exception as e:
        raise _internal_error("/api/dashboard/summary")


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

    if timeframe_norm not in ["all", "all_time", "odometer"]:
        _require_window_within(eff_from, eff_to, MAX_REPORT_HOURS)
    log_data_window("/api/dashboard/mileage", eff_from, eff_to)

    if from_time is not None and to_time is not None:
        window_spec = f"exact-{from_time}-{to_time}"
    elif from_date:
        window_spec = f"dates-{from_date}-{to_date or from_date}"
    elif timeframe_norm in ROLLING_TIMEFRAMES:
        window_spec = f"tf-{timeframe_norm}"
    else:
        # A calendar window's start moves at UAE midnight: a result cached before it never answers after it
        window_spec = f"tf-{timeframe_norm}-{eff_from}"

    cache_key = f"dashboard-mileage-v3-{window_spec}-{group_id}-uae"
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
        name_counts = Counter(str(v.get("nm", "")).strip() for v in vehicles)
        # Report rows carry only the unit name: a name shared by several units cannot be attributed
        ambiguous_names = sorted(n for n, c in name_counts.items() if c > 1)
        calculated_data = get_calculated_data(sid, vehicles)
        calculated_by_vehicle = {item.get("i"): item for item in calculated_data}

        mileage_units = []
        unmatched_rows = []
        report_incomplete = False

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
            report_group_id = int(group_id) if group_id else resolve_all_units_group_id(sid, raw_groups)
            group_report = execute_wialon_group_trips_report(
                sid=sid,
                from_time_utc=eff_from,
                to_time_utc=eff_to,
                group_id=report_group_id
            )

            # The report covers the group's members only, so when membership is known a name is ambiguous only if
            # several members share it. A row naming no member, or a group with unknown membership, is matched
            # against the whole fleet.
            report_group = next((g for g in raw_groups if g.get("id") == report_group_id), None)
            member_ids = set(report_group.get("u") or []) if report_group is not None else None
            covered_names = {rep_item["vehicle_name"] for rep_item in group_report}
            fleet_by_name = defaultdict(list)
            for vehicle in vehicles:
                fleet_by_name[str(vehicle.get("nm", "")).strip()].append(vehicle)

            def name_candidates(name):
                same_name = fleet_by_name.get(name, [])
                in_group = [v for v in same_name if v.get("id") in member_ids] if member_ids else []
                return in_group or same_name

            if member_ids:
                scoped_names = {str(v.get("nm", "")).strip() for v in vehicles if v.get("id") in member_ids}
            else:
                scoped_names = set(fleet_by_name)
            scoped_names |= covered_names
            # The group report has one summary row per unit, so a name on several rows is ambiguous too: summing
            # them could count the same unit twice, and picking one could drop a real unit's mileage
            row_counts = Counter(rep_item["vehicle_name"] for rep_item in group_report)
            ambiguous_names = sorted(n for n in scoped_names if len(name_candidates(n)) > 1 or row_counts[n] > 1)
            vehicles_by_name = {n: name_candidates(n)[0] for n in scoped_names if len(name_candidates(n)) == 1}

            # The report returns its rows or raises WialonAPIError (mapped to 502); it never returns None.
            for rep_item in group_report:
                v_name = rep_item["vehicle_name"]
                if not fleet_by_name.get(v_name):
                    # Same as /api/fleet/trips: a row naming no fleet unit is not added to the fleet total. It is
                    # listed with unknown mileage so the report shows as incomplete instead of silently shrinking.
                    report_incomplete = True
                    if v_name not in unmatched_rows:
                        unmatched_rows.append(v_name)
                        mileage_units.append({
                            "vehicle_id": None,
                            "vehicle_name": v_name,
                            "group": "General Fleet",
                            "groups": ["General Fleet"],
                            "mileage": None,
                            "source": "unmatched_report_row"
                        })
                    continue
                if v_name in ambiguous_names:
                    # The row cannot be attributed to one unit; each unit with that name is listed below as unknown
                    report_incomplete = True
                    continue
                v_obj = vehicles_by_name.get(v_name)
                vid = v_obj.get("id") if v_obj else None
                v_groups = unit_groups_map.get(vid, ["General Fleet"]) if vid else ["General Fleet"]
                primary_group = v_groups[0] if v_groups else "General Fleet"
                # An unreadable mileage cell is unknown (None), never reported as 0 km
                mileage_unreadable = "mileage_km" in (
                    (rep_item.get("incomplete_fields") or []) + (rep_item.get("unlocated_fields") or [])
                )
                raw_mileage = float(rep_item["mileage_km"])
                if mileage_unreadable:
                    report_incomplete = True
                if rep_item.get("column_warnings"):
                    report_incomplete = True

                # Diagnostic logging for high mileage, NEVER capping or zeroing legitimate Wialon values
                if raw_mileage > 1000.0:
                    logging.debug(
                        f"Diagnostic: High Wialon mileage {raw_mileage:.2f} km reported for vehicle {v_name} (ID: {vid}) in window {eff_from}..{eff_to}"
                    )

                mileage_units.append({
                    "vehicle_id": vid,
                    "vehicle_name": v_name,
                    "group": primary_group,
                    "groups": v_groups,
                    "mileage": None if mileage_unreadable else round(raw_mileage, 2),
                    "total_time": rep_item.get("total_time"),
                    "engine_hours": rep_item.get("engine_hours"),
                    "avg_speed": rep_item.get("avg_speed"),
                    "max_speed": rep_item.get("max_speed"),
                    "driver": rep_item.get("driver"),
                    "source": "wialon_native_report"
                })

            # The group report emits a row ("0.00 km") for every member even without activity, so a member
            # with no row was not covered by the report: its mileage is unknown, never 0 km.
            # A unit sharing its name with another unit is unknown too: report rows carry only the name.
            for vehicle in vehicles:
                vid = vehicle.get("id")
                v_name = str(vehicle.get("nm", "")).strip()
                if v_name in ambiguous_names:
                    # Group membership decides when known; otherwise the report naming the unit does
                    if not (vid in member_ids if member_ids else v_name in covered_names):
                        continue
                    source = "ambiguous_vehicle_name" if len(name_candidates(v_name)) > 1 else "duplicate_report_rows"
                elif member_ids is not None and vid in member_ids and v_name not in covered_names:
                    source = "missing_from_report"
                else:
                    continue
                report_incomplete = True
                v_groups = unit_groups_map.get(vid, ["General Fleet"])
                mileage_units.append({
                    "vehicle_id": vid,
                    "vehicle_name": vehicle.get("nm", f"Vehicle {vid}"),
                    "group": v_groups[0] if v_groups else "General Fleet",
                    "groups": v_groups,
                    "mileage": None,
                    "source": source
                })

        mileage_units.sort(key=lambda item: (item["mileage"] is not None, item["mileage"] or 0), reverse=True)
        known_mileage = [item["mileage"] for item in mileage_units if item["mileage"] is not None]
        # No known mileage at all is unknown (null), never 0 km; a partial total and average cover the known units
        total_km = round(sum(known_mileage), 2) if known_mileage else None
        avg_km = round(sum(known_mileage) / len(known_mileage), 2) if known_mileage else None
        incomplete_units = sorted(item["vehicle_name"] for item in mileage_units if item["mileage"] is None)

        result = {
            "timeframe": timeframe_norm,
            "timezone": "Asia/Dubai",
            "effective_from": eff_from,
            "effective_to": eff_to,
            "total_km": total_km,
            "average_km": avg_km,
            "available_units": len(mileage_units),
            "top_units": mileage_units[:5],
            "all_units": mileage_units,
            "ambiguous_vehicle_names": ambiguous_names,
            "incomplete_units": incomplete_units,
            "unmatched_report_rows": sorted(unmatched_rows),
        }

        # Do not cache a report with unreadable cells or unlocated columns; the next request re-reads it
        if not report_incomplete:
            set_cache(cache_key, result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error("/api/dashboard/mileage")


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
            logging.warning("Trend calculation failed for vehicle %s: %s", vid, exc)
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


def _build_idle_trend(events, eff_from, eff_to):
    """Idle trend points for [eff_from, eff_to] (inclusive), labelled in UAE local time.

    Spans over 48 h use one bucket per UAE calendar day: the first bucket starts at eff_from and every later one
    at UAE midnight, so a range starting mid-day is not shifted into 24 h windows that straddle two dates.
    Shorter spans keep hourly buckets stepped from eff_from.
    """
    use_daily_interval = (eff_to - eff_from) > (48 * 3600)
    date_format = "%b %d" if use_daily_interval else "%H:%M"

    if use_daily_interval:
        def _day_start(ts):
            local = datetime.fromtimestamp(ts, tz=UAE_TZ)
            return int(local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

        def bucket_key_for(ts):
            return max(_day_start(ts), eff_from)

        bucket_starts = []
        day = _day_start(eff_from)
        while day <= eff_to:
            bucket_starts.append(max(day, eff_from))
            day += 86400  # UAE observes no DST, so every local day is 24 h
    else:
        def bucket_key_for(ts):
            return eff_from + ((ts - eff_from) // 3600) * 3600

        bucket_starts = range(eff_from, eff_to + 1, 3600)

    trend_buckets = {}
    for start in bucket_starts:
        trend_buckets[start] = {
            "timestamp": start,
            "label": datetime.fromtimestamp(start, tz=UAE_TZ).strftime(date_format),
            "idle_seconds": 0,
            "event_count": 0
        }

    for ev in events:
        ev_start = ev.get("start_time", 0)
        ev_dur = ev.get("duration", 0)
        if eff_from <= ev_start <= eff_to:
            bucket_key = bucket_key_for(ev_start)
            if bucket_key in trend_buckets:
                trend_buckets[bucket_key]["idle_seconds"] += ev_dur
                trend_buckets[bucket_key]["event_count"] += 1

    return [
        {
            "timestamp": b["timestamp"],
            "label": b["label"],
            "idle_seconds": b["idle_seconds"],
            "idle_minutes": round(b["idle_seconds"] / 60.0, 2),
            "event_count": b["event_count"]
        }
        for b in sorted(trend_buckets.values(), key=lambda x: x["timestamp"])
    ]


@app.get("/api/idle/fleet-analytics")
@timed_calculation("/api/idle/fleet-analytics")
def get_fleet_idle_analytics(
    hours: Annotated[int, Query(ge=1, le=MAX_REPORT_HOURS)] = 24,
    timeframe: str = None,
    from_date: str = None,
    to_date: str = None,
    from_time: int = None,
    to_time: int = None,
    group_id: int = None,
    vehicle_id: int = None,
    include_events: bool = True,
    refresh: bool = False,
    skip_cache: bool = False,
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

    _require_window_within(eff_from, eff_to, MAX_REPORT_HOURS)
    log_data_window("/api/idle/fleet-analytics", eff_from, eff_to)

    if from_time is not None and to_time is not None:
        window_spec = f"exact-{from_time}-{to_time}"
    elif from_date:
        window_spec = f"dates-{from_date}-{to_date or from_date}"
    elif timeframe:
        window_spec = f"tf-{timeframe.lower().strip()}"
    else:
        window_spec = f"hours-{hours or 24}"

    events_tag = "full" if include_events else "summary"
    cache_key = f"fleet-idle-v8-{window_spec}-grp-{group_id}-veh-{vehicle_id}-{events_tag}-uae"
    if not refresh and not skip_cache:
        hit = cached(cache_key, ttl_seconds=180)
        log_cache_state("/api/idle/fleet-analytics", hit is not None)
        if hit:
            return hit

    # Overlapping identical requests (two tabs, a remount) wait for the one already running and reuse its cached
    # result instead of each paging the same Wialon report; a refresh still recomputes after it.
    with _single_flight(cache_key):
        if not refresh and not skip_cache:
            hit = cached(cache_key, ttl_seconds=180)
            if hit:
                return hit
        return _compute_fleet_idle_analytics(
            eff_from, eff_to, hours, timeframe, group_id, vehicle_id, include_events, cache_key
        )


def _compute_fleet_idle_analytics(eff_from, eff_to, hours, timeframe, group_id, vehicle_id, include_events, cache_key):
    """Build the /api/idle/fleet-analytics payload from Wialon and cache it under cache_key when complete."""
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

    # Filter target vehicles based on query parameters
    target_vehicles = live_vehicles
    if vehicle_id:
        target_vehicles = [v for v in live_vehicles if v.get("vehicle_id") == vehicle_id]
    elif group_id:
        target_group = next((g for g in raw_groups if g.get("id") == group_id), None)
        if target_group:
            target_unit_ids = set(target_group.get("u", []))
            target_vehicles = [v for v in live_vehicles if v.get("vehicle_id") in target_unit_ids]

    # Only target vehicles' events are used below; paging other units' sub-rows (one unit can have 100k+)
    # would only hold the shared report lock longer
    event_vehicle_names = None
    if target_vehicles is not live_vehicles:
        event_vehicle_names = {
            v.get("vehicle_name") or v.get("nm") or f"Vehicle {v.get('vehicle_id') or v.get('id')}"
            for v in target_vehicles
        }

    # Execute authoritative Wialon Engine Hours Report (Template 2). Paging every unit's idle events
    # can hold a report lock for minutes, so it runs on a dedicated Wialon session with its own lock:
    # Dashboard summary/mileage reports on the shared session never wait behind it.
    report_sid = login(session=EVENTS_SESSION) if include_events else sid
    rep_res = execute_wialon_group_engine_idle_report(
        report_sid,
        eff_from,
        eff_to,
        group_id=resolve_all_units_group_id(sid, raw_groups),
        include_events=include_events,
        event_vehicle_names=event_vehicle_names,
    )

    if rep_res is None:
        raise HTTPException(
            status_code=502,
            detail="Wialon idle report is unavailable; analytics could not be calculated."
        )

    wialon_vehicle_map = {}
    wialon_events_by_veh = defaultdict(list)
    # Report rows are keyed by unit name only; a name shared by several units (or rows) cannot be attributed
    live_name_counts = Counter(v.get("vehicle_name") or v.get("nm") for v in live_vehicles)
    report_name_counts = Counter(uv["vehicle_name"] for uv in (rep_res or {}).get("vehicles", []))
    ambiguous_names = sorted(
        str(n) for n in set(live_name_counts) | set(report_name_counts)
        if n and (live_name_counts.get(n, 0) > 1 or report_name_counts.get(n, 0) > 1)
    )
    events_incomplete = set((rep_res or {}).get("events_incomplete") or [])
    if rep_res:
        for uv in rep_res.get("vehicles", []):
            if uv["vehicle_name"] not in ambiguous_names:
                wialon_vehicle_map[uv["vehicle_name"]] = uv
        for ev in rep_res.get("events", []):
            if ev["vehicle_name"] not in ambiguous_names:
                wialon_events_by_veh[ev["vehicle_name"]].append(ev)

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
        v_reg_plate = v.get("registration_plate") or v.get("plate") or None
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
        # A shared name cannot be attributed to one unit, an unreadable cell cannot be parsed, and a value
        # from a guessed column position cannot be trusted: each makes that report total unknown (None).
        # The group report emits a row (with explicit zeros) for every member even without activity, so a
        # unit with no row was not covered by the report: unknown, not zero.
        v_unattributed = vname in ambiguous_names or vname not in report_name_counts
        v_unreadable = set(w_veh.get("incomplete_fields") or []) | set(rep_res.get("unlocated_fields") or [])
        v_idle_sec = None if v_unattributed or "total_idle_seconds" in v_unreadable else w_veh.get("total_idle_seconds", 0)
        v_engine_sec = None if v_unattributed or "engine_hours_seconds" in v_unreadable else w_veh.get("engine_hours_seconds", 0)
        v_total_sec = None if v_unattributed or "total_time_seconds" in v_unreadable else w_veh.get("total_time_seconds", 0)
        v_longest_sec = None if v_idle_sec is None else w_veh.get("longest_idle_seconds", 0)

        # Discrete events for this vehicle from Wialon
        raw_v_events = wialon_events_by_veh.get(vname, [])
        processed_v_events = []
        for idx, ev in enumerate(raw_v_events):
            ev_item = {
                "id": f"{vid}-{ev.get('start_time', idx)}",
                "vehicle_id": vid,
                "vehicle_name": vname,
                "registration_plate": v_reg_plate,
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

        all_events.extend(processed_v_events)
        # Unknown (None) when discrete events were not requested or could not be read completely. An unreadable
        # idle cell parses as 0, so the report never fetched that unit's idle events: they are unknown too.
        v_event_count = (
            len(processed_v_events)
            if include_events and vname not in events_incomplete and v_idle_sec is not None
            else None
        )

        by_vehicle.append({
            "vehicle_id": vid,
            "vehicle_name": vname,
            "registration_plate": v_reg_plate,
            "group_id": primary_grp["id"],
            "group_name": primary_grp["name"],
            "total_idle_seconds": v_idle_sec,
            "idle_minutes": round(v_idle_sec / 60.0, 2) if v_idle_sec is not None else None,
            "idle_events": v_event_count,
            "longest_idle_seconds": v_longest_sec,
            "engine_hours_seconds": v_engine_sec,
            "total_time_seconds": v_total_sec,
            "status": live_st,
            "idle_start": v_idle_st if live_st == "IDLING" else None,
            "idle_duration": v_idle_dur,
        })

        if live_st == "IDLING":
            currently_idling += 1
        if v_idle_sec:
            total_idle_vehicles += 1

        # Aggregate by group (a group containing an unattributable unit has an unknown total)
        gid = primary_grp["id"]
        if gid in group_agg:
            if v_idle_sec is None or group_agg[gid]["total_idle_seconds"] is None:
                group_agg[gid]["total_idle_seconds"] = None
            else:
                group_agg[gid]["total_idle_seconds"] += v_idle_sec
            if v_event_count is None or group_agg[gid]["idle_events"] is None:
                group_agg[gid]["idle_events"] = None
            else:
                group_agg[gid]["idle_events"] += v_event_count
            if live_st == "IDLING":
                group_agg[gid]["currently_idling"] += 1

    # ── Boundary Clipping & Qualifying Idle Event Selection ──────────────────────
    # Filter events strictly to those overlapping with the active reporting window [eff_from, eff_to].
    # Crucially preserve the authoritative discrete idle duration (ev["duration"]).
    qualifying_events = []
    for ev in all_events:
        ev_dur = ev.get("duration", 0)
        if ev_dur <= 0:
            continue
        ev_start = ev.get("start_time") or 0
        ev_end = ev.get("end_time") or (ev_start + ev_dur)
        if ev_start <= eff_to and ev_end >= eff_from:
            ev_copy = dict(ev)
            ev_copy["start_time"] = max(ev_start, eff_from)
            ev_copy["end_time"] = min(ev_end, eff_to)
            ev_copy["bounded_start_time"] = ev_copy["start_time"]
            ev_copy["bounded_end_time"] = ev_copy["end_time"]
            ev_copy["raw_start_time"] = ev_start
            ev_copy["raw_end_time"] = ev_end
            ev_copy["duration"] = ev_dur
            ev_copy["duration_minutes"] = round(ev_dur / 60.0, 2)
            qualifying_events.append(ev_copy)

    all_events = qualifying_events
    total_events_count = len(all_events)
    # Total idle time is the authoritative sum of Wialon vehicle idle totals; unattributable units are
    # left out and flagged via `total_idle_partial` rather than counted as zero
    known_idle_seconds = [v["total_idle_seconds"] for v in by_vehicle if v["total_idle_seconds"] is not None]
    total_idle_seconds = sum(known_idle_seconds)
    total_idle_partial = len(known_idle_seconds) < len(by_vehicle)
    # Event-based KPIs need every vehicle's discrete events; when events were not requested or could not be
    # read completely, the count, average and longest event are unknown (None), not 0 or a lower bound
    events_complete = all(bv["idle_events"] is not None for bv in by_vehicle)
    avg_idle_sec = round(total_idle_seconds / total_events_count, 2) if total_events_count > 0 else 0

    # Highest Idle: The single longest individual qualifying idling event within the selected reporting period
    longest_idle_event = None
    if not events_complete:
        avg_idle_sec = None
        longest_idle_seconds = None
        longest_idle_vehicle_id = None
        longest_idle_vehicle_name = None
        longest_idle_registration_plate = None
    elif all_events:
        longest_idle_event = max(all_events, key=lambda e: (e["duration"], -e["start_time"]))
        longest_idle_seconds = longest_idle_event["duration"]
        longest_idle_vehicle_id = longest_idle_event["vehicle_id"]
        longest_idle_vehicle_name = longest_idle_event["vehicle_name"]
        longest_idle_registration_plate = longest_idle_event.get("registration_plate")
    else:
        longest_idle_seconds = 0
        longest_idle_vehicle_id = None
        longest_idle_vehicle_name = None
        longest_idle_registration_plate = None

    # Update by_vehicle longest_idle_seconds based strictly on in-window qualifying events
    for bv in by_vehicle:
        if bv["total_idle_seconds"] is None or bv["idle_events"] is None:
            # Without this vehicle's complete event list its longest event is unknown
            bv["longest_idle_seconds"] = None
            continue
        v_events = [e for e in all_events if e["vehicle_id"] == bv["vehicle_id"]]
        bv["longest_idle_seconds"] = max((e["duration"] for e in v_events), default=0)

    # Format by_group list
    by_group = []
    for gid, gdata in group_agg.items():
        g_idle_sec = gdata["total_idle_seconds"]
        if gdata["vehicle_count"] > 0 or g_idle_sec is None or g_idle_sec > 0:
            if gdata["idle_events"] is None or g_idle_sec is None:
                avg_min = None
            else:
                avg_min = round((gdata["total_idle_seconds"] / gdata["idle_events"] / 60.0), 2) if gdata["idle_events"] > 0 else 0
            by_group.append({
                "group_id": gdata["group_id"],
                "group_name": gdata["group_name"],
                "vehicle_count": gdata["vehicle_count"],
                "total_idle_seconds": g_idle_sec,
                "idle_minutes": round(g_idle_sec / 60.0, 2) if g_idle_sec is not None else None,
                "idle_events": gdata["idle_events"],
                "currently_idling": gdata["currently_idling"],
                "average_idle_minutes": avg_min
            })

    # Unknown (None) totals sort after all known ones
    def _idle_sort_key(x):
        return (x["total_idle_seconds"] is not None, x["total_idle_seconds"] or 0)

    by_group.sort(key=_idle_sort_key, reverse=True)
    by_vehicle.sort(key=_idle_sort_key, reverse=True)

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
    trend_points = _build_idle_trend(all_events, eff_from, eff_to)

    # Sort all events chronologically newest first
    all_events.sort(key=lambda x: x["start_time"], reverse=True)

    total_fleet_vehicles = len(target_vehicles)

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
            "total_idle_partial": total_idle_partial,
            "average_idle_seconds": avg_idle_sec,
            "longest_idle_seconds": longest_idle_seconds,
            "longest_idle_vehicle_id": longest_idle_vehicle_id,
            "longest_idle_vehicle_name": longest_idle_vehicle_name,
            "longest_idle_registration_plate": longest_idle_registration_plate,
            "longest_idle_event": longest_idle_event,
            "highest_idle_event": longest_idle_event,
            "idle_event_count": total_events_count if events_complete else None,
            "idling_fleet_ratio": round((currently_idling / total_fleet_vehicles) * 100, 1) if total_fleet_vehicles > 0 else 0
        },
        "by_group": by_group,
        "by_vehicle": by_vehicle,
        "by_location": by_location[:10],
        "trend": trend_points,
        "events": all_events,
        # by_location, trend and events are built from discrete events: false when any vehicle's events were not
        # requested or could not be read completely (see by_vehicle idle_events None), so those sections are partial
        "events_complete": events_complete,
        "groups": valid_groups,
        "ambiguous_vehicle_names": ambiguous_names,
    }

    # Do not cache a report with unreadable cells, unread events or unlocated columns; the next request re-reads it
    report_incomplete = bool(
        rep_res.get("column_warnings")
        or rep_res.get("unlocated_fields")
        or events_incomplete
        or any(uv.get("incomplete_fields") for uv in rep_res.get("vehicles", []))
        or any(bv["vehicle_name"] not in report_name_counts for bv in by_vehicle)
    )
    if not report_incomplete:
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

    from app.ai.assistant import validate_chat_messages
    try:
        message_dicts = validate_chat_messages([
            {"role": m.role, "content": m.content}
            for m in request.messages
        ])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        from app.ai.assistant import run_assistant
        # run_assistant makes blocking Groq and Wialon calls; keep them off the event loop.
        reply = await run_in_threadpool(run_assistant, message_dicts)
        return {"reply": reply}
    except WialonAPIError:
        raise _internal_error("/api/ai/chat")
    except RuntimeError:
        # Raised when the assistant is not configured (e.g. missing GROQ_API_KEY); the text stays server-side.
        logging.exception("Fleet AI unavailable")
        raise HTTPException(status_code=503, detail="Fleet AI is not available. Contact your administrator.")
    except Exception as e:
        logging.exception("AI chat error")
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
        raise _internal_error("/api/fleet/health")


@app.get("/api/fleet/utilization")
@timed_calculation("/api/fleet/utilization")
def api_fleet_utilization(hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 24):
    """Fleet utilization breakdown: driving, idle, stopped, engine-on."""
    try:
        return get_fleet_utilization_analytics(hours=hours)
    except Exception as e:
        raise _internal_error("/api/fleet/utilization")


@app.get("/api/fleet/trips")
@timed_calculation("/api/fleet/trips")
def api_fleet_trips(
    hours: Annotated[int, Query(ge=1, le=MAX_REPORT_HOURS)] = 24,
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
            _require_window_within(eff_from, eff_to, MAX_REPORT_HOURS)
            # The caller's own bounds, not the resolved ones: the analytics cache key mirrors the resolver's
            # precedence, so resolved bounds would key every rolling timeframe to a unique, uncacheable window
            return get_trips_mileage_analytics(
                from_time=from_time,
                to_time=to_time,
                timeframe=timeframe,
                from_date=from_date,
                to_date=to_date
            )
        if from_time is not None and to_time is not None:
            _require_window_within(from_time, to_time, MAX_REPORT_HOURS)
        return get_trips_mileage_analytics(hours=hours, from_time=from_time, to_time=to_time)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _internal_error("/api/fleet/trips")


@app.get("/api/fleet/safety")
@timed_calculation("/api/fleet/safety")
def api_fleet_safety(hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 168):
    """Safety analytics: harsh braking, harsh acceleration, harsh cornering."""
    try:
        return get_safety_analytics(hours=hours)
    except Exception as e:
        raise _internal_error("/api/fleet/safety")


@app.get("/api/fleet/alerts")
@timed_calculation("/api/fleet/alerts")
def api_fleet_alerts():
    """Derived fleet alerts categorized by CRITICAL, WARNING, INFO."""
    try:
        return get_fleet_alerts_analytics()
    except Exception as e:
        raise _internal_error("/api/fleet/alerts")


@app.get("/api/fleet/groups")
@timed_calculation("/api/fleet/groups")
def api_fleet_groups():
    """Dynamic group rollups (distance, trips, idle, safety)."""
    try:
        return get_fleet_groups_analytics()
    except Exception as e:
        raise _internal_error("/api/fleet/groups")


@app.get("/api/vehicles/{vehicle_id}/analytics")
def api_vehicle_deep_analytics(vehicle_id: int, hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 24):
    """Comprehensive single vehicle drawer analytics & route points."""
    try:
        res = get_single_vehicle_deep_analytics(vehicle_id=vehicle_id, hours=hours)
        if not res.get("found"):
            raise HTTPException(status_code=404, detail="Vehicle not found")
        return res
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_error("/api/vehicles/{vehicle_id}/analytics")


@app.get("/api/vehicles/{vehicle_id}/route")
def api_vehicle_route(vehicle_id: int, hours: Annotated[int, Query(ge=1, le=MAX_HISTORY_HOURS)] = 24):
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
        raise _internal_error("/api/vehicles/{vehicle_id}/route")
