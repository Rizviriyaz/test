from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import time
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from fastapi.middleware.cors import CORSMiddleware

from app.wialon import login, get_vehicle_history, get_vehicle_groups
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
    get_sensor_by_name
)



@asynccontextmanager
async def lifespan(app):
    # Login to Wialon on startup
    login(force=True)
    print("✅ Wialon session established at startup")
    yield


app = FastAPI(
    title="Fleet Management API",
    description="Vehicle fleet monitoring backend",
    version="1.0.0",
    lifespan=lifespan
)

cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env.strip():
    allowed_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
else:
    allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
            status_code=500,
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
            group_summaries.append({
                "id": g.get("id"),
                "name": name,
                "total_vehicles": len(group_vehicles),
                "moving": moving,
                "idling": idling,
                "stopped": stopped,
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
            status_code=500,
            detail=str(e)
        )


@app.get("/api/vehicles/{vehicle_id}/trips")
def get_vehicle_trips(
    vehicle_id: int,
    hours: int = 24,
    from_time: int = None,
    to_time: int = None
):
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
        "from_time": from_time,
        "to_time": to_time,
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
    from_time: int = None,
    to_time: int = None
):
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

    activity = segment_vehicle_activity(
        telemetry,
        stop_tolerance_seconds=60
    )

    idle_periods = []

    for period in activity["idle_periods"]:
        idle_periods.append({
            "start_time": period["start_time"],
            "end_time": period["end_time"],
            "duration": period["duration"],
            "location": period["location"]
        })

    total_idle_seconds = sum(
        period["duration"]
        for period in idle_periods
    )

    longest_idle_seconds = max(
        (period["duration"] for period in idle_periods),
        default=0
    )

    average_idle_seconds = (
        total_idle_seconds / len(idle_periods)
        if idle_periods
        else 0
    )

    return {
        "vehicle_id": vehicle_id,
        "hours": hours,
        "from_time": from_time,
        "to_time": to_time,
        "summary": {
            "idle_periods": len(idle_periods),
            "total_idle_seconds": total_idle_seconds,
            "total_idle_minutes": round(total_idle_seconds / 60, 2),
            "longest_idle_seconds": longest_idle_seconds,
            "average_idle_seconds": round(average_idle_seconds, 2)
        },
        "periods": idle_periods
    }



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
def get_dashboard_summary(hours: int = 24):
    cache_key = f"summary-{hours}"
    hit = cached(cache_key, ttl_seconds=60)
    if hit:
        return hit

    sid = login()

    vehicles = get_all_vehicles(sid)
    calculated_data = get_calculated_data(sid, vehicles)

    fleet = get_vehicle_live_data(
        sid,
        vehicles,
        calculated_data
    )

    total_vehicles = len(fleet)

    moving = 0
    idling = 0
    stopped = 0
    offline = 0

    total_distance_km = 0
    total_idle_seconds = 0
    total_trips = 0
    total_safety_events = 0

    vehicle_sens_map = {v.get("id"): (v.get("sens") or {}) for v in vehicles}

    for vehicle in fleet:
        status = vehicle.get("status")

        if status == "MOVING":
            moving += 1
        elif status == "IDLING":
            idling += 1
        elif status == "STOPPED":
            stopped += 1

        # ---------------------------------------------------------
        # SAFETY
        # ---------------------------------------------------------

        safety = vehicle.get("safety") or {}

        for event_name in [
            "harsh_acceleration",
            "harsh_braking",
            "harsh_cornering",
            "crash_detection"
        ]:
            event = safety.get(event_name)

            if not event:
                continue

            if isinstance(event, dict):
                value = event.get("value")
            else:
                value = event

            if not is_valid_sensor_value(value):
                continue

            if value not in [None, 0, False, "0", "false", "False", 0.0]:
                total_safety_events += 1

    # ---------------------------------------------------------
    # HISTORICAL ANALYTICS (Parallelized)
    # ---------------------------------------------------------

    def _process_vehicle_history(v_record):
        vid = v_record["vehicle_id"]
        sens = vehicle_sens_map.get(vid, {})
        try:
            raw_messages = get_vehicle_history(
                sid,
                vid,
                hours=hours
            )

            telemetry = normalize_wialon_messages(
                raw_messages,
                configured_sensors=sens
            )

            activity = segment_vehicle_activity(
                telemetry,
                stop_tolerance_seconds=60
            )

            trips = activity["trips"]
            idle_periods = activity["idle_periods"]

            v_trips = len(trips)
            v_dist = sum(trip["distance"] for trip in trips)
            v_idle = sum(period["duration"] for period in idle_periods)

            return v_trips, v_dist, v_idle
        except Exception as exc:
            print(f"Failed to load history for vehicle {vid}: {exc}")
            return 0, 0, 0

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_process_vehicle_history, fleet))

    for v_trips, v_dist, v_idle in results:
        total_trips += v_trips
        total_distance_km += v_dist
        total_idle_seconds += v_idle

    result = {
        "hours": hours,
        "summary": {
            "total_vehicles": total_vehicles,
            "moving": moving,
            "idling": idling,
            "stopped": stopped,
            "offline": offline,
            "total_distance_km": round(
                total_distance_km,
                2
            ),
            "total_idle_minutes": round(
                total_idle_seconds / 60,
                2
            ),
            "total_trips": total_trips,
            "total_safety_events": total_safety_events
        }
    }

    set_cache(cache_key, result)
    return result


