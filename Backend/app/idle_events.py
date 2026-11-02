"""
Idle Dashboard idle events from Wialon's per-unit Idle Report-Unit template (see
wialon_reports.execute_wialon_unit_idle_events_report).

The template runs on one unit at a time (Wialon rejects a list of objects for it, error 7), so a fleet window is read
unit by unit on a small pool of dedicated Wialon sessions: each session holds one report result at a time, the pool
runs in parallel, and none of it touches the shared session the Dashboard reports use. Each unit's result is cached
per window, so the Vehicle Details drawer and group or vehicle filters reuse a fleet read (and vice versa).

Ongoing events: Wialon reports an event still in progress with its end at the unit's latest message, so the same
event (same unit and start) comes back on every run with a later end and a longer duration. An event is marked
ongoing only when it is the unit's latest event, its end is within IDLE_ONGOING_MAX_END_AGE_SECONDS of when the
report was read, and the unit's Idling sensor (read after the report) is on with telemetry no older than
CONN_STABLE_SECONDS. Anything else is a completed event with Wialon's final end and duration.
"""

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.utils.config import get_env_int
from app.utils.status_config import CONN_STABLE_SECONDS
from app.utils.ttl_cache import TTLCache
from app.vehicles import get_sensor_by_name
from app.wialon import WialonAPIError, login, wialon_request
from app.wialon_reports import IDLE_REPORT_RESOURCE_ID, execute_wialon_unit_idle_events_report

# Idle Dashboard retrieval limit: windows starting before UAE midnight IDLE_EVENTS_MAX_DAYS - 1 days ago are rejected
IDLE_EVENTS_MAX_DAYS = 7
# Dedicated Wialon sessions (one report each at a time) the per-unit reports run on in parallel
IDLE_EVENTS_SESSIONS = max(1, get_env_int("WIALON_IDLE_EVENTS_SESSIONS", 4))
IDLE_EVENTS_SESSION_PREFIX = "idle_events_"
# Freshness of a window that reaches into today (UAE); closed windows use closed_window_ttl
IDLE_EVENTS_TTL_SECONDS = 120
# An event whose end is older than this when the report was read is not ongoing, whatever the sensor says now
IDLE_ONGOING_MAX_END_AGE_SECONDS = get_env_int("IDLE_ONGOING_MAX_END_AGE_SECONDS", 300)

# (unit id, window spec) -> {"events", "complete", "read_at", "from_time", "to_time"}; 103 units x a few windows
_unit_results = TTLCache(max_entries=2048)
_sessions: "Queue[str]" = Queue()
for _i in range(IDLE_EVENTS_SESSIONS):
    _sessions.put(f"{IDLE_EVENTS_SESSION_PREFIX}{_i}")
_pool = ThreadPoolExecutor(max_workers=IDLE_EVENTS_SESSIONS, thread_name_prefix="idle-events")


def _read_unit(unit_id: int, from_time: int, to_time: int) -> Dict[str, Any]:
    session = _sessions.get()
    try:
        result = execute_wialon_unit_idle_events_report(login(session=session), unit_id, from_time, to_time)
    finally:
        _sessions.put(session)
    return dict(result, from_time=from_time, to_time=to_time)


def read_units_idle_events(
    unit_ids: Iterable[int], from_time: int, to_time: int, window_spec: str, ttl_seconds: float,
    reuse: bool = True, skip_units: Iterable[int] = (),
) -> Tuple[Dict[int, Dict[str, Any]], Dict[int, str]]:
    """
    Idle events of every unit in unit_ids for [from_time, to_time], as ({unit_id: result}, {unit_id: error}).

    A unit's result cached for this window_spec within ttl_seconds is reused unless reuse is False. Units in
    skip_units sent no message during the window, so the template has nothing to report for them: they get a complete,
    empty result without running the report. A unit whose report fails is listed in the errors (its events are
    unknown, never zero) and is not cached.
    """
    results: Dict[int, Dict[str, Any]] = {}
    errors: Dict[int, str] = {}
    skip = set(skip_units)
    to_read = []
    for uid in dict.fromkeys(int(u) for u in unit_ids):
        if uid in skip:
            results[uid] = {"events": [], "complete": True, "read_at": None, "from_time": from_time,
                            "to_time": to_time, "no_messages": True}
            continue
        hit = _unit_results.get((uid, window_spec)) if reuse else None
        if hit is not None:
            results[uid] = hit
        else:
            to_read.append(uid)

    futures = {uid: _pool.submit(_read_unit, uid, from_time, to_time) for uid in to_read}
    for uid, future in futures.items():
        try:
            result = future.result()
        except Exception as exc:
            # The message only: Wialon error payloads are logged by the gateway, never returned to clients
            logging.warning("Idle events report failed for unit %s (%s): %s", uid, window_spec, exc)
            errors[uid] = "Wialon idle events report failed"
            continue
        results[uid] = result
        if result["complete"]:
            _unit_results.set((uid, window_spec), result, ttl_seconds)
    return results, errors


