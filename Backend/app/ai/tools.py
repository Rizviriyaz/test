"""
Fleet AI Tool Definitions and Execution.

Tools wrap existing fleet service functions (get_all_vehicles, etc.).
No new Wialon calls are introduced beyond what the existing services do.
"""

import json
from datetime import datetime, timezone
from app.wialon import login, get_vehicle_history
from app.vehicles import get_all_vehicles, get_calculated_data, get_vehicle_live_data
from app.normalize import normalize_wialon_messages, segment_vehicle_activity


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


def _format_ts(ts) -> str:
    """Unix timestamp → readable string."""
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%b %d, %H:%M UTC")
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
    extended_idling = sum(1 for v in fleet if v.get("status") == "IDLING" and (v.get("idle_duration") or 0) >= 900)

    return {
        "total_vehicles": total,
        "moving": moving,
        "idling": idling,
        "stopped": stopped,
        "vehicles_requiring_idle_attention": extended_idling,
    }


def tool_get_vehicles_by_status(status: str) -> dict:
    """Return vehicles matching the given status (MOVING/IDLING/STOPPED/ALL)."""
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
            "speed": v.get("speed", 0),
            "idle_duration": _format_duration(v.get("idle_duration", 0)),
            "idle_seconds": v.get("idle_duration", 0) or 0,
            "group": v.get("group"),
            "latitude": v.get("latitude"),
            "longitude": v.get("longitude"),
        }
        for v in target
    ]

    if status == "IDLING":
        # Sort longest idle time first
        matched.sort(key=lambda x: x["idle_seconds"], reverse=True)

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
        "speed": vehicle.get("speed"),
        "idle_duration": _format_duration(vehicle.get("idle_duration", 0)),
        "idle_seconds": vehicle.get("idle_duration", 0) or 0,
        "latitude": vehicle.get("latitude"),
        "longitude": vehicle.get("longitude"),
        "group": vehicle.get("group"),
        "battery_voltage": vehicle.get("sensors", {}).get("battery_voltage"),
    }


def tool_get_idle_events(hours: int = 24, min_duration_minutes: int = 0, vehicle_id: int = None) -> dict:
    """Return idle events across the fleet or for a specific vehicle."""
    sid = login()
    vehicles = get_all_vehicles(sid)

    if vehicle_id:
        target = [v for v in vehicles if v.get("id") == vehicle_id]
    else:
        target = vehicles

    all_events = []
    min_seconds = min_duration_minutes * 60

    # Limit to first 20 vehicles for response speed (same pattern as existing fleet analytics)
    for v in target[:20]:
        vid = v.get("id")
        vname = v.get("nm", f"Vehicle {vid}")
        sens = v.get("sens") or {}
        try:
            raw = get_vehicle_history(sid, vid, hours=hours)
            telemetry = normalize_wialon_messages(raw, configured_sensors=sens)
            activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)
            for p in activity.get("idle_periods", []):
                dur = p.get("duration", 0)
                if dur >= min_seconds:
                    loc = p.get("location") or {}
                    all_events.append({
                        "vehicle_id": vid,
                        "vehicle_name": vname,
                        "idle_start": _format_ts(p.get("start_time")),
                        "idle_end": _format_ts(p.get("end_time")),
                        "duration": _format_duration(dur),
                        "duration_seconds": dur,
                        "latitude": loc.get("latitude"),
                        "longitude": loc.get("longitude"),
                    })
        except Exception:
            continue

    # Sort longest first
    all_events.sort(key=lambda x: x["duration_seconds"], reverse=True)

    return {
        "hours_window": hours,
        "min_duration_minutes": min_duration_minutes,
        "event_count": len(all_events),
        "events": all_events[:50],  # cap response size
    }


def tool_get_vehicle_trips(vehicle_id: int, hours: int = 24) -> dict:
    """Return trip summary for a specific vehicle."""
    sid = login()
    vehicles = get_all_vehicles(sid)
    target = next((v for v in vehicles if v.get("id") == vehicle_id), None)
    sens = (target.get("sens") or {}) if target else {}
    vname = target.get("nm", f"Vehicle {vehicle_id}") if target else f"Vehicle {vehicle_id}"

    raw = get_vehicle_history(sid, vehicle_id, hours=hours)
    telemetry = normalize_wialon_messages(raw, configured_sensors=sens)
    activity = segment_vehicle_activity(telemetry, stop_tolerance_seconds=60)

    trips = []
    for t in activity.get("trips", []):
        trips.append({
            "start_time": _format_ts(t.get("start_time")),
            "end_time": _format_ts(t.get("end_time")),
            "duration": _format_duration(t.get("duration", 0)),
            "distance_km": round(t.get("distance", 0), 2),
            "max_speed_kmh": t.get("max_speed", 0),
        })

    return {
        "vehicle_id": vehicle_id,
        "vehicle_name": vname,
        "hours_window": hours,
        "trip_count": len(trips),
        "trips": trips,
    }


# ─── Groq Tool Schemas (JSON Schema) ──────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_fleet_summary",
            "description": "Get the current fleet status summary: total vehicles, how many are moving, idling, and stopped.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_vehicles_by_status",
            "description": "Get vehicles with a specific status or all vehicles. Use status=MOVING, IDLING, STOPPED, or ALL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["MOVING", "IDLING", "STOPPED", "ALL"],
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
            "description": "Get the live status, speed, location, and idle duration for a specific vehicle by its ID or name.",
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
            "name": "get_idle_events",
            "description": "Get idle events across the fleet or for a specific vehicle within a time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "Look-back window in hours. Default 24.", "default": 24},
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
                    "hours": {"type": "integer", "description": "Look-back window in hours. Default 24.", "default": 24},
                },
                "required": ["vehicle_id"],
            },
        },
    },
]


def dispatch_tool(name: str, args: dict) -> str:
    """Execute a tool by name and return the result as a JSON string."""
    try:
        if name == "get_fleet_summary":
            result = tool_get_fleet_summary()
        elif name == "get_vehicles_by_status":
            result = tool_get_vehicles_by_status(args.get("status", "ALL"))
        elif name == "get_vehicle_status":
            result = tool_get_vehicle_status(args.get("vehicle_id"))
        elif name == "get_idle_events":
            result = tool_get_idle_events(
                hours=int(args.get("hours", 24)),
                min_duration_minutes=int(args.get("min_duration_minutes", 0)),
                vehicle_id=int(args["vehicle_id"]) if args.get("vehicle_id") else None,
            )
        elif name == "get_vehicle_trips":
            result = tool_get_vehicle_trips(
                vehicle_id=int(args["vehicle_id"]),
                hours=int(args.get("hours", 24)),
            )
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        result = {"error": str(e)}

    return json.dumps(result)
