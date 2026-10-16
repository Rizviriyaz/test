from app.utils.datetime_helpers import now_timestamp, to_iso_uae
from app.utils.status_config import CONN_DELAYED_SECONDS

from app.wialon import (
    wialon_request,
    WialonAPIError,
    login,
    get_vehicle_history,
    get_vehicle_groups
)

from app.status import (
    calculate_status,
    calculate_idle_duration,
    format_duration
)

from app.normalize import is_valid_sensor_value, resolve_unit_sensors

idle_start_times = {}

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

        last_message_ts = (vehicle.get("lmsg") or {}).get("t") or position.get("t")
        seconds_since_update = (current_time - int(last_message_ts)) if last_message_ts else None

        if seconds_since_update is None or seconds_since_update > CONN_DELAYED_SECONDS:
            connection_status = "OFFLINE"
        elif seconds_since_update > 600:
            connection_status = "DELAYED"
        else:
            connection_status = "STABLE"
        is_offline = (connection_status == "OFFLINE")

        if is_offline:
            status = "OFFLINE"
        else:
            status = calculate_status(
                speed,
                ignition_on
            )

        # ---------------------------------------------------------
        # IDLE DURATION
        # ---------------------------------------------------------

        if status == "IDLING":

            if vehicle_id not in idle_start_times:

                messages = get_vehicle_history(
                    sid,
                    vehicle_id,
                    hours=2
                )

                idle_duration_from_history = (
                    calculate_idle_duration(
                        messages,
                        current_time,
                        configured_sensors
                    )
                )

                idle_start_times[
                    vehicle_id
                ] = (
                    current_time
                    - idle_duration_from_history
                )

            idle_start = idle_start_times.get(
                vehicle_id
            )

            if idle_start is not None:
                idle_duration = (
                    current_time - idle_start
                )

                if idle_duration < 0:
                    idle_duration = 0
            else:
                idle_duration = 0

        else:
            idle_start_times.pop(
                vehicle_id,
                None
            )

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
            "connection_status": connection_status,
            "is_offline": is_offline,
            "seconds_since_update": seconds_since_update,
            "last_message_timestamp": last_message_ts,
            "idle_duration": idle_duration,
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

    return fleet
