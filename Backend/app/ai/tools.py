"""
Fleet AI Tool Definitions and Execution.

Tools wrap existing fleet service functions (get_all_vehicles, etc.).
No new Wialon calls are introduced beyond what the existing services do.
"""

import json
import logging
import time
from datetime import datetime, timezone
from app.wialon import WialonAPIError, login, get_vehicle_history
from app.vehicles import get_all_vehicles, get_calculated_data, get_vehicle_live_data
from app.normalize import normalize_wialon_messages, segment_vehicle_activity
from app.utils.datetime_helpers import UAE_TZ
from app.wialon_reports import execute_wialon_unit_trips_report, parse_float_safe


def _format_duration(seconds: int) -> str:
    """Human-readable duration from seconds."""
    s = max(0, int(seconds or 0))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h}h {m}m"
    if m > 0:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _idle_fields(v: dict) -> dict:
    """Current idle duration of a live record: unknown when its idle start could not be read, a lower bound ("at
    least") when the run started before the history searched."""
    status = v.get("idle_duration_status", "known")
    seconds = v.get("idle_duration", 0) or 0
    if status == "unknown":
        text = "unknown"
    elif status == "lower_bound":
        text = f"at least {_format_duration(seconds)}"
    else:
        text = _format_duration(seconds)
    return {"idle_duration": text, "idle_seconds": None if status == "unknown" else seconds,
            "idle_duration_status": status}


def _resolve_unit(units: list, ref):
    """
    The raw Wialon unit whose ID equals `ref`, else whose name equals it exactly (case-insensitive), else None.
    Fleet names are often numeric (e.g. "72796"), so the model can pass a name where an ID is expected.
    """
    ref_str = str(ref).strip().lower()
    return (next((u for u in units if str(u.get("id")) == ref_str), None)
            or next((u for u in units if str(u.get("nm", "")).strip().lower() == ref_str), None))


def _data_age_fields(v: dict) -> dict:
    """Message age and GPS fix age (minutes, None when unknown) of a live record. A DELAYED or OFFLINE unit's speed and
    position are the last reported values, and a stale GPS fix means its position is not current."""
    def _mins(sec):
        return None if sec is None else round(sec / 60, 1)
    return {
        "last_known_status": v.get("last_known_status"),
        "message_age_minutes": _mins(v.get("seconds_since_update")),
        "gps_fix_age_minutes": _mins(v.get("seconds_since_position")),
        "gps_position_stale": bool(v.get("position_stale")),
        "speed_is_last_reported": v.get("status") in ("DELAYED", "OFFLINE"),
    }


def _format_ts(ts) -> str:
    """Unix timestamp → readable string in UAE time."""
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(UAE_TZ).strftime("%b %d, %H:%M UAE")
    except Exception:
        return str(ts)


# ─── Individual Tool Implementations ──────────────────────────────────────────

def tool_get_fleet_summary() -> dict:
    """Return top-level fleet status counts and idle attention count."""
    sid = login()
    vehicles = get_all_vehicles(sid)
    calculated = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calculated)

    total = len(fleet)
    moving = sum(1 for v in fleet if v.get("status") == "MOVING")
    idling = sum(1 for v in fleet if v.get("status") == "IDLING")
    stopped = sum(1 for v in fleet if v.get("status") == "STOPPED")
    delayed = sum(1 for v in fleet if v.get("status") == "DELAYED")  # stale telemetry, not live
    offline = sum(1 for v in fleet if v.get("status") == "OFFLINE")
    extended_idling = sum(1 for v in fleet if v.get("status") == "IDLING" and v.get("idle_duration_status") != "unknown"
                          and (v.get("idle_duration") or 0) >= 900)
    idle_duration_unknown = sum(1 for v in fleet if v.get("status") == "IDLING" and v.get("idle_duration_status") == "unknown")

    return {
        "total_vehicles": total,
        "moving": moving,
        "idling": idling,
        "stopped": stopped,
        "delayed": delayed,
        "offline": offline,
        "vehicles_requiring_idle_attention": extended_idling,
        # Idling units whose idle start could not be read (not counted above)
        "idling_vehicles_with_unknown_duration": idle_duration_unknown,
    }