def clear_unit_results() -> None:
    _unit_results.clear()


def _is_on(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value == 1


def idling_sensor_values(sid: str, vehicles_by_id: Dict[int, Dict[str, Any]], sensor_names: Dict[int, str]) -> Dict[int, Optional[bool]]:
    """
    Current state of each unit's Idling sensor (the sensor the template reports, by name) from one unit/calc_last
    call: True on, False off, None unknown (no such sensor or an invalid reading).
    """
    ids = [uid for uid in sensor_names if uid in vehicles_by_id]
    if not ids:
        return {}
    calculated = wialon_request("unit/calc_last", {"itemIds": ids}, sid=sid)
    if not isinstance(calculated, list):
        raise WialonAPIError(f"Wialon calculated data request failed: {calculated}")
    calc_by_id = {c.get("i"): c for c in calculated if isinstance(c, dict)}
    states: Dict[int, Optional[bool]] = {}
    for uid in ids:
        sensor = get_sensor_by_name(vehicles_by_id[uid], calc_by_id.get(uid) or {}, sensor_names[uid])
        value = sensor.get("value") if sensor else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= -100000:
            value = None  # Wialon's invalid-value sentinel
        states[uid] = None if value is None else _is_on(value)
    return states


def ongoing_candidate(result: Dict[str, Any], window_open: bool) -> Optional[Dict[str, Any]]:
    """The unit's latest event when it may still be in progress (ended near the report's read time), else None."""
    events = result.get("events") or []
    read_at = result.get("read_at")
    if not window_open or not events or not read_at:
        return None
    latest = events[-1]
    return latest if read_at - latest["end_time"] <= IDLE_ONGOING_MAX_END_AGE_SECONDS else None


def is_ongoing(candidate: Optional[Dict[str, Any]], sensor_on: Optional[bool], seconds_since_update: Any) -> bool:
    """A candidate event is ongoing only while the Idling sensor is on and the unit's telemetry is fresh."""
    return (
        candidate is not None and sensor_on is True
        and isinstance(seconds_since_update, (int, float)) and 0 <= seconds_since_update <= CONN_STABLE_SECONDS
    )


def sensor_names_of(results: Dict[int, Dict[str, Any]], window_open: bool) -> Dict[int, str]:
    """Units with an ongoing candidate -> the name of the sensor its event comes from (default "Idling")."""
    names = {}
    for uid, result in results.items():
        cand = ongoing_candidate(result, window_open)
        if cand is not None:
            names[uid] = cand.get("sensor_name") or "Idling"
    return names


def events_of(results: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [ev for r in results.values() for ev in r.get("events") or []]


# ─── Idle Event Details: department and driver ───────────────────────────────

# Unit Profile field holding the vehicle's department in this account
DEPARTMENT_PROFILE_FIELD = "vehicle_type"
# Driver bindings are read back this far before the event: a driver bound earlier is not identified (no phone shown)
DRIVER_BINDING_LOOKBACK_SECONDS = 7 * 86400


def vehicle_department(unit: Optional[Dict[str, Any]]) -> Optional[str]:
    """The unit's department from its Profile field DEPARTMENT_PROFILE_FIELD, or None when absent or empty."""
    pflds = (unit or {}).get("pflds") or {}
    entries = pflds.values() if isinstance(pflds, dict) else (pflds if isinstance(pflds, list) else [])
    for field in entries:
        if isinstance(field, dict) and field.get("n") == DEPARTMENT_PROFILE_FIELD:
            value = str(field.get("v") or "").strip()
            return value or None
    return None


# Driver record custom field holding the Driver ID shown in Wialon (e.g. "100380"). The record's own `id` is only
# Wialon's resource-local record number (1, 2, 3 ...), which binding history uses to name the driver.
DRIVER_ID_CUSTOM_FIELD = "ID"


def driver_custom_id(record: Optional[Dict[str, Any]]) -> Optional[str]:
    """The Driver ID custom field (`jp`) of a Wialon driver record, or None when absent or empty."""
    fields = (record or {}).get("jp")
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError:
            return None
    if not isinstance(fields, dict):
        return None
    value = str(fields.get(DRIVER_ID_CUSTOM_FIELD) or "").strip()
    return value or None


def _same_name(a: Any, b: Any) -> bool:
    norm = lambda s: " ".join(str(s or "").split()).casefold()
    return bool(norm(a)) and norm(a) == norm(b)


def drivers_bound_at(bindings: Any, unit_id: int, at_ts: int) -> List[int]:
    """Driver ids whose latest binding entry at or before at_ts binds them to unit_id (u = 0 is an unbinding)."""
    bound = []
    for driver_id, entries in (bindings or {}).items() if isinstance(bindings, dict) else []:
        latest = None
        for entry in entries or []:
            t = entry.get("t") if isinstance(entry, dict) else None
            if isinstance(t, (int, float)) and t <= at_ts and (latest is None or t >= latest["t"]):
                latest = entry
        if latest is not None and latest.get("u") == unit_id:
            try:
                bound.append(int(driver_id))
            except (TypeError, ValueError):
                continue
    return bound


def resolve_event_driver(sid: str, unit_id: int, at_ts: int, report_driver_name: Optional[str]) -> Dict[str, Any]:
    """
    The driver of an idle event, from Wialon's driver binding history on the idle report resource: the one driver
    bound to unit_id at at_ts (the event start), looked up by driver id. The phone is returned only when exactly one
    driver was bound, its record exists and its name agrees with the Idle Report's driver name; never by name alone.
    `driver_id` is that record's Driver ID custom field, returned under the same condition (None when the record has
    none); the record number binding history uses is never returned as the Driver ID.
    `status`: matched | no_driver_name | no_binding | ambiguous | driver_not_found | name_mismatch.
    """
    out = {"vehicle_id": unit_id, "start_time": at_ts, "status": None, "driver_name": None, "driver_id": None, "phone": None}
    if not str(report_driver_name or "").strip():
        out["status"] = "no_driver_name"
        return out
    bindings = wialon_request("resource/get_driver_bindings", {
        "resourceId": IDLE_REPORT_RESOURCE_ID, "unitId": int(unit_id), "driverId": 0,
        "timeFrom": int(at_ts) - DRIVER_BINDING_LOOKBACK_SECONDS, "timeTo": int(at_ts),
    }, sid=sid)
    if not isinstance(bindings, dict) or "error" in bindings:
        raise WialonAPIError(f"Wialon driver bindings request failed for unit {unit_id}")
    bound = drivers_bound_at(bindings, int(unit_id), int(at_ts))
    if len(bound) != 1:
        out["status"] = "no_binding" if not bound else "ambiguous"
        return out
    resource = wialon_request("core/search_item", {"id": IDLE_REPORT_RESOURCE_ID, "flags": 0x1 | 0x100}, sid=sid)
    item = resource.get("item") if isinstance(resource, dict) else None
    if not isinstance(item, dict):
        raise WialonAPIError("Wialon driver records request failed")
    record = next((d for d in (item.get("drvrs") or {}).values() if isinstance(d, dict) and d.get("id") == bound[0]), None)
    if record is None:
        out["status"] = "driver_not_found"
        return out
    if not _same_name(record.get("n"), report_driver_name):
        logging.warning("Idle event driver mismatch for unit %s at %s: binding gives driver %s", unit_id, at_ts, bound[0])
        out["status"] = "name_mismatch"
        return out
    out["status"] = "matched"
    out["driver_name"] = str(record.get("n")).strip()
    out["driver_id"] = driver_custom_id(record)
    out["phone"] = str(record.get("p") or "").strip() or None
    return out
