INVALID_WIALON_VALUE = -348201.3876


def is_valid_sensor_value(value):
    """
    Check whether a Wialon sensor value is usable.
    Filters out None, invalid sentinels (-348201.3876), and empty/null string placeholders.
    """

    if value is None:
        return False

    if isinstance(value, (int, float)):
        if value == INVALID_WIALON_VALUE or abs(value - INVALID_WIALON_VALUE) < 0.001:
            return False

    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {
            "",
            "---",
            "null",
            "none",
            "n/a",
            "na",
            "-348201.3876",
            "invalid"
        }:
            return False
        try:
            num_val = float(cleaned)
            if abs(num_val - INVALID_WIALON_VALUE) < 0.001:
                return False
        except (ValueError, TypeError):
            pass

    return True


def get_sensor_value(sensors, sensor_id):
    """
    Safely retrieve a sensor value from a Wialon message.
    """

    sensor = sensors.get(str(sensor_id))

    if not sensor:
        return None

    value = sensor.get("value")

    if not is_valid_sensor_value(value):
        return None

    return value


def normalize_driver_id(driver_id):
    """
    Convert Wialon placeholder driver IDs into None.
    """

    if not is_valid_sensor_value(driver_id):
        return None

    if str(driver_id) == "0000000000000000":
        return None

    return str(driver_id)


def resolve_sensor_id_by_name(configured_sensors, name):
    """
    Find sensor ID matching the sensor name (case-insensitive).
    """
    if not configured_sensors:
        return None

    target = name.strip().lower()
    for sid, sconfig in configured_sensors.items():
        if not sconfig:
            continue
        if str(sconfig.get("n", "")).strip().lower() == target:
            return str(sid)

    return None


def normalize_wialon_message(message, configured_sensors=None, sensor_id_map=None):
    """
    Convert a raw Wialon history message into
    a clean, application-friendly telemetry object.

    Returns None when the message does not contain
    usable GPS telemetry.
    """

    position = message.get("pos") or {}
    sensors = message.get("sensors") or {}

    latitude = position.get("y")
    longitude = position.get("x")

    # GPS is required for a usable telemetry point.
    if latitude is None or longitude is None:
        return None

    timestamp = message.get("t")

    if timestamp is None:
        return None

    speed = position.get("s", 0)

    if not is_valid_sensor_value(speed):
        speed = 0

    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 0

    # Build or use cached sensor ID mapping
    if sensor_id_map is None:
        sensor_id_map = {}
        if configured_sensors:
            for sid, sconfig in configured_sensors.items():
                if sconfig and "n" in sconfig:
                    sensor_id_map[str(sconfig["n"]).strip().lower()] = str(sid)

    # Dynamic sensor lookup with graceful fallbacks
    ign_id = sensor_id_map.get("ignition") or "11"
    key_id = sensor_id_map.get("key") or "14"
    bat_id = sensor_id_map.get("battery") or "1"
    odo_id = sensor_id_map.get("odometer") or "12"
    drv_id = sensor_id_map.get("driver id") or "3"

    ignition_val = get_sensor_value(sensors, ign_id)
    key_val = get_sensor_value(sensors, key_id)
    battery_voltage = get_sensor_value(sensors, bat_id)
    odometer = get_sensor_value(sensors, odo_id)
    driver_id = get_sensor_value(sensors, drv_id)

    # Normalize binary sensors.
    ignition = True if ignition_val == 1 else False
    key = True if key_val == 1 else False

    # Driver placeholder means no assigned driver.
    driver_id = normalize_driver_id(driver_id)

    # Determine vehicle state.
    if speed > 0:
        status = "MOVING"
    elif ignition:
        status = "IDLING"
    else:
        status = "STOPPED"

    return {
        "timestamp": timestamp,

        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "course": position.get("c"),
            "altitude": position.get("z"),
            "satellites": position.get("sc"),
        },

        "status": {
            "state": status,
            "speed": speed,
            "ignition": ignition,
            "key": key,
        },

        "metrics": {
            "odometer": odometer,
        },

        "health": {
            "battery_voltage": battery_voltage,
        },

        "driver_id": driver_id,
    }


