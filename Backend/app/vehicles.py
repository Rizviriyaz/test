import logging
import math
import time

from app.performance_diagnostics import request_trace_id
from app.utils.datetime_helpers import now_timestamp, to_iso_uae
from app.utils.status_config import CONN_DELAYED_SECONDS, CONN_STABLE_SECONDS

from app.wialon import (
    wialon_request,
    WialonAPIError,
    login,
    get_vehicle_history,
    get_vehicle_groups,
    history_truncated,
)

from app.status import (
    calculate_status,
    calculate_idle_duration,
    format_duration
)

from app.normalize import is_valid_sensor_value, resolve_unit_sensors

idle_start_times = {}
# Units whose seeded idle start is only a lower bound (see idle_duration_status below)
idle_start_lower_bound = set()

IDLE_DURATION_KNOWN = "known"
IDLE_DURATION_LOWER_BOUND = "lower_bound"
IDLE_DURATION_UNKNOWN = "unknown"

# History windows (hours) searched for the start of a vehicle's current idle period when it is first seen idling
# (e.g. after a restart). A wider window is loaded only when the idle run fills the previous one entirely.
IDLE_SEED_LOOKBACK_HOURS = (2, 24)


def parse_wialon_epoch(value):
    """
    A Wialon time (last message `lmsg.t`, GPS position `pos.t`) as int epoch seconds, or None when missing or
    malformed: None, a bool, a non-numeric or non-finite value, or a time <= 0 (Wialon sends 0 when there is no time).
    One unit's bad value is unknown, never a crash of the whole fleet response. Future times are passed through.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number)

def extract_registration_plate(unit):
    """
    Safely extract the verified vehicle registration plate from Wialon unit profile fields (pflds).
    Matches entry where n == 'registration_plate'.
    Returns cleaned string value if found, or None if absent/empty/malformed.
    """
    if not isinstance(unit, dict):
        return None
    pflds = unit.get("pflds")
    if not pflds:
        return None

    entries = pflds.values() if isinstance(pflds, dict) else (pflds if isinstance(pflds, list) else [])
    for field in entries:
        if isinstance(field, dict) and field.get("n") == "registration_plate":
            val = field.get("v")
            if val is not None:
                cleaned = str(val).strip()
                if cleaned:
                    return cleaned
    return None


def get_all_vehicles(sid):
    # Flags: 5121 | 8388608 (0x800000) = 8393729
    # 1: Base (id, nm, cls, mu)
    # 1024: Last message
    # 4096: Admin fields
    # 8388608: Profile fields (pflds)
    vehicle_data = wialon_request(
        "core/search_items",
        {
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name"
            },
            "force": 1,
            "flags": 8393729,
            "from": 0,
            "to": 0
        },
        sid
    )

    if isinstance(vehicle_data, dict) and "error" in vehicle_data:
        raise WialonAPIError(f"Wialon vehicle search failed: {vehicle_data}")

    vehicles = vehicle_data.get("items", [])

    return vehicles


def get_calculated_data(sid, vehicles):
    vehicle_ids = [
        vehicle.get("id")
        for vehicle in vehicles
        if vehicle.get("id")
    ]

    if not vehicle_ids:
        return []

    calculated_data = wialon_request(
        "unit/calc_last",
        {
            "itemIds": vehicle_ids
        },
        sid
    )

    if isinstance(calculated_data, dict) and "error" in calculated_data:
        raise WialonAPIError(f"Wialon calculated data request failed: {calculated_data}")

    return calculated_data


def get_sensor_by_name(vehicle, calculated, sensor_name):
    """
    Find a calculated sensor using the configured Wialon sensor name.
    Does not assume a fixed sensor ID.
    """

    target_name = sensor_name.strip().lower()

    configured_sensors = vehicle.get("sens") or {}
    calculated_sensors = calculated.get("sensors") or {}

    for sensor_id, sensor_config in configured_sensors.items():
        if not sensor_config:
            continue

        name = str(sensor_config.get("n", "")).strip().lower()

        if name != target_name:
            continue

        sensor_value = calculated_sensors.get(str(sensor_id))

        if sensor_value is None:
            return None

        return {
            "id": sensor_id,
            "name": sensor_config.get("n"),
            "value": sensor_value.get("value"),
            "formatted": (
                sensor_value.get("format", {}).get("value")
                if sensor_value.get("format")
                else None
            )
        }

    return None


def get_vehicle_live_data(sid, vehicles, calculated_data, groups=None):

    if groups is None:
        try:
            groups = get_vehicle_groups(sid)
        except Exception:
            raise

    unit_groups_map = {}
    for group in groups:
        group_name = group.get("nm", "").strip()
        if not group_name or group_name.lower() == "all unit":
            continue
        for uid in group.get("u", []):
            if uid not in unit_groups_map:
                unit_groups_map[uid] = []
            if group_name not in unit_groups_map[uid]:
                unit_groups_map[uid].append(group_name)

    calculated_by_vehicle = {
        vehicle["i"]: vehicle
        for vehicle in calculated_data
    }

    fleet = []
    current_time = now_timestamp()
    # PERF evidence for the first request after a restart: idle-start seeding reads history per vehicle
    seed_vehicles = seed_history_reads = 0
    seed_seconds = 0.0

    for vehicle in vehicles:
        vehicle_id = vehicle.get("id")
        vehicle_name = vehicle.get("nm", "Unknown")
        reg_plate = extract_registration_plate(vehicle)

        calculated = calculated_by_vehicle.get(vehicle_id) or {}

        # ---------------------------------------------------------
        # POSITION
        # ---------------------------------------------------------

        position = calculated.get("pos") or {}

        speed_data = position.get("s", 0)

        if isinstance(speed_data, dict):
            speed = speed_data.get("value", 0)
        else:
            speed = speed_data

        speed = speed or 0

        latitude = position.get("y")
        longitude = position.get("x")

        course = position.get("c")
        altitude_data = position.get("z") or {}
        satellites = position.get("sc")

        if isinstance(altitude_data, dict):
            altitude = altitude_data.get("value")
        else:
            altitude = altitude_data

        # ---------------------------------------------------------
        # SENSOR CONFIGURATION
        # ---------------------------------------------------------

        configured_sensors = vehicle.get("sens") or {}
        calculated_sensors = calculated.get("sensors") or {}

        sensors_by_name = {}

        for sensor_id, sensor_config in configured_sensors.items():
            sensor_name = (
                sensor_config.get("n", "").strip()
                if sensor_config
                else ""
            )

            if not sensor_name:
                continue

            sensor_value = calculated_sensors.get(
                str(sensor_id)
            )

            if not sensor_value:
                continue

            sensors_by_name[sensor_name.lower()] = {
                "id": sensor_id,
                "name": sensor_name,
                "value": sensor_value.get("value"),
                "formatted": (
                    sensor_value.get("format", {})
                    .get("value")
                )
            }

        # ---------------------------------------------------------
        # SENSOR RESOLUTION & EVALUATION
        # ---------------------------------------------------------

        sensor_map = resolve_unit_sensors(configured_sensors)
        ign_sid = sensor_map["ignition_id"]
        bat_sid = sensor_map["battery_id"]

        ignition_sensor = calculated_sensors.get(str(ign_sid)) if ign_sid else None
        battery_sensor = calculated_sensors.get(str(bat_sid)) if bat_sid else None

        ignition_value = (
            ignition_sensor.get("value")
            if ignition_sensor and is_valid_sensor_value(ignition_sensor.get("value"))
            else 0
        )

        ignition_on = (ignition_value == 1) or str(ignition_value).strip().lower() in ["1", "true", "on"]

        battery_voltage = (
            battery_sensor.get("value")
            if battery_sensor and is_valid_sensor_value(battery_sensor.get("value"))
            else None
        )

        # Wialon calculated mileage is the authoritative cumulative odometer.
        mileage_data = calculated.get("mileage") or {}
        mileage = mileage_data.get("value")
        odometer = mileage

        # ---------------------------------------------------------
        # ENGINE HOURS
        # ---------------------------------------------------------

        engine_hours_data = (
            calculated.get("engine_hours") or {}
        )

        engine_hours = engine_hours_data.get("value")

        # ---------------------------------------------------------
        # DRIVING STATUS & CONNECTIVITY
        # ---------------------------------------------------------

        # Last message time, else calc_last's position time (as before). Missing or malformed: None, so the unit is
        # OFFLINE with an unknown age, like a unit that never reported, instead of failing the whole response.
        last_msg = vehicle.get("lmsg")
        last_message_ts = parse_wialon_epoch(last_msg.get("t") if isinstance(last_msg, dict) else None)
        if last_message_ts is None:
            last_message_ts = parse_wialon_epoch(position.get("t"))
        seconds_since_update = (current_time - last_message_ts) if last_message_ts is not None else None
        # Time of the last GPS position (core/search_items `pos`, flag 1024); unit/calc_last's `pos` carries the speed
        # but no time. None when Wialon sends none or a malformed one: unknown, never treated as stale.
        search_pos = vehicle.get("pos")
        position_ts = parse_wialon_epoch(search_pos.get("t") if isinstance(search_pos, dict) else None)
        seconds_since_position = (current_time - position_ts) if position_ts is not None else None

        if seconds_since_update is None or seconds_since_update > CONN_DELAYED_SECONDS:
            connection_status = "OFFLINE"
        elif seconds_since_update > CONN_STABLE_SECONDS:
            connection_status = "DELAYED"
        else:
            connection_status = "STABLE"
        is_offline = (connection_status == "OFFLINE")

        # The state the last message describes. Stale telemetry last seen Moving/Idling is DELAYED, never shown as
        # live; a stale unit last seen Stopped (a parked tracker reports less often) stays STOPPED. Both become
        # OFFLINE past CONN_DELAYED_SECONDS.
        last_known_status = calculate_status(speed, ignition_on) if last_message_ts else None
        # R2: True only when the GPS-position rule below makes the unit DELAYED (messages are recent)
        position_stale = False
        if is_offline:
            status = "OFFLINE"
        elif connection_status == "DELAYED" and last_known_status in ("MOVING", "IDLING"):
            status = "DELAYED"
        elif last_known_status == "MOVING" and seconds_since_position is not None \
                and seconds_since_position > CONN_STABLE_SECONDS:
            # Messages without a new GPS fix keep the last position's speed: MOVING from a position older than
            # CONN_STABLE_SECONDS is not live (DELAYED; last_known_status MOVING, speed kept). IDLING/STOPPED are
            # not judged by position age: a stationary tracker may not send new positions.
            status = "DELAYED"
            position_stale = True
        else:
            status = last_known_status

        # ---------------------------------------------------------
        # IDLE DURATION
        # ---------------------------------------------------------

        # "known": idle_duration is the measured current idle run; "lower_bound": the run started before the
        # history searched (or no history message was found), so it lasted at least idle_duration; "unknown": the
        # history read failed, idle_duration is 0 and must not be shown as a duration (retried on the next request).
        idle_duration_status = IDLE_DURATION_KNOWN
        if status == "IDLING":

            if vehicle_id not in idle_start_times:

                seed_vehicles += 1
                seed_started = time.perf_counter()
                idle_duration_from_history = 0
                lower_bound = False
                try:
                    for lookback_hours in IDLE_SEED_LOOKBACK_HOURS:
                        seed_history_reads += 1
                        messages = get_vehicle_history(
                            sid,
                            vehicle_id,
                            from_time=current_time - lookback_hours * 3600,
                            to_time=current_time
                        )

                        idle_duration_from_history = (
                            calculate_idle_duration(
                                messages,
                                current_time,
                                configured_sensors
                            )
                        )

                        # The idle run began after the window's first message, so its real start was found.
                        # Otherwise it fills the whole window and is re-read over the next, wider one.
                        # Only data messages count, matching calculate_idle_duration.
                        first_ts = min((m["t"] for m in messages if m.get("tp") == "ud" and m.get("t")), default=None)
                        if first_ts is None:
                            # No data message in the window: the run's start is not known, only that it is ongoing
                            lower_bound = True
                            break
                        if current_time - idle_duration_from_history > first_ts:
                            lower_bound = history_truncated(messages)
                            break
                    else:
                        # The run fills even the widest window searched
                        lower_bound = True
                except Exception as exc:
                    # One unit's history failure must not fail the fleet response: its idle start is unknown
                    logging.warning("Idle start history read failed for vehicle %s; idle duration unknown: %s", vehicle_id, exc)
                    idle_duration_status = IDLE_DURATION_UNKNOWN
                else:
                    idle_start_times[
                        vehicle_id
                    ] = (
                        current_time
                        - idle_duration_from_history
                    )
                    if lower_bound:
                        idle_start_lower_bound.add(vehicle_id)
                    else:
                        idle_start_lower_bound.discard(vehicle_id)
                seed_seconds += time.perf_counter() - seed_started

            idle_start = idle_start_times.get(
                vehicle_id
            )

            if idle_start is not None:
                idle_duration = (
                    current_time - idle_start
                )

                if idle_duration < 0:
                    idle_duration = 0
                if vehicle_id in idle_start_lower_bound:
                    idle_duration_status = IDLE_DURATION_LOWER_BOUND
            else:
                idle_duration = 0
                idle_duration_status = IDLE_DURATION_UNKNOWN

        else:
            idle_start_times.pop(
                vehicle_id,
                None
            )
            idle_start_lower_bound.discard(vehicle_id)

            idle_duration = 0

        # ---------------------------------------------------------
        # SAFETY EVENTS
        # ---------------------------------------------------------

        harsh_acceleration = (
            sensors_by_name.get(
                "harsh acceleration"
            )
        )

        harsh_braking = (
            sensors_by_name.get(
                "harsh braking"
            )
        )

        harsh_cornering = (
            sensors_by_name.get(
                "harsh cornering"
            )
        )

        crash_detection = (
            sensors_by_name.get(
                "crash detection"
            )
        )

        # ---------------------------------------------------------
        # DRIVER
        # ---------------------------------------------------------

        driver_sensor = (
            sensors_by_name.get("driver id")
        )

        driver_id = (
            driver_sensor.get("value")
            if driver_sensor
            else None
        )

        v_groups = unit_groups_map.get(vehicle_id, ["General Fleet"])
        primary_group = v_groups[0] if v_groups else "General Fleet"

        # ---------------------------------------------------------
        # FINAL NORMALIZED VEHICLE OBJECT
        # ---------------------------------------------------------

        vehicle_record = {

            # Existing frontend fields & identity mapping
            "vehicle_id": vehicle_id,
            "vehicle_name": vehicle_name,
            "unit_name": vehicle_name,
            "registration_plate": reg_plate,
            "plate": reg_plate or vehicle_name,
            "plate_number": reg_plate or vehicle_name,
            "group": primary_group,
            "groups": v_groups,
            "speed": speed,
            "ignition": ignition_on,
            "status": status,
            "last_known_status": last_known_status,
            "connection_status": connection_status,
            "is_offline": is_offline,
            "seconds_since_update": seconds_since_update,
            "last_message_timestamp": last_message_ts,
            # GPS fix age, separate from message age (None when Wialon sends no or a malformed position time)
            "position_timestamp": position_ts,
            "seconds_since_position": seconds_since_position,
            "position_stale": position_stale,
            "idle_duration": idle_duration,
            # "known" | "lower_bound" (at least idle_duration) | "unknown" (idle_duration is 0 and not a measurement)
            "idle_duration_status": idle_duration_status,
            "idle_start": idle_start if status == "IDLING" else None,
            "idle_start_iso": to_iso_uae(idle_start) if status == "IDLING" and idle_start else None,
            "idle_duration_formatted": (
                format_duration(
                    idle_duration
                )
            ),
            "latitude": latitude,
            "longitude": longitude,

            # Location
            "location": {
                "latitude": latitude,
                "longitude": longitude,
                "speed": speed,
                "course": course,
                "altitude": altitude,
                "satellites": satellites
            },

            # Vehicle metrics
            "mileage": mileage,
            "odometer": odometer,
            "engine_hours": engine_hours,

            # Sensors
            "sensors": {
                "battery_voltage": battery_voltage,
                "ignition": ignition_value,
                "driver_id": driver_id
            },

            # Driver
            "driver": {
                "id": driver_id
            },

            # Idle
            "idle": {
                "is_idling": status == "IDLING",
                "duration": idle_duration,
                "duration_status": idle_duration_status,
                "start_time": idle_start if status == "IDLING" else None,
                "start_time_iso": to_iso_uae(idle_start) if status == "IDLING" and idle_start else None
            },

            # Safety
            "safety": {
                "harsh_acceleration": (
                    harsh_acceleration
                ),
                "harsh_braking": (
                    harsh_braking
                ),
                "harsh_cornering": (
                    harsh_cornering
                ),
                "crash_detection": (
                    crash_detection
                )
            }
        }

        fleet.append(vehicle_record)

    if seed_vehicles:
        logging.debug(
            "PERF idle_seed request_id=%s vehicles=%d history_reads=%d duration_ms=%.1f",
            request_trace_id.get(), seed_vehicles, seed_history_reads, seed_seconds * 1000,
        )
    return fleet
