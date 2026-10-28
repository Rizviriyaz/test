"""
Wialon Dynamic Vehicle Distance, Activity and Matching Service for ATS Fleet Management.
Authoritatively resolves vehicle mileage, driving duration, and idling duration
over date intervals using Wialon's native report engine.

Data-completeness rules:
  - A Wialon report that still fails after one forced re-login propagates WialonAPIError (HTTP 502).
  - Anything Wialon did not report, or reported unreadably, is None - never 0. Recorded zeros stay 0.
  - Fleet telematics are cached with a TTL (short for ranges that include today) and fetched once per
    key even under concurrent requests. Partial results are cached only briefly.
"""
import contextvars
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.utils.config import get_env_int
from app.utils.datetime_helpers import get_uae_now, now_timestamp, parse_date_to_uae_bounds
from app.utils.ttl_cache import TTLCache
from app.vehicles import extract_registration_plate, get_all_vehicles
from app.wialon import (
    FUEL_ENGINE_SESSION, FUEL_SESSION, FUEL_TRIPS_SESSION, WIALON_LIMIT_MSGS_ACTIVITY, WialonAPIError, current_sid, login,
    session_name, wialon_error_code as _wialon_error_code,
)
from app.wialon_reports import (
    GROUP_ID_ALL_UNITS,
    _report_lock_for,
    execute_wialon_group_engine_idle_report,
    execute_wialon_group_trips_report,
    execute_wialon_unit_engine_report,
    execute_wialon_unit_trips_report,
)

logger = logging.getLogger(__name__)

TELEMATICS_TTL_LIVE_S = 120      # ranges ending today or later (data still changing)
TELEMATICS_TTL_CLOSED_S = 3600   # ranges fully in the past
TELEMATICS_TTL_PARTIAL_S = 30    # incomplete bundles: retry soon

# Fleet telematics bundles keyed by (date_from, date_to)
_TELEMATICS_CACHE = TTLCache(max_entries=128)
# Per-unit trip lists keyed by (unit_id, date_from, date_to), for actual per-period trend distance
_UNIT_TRIPS_CACHE = TTLCache(max_entries=2048)
# Every unit's trips from one fleet trips report, keyed by (date_from, date_to)
_FLEET_TRIPS_CACHE = TTLCache(max_entries=32)


def clear_distance_cache() -> None:
    """Clear in-memory distance and telematics cache."""
    _TELEMATICS_CACHE.clear()
    _UNIT_TRIPS_CACHE.clear()
    _FLEET_TRIPS_CACHE.clear()


def normalize_alphanumeric(val: Any) -> str:
    """Normalize string by removing non-alphanumeric characters and lowercasing."""
    if not val:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(val)).lower()


def extract_plate_tokens(val: Any) -> List[str]:
    """Extract split tokens and trailing numeric registration digits from plate identifier."""
    if not val:
        return []
    s = str(val).strip()
    tokens = [t.lower() for t in re.split(r"[\s\-_/]+", s) if t]

    # Extract trailing digits (e.g. '13-AUH-30561' -> '30561', 'T-DXB-15770' -> '15770')
    trailing = re.search(r"(\d+)$", s)
    if trailing:
        tokens.append(trailing.group(1).lower())
        tokens.append(trailing.group(1).lstrip("0").lower())
    return list(dict.fromkeys(tokens))  # preserve order, unique


def unit_match_keys(wialon_units: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Optional[str], str, str, str]]:
    """
    Per-unit (unit, plate_lower, plate_norm, name_lower, name_norm) used by match_fuel_vehicle_to_wialon.
    They depend only on the unit, so a caller matching many identifiers against one unit list computes them once.
    """
    keys = []
    for u in wialon_units or []:
        plate = extract_registration_plate(u)
        keys.append((
            u,
            str(plate).strip().lower() if plate else None,
            normalize_alphanumeric(plate) if plate else "",
            str(u.get("nm", "")).strip().lower(),
            normalize_alphanumeric(u.get("nm", "")),
        ))
    return keys


