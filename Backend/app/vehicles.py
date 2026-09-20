from datetime import datetime, timezone

from app.wialon import (
    wialon_request,
    login,
    get_vehicle_history,
    get_vehicle_groups
)

from app.status import (
    calculate_status,
    calculate_idle_duration,
    format_duration
)

from app.normalize import is_valid_sensor_value

idle_start_times = {}

def get_all_vehicles(sid):
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
            "flags": 5121,
            "from": 0,
            "to": 0
        },
        sid
    )

    if "error" in vehicle_data:
        print("Vehicle search failed:")
        print(vehicle_data)
        return []

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

    if "error" in calculated_data:
        print("Calculated data request failed:")
        print(calculated_data)
        return []

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
            groups = []

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
    current_time = int(
        datetime.now(timezone.utc).timestamp()
    )


    for vehicle in vehicles:
        vehicle_id = vehicle.get("id")
        vehicle_name = vehicle.get("nm", "Unknown")

        calculated = calculated_by_vehicle.get(vehicle_id)

        if not calculated:
            continue

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
        # IGNITION
        # ---------------------------------------------------------

        ignition_sensor = sensors_by_name.get("ignition")

        ignition_value = (
            ignition_sensor.get("value")
            if ignition_sensor and is_valid_sensor_value(ignition_sensor.get("value"))
            else 0
        )

        ignition_on = ignition_value == 1

        # ---------------------------------------------------------
        # BATTERY
        # ---------------------------------------------------------

        battery_sensor = sensors_by_name.get("battery")

        battery_voltage = (
            battery_sensor.get("value")
            if battery_sensor and is_valid_sensor_value(battery_sensor.get("value"))
            else None
        )

        # ---------------------------------------------------------
        # ODOMETER
        # ---------------------------------------------------------

        odometer_sensor = sensors_by_name.get("odometer")

        odometer = (
            odometer_sensor.get("value")
            if odometer_sensor and is_valid_sensor_value(odometer_sensor.get("value"))
            else None
        )

        # Wialon calculated mileage is also available.
        mileage_data = calculated.get("mileage") or {}

        mileage = mileage_data.get("value")

        # ---------------------------------------------------------
        # ENGINE HOURS
        # ---------------------------------------------------------

        engine_hours_data = (
            calculated.get("engine_hours") or {}
        )

        engine_hours = engine_hours_data.get("value")

        # ---------------------------------------------------------
        # DRIVING STATUS
        # ---------------------------------------------------------

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
                    hours=24
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

            # Existing frontend fields
            "vehicle_id": vehicle_id,
            "vehicle_name": vehicle_name,
            "group": primary_group,
            "groups": v_groups,
            "speed": speed,
            "ignition": ignition_on,
            "status": status,
            "idle_duration": idle_duration,
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
                "duration": idle_duration
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