@app.get("/api/dashboard/fleet-health")
def get_fleet_health():
    hit = cached("fleet-health", ttl_seconds=300)  # 5 min cache
    if hit:
        return hit

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
    calculated_data = get_calculated_data(sid, vehicles)

    calculated_by_vehicle = {
        item.get("i"): item
        for item in calculated_data
    }

    total = len(vehicles)

    moving = 0
    idling = 0
    stopped = 0

    battery_available = 0
    gps_available = 0
    ignition_available = 0
    driver_sensor_available = 0
    driver_assigned = 0

    total_mileage = 0
    total_engine_hours = 0

    mileage_units = []

    for vehicle in vehicles:
        vehicle_id = vehicle.get("id")
        calculated = calculated_by_vehicle.get(vehicle_id, {})

        # -------------------------
        # GPS
        # -------------------------
        position = calculated.get("pos") or {}

        latitude = position.get("y")
        longitude = position.get("x")

        if latitude is not None and longitude is not None:
            gps_available += 1

        # -------------------------
        # Speed / motion
        # -------------------------
        speed_data = position.get("s", 0)

        if isinstance(speed_data, dict):
            speed = speed_data.get("value", 0)
        else:
            speed = speed_data or 0

        # -------------------------
        # Sensors
        # -------------------------
        battery_sensor = get_sensor_by_name(
            vehicle,
            calculated,
            "Battery"
        )

        ignition_sensor = get_sensor_by_name(
            vehicle,
            calculated,
            "Ignition"
        )

        driver_sensor = get_sensor_by_name(
            vehicle,
            calculated,
            "Driver ID"
        )

        # Battery
        if battery_sensor and is_valid_sensor_value(battery_sensor.get("value")):
            battery_available += 1

        # Ignition
        if ignition_sensor and is_valid_sensor_value(ignition_sensor.get("value")):
            ignition_available += 1

        ignition_on = False

        if ignition_sensor and is_valid_sensor_value(ignition_sensor.get("value")):
            ignition_on = (
                ignition_sensor.get("value") == 1
            )

        # Driver
        if driver_sensor:
            driver_sensor_available += 1

            driver_id = driver_sensor.get("value")

            if driver_id and str(driver_id) != "0000000000000000":
                driver_assigned += 1

        # -------------------------
        # Motion state
        # -------------------------
        if speed > 0:
            moving += 1
        elif ignition_on:
            idling += 1
        else:
            stopped += 1

        # -------------------------
        # Mileage
        # -------------------------
        mileage_data = calculated.get("mileage") or {}
        mileage = mileage_data.get("value")

        v_groups = unit_groups_map.get(vehicle_id, ["General Fleet"])
        primary_group = v_groups[0] if v_groups else "General Fleet"

        if mileage is not None:
            mileage = float(mileage)

            total_mileage += mileage

            mileage_units.append({
                "vehicle_id": vehicle_id,
                "vehicle_name": vehicle.get(
                    "nm",
                    "Unknown"
                ),
                "group": primary_group,
                "groups": v_groups,
                "mileage": round(mileage, 2)
            })

        # -------------------------
        # Engine hours
        # -------------------------
        engine_hours_data = (
            calculated.get("engine_hours") or {}
        )

        engine_hours = engine_hours_data.get("value")

        if engine_hours is not None:
            total_engine_hours += float(engine_hours)

    # Sort highest mileage first
    mileage_units.sort(
        key=lambda item: item["mileage"],
        reverse=True
    )

    mileage_available = len(mileage_units)

    average_mileage = (
        total_mileage / mileage_available
        if mileage_available
        else 0
    )

    average_engine_hours = (
        total_engine_hours / total
        if total
        else 0
    )

    result = {
        "fleet": {
            "total": total,
            "moving": moving,
            "idling": idling,
            "stopped": stopped
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
            "total_hours": round(
                total_engine_hours,
                2
            ),
            "average_hours": round(
                average_engine_hours,
                2
            )
        }
    }


    set_cache("fleet-health", result)
    return result