MATCH_EXACT_PLATE = "exact_plate"
MATCH_EXACT_NAME = "exact_name"
MATCH_NUMBER_ONLY = "number_only"


def registration_number(val: Any) -> Optional[str]:
    """Trailing registration number of a plate identifier ('13-AUH-30561' -> '30561'), or None."""
    trailing = re.search(r"(\d+)$", str(val or "").strip())
    return trailing.group(1) if trailing else None


def plate_code_prefix(val: Any) -> str:
    """Normalized identifier without its trailing registration number ('13-AUH-30561' -> '13auh', '30561' -> '')."""
    norm = normalize_alphanumeric(val)
    number = registration_number(val)
    return norm[: len(norm) - len(number)] if number and norm.endswith(number) else norm


def _distinct_result(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The single candidate unit, an ambiguous marker for several distinct units, or None."""
    distinct_ids = {c.get("id") for c in candidates if c.get("id")}
    if len(distinct_ids) == 1:
        return candidates[0]
    if len(distinct_ids) > 1:
        return {"ambiguous": True, "candidates": candidates, "id": None, "nm": None}
    return None


def match_fuel_vehicle_with_basis(
    vehicle_identifier: str,
    wialon_units: List[Dict[str, Any]],
    match_keys: Optional[List[Tuple[Dict[str, Any], Optional[str], str, str, str]]] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Match a fuel vehicle identifier (e.g. 'T-DXB-15770', '13-AUH-30561') to one Wialon unit from a dynamically
    fetched unit list. Returns (result, basis); result is the unit, an ambiguous marker, or None.

    1. Exact (basis exact_plate / exact_name): the identifier equals a unit's verified Profile Registration Plate,
       or its unit name (General -> Name). Plate and name candidates are considered together, so a plate naming
       one unit and a name naming another is ambiguous rather than the plate winning.
    2. Number only (basis number_only): the identifier's trailing registration number equals a unit plate or
       name that is just that number. Prefix/emirate tokens ('13', 'AUH', 'T') never match. The emirate/code
       cannot be verified on this basis; scope.UnitResolver rejects it when other identifiers conflict.

    If several Wialon units match on one basis, does NOT guess; returns an ambiguous marker.
    Zero hardcoded values: matching is purely algorithmic and scales to any fleet size.
    """
    if not vehicle_identifier or not wialon_units:
        return None, None

    fuel_raw = vehicle_identifier.strip()
    fuel_lower = fuel_raw.lower()
    fuel_norm = normalize_alphanumeric(fuel_raw)
    keys = match_keys if match_keys is not None else unit_match_keys(wialon_units)

    # 1. Exact plate or exact unit name
    exact: List[Dict[str, Any]] = []
    by_plate = set()
    for u, u_plate_lower, u_plate_norm, u_nm, _ in keys:
        plate_hit = u_plate_lower is not None and (
            u_plate_lower == fuel_lower or (u_plate_norm and u_plate_norm == fuel_norm))
        name_hit = bool(u_nm) and (u_nm == fuel_lower or u_nm == fuel_norm)
        if plate_hit or name_hit:
            if u not in exact:
                exact.append(u)
            if plate_hit:
                by_plate.add(id(u))
    result = _distinct_result(exact)
    if result is not None:
        if result.get("ambiguous"):
            return result, None
        return result, MATCH_EXACT_PLATE if id(result) in by_plate else MATCH_EXACT_NAME

    # 2. Registration number only, against units whose plate or name is just that number
    number = registration_number(fuel_raw)
    numbers = {n for n in (number, number.lstrip("0") if number else None) if n}
    if not numbers:
        return None, None
    by_number = [
        u for u, _, u_plate_norm, _, u_nm_norm in keys
        if (u_plate_norm and u_plate_norm in numbers) or (u_nm_norm and u_nm_norm in numbers)
    ]
    result = _distinct_result(by_number)
    if result is None:
        return None, None
    return result, None if result.get("ambiguous") else MATCH_NUMBER_ONLY


def match_fuel_vehicle_to_wialon(
    vehicle_identifier: str,
    wialon_units: List[Dict[str, Any]],
    match_keys: Optional[List[Tuple[Dict[str, Any], Optional[str], str, str, str]]] = None,
) -> Optional[Dict[str, Any]]:
    """The unit, ambiguous marker or None of match_fuel_vehicle_with_basis (see there for the rules)."""
    return match_fuel_vehicle_with_basis(vehicle_identifier, wialon_units, match_keys)[0]


# ─── Report execution helpers ──────────────────────────────────────────────────

def _run_report_with_retry(report_fn: Callable[..., Any], sid: str, *args, **kwargs) -> Tuple[Any, str]:
    """Run a report; on WialonAPIError re-login once and retry. A second failure propagates (HTTP 502).
    Wialon error 1004 (LIMIT msgs_activity) propagates at once: a new session would load the same messages again."""
    try:
        return report_fn(sid, *args, **kwargs), sid
    except WialonAPIError as exc:
        if _wialon_error_code(exc) == WIALON_LIMIT_MSGS_ACTIVITY:
            raise
        # Re-login (the same named session, or the shared one) while holding that session's report lock so
        # no other thread's in-flight report (which reads its result rows from the session) has it replaced mid-report.
        with _report_lock_for(sid):
            fresh_sid = login(force=True, session=session_name(sid))
        return report_fn(fresh_sid, *args, **kwargs), fresh_sid


def _hours(seconds: Optional[int]) -> Optional[float]:
    return round(seconds / 3600.0, 2) if seconds is not None else None


def _ttl_for_bundle(date_to: str, bundle: Dict[str, Any]) -> float:
    # Only whole-report problems (an empty report) are worth retrying soon. Unit-level gaps still mark
    # the bundle "partial" for callers but use the normal TTL, so one silent unit cannot force a refetch
    # of both group reports every 30 seconds.
    if bundle.get("errors"):
        return get_env_int("FUEL_TELEMATICS_TTL_PARTIAL_S", TELEMATICS_TTL_PARTIAL_S)
    if str(date_to)[:10] >= get_uae_now().date().isoformat():
        return get_env_int("FUEL_TELEMATICS_TTL_LIVE_S", TELEMATICS_TTL_LIVE_S)
    return get_env_int("FUEL_TELEMATICS_TTL_CLOSED_S", TELEMATICS_TTL_CLOSED_S)


def _empty_entry(name: str) -> Dict[str, Any]:
    return {
        "vehicle_name": name,
        "mileage_km": None,
        "driving_seconds": None,
        "idle_seconds": None,
        "engine_hours_seconds": None,
        "total_time_seconds": None,
        "driving_hours": None,
        "idle_hours": None,
        "engine_hours": None,
        "total_time_hours": None,
        "has_telemetry": False,
        "distance_source": None,
        "in_trips_report": False,
        "in_engine_report": False,
        "incomplete_fields": [],
    }


def _build_fleet_telematics_bundle(date_from: str, date_to: str, sid: Optional[str]) -> Dict[str, Any]:
    # `sid` (the caller's shared session) is not used for these reports: they run on the fuel sessions below
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    errors: List[str] = []
    warnings: List[str] = []
    duplicate_names = set()
    by_name: Dict[str, Dict[str, Any]] = {}
    column_fallbacks: List[str] = []

    def note(msgs):
        for m in msgs or []:
            if m not in warnings:
                warnings.append(m)
            if m not in column_fallbacks:
                column_fallbacks.append(m)

    # 1-2. The two fleet reports are independent: they run at the same time, each on its own fuel session (under
    # that session's report lock), so neither waits for the other and the shared session stays free for Dashboard.
    trips_sid = login(session=FUEL_SESSION)
    engine_sid = login(session=FUEL_ENGINE_SESSION)
    with ThreadPoolExecutor(max_workers=1) as pool:
        engine_job = pool.submit(
            contextvars.copy_context().run,  # keeps the request's PERF trace id in the engine report's logs
            _run_report_with_retry,
            execute_wialon_group_engine_idle_report,
            engine_sid,
            from_ts,
            to_ts,
            group_id=GROUP_ID_ALL_UNITS,
            include_events=False,
        )
        group_trips, _ = _run_report_with_retry(execute_wialon_group_trips_report, trips_sid, from_ts, to_ts)
        group_engine, _ = engine_job.result()

    # 1. Group Trips report: authoritative distance
    if not group_trips:
        errors.append("group_trips_empty")
    for row in group_trips or []:
        name = str(row.get("vehicle_name", "")).strip()
        if not name:
            continue
        if name in by_name:
            duplicate_names.add(name)
            continue
        entry = _empty_entry(name)
        entry["in_trips_report"] = True
        # An unreadable cell, or one read from a guessed column position (Mileage not found by header), is unknown
        if "mileage_km" in (row.get("incomplete_fields") or []) + (row.get("unlocated_fields") or []):
            entry["incomplete_fields"].append("mileage_km")
        else:
            entry["mileage_km"] = float(row.get("mileage_km", 0.0))
            entry["distance_source"] = "wialon_group_trips_report"
        note(row.get("column_warnings"))
        by_name[name] = entry

    # 2. Group Engine & Idling report: authoritative engine / in-motion / idle durations
    engine_rows = (group_engine or {}).get("vehicles") or []
    if not engine_rows:
        errors.append("group_engine_empty")
    note((group_engine or {}).get("column_warnings"))
    engine_seen = set()
    # Fields the engine report read from guessed column positions are unknown for every row
    engine_unlocated = set((group_engine or {}).get("unlocated_fields") or [])
    for row in engine_rows:
        name = str(row.get("vehicle_name", "")).strip()
        if not name:
            continue
        if name in engine_seen:
            duplicate_names.add(name)
            continue
        engine_seen.add(name)
        incomplete = set(row.get("incomplete_fields") or []) | engine_unlocated
        entry = by_name.get(name)
        if entry is None:
            # Present in the engine report but not the trips report: use the engine report's own mileage
            entry = _empty_entry(name)
            if row.get("mileage_km") is not None and "mileage_km" not in incomplete:
                entry["mileage_km"] = float(row["mileage_km"])
                entry["distance_source"] = "wialon_group_engine_report"
            else:
                entry["incomplete_fields"].append("mileage_km")
            by_name[name] = entry
        entry["in_engine_report"] = True
        for key, field in (("in_motion_seconds", "driving_seconds"), ("total_idle_seconds", "idle_seconds"),
                           ("engine_hours_seconds", "engine_hours_seconds"), ("total_time_seconds", "total_time_seconds")):
            if key in incomplete:
                entry[field] = None
                entry["incomplete_fields"].append(field)
            else:
                entry[field] = int(row.get(key, 0))
        entry["driving_hours"] = _hours(entry["driving_seconds"])
        entry["idle_hours"] = _hours(entry["idle_seconds"])
        entry["engine_hours"] = _hours(entry["engine_hours_seconds"])
        entry["total_time_hours"] = _hours(entry["total_time_seconds"])

    # 3. Units only in the trips report have no engine data: their hours are unknown, not zero
    for entry in by_name.values():
        if not entry["in_engine_report"] and engine_rows:
            entry["incomplete_fields"].append("not_in_engine_report")
        entry["has_telemetry"] = any(
            (entry.get(k) or 0) > 0 for k in ("mileage_km", "driving_seconds", "idle_seconds", "engine_hours_seconds")
        )

    # 4. Duplicate unit names cannot be attributed: withhold their telemetry entirely
    for name in duplicate_names:
        by_name.pop(name, None)
    if duplicate_names:
        warnings.append(
            f"Wialon reports contain duplicate unit names ({', '.join(sorted(duplicate_names))}); their telemetry is withheld."
        )

    incomplete_units = sorted(n for n, e in by_name.items() if e["incomplete_fields"])
    if incomplete_units:
        warnings.append(f"Telematics incomplete for {len(incomplete_units)} unit(s): {', '.join(incomplete_units[:20])}")
    if "group_engine_empty" in errors:
        warnings.append("Wialon engine report returned no rows; driving, idle and engine hours are unavailable.")
    if "group_trips_empty" in errors:
        warnings.append("Wialon trips report returned no rows; distance is taken only from the engine report where available.")

    # A column read by historical position (header name not found) is unverified, so not "complete"
    status = "complete" if not (errors or duplicate_names or incomplete_units or column_fallbacks) else "partial"
    return {
        "by_name": by_name,
        "duplicate_names": sorted(duplicate_names),
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "column_fallbacks": column_fallbacks,
        "date_from": date_from,
        "date_to": date_to,
        "fetched_at_utc": now_timestamp(),
    }


def get_fleet_telematics_bundle(
    date_from: str,
    date_to: str,
    sid: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fleet telematics for [date_from, date_to] (UAE days) plus completeness metadata:
      {"by_name": {unit_name: entry}, "duplicate_names": [...], "status": "complete"|"partial",
       "errors": [...], "warnings": [...], "fetched_at_utc": int}
    Entries carry None for any metric Wialon did not report readably.
    Raises WialonAPIError when a report fails after one re-login retry.
    """
    return _TELEMATICS_CACHE.get_or_compute(
        (date_from, date_to),
        lambda: _build_fleet_telematics_bundle(date_from, date_to, sid),
        lambda bundle: _ttl_for_bundle(date_to, bundle),
    )


def _fetch_unit_trips(unit_id: int, date_from: str, date_to: str, sid: Optional[str]) -> Dict[str, Any]:
    """
    One unit's Wialon trips (template 4) for the range: {status, trips[{start_time, end_time, distance_km}], error}.
    status: complete | partial (a trip distance was unreadable; that trip's distance_km is None) |
    failed (report failed after one re-login; never treated as 0 km).
    """
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    try:
        rep, _ = _run_report_with_retry(execute_wialon_unit_trips_report, sid or login(), int(unit_id), from_ts, to_ts)
    except WialonAPIError as exc:
        return {"status": "failed", "trips": [], "error": str(exc)}
    rep = rep or {}
    trips = []
    partial = bool(rep.get("column_warnings")) or bool(rep.get("incomplete_fields")) or bool(rep.get("unlocated_fields"))
    for t in rep.get("trips") or []:
        unreadable = "distance_km" in (t.get("incomplete_fields") or [])
        partial = partial or unreadable
        trips.append({
            "start_time": t.get("start_time"),
            "end_time": t.get("end_time"),
            "distance_km": None if unreadable else float(t.get("distance_km") or 0.0),
        })
    return {"status": "partial" if partial else "complete", "trips": trips, "error": None}


def _ttl_for_unit_trips(date_to: str, result: Dict[str, Any]) -> float:
    if result.get("status") == "failed":
        return get_env_int("FUEL_TELEMATICS_TTL_PARTIAL_S", TELEMATICS_TTL_PARTIAL_S)
    return _ttl_for_bundle(date_to, {})


def _fetch_fleet_trips(date_from: str, date_to: str) -> Dict[str, Any]:
    """
    Every unit's trips from ONE fleet trips report (template 5 with its per-trip sub-rows), run on the fuel trips
    session: {"by_name": {unit_name: {"trips", "trip_unit_ids", "complete"}}, "duplicate_names": [...]}.
    A row is complete only when its sub-rows were all read and parsed and its trip distances add up to the
    row's own Mileage total. Raises WialonAPIError when the report fails after one re-login retry.
    """
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    rows, _ = _run_report_with_retry(
        execute_wialon_group_trips_report, login(session=FUEL_TRIPS_SESSION), from_ts, to_ts, include_trips=True
    )
    by_name: Dict[str, Dict[str, Any]] = {}
    duplicates = set()
    for row in rows or []:
        name = str(row.get("vehicle_name", "")).strip()
        if not name:
            continue
        if name in by_name:
            duplicates.add(name)
            continue
        trips = row.get("trips") or []
        complete = bool(row.get("trips_complete")) and "mileage_km" not in (row.get("incomplete_fields") or [])
        if complete:
            # Trip cells are rounded to 0.01 km each, so allow that much per trip (plus the total's own rounding)
            total = sum(t["distance_km"] for t in trips)
            complete = abs(total - float(row.get("mileage_km") or 0.0)) <= 0.01 * (len(trips) + 1)
        by_name[name] = {"trips": trips, "trip_unit_ids": row.get("trip_unit_ids") or [], "complete": complete}
    for name in duplicates:
        by_name.pop(name, None)
    return {"by_name": by_name, "duplicate_names": sorted(duplicates)}


def fleet_trips_enabled() -> bool:
    """FUEL_TREND_FLEET_TRIPS=0 restores one trips report per unit for the fuel trend."""
    return get_env_int("FUEL_TREND_FLEET_TRIPS", 1) != 0


def get_fleet_trips(date_from: str, date_to: str) -> Optional[Dict[str, Any]]:
    """The cached fleet trips report for the range, or None when disabled (callers report per unit) or it failed."""
    if not fleet_trips_enabled():
        return None
    try:
        return _FLEET_TRIPS_CACHE.get_or_compute(
            (date_from, date_to),
            lambda: _fetch_fleet_trips(date_from, date_to),
            lambda r: _ttl_for_bundle(date_to, {}),
        )
    except WialonAPIError as exc:
        logger.warning("Fleet trips report failed for %s..%s; trip distance unavailable (no per-unit reports): %s",
                       date_from, date_to, exc)
        return None


def _fleet_unit_trips(fleet: Optional[Dict[str, Any]], uid: int, name: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    A unit's trips from the fleet report, shaped like _fetch_unit_trips, or None when the fleet report cannot
    attribute them with certainty (unit absent, name not unique, row incomplete, or trip cells naming another
    unit) - the caller then runs that unit's own trips report.
    """
    if fleet is None or not name:
        return None
    row = fleet["by_name"].get(name)
    if row is None or not row["complete"] or any(u != uid for u in row["trip_unit_ids"]):
        return None
    return {"status": "complete", "trips": [dict(t) for t in row["trips"]], "error": None}


def get_units_trips_for_range(
    unit_ids: List[int], date_from: str, date_to: str, sid: Optional[str] = None,
    units: Optional[List[Dict[str, Any]]] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Trips per Wialon unit id over [date_from, date_to] (UAE days), cached, so day, week and month groupings
    reuse the same reports. A unit whose report fails is returned as status 'failed' - callers must not count it as 0 km.

    With `units` (the Wialon unit list, for id -> name), every unit is first taken from one fleet trips report
    (get_fleet_trips); a unit it cannot cover with certainty, or every unit when it is disabled, gets its own trips
    report exactly as without `units`. Per-unit reports run serially under the shared report lock, each on the
    gateway's current session, so a session refreshed mid-batch (Wialon error 1 or a forced re-login) is picked
    up by the remaining units instead of each of their calls first failing on the stale one.
    When the fleet trips report itself failed, no per-unit reports run for the range (one per unit would pile onto
    whatever stopped it): uncovered units are returned as 'failed' (not cached), so the next request retries.
    """
    wanted = sorted({int(u) for u in unit_ids})
    fleet = get_fleet_trips(date_from, date_to) if units is not None and wanted else None
    fleet_failed = fleet is None and units is not None and bool(wanted) and fleet_trips_enabled()
    names = {int(u["id"]): str(u.get("nm", "")).strip() for u in units or [] if u.get("id") is not None}
    name_counts = Counter(names.values())
    out: Dict[int, Dict[str, Any]] = {}
    per_unit = 0
    for uid in wanted:
        name = names.get(uid)
        res = _fleet_unit_trips(fleet, uid, name if name and name_counts[name] == 1 else None)
        if res is None and fleet_failed:
            res = {"status": "failed", "trips": [], "error": "fleet trips report failed"}
        elif res is None:
            per_unit += 1
            res = _UNIT_TRIPS_CACHE.get_or_compute(
                (uid, date_from, date_to),
                lambda uid=uid: _fetch_unit_trips(uid, date_from, date_to, current_sid() or sid),
                lambda r: _ttl_for_unit_trips(date_to, r),
            )
        out[uid] = res
    if units is not None:
        logger.info("PERF fuel_unit_trips range=%s..%s units=%s fleet_report=%s per_unit_fallback=%s",
                    date_from, date_to, len(wanted), "ok" if fleet is not None else "unavailable", per_unit)
    return out


def get_fleet_distances_map(
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, float]:
    """
    Fleet distances {wialon_unit_name: mileage_km} for units whose distance Wialon reported readably.
    Units with unknown distance are omitted (treated as unknown downstream), never set to 0.
    """
    telematics = get_fleet_telematics_map(date_from, date_to, sid)
    return {k: v["mileage_km"] for k, v in telematics.items() if v.get("mileage_km") is not None}


def get_fleet_telematics_map(
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Retrieves authoritative distance, driving duration, idling duration, and engine hours
    for all fleet units in unified cached calls using Wialon Group Reports.
    Returns: {wialon_unit_name: {"mileage_km", "driving_seconds", "idle_seconds", "engine_hours_seconds", ...}}
    with None for metrics that are unavailable (see get_fleet_telematics_bundle for status/warnings).
    """
    return get_fleet_telematics_bundle(date_from, date_to, sid)["by_name"]


def get_vehicle_distance(
    wialon_unit_id: int,
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Authoritatively retrieves vehicle distance traveled over a date range directly
    from Wialon's native Trips Report engine for a single unit.
    Raises WialonAPIError if any trip's distance could not be read (no partial sum is returned).
    """
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    current_sid = sid or login()

    rep_result, current_sid = _run_report_with_retry(
        execute_wialon_unit_trips_report, current_sid, int(wialon_unit_id), from_ts, to_ts
    )

    if not rep_result:
        return {
            "distance": 0.0,
            "unit": "km",
            "source": "wialon_native_trips_report",
            "date_from": date_from,
            "date_to": date_to,
            "trip_count": 0,
            "has_data": False,
        }

    if "distance_km" in (rep_result.get("incomplete_fields") or []) + (rep_result.get("unlocated_fields") or []):
        raise WialonAPIError(f"Wialon trips report for unit {wialon_unit_id} contains unreadable trip distances")

    trips = rep_result.get("trips", [])
    total_km = sum(float(t.get("distance_km", 0.0)) for t in trips)

    has_data = len(trips) > 0

    return {
        "distance": round(total_km, 2),
        "unit": "km",
        "source": "wialon_native_trips_report",
        "date_from": date_from,
        "date_to": date_to,
        "trip_count": len(trips),
        "has_data": has_data,
    }


def get_vehicle_telematics_detail(
    wialon_unit_id: int,
    date_from: str,
    date_to: str,
    sid: Optional[str] = None
) -> Dict[str, Any]:
    """
    Authoritatively retrieves complete telematics detail (trips, engine hours, idle segments)
    for a single unit.

    - Distance: sum of Wialon trip distances (None if any trip distance is unreadable).
    - Driving hours: the engine report's "In motion" time (None if unavailable). Trip "Total time"
      includes stops and is never used as driving time.
    - Idle / engine hours: engine report (None if unreadable).
    A report that still fails after one re-login propagates WialonAPIError (HTTP 502).

    Source difference (documented, not a defect): this is the per-unit Engine Hours report, while the fleet KPI, the
    vehicle rankings and the waste report use the GROUP Engine & Idling report (get_fleet_telematics_bundle). Wialon
    can report slightly different idle/driving totals for one unit in the two reports, so the vehicle-detail idle
    estimate may differ a little from that vehicle's ranking row. The raw durations are returned in seconds as well
    (driving_seconds / idle_seconds / engine_hours_seconds) so the detail estimate is computed from seconds like the
    ranking, not from the hours rounded to 0.01 h here.
    """
    from_ts, to_ts = parse_date_to_uae_bounds(date_from, date_to)
    current_sid = sid or login()

    trips_rep, current_sid = _run_report_with_retry(
        execute_wialon_unit_trips_report, current_sid, int(wialon_unit_id), from_ts, to_ts
    )
    engine_rep, current_sid = _run_report_with_retry(
        execute_wialon_unit_engine_report, current_sid, int(wialon_unit_id), from_ts, to_ts
    )

    warnings: List[str] = []
    for rep in (trips_rep, engine_rep):
        for w in (rep or {}).get("column_warnings") or []:
            if w not in warnings:
                warnings.append(w)
    column_fallback = bool(warnings)

    trips = trips_rep.get("trips", []) if trips_rep else []
    trips_incomplete = "distance_km" in (((trips_rep or {}).get("incomplete_fields") or [])
                                         + ((trips_rep or {}).get("unlocated_fields") or []))
    total_distance_km = None if trips_incomplete else round(sum(float(t.get("distance_km", 0.0)) for t in trips), 2)
    if trips_incomplete:
        warnings.append("One or more Wialon trip distances could not be read; distance unavailable.")

    engine_incomplete = set((engine_rep or {}).get("incomplete_fields") or [])
    total_idle_seconds = None if "idle_seconds" in engine_incomplete else (engine_rep or {}).get("total_idle_seconds", 0)
    total_engine_seconds = None if "engine_hours" in engine_incomplete else (engine_rep or {}).get("total_engine_seconds", 0)
    in_motion_seconds = (engine_rep or {}).get("total_in_motion_seconds")
    idle_events = (engine_rep or {}).get("idle_events", [])
    # The event list is complete only when Wialon delivered every idle interval and the idle cell was read
    # (an unreadable one parses as 0, so no intervals were fetched); otherwise the count is unknown (None)
    idle_events_complete = engine_rep is not None and "idle_events" not in engine_incomplete and total_idle_seconds is not None
    if engine_rep is not None and total_idle_seconds is not None and not idle_events_complete:
        warnings.append("Some Wialon idle events could not be read; idle session count unavailable.")
    if engine_rep is not None and not engine_rep.get("rows_total"):
        warnings.append("Wialon engine report returned no rows for this unit in the selected period.")
    if in_motion_seconds is None:
        warnings.append("Wialon 'In motion' time unavailable; driving hours not reported.")
    if total_idle_seconds is None or total_engine_seconds is None:
        warnings.append("Wialon engine/idle durations could not be read; those values are unavailable.")

    total_interval_seconds = max(0, to_ts - from_ts)
    stopped_seconds = max(0, total_interval_seconds - total_engine_seconds) if total_engine_seconds is not None else None

    unavailable = column_fallback or any(
        v is None for v in (total_distance_km, in_motion_seconds, total_idle_seconds, total_engine_seconds)
    )

    return {
        "distance_km": total_distance_km,
        "trip_count": len(trips),
        "driving_duration_hours": _hours(in_motion_seconds),
        "idle_duration_hours": _hours(total_idle_seconds),
        "engine_hours": _hours(total_engine_seconds),
        "stopped_hours": _hours(stopped_seconds),
        "driving_seconds": in_motion_seconds,
        "idle_seconds": total_idle_seconds,
        "engine_hours_seconds": total_engine_seconds,
        "idle_events_count": len(idle_events) if idle_events_complete else None,
        "idle_events_complete": idle_events_complete,
        "idle_events": idle_events,
        "trips": trips,
        "has_data": (len(trips) > 0 or (total_engine_seconds or 0) > 0),
        "status": "partial" if unavailable else "complete",
        "driving_basis": "engine_report_in_motion" if in_motion_seconds is not None else "unavailable",
        "distance_source": "wialon_unit_trips_report" if total_distance_km is not None else "unavailable",
        "warnings": warnings,
    }
