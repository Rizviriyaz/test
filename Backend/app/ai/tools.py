"""
Fleet AI Tool Definitions and Execution.

Tools wrap existing fleet service functions (get_all_vehicles, etc.).
No new Wialon calls are introduced beyond what the existing services do.
"""

import json
import logging
from datetime import datetime, timezone
from app.wialon import login, get_vehicle_history
from app.vehicles import get_all_vehicles, get_calculated_data, get_vehicle_live_data
from app.normalize import normalize_wialon_messages, segment_vehicle_activity
from app.utils.datetime_helpers import UAE_TZ


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
    offline = sum(1 for v in fleet if v.get("status") == "OFFLINE")
    extended_idling = sum(1 for v in fleet if v.get("status") == "IDLING" and (v.get("idle_duration") or 0) >= 900)

    return {
        "total_vehicles": total,
        "moving": moving,
        "idling": idling,
        "stopped": stopped,
        "offline": offline,
        "vehicles_requiring_idle_attention": extended_idling,
    }


def tool_get_vehicles_by_status(status: str) -> dict:
    """Return vehicles matching the given status (MOVING/IDLING/STOPPED/OFFLINE/ALL)."""
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

    for v in target:
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
    try:
        vid = int(vehicle_id)
    except Exception:
        # Search by name in fleet
        sid = login()
        v_list = get_all_vehicles(sid)
        target = next((v for v in v_list if str(vehicle_id).lower() in str(v.get("nm", "")).lower()), None)
        vid = target.get("id") if target else None
    if not vid:
        return {"found": False, "vehicle_id": vehicle_id}
    return get_single_vehicle_deep_analytics(vehicle_id=vid, hours=hours)


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
            "description": "Get vehicles with a specific status or all vehicles. Use status=MOVING, IDLING, STOPPED, OFFLINE, or ALL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["MOVING", "IDLING", "STOPPED", "OFFLINE", "ALL"],
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
            "description": "Get live status, speed, location, and idle duration for a specific vehicle by its ID or name.",
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
            "description": "Get fleet health diagnostics: battery warnings, GPS issues, offline devices, and average health score.",
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
                    "hours": {"type": "integer", "description": "Time window in hours. Default 24.", "default": 24}
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
                    "hours": {"type": "integer", "description": "Look-back window in hours. Default 168 (7 days).", "default": 168}
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_fleet_alerts",
            "description": "Get active fleet operational alerts: critical low battery, device offline, weak GPS, and safety triggers.",
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
            "description": "Get deep analytics for a vehicle: health score, battery, GPS quality, 24h distance, trips, and engine hours.",
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
        elif name == "get_fleet_health":
            result = tool_get_fleet_health()
        elif name == "get_fleet_utilization":
            result = tool_get_fleet_utilization(hours=int(args.get("hours", 24)))
        elif name == "get_fleet_safety":
            result = tool_get_fleet_safety(hours=int(args.get("hours", 168)))
        elif name == "get_fleet_alerts":
            result = tool_get_fleet_alerts()
        elif name == "get_group_analytics":
            result = tool_get_group_analytics()
        elif name == "get_vehicle_deep_analytics":
            result = tool_get_vehicle_deep_analytics(args.get("vehicle_id"), hours=int(args.get("hours", 24)))
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
                limit=int(args.get("limit", 5)),
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
    except ValueError as e:
        # Input validation messages (e.g. date format) help the model correct its call.
        result = {"error": str(e)}
    except Exception as e:
        # Upstream errors can carry raw Wialon/Supabase payloads: log them, give the model a generic error.
        logging.getLogger(__name__).warning("AI tool %s failed: %s", name, e, exc_info=True)
        result = {"error": "The data source for this tool is currently unavailable."}

    return json.dumps(result)


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
        raise ValueError(f"{name} must be YYYY-MM-DD, got {value!r}") from None


def _fuel_scope(date_from: str = None, date_to: str = None, vehicle_identifier: str = None):
    from app.fuel.db import fetch_fuel_transactions_for_analytics
    from app.fuel.scope import build_scope

    date_from, date_to = _check_date(date_from, "date_from"), _check_date(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from must be on or before date_to")

    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier}
    return build_scope(filters, fetch_fuel_transactions_for_analytics, lambda: login(), lambda sid: get_all_vehicles(sid))


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
        except ValueError as exc:
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
