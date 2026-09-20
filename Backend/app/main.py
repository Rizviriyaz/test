from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import os
import time
import traceback

from fastapi import FastAPI, HTTPException
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















