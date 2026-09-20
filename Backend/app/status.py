def calculate_status(speed, ignition_on):

    if speed > 0:
        return "MOVING"

    elif ignition_on:
        return "IDLING"

    else:
        return "STOPPED"


def calculate_idle_duration(
    messages,
    current_time,
    configured_sensors
):
    """
    Calculate the current continuous idling duration.

    The vehicle must currently be IDLING.
    Historical messages are checked backwards until
    the previous non-idling status is found.
    """

    # --------------------------------------------------
    # Find the Ignition sensor dynamically
    # --------------------------------------------------

    ignition_sensor_id = None

    for sensor_id, sensor_config in configured_sensors.items():

        sensor_name = sensor_config.get("n", "")

        if sensor_name.lower() == "ignition":
            ignition_sensor_id = str(sensor_id)
            break

    if ignition_sensor_id is None:
        return 0

    # --------------------------------------------------
    # Build status history
    # --------------------------------------------------

    status_history = []

    for message in messages:

        if message.get("tp") != "ud":
            continue

        timestamp = message.get("t")

        if timestamp is None:
            continue

        # Speed
        position = message.get("pos") or {}
        speed = position.get("s", 0)

        if speed is None:
            speed = 0

        # Ignition
        sensors = message.get(
            "sensors",
            {}
        )

        ignition_sensor = sensors.get(
            ignition_sensor_id,
            {}
        )

        ignition_value = ignition_sensor.get(
            "value",
            0
        )

        ignition_on = ignition_value == 1

        # Status
        status = calculate_status(
            speed,
            ignition_on
        )

        status_history.append({
            "timestamp": timestamp,
            "status": status
        })

    if not status_history:
        return 0

    # --------------------------------------------------
    # Sort oldest → newest
    # --------------------------------------------------

    status_history.sort(
        key=lambda item: item["timestamp"]
    )

    # --------------------------------------------------
    # Vehicle must currently be IDLING
    # --------------------------------------------------

    if status_history[-1]["status"] != "IDLING":
        return 0

    # --------------------------------------------------
    # Find beginning of current idle period
    # --------------------------------------------------

    idle_start = status_history[-1]["timestamp"]

    for i in range(
        len(status_history) - 2,
        -1,
        -1
    ):

        if status_history[i]["status"] == "IDLING":
            idle_start = status_history[i]["timestamp"]
        else:
            break

    # --------------------------------------------------
    # Calculate duration
    # --------------------------------------------------

    duration = current_time - idle_start

    if duration < 0:
        duration = 0

    return duration


def format_duration(seconds):

    hours = seconds // 3600

    minutes = (
        seconds % 3600
    ) // 60

    seconds = seconds % 60

    return (
        f"{hours:02d}:"
        f"{minutes:02d}:"
        f"{seconds:02d}"
    )