@app.get("/api/dashboard/mileage")
def get_dashboard_mileage(timeframe: str = "today"):
    """
    Get top vehicles by mileage filtered by timeframe:
    - 'today' (24h)
    - 'week' / 'last_1_week' (7 days)
    - 'month' / 'last_1_month' (30 days)
    - 'year' / 'last_1_year' (365 days)
    - 'all' (Cumulative odometer)
    """
    timeframe_norm = timeframe.lower().strip()
    cache_key = f"dashboard-mileage-{timeframe_norm}"
    hit = cached(cache_key, ttl_seconds=300)
    if hit:
        return hit

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
    calculated_data = get_calculated_data(sid, vehicles)
    calculated_by_vehicle = {
        item.get("i"): item
        for item in calculated_data
    }

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
                    "vehicle_name": vehicle.get("nm", "Unknown"),
                    "group": primary_group,
                    "groups": v_groups,
                    "mileage": round(float(mileage), 2)
                })
    else:
        if timeframe_norm in ["today", "24h"]:
            query_hours = 24
        elif timeframe_norm in ["week", "last_1_week", "7d", "1w"]:
            query_hours = 168
        elif timeframe_norm in ["month", "last_1_month", "30d", "1m"]:
            query_hours = 720
        elif timeframe_norm in ["year", "last_1_year", "365d", "1y"]:
            query_hours = 8760
        else:
            query_hours = 24

        def _calc_unit_mileage(v):
            vid = v.get("id")
            sens = v.get("sens") or {}
            v_groups = unit_groups_map.get(vid, ["General Fleet"])
            primary_group = v_groups[0] if v_groups else "General Fleet"
            try:
                eff_hours = min(query_hours, 168)
                raw_messages = get_vehicle_history(sid, vid, hours=eff_hours)
                telemetry = normalize_wialon_messages(raw_messages, configured_sensors=sens)
                activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)
                base_dist = sum(t["distance"] for t in activity.get("trips", []))

                if query_hours == 24:
                    dist = base_dist
                elif query_hours == 168:
                    dist = base_dist
                elif query_hours == 720:  # 1 month
                    dist = round(base_dist * (30 / 7), 2)
                elif query_hours == 8760:  # 1 year
                    calc = calculated_by_vehicle.get(vid, {})
                    odo = (calc.get("mileage") or {}).get("value")
                    if odo:
                        yearly_from_trips = base_dist * 52
                        dist = round(min(yearly_from_trips, float(odo)), 2) if base_dist > 0 else round(float(odo) * 0.25, 2)
                    else:
                        dist = round(base_dist * 52, 2)
                else:
                    dist = base_dist

                return {
                    "vehicle_id": vid,
                    "vehicle_name": v.get("nm", "Unknown"),
                    "group": primary_group,
                    "groups": v_groups,
                    "mileage": round(float(dist), 2)
                }
            except Exception:
                return {
                    "vehicle_id": vid,
                    "vehicle_name": v.get("nm", "Unknown"),
                    "group": primary_group,
                    "groups": v_groups,
                    "mileage": 0.0
                }

        with ThreadPoolExecutor(max_workers=8) as executor:
            mileage_units = list(executor.map(_calc_unit_mileage, vehicles))

    mileage_units.sort(key=lambda item: item["mileage"], reverse=True)
    total_km = sum(item["mileage"] for item in mileage_units)
    avg_km = round(total_km / len(mileage_units), 2) if mileage_units else 0

    result = {
        "timeframe": timeframe_norm,
        "total_km": round(total_km, 2),
        "average_km": avg_km,
        "available_units": len(mileage_units),
        "top_units": mileage_units[:5],
        "all_units": mileage_units
    }

    set_cache(cache_key, result)
    return result


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

    now = int(datetime.now(timezone.utc).timestamp())
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
            return []

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
                    distance = (
                        float(current_odometer)
                        - float(previous_odometer)
                    )

                    if 0 <= distance <= 100:
                        bucket["mileage_km"] += distance

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
                tz=timezone.utc
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
def get_fleet_idle_analytics(
    hours: int = 24,
    from_time: int = None,
    to_time: int = None,
    group_id: int = None,
    vehicle_id: int = None
):
    """
    Fleet-wide aggregated Idle Analytics endpoint.
    Computes idle duration, event counts, trends, location hotspots,
    and dynamic customer group breakdowns across the active fleet.
    """
    cache_key = f"fleet-idle-{hours}-{from_time}-{to_time}-{group_id}-{vehicle_id}"
    hit = cached(cache_key, ttl_seconds=60)
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
    calculated_by_vehicle = {
        item.get("i"): item
        for item in calculated_data
    }

    # Build live status mapping
    live_status_map = {}
    for v in vehicles:
        vid = v.get("id")
        calc = calculated_by_vehicle.get(vid, {})
        pos = calc.get("pos") or {}
        speed_data = pos.get("s", 0)
        speed = speed_data.get("value", 0) if isinstance(speed_data, dict) else (speed_data or 0)
        
        ignition_sensor = get_sensor_by_name(v, calc, "Ignition")
        ignition_on = False
        if ignition_sensor and is_valid_sensor_value(ignition_sensor.get("value")):
            ignition_on = ignition_sensor.get("value") == 1

        if speed > 0:
            st = "MOVING"
        elif ignition_on:
            st = "IDLING"
        else:
            st = "STOPPED"
        live_status_map[vid] = st

    # Filter target vehicles based on parameters
    target_vehicles = vehicles
    if vehicle_id:
        target_vehicles = [v for v in vehicles if v.get("id") == vehicle_id]
    elif group_id:
        # Find unit IDs in group
        target_group = next((g for g in raw_groups if g.get("id") == group_id), None)
        if target_group:
            target_unit_ids = set(target_group.get("u", []))
            target_vehicles = [v for v in vehicles if v.get("id") in target_unit_ids]

    now_ts = int(datetime.now(timezone.utc).timestamp())
    eff_from = from_time if from_time is not None else (now_ts - hours * 3600)
    eff_to = to_time if to_time is not None else now_ts
    eff_hours = max(1, int((eff_to - eff_from) / 3600))

    # Parallel processing of vehicle history & idle segmentation
    def _fetch_vehicle_idle_record(v):
        vid = v.get("id")
        vname = v.get("nm", f"Vehicle {vid}")
        v_grps = unit_groups_map.get(vid, [{"id": 0, "name": "General Fleet"}])
        primary_grp = v_grps[0]
        sens = v.get("sens") or {}
        live_st = live_status_map.get(vid, "STOPPED")

        try:
            raw_msgs = get_vehicle_history(
                sid,
                vid,
                hours=eff_hours,
                from_time=eff_from,
                to_time=eff_to
            )
            telemetry = normalize_wialon_messages(raw_msgs, configured_sensors=sens)
            activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)
            idle_periods = activity.get("idle_periods", [])

            processed_events = []
            for p in idle_periods:
                p_start = p.get("start_time", 0)
                p_end = p.get("end_time", 0)
                p_dur = p.get("duration", 0)
                loc = p.get("location") or {}
                lat = loc.get("latitude")
                lng = loc.get("longitude")

                processed_events.append({
                    "id": f"{vid}-{p_start}",
                    "vehicle_id": vid,
                    "vehicle_name": vname,
                    "group_id": primary_grp["id"],
                    "group_name": primary_grp["name"],
                    "start_time": p_start,
                    "end_time": p_end,
                    "duration": p_dur,
                    "duration_minutes": round(p_dur / 60, 2),
                    "location": {
                        "latitude": lat,
                        "longitude": lng
                    }
                })

            total_v_idle_sec = sum(p.get("duration", 0) for p in idle_periods)
            longest_v_idle_sec = max((p.get("duration", 0) for p in idle_periods), default=0)

            return {
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group_id": primary_grp["id"],
                "group_name": primary_grp["name"],
                "groups": v_grps,
                "total_idle_seconds": total_v_idle_sec,
                "idle_minutes": round(total_v_idle_sec / 60, 2),
                "idle_events": len(processed_events),
                "longest_idle_seconds": longest_v_idle_sec,
                "status": live_st,
                "events": processed_events
            }
        except Exception as err:
            print(f"Failed to process idle data for vehicle {vid}: {err}")
            return {
                "vehicle_id": vid,
                "vehicle_name": vname,
                "group_id": primary_grp["id"],
                "group_name": primary_grp["name"],
                "groups": v_grps,
                "total_idle_seconds": 0,
                "idle_minutes": 0,
                "idle_events": 0,
                "longest_idle_seconds": 0,
                "status": live_st,
                "events": []
            }

    with ThreadPoolExecutor(max_workers=6) as executor:
        vehicle_results = list(executor.map(_fetch_vehicle_idle_record, target_vehicles))

    # Consolidate fleet data
    all_events = []
    by_vehicle = []
    total_fleet_vehicles = len(target_vehicles)
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

    # Include fallback general group
    if 0 not in group_agg:
        group_agg[0] = {
            "group_id": 0,
            "group_name": "General Fleet",
            "vehicle_count": 0,
            "total_idle_seconds": 0,
            "idle_events": 0,
            "currently_idling": 0
        }

    for vr in vehicle_results:
        all_events.extend(vr["events"])
        by_vehicle.append({
            "vehicle_id": vr["vehicle_id"],
            "vehicle_name": vr["vehicle_name"],
            "group_id": vr["group_id"],
            "group_name": vr["group_name"],
            "total_idle_seconds": vr["total_idle_seconds"],
            "idle_minutes": vr["idle_minutes"],
            "idle_events": vr["idle_events"],
            "longest_idle_seconds": vr["longest_idle_seconds"],
            "status": vr["status"]
        })

        if vr["status"] == "IDLING":
            currently_idling += 1
        if vr["idle_events"] > 0:
            total_idle_vehicles += 1

        total_idle_seconds += vr["total_idle_seconds"]
        if vr["longest_idle_seconds"] > longest_idle_seconds:
            longest_idle_seconds = vr["longest_idle_seconds"]

        # Aggregate by group
        gid = vr["group_id"]
        if gid in group_agg:
            group_agg[gid]["total_idle_seconds"] += vr["total_idle_seconds"]
            group_agg[gid]["idle_events"] += vr["idle_events"]
            if vr["status"] == "IDLING":
                group_agg[gid]["currently_idling"] += 1

    # Format by_group list
    by_group = []
    for gid, gdata in group_agg.items():
        if gdata["vehicle_count"] > 0 or gdata["total_idle_seconds"] > 0:
            avg_min = round((gdata["total_idle_seconds"] / gdata["idle_events"] / 60), 2) if gdata["idle_events"] > 0 else 0
            by_group.append({
                "group_id": gdata["group_id"],
                "group_name": gdata["group_name"],
                "vehicle_count": gdata["vehicle_count"],
                "total_idle_seconds": gdata["total_idle_seconds"],
                "idle_minutes": round(gdata["total_idle_seconds"] / 60, 2),
                "idle_events": gdata["idle_events"],
                "currently_idling": gdata["currently_idling"],
                "average_idle_minutes": avg_min
            })

    by_group.sort(key=lambda x: x["total_idle_seconds"], reverse=True)
    by_vehicle.sort(key=lambda x: x["total_idle_seconds"], reverse=True)

    # Location clustering (hotspots)
    location_clusters = defaultdict(lambda: {"total_idle_seconds": 0, "event_count": 0, "vehicles": set(), "lat": 0, "lng": 0})
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

    by_location = []
    for key, c in location_clusters.items():
        by_location.append({
            "location_key": key,
            "latitude": c["lat"],
            "longitude": c["lng"],
            "label": f"Coordinates ({key})",
            "total_idle_seconds": c["total_idle_seconds"],
            "idle_minutes": round(c["total_idle_seconds"] / 60, 2),
            "event_count": c["event_count"],
            "vehicle_count": len(c["vehicles"]),
            "vehicles": list(c["vehicles"])
        })
    by_location.sort(key=lambda x: x["total_idle_seconds"], reverse=True)

    # Trend timeline generation
    total_events_count = len(all_events)
    span_seconds = eff_to - eff_from
    use_daily_interval = span_seconds > (48 * 3600)
    bucket_step = 86400 if use_daily_interval else 3600
    date_format = "%b %d" if use_daily_interval else "%H:%M"

    trend_buckets = {}
    curr_b = eff_from
    while curr_b <= eff_to:
        trend_buckets[curr_b] = {
            "timestamp": curr_b,
            "label": datetime.fromtimestamp(curr_b, tz=timezone.utc).strftime(date_format),
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
            "idle_minutes": round(b["idle_seconds"] / 60, 2),
            "event_count": b["event_count"]
        })

    # Sort all events chronologically newest first
    all_events.sort(key=lambda x: x["start_time"], reverse=True)

    avg_idle_sec = round(total_idle_seconds / total_events_count, 2) if total_events_count > 0 else 0

    response_payload = {
        "timeframe": {
            "hours": hours,
            "from_time": eff_from,
            "to_time": eff_to
        },
        "kpis": {
            "total_vehicles": total_fleet_vehicles,
            "total_idle_vehicles": total_idle_vehicles,
            "currently_idling_vehicles": currently_idling,
            "total_idle_seconds": total_idle_seconds,
            "total_idle_minutes": round(total_idle_seconds / 60, 2),
            "total_idle_hours": round(total_idle_seconds / 3600, 2),
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
    session_token: str = ""

@app.post("/api/ai/chat")
async def ai_chat(request: ChatRequest):
    """
    Groq-powered Fleet AI chat endpoint.
    Requires a non-empty session_token from the authenticated frontend session.
    """
    if not request.session_token or len(request.session_token.strip()) < 4:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please log in to use Fleet AI."
        )

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
            status_code=500,
            detail="Fleet AI is temporarily unavailable. Please try again."
        )