def tool_get_vehicles_by_status(status: str) -> dict:
    """Return vehicles matching the given status (MOVING/IDLING/STOPPED/DELAYED/OFFLINE/ALL)."""
    status = status.upper().strip()
    sid = login()
    vehicles = get_all_vehicles(sid)
    calculated = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calculated)

    if status == "ALL":
        target = fleet
    else:
        target = [v for v in fleet if v.get("status") == status]

    matched = [
        {
            "vehicle_id": v["vehicle_id"],
            "vehicle_name": v["vehicle_name"],
            "status": v.get("status"),
            **_data_age_fields(v),
            "speed": v.get("speed", 0),
            **_idle_fields(v),
            "group": v.get("group"),
            "latitude": v.get("latitude"),
            "longitude": v.get("longitude"),
        }
        for v in target
    ]

    if status == "IDLING":
        # Sort longest idle time first (unknown durations last)
        matched.sort(key=lambda x: (x["idle_seconds"] is not None, x["idle_seconds"] or 0), reverse=True)

    return {
        "status": status,
        "count": len(matched),
        "vehicles": matched,
    }


def tool_get_vehicle_status(vehicle_id) -> dict:
    """Return live status for a specific vehicle by ID or name search."""
    sid = login()
    vehicles = get_all_vehicles(sid)
    calculated = get_calculated_data(sid, vehicles)
    fleet = get_vehicle_live_data(sid, vehicles, calculated)

    target_str = str(vehicle_id).strip().lower()
    vehicle = next(
        (v for v in fleet if str(v.get("vehicle_id")) == target_str or str(v.get("vehicle_name", "")).lower() == target_str or target_str in str(v.get("vehicle_name", "")).lower()),
        None
    )
    if not vehicle:
        return {"found": False, "vehicle_id": vehicle_id}

    return {
        "found": True,
        "vehicle_id": vehicle["vehicle_id"],
        "vehicle_name": vehicle["vehicle_name"],
        "status": vehicle.get("status"),
        **_data_age_fields(vehicle),
        "speed": vehicle.get("speed"),
        **_idle_fields(vehicle),
        "latitude": vehicle.get("latitude"),
        "longitude": vehicle.get("longitude"),
        "group": vehicle.get("group"),
        "battery_voltage": vehicle.get("sensors", {}).get("battery_voltage"),
    }


def tool_get_idle_events(hours: int = 24, min_duration_minutes: int = 0, vehicle_id: int = None) -> dict:
    """
    Idle events across the fleet or for one vehicle: the Idle Analytics page's own data (Wialon Engine Hours report,
    discrete idle intervals, same window clipping, vehicle attribution and completeness flags), so the assistant and
    the page give the same numbers for the same window.
    """
    from app.main import get_fleet_idle_analytics

    vid = None
    if vehicle_id:
        sid = login()
        unit = _resolve_unit(get_all_vehicles(sid), vehicle_id)
        if not unit:
            return {"hours_window": hours, "vehicle_found": False, "vehicle_id": vehicle_id, "event_count": 0,
                    "events": [], "complete": True}
        vid = unit.get("id")

    data = get_fleet_idle_analytics(hours=hours, vehicle_id=vid, include_events=True)
    min_seconds = min_duration_minutes * 60
    events = [ev for ev in data.get("events") or [] if (ev.get("duration") or 0) >= min_seconds]
    events.sort(key=lambda ev: ev.get("duration") or 0, reverse=True)
    kpis = data.get("kpis") or {}
    complete = bool(data.get("events_complete")) and not kpis.get("total_idle_partial")
    unknown_vehicles = [v["vehicle_name"] for v in data.get("by_vehicle") or [] if v.get("idle_events") is None]
    result = {
        "hours_window": hours,
        "min_duration_minutes": min_duration_minutes,
        "source": "Wialon Engine Hours report (same as the Idle Analytics page)",
        "event_count": len(events),
        "events": [
            {
                "vehicle_id": ev.get("vehicle_id"),
                "vehicle_name": ev.get("vehicle_name"),
                # Bounds of the engine-on interval (clipped to the window); the duration is the idling within it
                "interval_start": _format_ts(ev.get("start_time")),
                "interval_end": _format_ts(ev.get("end_time")),
                "duration": _format_duration(ev.get("duration") or 0),
                "duration_seconds": ev.get("duration") or 0,
                "address": ev.get("address"),
                "latitude": (ev.get("location") or {}).get("latitude"),
                "longitude": (ev.get("location") or {}).get("longitude"),
            }
            for ev in events[:50]  # cap response size
        ],
        "total_idle_seconds": kpis.get("total_idle_seconds"),
        "total_idle_partial": bool(kpis.get("total_idle_partial")),
        "vehicles_with_unknown_events": unknown_vehicles[:20],
        "vehicles_with_unknown_events_count": len(unknown_vehicles),
        "complete": complete,
    }
    if not complete:
        result["note"] = (
            "Partial results: idle events or idle totals are unknown for some vehicles (listed). Say so in the answer; "
            "never present the counts as complete."
        )
    return result