def normalize_vehicle_history(messages, configured_sensors=None):
    """
    Normalize a complete Wialon message collection.

    Invalid messages are removed and valid messages
    are sorted chronologically.
    """

    sensor_id_map = {}
    if configured_sensors:
        for sid, sconfig in configured_sensors.items():
            if sconfig and "n" in sconfig:
                sensor_id_map[str(sconfig["n"]).strip().lower()] = str(sid)

    normalized = []

    for message in messages:
        telemetry = normalize_wialon_message(
            message,
            configured_sensors=configured_sensors,
            sensor_id_map=sensor_id_map
        )

        if telemetry is not None:
            normalized.append(telemetry)

    # Oldest -> newest
    normalized.sort(
        key=lambda item: item["timestamp"]
    )

    return normalized


normalize_wialon_messages = normalize_vehicle_history


def segment_vehicle_activity(
    telemetry,
    stop_tolerance_seconds=60,
    minimum_idle_seconds=30,
    minimum_stop_seconds=30
):
    """
    Convert normalized telemetry into historical
    activity segments.

    Returns:
        {
            "trips": [...],
            "idle_periods": [...],
            "stops": [...]
        }

    Rules:

    MOVING:
        speed > 0

    IDLING:
        speed == 0 + ignition ON

    STOPPED:
        speed == 0 + ignition OFF

    A trip is ended only after the vehicle has remained
    non-moving for stop_tolerance_seconds.

    Very short idle/stop periods are ignored.
    """

    if not telemetry:
        return {
            "trips": [],
            "idle_periods": [],
            "stops": []
        }

    # ------------------------------------------------------
    # 1. Sort chronologically
    # ------------------------------------------------------

    telemetry = sorted(
        telemetry,
        key=lambda item: item["timestamp"]
    )

    # ------------------------------------------------------
    # 2. Remove duplicate timestamps
    # ------------------------------------------------------

    cleaned = []
    previous_timestamp = None

    for point in telemetry:

        timestamp = point.get("timestamp")

        if timestamp is None:
            continue

        if timestamp == previous_timestamp:
            continue

        cleaned.append(point)
        previous_timestamp = timestamp

    telemetry = cleaned

    if not telemetry:
        return {
            "trips": [],
            "idle_periods": [],
            "stops": []
        }

    trips = []
    idle_periods = []
    stops = []

    current_trip = None

    pending_non_moving = []

    current_idle = None
    current_stop = None

    # ------------------------------------------------------
    # Helper functions
    # ------------------------------------------------------

    def close_idle(end_time):
        nonlocal current_idle

        if current_idle is None:
            return

        duration = (
            end_time
            - current_idle["start_time"]
        )

        if duration >= minimum_idle_seconds:

            current_idle["end_time"] = end_time
            current_idle["duration"] = duration

            idle_periods.append(
                current_idle
            )

        current_idle = None

    def close_stop(end_time):
        nonlocal current_stop

        if current_stop is None:
            return

        duration = (
            end_time
            - current_stop["start_time"]
        )

        if duration >= minimum_stop_seconds:

            current_stop["end_time"] = end_time
            current_stop["duration"] = duration

            stops.append(
                current_stop
            )

        current_stop = None

    def start_idle(point):
        nonlocal current_idle

        if current_idle is None:

            current_idle = {
                "start_time": point["timestamp"],
                "end_time": None,
                "duration": 0,
                "location": point["location"]
            }

    def start_stop(point):
        nonlocal current_stop

        if current_stop is None:

            current_stop = {
                "start_time": point["timestamp"],
                "end_time": None,
                "duration": 0,
                "location": point["location"]
            }

    # ------------------------------------------------------
    # 3. Process telemetry
    # ------------------------------------------------------

    for index, point in enumerate(telemetry):

        timestamp = point["timestamp"]

        status = point["status"]["state"]

        speed = point["status"]["speed"]

        ignition = point["status"]["ignition"]

        # --------------------------------------------------
        # MOVING
        # --------------------------------------------------

        if status == "MOVING":

            # Any pending non-moving period was not long
            # enough to terminate the trip.
            pending_non_moving = []

            # Close idle period.
            if current_idle is not None:
                close_idle(timestamp)

            # Close stopped period.
            if current_stop is not None:
                close_stop(timestamp)

            # Start trip.
            if current_trip is None:

                current_trip = {
                    "start_time": timestamp,
                    "end_time": None,

                    "start_location": point["location"],
                    "end_location": None,

                    "max_speed": speed,

                    "distance": 0,

                    "points": 1
                }

            else:

                # ------------------------------------------
                # Calculate odometer delta
                # ------------------------------------------

                previous_point = telemetry[index - 1]

                previous_odo = (
                    previous_point["metrics"]
                    .get("odometer")
                )

                current_odo = (
                    point["metrics"]
                    .get("odometer")
                )

                if (
                    previous_odo is not None
                    and current_odo is not None
                    and current_odo >= previous_odo
                ):

                    current_trip["distance"] += (
                        current_odo - previous_odo
                    ) / 10

                current_trip["max_speed"] = max(
                    current_trip["max_speed"],
                    speed
                )

                current_trip["points"] += 1

            continue

        # --------------------------------------------------
        # NON-MOVING WHILE A TRIP IS ACTIVE
        # --------------------------------------------------

        if current_trip is not None:

            if not pending_non_moving:

                pending_non_moving = [point]

            else:

                pending_non_moving.append(point)

            pending_duration = (
                timestamp
                - pending_non_moving[0]["timestamp"]
            )

            # Do not end trip yet.
            if pending_duration < stop_tolerance_seconds:
                continue

            # ----------------------------------------------
            # Confirm trip end
            # ----------------------------------------------

            trip_end_point = pending_non_moving[0]

            current_trip["end_time"] = (
                trip_end_point["timestamp"]
            )

            current_trip["end_location"] = (
                trip_end_point["location"]
            )

            current_trip["duration"] = (
                current_trip["end_time"]
                - current_trip["start_time"]
            )

            trips.append(current_trip)

            current_trip = None

            # ----------------------------------------------
            # Start the confirmed non-moving activity
            # ----------------------------------------------

            if ignition:

                start_idle(
                    pending_non_moving[0]
                )

            else:

                start_stop(
                    pending_non_moving[0]
                )

            pending_non_moving = []

            continue

        # --------------------------------------------------
        # IDLING
        # --------------------------------------------------

        if status == "IDLING":

            if current_stop is not None:
                close_stop(timestamp)

            start_idle(point)

            continue

        # --------------------------------------------------
        # STOPPED
        # --------------------------------------------------

        if status == "STOPPED":

            if current_idle is not None:
                close_idle(timestamp)

            start_stop(point)

            continue

    # ------------------------------------------------------
    # 4. Close final trip
    # ------------------------------------------------------

    if current_trip is not None:

        final_point = telemetry[-1]

        current_trip["end_time"] = (
            final_point["timestamp"]
        )

        current_trip["end_location"] = (
            final_point["location"]
        )

        current_trip["duration"] = (
            current_trip["end_time"]
            - current_trip["start_time"]
        )

        trips.append(current_trip)

    # ------------------------------------------------------
    # 5. Close final idle/stop
    # ------------------------------------------------------

    final_timestamp = telemetry[-1]["timestamp"]

    if current_idle is not None:
        close_idle(final_timestamp)

    if current_stop is not None:
        close_stop(final_timestamp)

    return {
        "trips": trips,
        "idle_periods": idle_periods,
        "stops": stops
    }

