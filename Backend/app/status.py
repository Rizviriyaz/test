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
    if isinstance(configured_sensors, dict):
        for sensor_id, sensor_config in configured_sensors.items():
            s_type = str(sensor_config.get("t", "")).lower()
            s_name = str(sensor_config.get("n", "")).lower()
            if s_type == "engine operation" or any(kw in s_name for kw in ["ignition", "engine", "acc", "ig"]):
                ignition_sensor_id = str(sensor_id)
                break

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
        ignition_on = False
        sensors = message.get("sensors", {})
        if ignition_sensor_id and ignition_sensor_id in sensors:
            ig_val = sensors[ignition_sensor_id].get("value")
            if ig_val is not None:
                ignition_on = (ig_val == 1 or str(ig_val).lower() in ["1", "true", "on"])
        else:
            # Fallback to telemetry parameters (io_11 or in1)
            params = message.get("p") or {}
            if "io_11" in params:
                ignition_on = float(params["io_11"]) > 0
            elif "in1" in params:
                ignition_on = float(params["in1"]) > 0

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