def tool_get_vehicle_trips(vehicle_id: int, hours: int = 24) -> dict:
    """Return trip summary for a specific vehicle."""
    sid = login()
    vehicles = get_all_vehicles(sid)
    target = _resolve_unit(vehicles, vehicle_id)
    if not target:
        return {"found": False, "vehicle_id": vehicle_id}
    vehicle_id = target.get("id")
    vname = target.get("nm", f"Vehicle {vehicle_id}")

    # Wialon's trips report, the same authoritative source as the vehicle drawer and fleet trip KPI
    # (segmenting raw messages here gave different trip counts than the dashboard)
    window_to = int(time.time())
    rep = execute_wialon_unit_trips_report(sid, vehicle_id, window_to - hours * 3600, window_to)
    unknown_distance = "distance_km" in (rep.get("incomplete_fields") or []) + (rep.get("unlocated_fields") or [])

    trips = []
    for t in rep.get("trips", []):
        dur_min = t.get("duration_minutes")
        trips.append({
            "start_time": _format_ts(t.get("start_time")),
            "end_time": _format_ts(t.get("end_time")),
            "duration": _format_duration(dur_min * 60) if isinstance(dur_min, (int, float)) else "unknown",
            "distance_km": None if "distance_km" in (t.get("incomplete_fields") or []) or unknown_distance else t.get("distance_km"),
            "max_speed_kmh": parse_float_safe(t.get("max_speed"), default=None),
        })

    result = {
        "vehicle_id": vehicle_id,
        "vehicle_name": vname,
        "hours_window": hours,
        "trip_count": len(trips),
        "trips": trips,
    }
    if unknown_distance:
        result["note"] = "Some trip distances could not be read from Wialon (null); say they are unknown."
    if "row_shape" in (rep.get("incomplete_fields") or []):
        # A malformed trip row Wialon sent was dropped: it is in neither the trips list nor trip_count
        result["note"] = (result.get("note", "") + " Some Wialon trip rows could not be read and were dropped, so "
                          "trip_count may be incomplete (a lower bound); say so.").strip()
    return result


def tool_get_fleet_health() -> dict:
    """Return fleet health overview: battery status, GPS signal quality, connectivity, and warning counts."""
    from app.fleet_analytics import get_fleet_health_analytics
    return get_fleet_health_analytics()


def tool_get_fleet_utilization(hours: int = 24) -> dict:
    """Return fleet utilization, driving time, idle time, stopped time, and top utilized vehicles."""
    from app.fleet_analytics import get_fleet_utilization_analytics
    return get_fleet_utilization_analytics(hours=hours)


def tool_get_fleet_safety(hours: int = 168) -> dict:
    """Return safety analytics: harsh braking, harsh acceleration, harsh cornering counts and events."""
    from app.fleet_analytics import get_safety_analytics
    return get_safety_analytics(hours=hours)


def tool_get_fleet_alerts() -> dict:
    """Return current active fleet alerts categorized by CRITICAL, WARNING, and INFO."""
    from app.fleet_analytics import get_fleet_alerts_analytics
    return get_fleet_alerts_analytics()


def tool_get_group_analytics() -> dict:
    """Return group-level analytics: total vehicles, distance, trips, and safety events by group."""
    from app.fleet_analytics import get_fleet_groups_analytics
    return get_fleet_groups_analytics()


def tool_get_vehicle_deep_analytics(vehicle_id, hours: int = 24) -> dict:
    """Return deep diagnostics, health, today's metrics, and trip count for a specific vehicle."""
    from app.fleet_analytics import get_single_vehicle_deep_analytics
    sid = login()
    v_list = get_all_vehicles(sid)
    # Exact ID or name first (numeric names are not IDs), then the previous partial-name search
    target = _resolve_unit(v_list, vehicle_id) or next(
        (v for v in v_list if str(vehicle_id).lower() in str(v.get("nm", "")).lower()), None)
    vid = target.get("id") if target else None
    if not vid:
        return {"found": False, "vehicle_id": vehicle_id}
    result = get_single_vehicle_deep_analytics(vehicle_id=vid, hours=hours)
    if not result.get("found"):
        return result
    # The analytics' distance is segmented from raw messages, a different measurement from the Wialon Trips Report
    # every other mileage figure uses: it is renamed so it cannot be read as that mileage, and the Trips Report
    # distance is added. A copy: the analytics result is cached and shared.
    metrics = dict(result.get("metrics_24h") or {})
    metrics.pop("distance_source", None)
    metrics["message_segmented_distance_km"] = metrics.pop("distance_km", None)
    metrics["trips_report_distance_km"] = _trips_report_distance_km(sid, vid, hours)
    return dict(
        result,
        metrics_24h=metrics,
        note=(
            "Quote trips_report_distance_km as the vehicle's distance (Wialon Trips Report); null means unknown, "
            "say so. message_segmented_distance_km, trips_count and trips[] are segmented from raw GPS messages "
            "and can differ from the Trips Report: never present them as the vehicle's mileage."
        ),
    )


def _trips_report_distance_km(sid: str, vehicle_id: int, hours: int):
    """Wialon Trips Report distance over the last `hours`; None when the report failed or any distance is unknown."""
    window_to = int(time.time())
    try:
        rep = execute_wialon_unit_trips_report(sid, vehicle_id, window_to - hours * 3600, window_to)
    except WialonAPIError as exc:
        logging.warning("Trips report failed for vehicle %s; AI distance unknown: %s", vehicle_id, exc)
        return None
    if "distance_km" in (rep.get("incomplete_fields") or []) + (rep.get("unlocated_fields") or []):
        return None
    return round(sum(t.get("distance_km") or 0.0 for t in rep.get("trips", [])), 2)


# ─── Groq Tool Schemas (JSON Schema) ──────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_fleet_summary",
            "description": "Get current fleet status summary: total vehicles, moving, idling, stopped, and offline counts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicles_by_status",
            "description": "Get vehicles with a specific status or all vehicles. Use status=MOVING, IDLING, STOPPED, DELAYED (last seen MOVING or IDLING with telemetry older than 10 minutes, or last seen MOVING with a GPS position older than 10 minutes although messages are recent; last_known_status is the last reported state, not live; a vehicle last seen STOPPED stays STOPPED until OFFLINE after 12 hours), OFFLINE, or ALL. message_age_minutes is the age of the last message and gps_fix_age_minutes the age of the last GPS position; when speed_is_last_reported or gps_position_stale is true, say the speed and location are the last reported values with their age, never that the vehicle is currently at that speed or place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["MOVING", "IDLING", "STOPPED", "DELAYED", "OFFLINE", "ALL"],
                        "description": "Vehicle operational status to filter by, or ALL for entire fleet.",
                    }
                },
                "required": ["status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_status",
            "description": "Get live status, speed, location, and idle duration for a specific vehicle by its ID or name. message_age_minutes is the age of the last message and gps_fix_age_minutes the age of the last GPS position; when speed_is_last_reported or gps_position_stale is true, say the speed and location are the last reported values with their age, never that the vehicle is currently at that speed or place.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vehicle_id": {
                        "type": ["string", "integer"],
                        "description": "The vehicle ID or vehicle name.",
                    }
                },
                "required": ["vehicle_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fleet_health",
            "description": "Get fleet health diagnostics: battery warnings, GPS issues (gps.status WEAK, or STALE: last seen moving with a GPS fix older than 10 minutes although messages are recent), offline devices, and average health score.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fleet_utilization",
            "description": "Get fleet utilization rates, total driving time vs idle time, and vehicle utilization rankings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "Time window in hours (1-168). Default 24.", "default": 24}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fleet_safety",
            "description": "Get safety analytics: harsh braking, harsh acceleration, and harsh cornering events across the fleet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "Look-back window in hours (1-168). Default 168 (7 days).", "default": 168}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fleet_alerts",
            "description": "Get active fleet operational alerts: critical low battery, device offline, weak GPS, stale GPS position (GPS_POSITION_STALE: last seen moving, GPS fix older than 10 minutes although messages are recent, so its position and speed are not current), and safety triggers.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_group_analytics",
            "description": "Get analytics aggregated by dynamic fleet customer groups: distance, trips, vehicle count, and safety.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_deep_analytics",
            "description": "Get deep analytics for a vehicle: health score, battery, GPS quality, 24h distance (trips_report_distance_km, from the Wialon Trips Report), trips, and engine hours.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vehicle_id": {"type": ["string", "integer"], "description": "Vehicle ID or name."}
                },
                "required": ["vehicle_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_idle_events",
            "description": "Get idle events across the fleet or for a specific vehicle within a time window, from the Wialon Engine Hours report used by the Idle Analytics page. The result says whether it is complete and lists vehicles whose events are unknown.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "Look-back window in hours (1-168). Default 24.", "default": 24},
                    "min_duration_minutes": {"type": "integer", "description": "Minimum idle duration in minutes to include. Default 0.", "default": 0},
                    "vehicle_id": {"type": "integer", "description": "Specific vehicle ID. Omit for fleet-wide results."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_trips",
            "description": "Get trip history for a specific vehicle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vehicle_id": {"type": "integer", "description": "The numeric vehicle ID."},
                    "hours": {"type": "integer", "description": "Look-back window in hours (1-168). Default 24.", "default": 24},
                },
                "required": ["vehicle_id"],
            },
        },
    },
]


# Model-selected look-back windows are validated and clamped before any Wialon/analytics call.
MAX_TOOL_HOURS = 168
MAX_IDLE_MIN_DURATION_MINUTES = 1440

# Tool results go back to Groq whole. The account's limit is 8000 tokens per request-minute and the prompt,
# tool schemas and reply budget take ~4k (a ~7.5k-token alerts result requested 11.5k), so analytics results of
# 20-60k chars were rejected (HTTP 413, measured live). Larger results keep every count/total field and get
# their longest lists shortened to fit (~3k tokens).
MAX_TOOL_RESULT_CHARS = 12000


def _fit_for_model(result) -> str:
    """JSON for the model, at most MAX_TOOL_RESULT_CHARS when possible: the longest lists are halved until it fits
    (by item count, so per-group summaries outlast the listings inside them),
    and `truncated_lists` says which were cut ("showing first k of n") so the model can say a list is partial."""
    text = json.dumps(result)
    if len(text) <= MAX_TOOL_RESULT_CHARS or not isinstance(result, dict):
        return text
    data = json.loads(text)  # deep copy: results can be cached dicts
    originals: dict = {}

    def lists(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                yield from lists(v, f"{path}.{k}" if path else k)
                if isinstance(v, list) and len(v) > 1:
                    yield len(json.dumps(v)), (f"{path}.{k}" if path else k), node, k
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from lists(v, f"{path}[{i}]")

    while len(text) > MAX_TOOL_RESULT_CHARS:
        candidates = list(lists(data, ""))
        if not candidates:
            break
        # Most items first: long lists are listings (alerts, vehicles), short ones summaries (groups) kept whole longest
        _, path, parent, key = max(candidates, key=lambda c: (len(c[2][c[3]]), c[0]))
        originals.setdefault(path, len(parent[key]))
        parent[key] = parent[key][: len(parent[key]) // 2]
        text = json.dumps(data)

    kept = {p: _get_path(data, p) for p in originals}
    data["truncated_lists"] = {p: f"showing first {len(kept[p])} of {n}" for p, n in originals.items() if kept[p] is not None}
    data["truncation_note"] = ("Lists named in truncated_lists were shortened to fit; count and total fields are complete. "
                               "Say a listing is partial when it matters.")
    return json.dumps(data)


def _get_path(data, path):
    """Value at a dotted/indexed path built by _fit_for_model (None when an ancestor list was shortened past it)."""
    node = data
    for part in path.replace("[", ".[").split("."):
        if part.startswith("["):
            idx = int(part[1:-1])
            node = node[idx] if isinstance(node, list) and idx < len(node) else None
        else:
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


class ToolInputError(ValueError):
    """An invalid tool argument. Only these messages are shown to the model so it can correct its call;
    any other exception (including a ValueError from deep inside an upstream call) stays generic."""


def _int_arg(args: dict, key: str, default=None):
    """Read an optional whole-number tool argument (None/"" -> default), rejecting non-numeric values."""
    value = args.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ToolInputError(f"{key} must be a whole number, got {value!r}")
    try:
        return int(float(value)) if isinstance(value, (int, float)) else int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        raise ToolInputError(f"{key} must be a whole number, got {value!r}") from None


def _bounded_int(args: dict, key: str, default: int, low: int, high: int, clamped: list) -> int:
    """Read an integer tool argument, rejecting non-numeric values and clamping it into [low, high]."""
    value = args.get(key, default)
    if value is None or value == "":
        value = default
    if isinstance(value, bool):
        raise ToolInputError(f"{key} must be a whole number, got {value!r}")
    try:
        number = int(float(value)) if isinstance(value, (int, float)) else int(str(value).strip())
    except (TypeError, ValueError, OverflowError):
        raise ToolInputError(f"{key} must be a whole number, got {value!r}") from None
    bounded = min(max(number, low), high)
    if bounded != number:
        clamped.append(f"{key}={number} was clamped to {bounded} (allowed {low}-{high})")
    return bounded


def dispatch_tool(name: str, args: dict) -> str:
    """Execute a tool by name and return the result as a JSON string."""
    clamped: list = []

    def hours(default):
        return _bounded_int(args, "hours", default, 1, MAX_TOOL_HOURS, clamped)

    try:
        if name == "get_fleet_summary":
            result = tool_get_fleet_summary()
        elif name == "get_vehicles_by_status":
            result = tool_get_vehicles_by_status(args.get("status", "ALL"))
        elif name == "get_vehicle_status":
            result = tool_get_vehicle_status(args.get("vehicle_id"))
        elif name == "get_fleet_health":
            result = tool_get_fleet_health()
        elif name == "get_fleet_utilization":
            result = tool_get_fleet_utilization(hours=hours(24))
        elif name == "get_fleet_safety":
            result = tool_get_fleet_safety(hours=hours(168))
        elif name == "get_fleet_alerts":
            result = tool_get_fleet_alerts()
        elif name == "get_group_analytics":
            result = tool_get_group_analytics()
        elif name == "get_vehicle_deep_analytics":
            result = tool_get_vehicle_deep_analytics(args.get("vehicle_id"), hours=hours(24))
        elif name == "get_idle_events":
            result = tool_get_idle_events(
                hours=hours(24),
                min_duration_minutes=_bounded_int(args, "min_duration_minutes", 0, 0, MAX_IDLE_MIN_DURATION_MINUTES, clamped),
                vehicle_id=_int_arg(args, "vehicle_id") or None,
            )
        elif name == "get_vehicle_trips":
            vehicle_id = _int_arg(args, "vehicle_id")
            if vehicle_id is None:
                raise ToolInputError("vehicle_id is required")
            result = tool_get_vehicle_trips(
                vehicle_id=vehicle_id,
                hours=hours(24),
            )
        elif name == "get_fuel_summary":
            result = tool_get_fuel_summary(
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
            )
        elif name == "get_fleet_fuel_efficiency":
            result = tool_get_fleet_fuel_efficiency(
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
            )
        elif name == "get_vehicle_fuel_ranking":
            result = tool_get_vehicle_fuel_ranking(
                sort_by=args.get("sort_by", "litres"),
                sort_order=args.get("sort_order", "desc"),
                limit=_int_arg(args, "limit", 5),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
            )
        elif name == "get_vehicle_fuel_performance":
            result = tool_get_vehicle_fuel_performance(
                vehicle_identifier=str(args.get("vehicle_identifier", "")),
                date_from=args.get("date_from"),
                date_to=args.get("date_to"),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
    except ToolInputError as e:
        # Input validation messages (e.g. date format) help the model correct its call.
        result = {"error": str(e)}
    except Exception as e:
        # Upstream errors can carry raw Wialon/Supabase payloads: log them, give the model a generic error.
        logging.getLogger(__name__).warning("AI tool %s failed: %s", name, e, exc_info=True)
        result = {"error": "The data source for this tool is currently unavailable."}

    if clamped and isinstance(result, dict):
        # Copy: analytics results can be cached dicts that must not be mutated.
        result = {**result, "time_range_note": "; ".join(clamped)}
    return _fit_for_model(result)


# ─── Fuel Intelligence Tool Implementations ───────────────────────────────────
# Same KPI scope as the /api/fuel endpoints (app.fuel.scope): only transactions resolving to exactly one
# Wialon unit count, excluded rows are reported, dates come from the request or the data (no fixed period),
# and the vehicle filter is exact.

def _check_date(value, name):
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if len(text) != 10:
            raise ValueError
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise ToolInputError(f"{name} must be YYYY-MM-DD, got {value!r}") from None


def _fuel_scope(date_from: str = None, date_to: str = None, vehicle_identifier: str = None):
    from app.fuel.db import fetch_fuel_transactions_for_analytics, get_fuel_vehicle_identifiers
    from app.fuel.scope import build_scope

    date_from, date_to = _check_date(date_from, "date_from"), _check_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise ToolInputError("date_from must be on or before date_to")

    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier}
    return build_scope(filters, fetch_fuel_transactions_for_analytics, lambda: login(), lambda sid: get_all_vehicles(sid),
                       get_fuel_vehicle_identifiers)


def _excluded_brief(scope) -> dict:
    ex = scope.excluded_scope
    return {
        "excluded_transactions": ex["excluded_transaction_count"],
        "excluded_litres": ex["excluded_litres"],
        "excluded_cost_aed": ex["excluded_cost"],
        "unmatched_identifiers": ex["unmatched"]["identifier_count"],
        "ambiguous_identifiers": ex["ambiguous"]["identifier_count"],
        "suspected_duplicates": ex["suspected_duplicates"]["transaction_count"],
        "all_transactions": ex["all_transaction_count"],
        "warnings": ex["warnings"],
    }


def _fuel_tool(fn):
    """Fuel read failures become an explicit error instead of partial numbers."""
    def wrapper(*args, **kwargs):
        from app.fuel.db import FuelDataFetchError
        try:
            return fn(*args, **kwargs)
        except FuelDataFetchError as exc:
            return {"error": f"Fuel data unavailable; results withheld to avoid partial KPIs ({exc})."}
        except ToolInputError as exc:
            # Other ValueErrors propagate to dispatch_tool, which logs them and returns a generic error
            return {"error": str(exc)}
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _telematics_for(scope, date_from, date_to):
    """(bundle, (from, to)) for the scope, or (None, None) when there is nothing to measure."""
    from app.fuel.scope import resolve_effective_dates
    from app.fuel.wialon_distance import get_fleet_telematics_bundle

    dates = resolve_effective_dates(scope.matched, date_from, date_to)
    if scope.is_empty or dates is None:
        return None, None
    return get_fleet_telematics_bundle(dates[0], dates[1], scope.sid), dates


@_fuel_tool
def tool_get_fuel_summary(date_from: str = None, date_to: str = None) -> dict:
    """Return fleet fuel purchased (net of refunds), expenditure and refuel counts for matched Wialon units."""
    from app.fuel.analytics import calculate_fuel_summary

    scope = _fuel_scope(date_from, date_to)
    s = calculate_fuel_summary(scope.matched)
    return {
        "total_litres": round(s.total_litres, 1),
        "total_cost_aed": round(s.total_cost, 2),
        "transaction_count": s.transaction_count,
        "unique_vehicles": s.vehicle_count,
        "average_unit_price": round(s.average_unit_price, 2),
        "refund_count": s.refund_count,
        "basis": "fuel purchased (recorded statements), not measured consumption",
        "excluded_scope": _excluded_brief(scope),
    }


@_fuel_tool
def tool_get_fleet_fuel_efficiency(date_from: str = None, date_to: str = None) -> dict:
    """Return purchase-based fleet efficiency (L/100 km), cost per km, Wialon distance and coverage."""
    from app.fuel.efficiency import calculate_fleet_efficiency

    scope = _fuel_scope(date_from, date_to)
    bundle, dates = _telematics_for(scope, date_from, date_to)
    if bundle is None:
        eff = calculate_fleet_efficiency([], {}, [])
    else:
        eff = calculate_fleet_efficiency(
            scope.matched, bundle["by_name"], scope.units,
            telematics_status=bundle["status"], data_warnings=list(bundle.get("warnings") or []),
            duplicate_names=bundle["duplicate_names"],
        )
    return {
        "period": {"date_from": dates[0], "date_to": dates[1]} if dates else None,
        "fuel_efficiency_l_per_100km": eff.fuel_efficiency_l_per_100km,
        "fuel_cost_per_km_aed": eff.fuel_cost_per_km,
        "efficiency_basis": "purchase_based (fuel purchased / Wialon distance; derived, not measured)",
        "efficiency_suppressed_reason": eff.efficiency_suppressed_reason,
        "total_distance_km": round(eff.total_distance_km, 1) if eff.total_distance_km is not None else None,
        "total_litres": round(eff.total_litres, 1),
        "total_cost_aed": round(eff.total_cost, 2),
        "tracked_vehicles": eff.vehicles_with_distance,
        "vehicles_in_scope": eff.vehicle_count,
        "total_idle_hours": eff.total_idle_hours,
        "estimated_idle_fuel_litres": eff.estimated_idle_fuel_litres,
        "telematics_status": eff.telematics_status,
        "data_warnings": eff.data_warnings,
        "excluded_scope": _excluded_brief(scope),
    }


@_fuel_tool
def tool_get_vehicle_fuel_ranking(sort_by: str = "litres", sort_order: str = "desc", limit: int = 5,
                                  date_from: str = None, date_to: str = None) -> dict:
    """Return vehicles ranked by fuel, cost, distance, purchase-based efficiency (L/100km) or cost/km."""
    from app.fuel.efficiency import calculate_vehicle_efficiency_records, paginate_and_sort_vehicle_efficiency

    scope = _fuel_scope(date_from, date_to)
    bundle, dates = _telematics_for(scope, date_from, date_to)
    records = [] if bundle is None else calculate_vehicle_efficiency_records(
        scope.matched, bundle["by_name"], scope.units, duplicate_names=bundle["duplicate_names"])
    paged = paginate_and_sort_vehicle_efficiency(records, 1, min(50, max(1, int(limit))), sort_by, sort_order)
    return {
        "sort_by": sort_by,
        "sort_order": sort_order,
        "period": {"date_from": dates[0], "date_to": dates[1]} if dates else None,
        "vehicles": [
            {
                "vehicle": v.vehicle_identifier,
                "wialon_unit": v.wialon_unit_name,
                "merged_identifiers": v.merged_identifiers,
                "litres": round(v.litres, 1),
                "cost_aed": round(v.total_cost, 2),
                "distance_km": round(v.distance_km, 1) if v.distance_km is not None else None,
                "fuel_efficiency_l_per_100km": round(v.fuel_efficiency_l_per_100km, 2) if v.fuel_efficiency_l_per_100km is not None else None,
                "cost_per_km_aed": round(v.fuel_cost_per_km, 2) if v.fuel_cost_per_km is not None else None,
                "efficiency_suppressed_reason": v.efficiency_suppressed_reason,
                "status": v.status,
            }
            for v in paged.data
        ],
        "telematics_status": bundle["status"] if bundle else None,
        "excluded_scope": _excluded_brief(scope),
    }


@_fuel_tool
def tool_get_vehicle_fuel_performance(vehicle_identifier: str, date_from: str = None, date_to: str = None) -> dict:
    """Get one vehicle's fuel purchased, Wialon distance and purchase-based efficiency (exact match)."""
    from app.fuel.efficiency import calculate_vehicle_efficiency_records

    scope = _fuel_scope(date_from, date_to, vehicle_identifier)
    if scope.is_empty:
        ex = scope.excluded_scope
        reason = "no transactions" if not ex["all_transaction_count"] else "identifier not attributable to exactly one Wialon unit"
        return {"error": f"No KPI-scope fuel data for '{vehicle_identifier}' ({reason}).", "excluded_scope": _excluded_brief(scope)}
    bundle, dates = _telematics_for(scope, date_from, date_to)
    records = calculate_vehicle_efficiency_records(
        scope.matched, bundle["by_name"], scope.units, duplicate_names=bundle["duplicate_names"])
    v = records[0] if len(records) == 1 else None
    if v is None:
        return {"error": f"'{vehicle_identifier}' resolves to {len(records)} records; refine the identifier."}
    return {
        "vehicle": v.vehicle_identifier,
        "wialon_unit": v.wialon_unit_name,
        "merged_identifiers": v.merged_identifiers,
        "period": {"date_from": dates[0], "date_to": dates[1]},
        "litres": round(v.litres, 1),
        "cost_aed": round(v.total_cost, 2),
        "distance_km": round(v.distance_km, 1) if v.distance_km is not None else None,
        "fuel_efficiency_l_per_100km": round(v.fuel_efficiency_l_per_100km, 2) if v.fuel_efficiency_l_per_100km is not None else None,
        "cost_per_km_aed": round(v.fuel_cost_per_km, 2) if v.fuel_cost_per_km is not None else None,
        "efficiency_suppressed_reason": v.efficiency_suppressed_reason,
        "idle_hours": v.idle_duration_hours,
        "refuel_transactions": v.transaction_count,
        "status": v.status,
        "telemetry_status": v.telemetry_status,
        "excluded_scope": _excluded_brief(scope),
    }


_DATE_PARAMS = {
    "date_from": {"type": ["string", "null"], "description": "Start date (YYYY-MM-DD). Optional; defaults to the data's own range."},
    "date_to": {"type": ["string", "null"], "description": "End date (YYYY-MM-DD). Optional; defaults to the data's own range."},
}


# Append fuel tools to TOOL_DEFINITIONS
TOOL_DEFINITIONS.extend([
    {
        "type": "function",
        "function": {
            "name": "get_fuel_summary",
            "description": "Get fleet fuel consumption summary: total litres, expenditure in AED, transaction count, and average fuel price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": ["string", "null"], "description": "Start date (YYYY-MM-DD). Optional."},
                    "date_to": {"type": ["string", "null"], "description": "End date (YYYY-MM-DD). Optional."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fleet_fuel_efficiency",
            "description": "Get weighted fleet fuel efficiency in L/100 km, cost per km (AED/km), total GPS distance, and tracked coverage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_from": {"type": ["string", "null"], "description": "Start date (YYYY-MM-DD). Optional."},
                    "date_to": {"type": ["string", "null"], "description": "End date (YYYY-MM-DD). Optional."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_fuel_ranking",
            "description": "Get top or bottom ranked vehicles by fuel consumption, expenditure, distance, fuel efficiency (L/100km), or cost/km.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sort_by": {"type": "string", "enum": ["litres", "cost", "distance", "efficiency", "cost_per_km"], "description": "Metric to rank vehicles by."},
                    "sort_order": {"type": "string", "enum": ["asc", "desc"], "description": "Sort order: 'desc' for highest, 'asc' for lowest."},
                    "limit": {"type": "integer", "description": "Number of vehicles to return (default 5)."},
                    "date_from": _DATE_PARAMS["date_from"],
                    "date_to": _DATE_PARAMS["date_to"],
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicle_fuel_performance",
            "description": "Get detailed fuel consumption, expenditure, GPS distance, L/100 km, and cost/km for a specific vehicle plate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vehicle_identifier": {"type": "string", "description": "Vehicle registration or plate number (exact)."},
                    "date_from": _DATE_PARAMS["date_from"],
                    "date_to": _DATE_PARAMS["date_to"],
                },
                "required": ["vehicle_identifier"],
            },
        },
    },
])
