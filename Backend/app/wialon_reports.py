"""
Wialon Native Report Integration Module for ATS Fleet Management.

Provides high-performance, authoritative native report execution directly through Wialon's report engine:
  1. Unit Trips Report (Distance, Duration, Moving Duration, Avg/Max Speed, Start/End Locations)
  2. Unit Engine & Idle Report (Engine Hours, Idle Duration, Stop Duration)
  3. Group Trips & Utilization Rollups (All Units or Specific Groups)
  4. Group Engine & Idle Rollups (Engine Hours, Motion, Idling, Discrete Idle Events)
"""

import logging
import threading
from typing import Dict, List, Any, Optional
from app.wialon import WialonAPIError, wialon_request

# Wialon Resource ID containing official templates in this account
WIALON_RESOURCE_ID = 601814494
TEMPLATE_ID_UNIT_TRIPS = 4
TEMPLATE_ID_UNIT_ENGINE = 1
TEMPLATE_ID_GROUP_TRIPS = 5
TEMPLATE_ID_GROUP_ENGINE = 2

# Standard 'All Unit' group ID in ATS Wialon account
GROUP_ID_ALL_UNITS = 601817480

_report_lock = threading.Lock()


def extract_cell_value(cell: Any) -> Any:
    """Extract numeric or primitive value from Wialon report cell (dict or primitive)."""
    if isinstance(cell, dict):
        return cell.get("v") if cell.get("v") is not None else cell.get("t")
    return cell


def extract_cell_text(cell: Any) -> str:
    """Extract string text representation from Wialon report cell."""
    if isinstance(cell, dict):
        return str(cell.get("t") if cell.get("t") is not None else cell.get("v", ""))
    return str(cell or "")


def parse_float_safe(val: Any, default: float = 0.0) -> float:
    """
    Safely parse float from Wialon cell (primitive, dict, or string with units/separators).
    Handles comma/dot decimal separators, thousand separators, and units like 'km', 'km/h'.
    """
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, dict):
        raw = val.get("v") if val.get("v") is not None else val.get("t")
        return parse_float_safe(raw, default=default)

    clean_str = str(val).replace("km/h", "").replace("km", "").replace(" ", "").strip()
    if not clean_str or clean_str in ("---", "N/A", "null", "none"):
        return default

    # Handle thousand and decimal separators: "1,234.56" vs "12,5"
    if "," in clean_str and "." in clean_str:
        clean_str = clean_str.replace(",", "")
    elif "," in clean_str and "." not in clean_str:
        clean_str = clean_str.replace(",", ".")

    try:
        return float(clean_str)
    except (ValueError, TypeError):
        return default


def parse_duration_to_seconds(dur_str: Any) -> int:
    """Parse Wialon duration string (HH:MM:SS or MM:SS) to total seconds."""
    if isinstance(dur_str, (int, float)):
        return int(dur_str)
    if isinstance(dur_str, dict):
        dur_str = dur_str.get("v") if dur_str.get("v") is not None else dur_str.get("t", "0")
    try:
        parts = str(dur_str).strip().split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
        return int(float(dur_str))
    except (ValueError, TypeError):
        return 0


def parse_duration_to_minutes(dur_str: Any) -> float:
    """Parse Wialon duration string to minutes rounded to 2 decimal places."""
    sec = parse_duration_to_seconds(dur_str)
    return round(sec / 60.0, 2)


def execute_wialon_unit_trips_report(
    sid: str, vehicle_id: int, from_time_utc: int, to_time_utc: int
) -> Optional[Dict[str, Any]]:
    """
    Execute Wialon's native Trips Report template (ID 4) for an individual unit.
    Returns authoritative trip list and summary metrics directly from Wialon.

    Guarantees report cleanup inside finally block and thread-safe execution.
    """
    with _report_lock:
        report_executed = False
        try:
            exec_params = {
                "reportResourceId": WIALON_RESOURCE_ID,
                "reportTemplateId": TEMPLATE_ID_UNIT_TRIPS,
                "reportObjectId": int(vehicle_id),
                "reportObjectSecId": 0,
                "interval": {
                    "flags": 0,
                    "from": int(from_time_utc),
                    "to": int(to_time_utc),
                },
            }

            res = wialon_request("report/exec_report", exec_params, sid=sid, timeout=60)
            if not isinstance(res, dict) or "error" in res or "reportResult" not in res:
                logging.warning(
                    f"Wialon report exec failed for vehicle {vehicle_id}: {res}"
                )
                raise WialonAPIError(f"Wialon trips report failed for vehicle {vehicle_id}")

            report_executed = True
            rr = res["reportResult"]
            stats_raw = rr.get("stats", [])
            stats = {}
            for st in stats_raw:
                if len(st) >= 2:
                    stats[str(st[0]).strip().lower()] = st[1]

            tables = rr.get("tables", [])
            trips = []

            if tables:
                total_rows = tables[0].get("rows", 0)
                if total_rows > 0:
                    row_res = wialon_request(
                        "report/get_result_rows",
                        {"tableIndex": 0, "indexFrom": 0, "indexTo": total_rows - 1},
                        sid=sid,
                    )
                    if isinstance(row_res, dict) and "error" in row_res:
                        raise WialonAPIError(f"Wialon trip rows failed for vehicle {vehicle_id}: {row_res}")
                    if not isinstance(row_res, list):
                        raise WialonAPIError(f"Unexpected response structure for trip rows of vehicle {vehicle_id}: {row_res}")
                    for r in row_res:
                        cols = r.get("c", [])
                        if len(cols) >= 10:
                            start_obj = cols[1] if isinstance(cols[1], dict) else {}
                            start_loc_obj = cols[2] if isinstance(cols[2], dict) else {}
                            end_obj = cols[3] if isinstance(cols[3], dict) else {}
                            end_loc_obj = cols[4] if isinstance(cols[4], dict) else {}
                            max_spd_obj = cols[8] if isinstance(cols[8], dict) else {}

                            dist_km = parse_float_safe(extract_cell_value(cols[9]) if len(cols) > 9 else 0.0)

                            dur_str = extract_cell_text(cols[5])
                            mov_str = extract_cell_text(cols[6])
                            dur_mins = parse_duration_to_minutes(dur_str)

                            trips.append(
                                {
                                    "start_time": start_obj.get("v"),
                                    "start_time_str": start_obj.get("t"),
                                    "start_location": start_loc_obj.get("t") or "Unknown",
                                    "start_latitude": start_loc_obj.get("y"),
                                    "start_longitude": start_loc_obj.get("x"),
                                    "end_time": end_obj.get("v"),
                                    "end_time_str": end_obj.get("t"),
                                    "end_location": end_loc_obj.get("t") or "Unknown",
                                    "end_latitude": end_loc_obj.get("y"),
                                    "end_longitude": end_loc_obj.get("x"),
                                    "duration": dur_str,
                                    "duration_str": dur_str,
                                    "duration_minutes": dur_mins,
                                    "moving_duration": mov_str,
                                    "moving_duration_str": mov_str,
                                    "average_speed": str(cols[7]),
                                    "max_speed": (
                                        max_spd_obj.get("t")
                                        if isinstance(max_spd_obj, dict)
                                        else str(max_spd_obj)
                                    ),
                                    "distance_km": round(dist_km, 2),
                                }
                            )

            return {"stats": stats, "trips": trips}

        except Exception as exc:
            logging.error(
                f"Error executing Wialon trips report for vehicle {vehicle_id}: {exc}"
            )
            raise WialonAPIError(f"Wialon trips report failed for vehicle {vehicle_id}") from exc
        finally:
            if report_executed:
                try:
                    wialon_request("report/cleanup_result", {}, sid=sid)
                except Exception as clean_err:
                    logging.debug(f"Failed to cleanup report for vehicle {vehicle_id}: {clean_err}")


def execute_wialon_unit_engine_report(
    sid: str, vehicle_id: int, from_time_utc: int, to_time_utc: int
) -> Optional[Dict[str, Any]]:
    """
    Execute Wialon's native Engine Hours Report template (ID 1) for a single unit.
    Returns authoritative engine hours, in motion duration, idling duration,
    and individual idle periods directly from Wialon.
    """
    with _report_lock:
        report_executed = False
        try:
            exec_params = {
                "reportResourceId": WIALON_RESOURCE_ID,
                "reportTemplateId": TEMPLATE_ID_UNIT_ENGINE,
                "reportObjectId": int(vehicle_id),
                "reportObjectSecId": 0,
                "interval": {
                    "flags": 0,
                    "from": int(from_time_utc),
                    "to": int(to_time_utc),
                },
            }

            res = wialon_request("report/exec_report", exec_params, sid=sid, timeout=60)
            if not isinstance(res, dict) or "error" in res or "reportResult" not in res:
                logging.warning(
                    f"Wialon unit engine report exec failed for vehicle {vehicle_id}: {res}"
                )
                raise WialonAPIError(f"Wialon engine report failed for vehicle {vehicle_id}")

            report_executed = True
            rr = res["reportResult"]
            tables = rr.get("tables", [])
            idle_events = []
            total_idle_sec = 0
            total_engine_sec = 0

            if tables:
                total_rows = tables[0].get("rows", 0)
                if total_rows > 0:
                    row_res = wialon_request(
                        "report/get_result_rows",
                        {"tableIndex": 0, "indexFrom": 0, "indexTo": min(total_rows - 1, 100)},
                        sid=sid,
                    )
                    if isinstance(row_res, dict) and "error" in row_res:
                        raise WialonAPIError(f"Wialon engine rows failed for vehicle {vehicle_id}: {row_res}")
                    if not isinstance(row_res, list):
                        raise WialonAPIError(f"Unexpected response structure for engine rows of vehicle {vehicle_id}: {row_res}")
                    for row_idx, r in enumerate(row_res):
                        cols = r.get("c", [])
                        if len(cols) >= 9:
                            engine_hours_str = str(cols[6]) if len(cols) > 6 else "0:00:00"
                            idling_str = str(cols[8]) if len(cols) > 8 else "0:00:00"
                            total_engine_sec += parse_duration_to_seconds(engine_hours_str)
                            total_idle_sec += parse_duration_to_seconds(idling_str)

                            # Fetch subrows for discrete idle segments
                            if r.get("d", 0) > 0 and parse_duration_to_seconds(idling_str) > 0:
                                sub = wialon_request(
                                    "report/get_result_subrows",
                                    {"tableIndex": 0, "rowIndex": row_idx, "indexFrom": 0, "indexTo": 200},
                                    sid=sid,
                                )
                                if isinstance(sub, list):
                                    for s in sub:
                                        sc = s.get("c", [])
                                        if len(sc) >= 9:
                                            s_idle_str = str(sc[8]).strip()
                                            s_idle_sec = parse_duration_to_seconds(s_idle_str)
                                            if s_idle_sec > 0:
                                                st_obj = sc[1] if isinstance(sc[1], dict) else {}
                                                et_obj = sc[2] if isinstance(sc[2], dict) else {}
                                                loc_obj = sc[3] if isinstance(sc[3], dict) else (sc[4] if isinstance(sc[4], dict) else {})
                                                st_ts = st_obj.get("v") or s.get("t1") or 0
                                                et_ts = et_obj.get("v") or s.get("t2") or (st_ts + s_idle_sec)
                                                lat = loc_obj.get("y") or st_obj.get("y") or et_obj.get("y")
                                                lng = loc_obj.get("x") or st_obj.get("x") or et_obj.get("x")
                                                addr = loc_obj.get("t") or "Unknown Location"

                                                idle_events.append({
                                                    "start_time": st_ts,
                                                    "end_time": et_ts,
                                                    "duration": s_idle_sec,
                                                    "duration_minutes": round(s_idle_sec / 60.0, 2),
                                                    "location": {
                                                        "latitude": lat,
                                                        "longitude": lng,
                                                    },
                                                    "address": addr,
                                                })

            return {
                "total_idle_seconds": total_idle_sec,
                "total_idle_minutes": round(total_idle_sec / 60.0, 2),
                "total_engine_seconds": total_engine_sec,
                "total_engine_hours": round(total_engine_sec / 3600.0, 2),
                "idle_events": idle_events,
            }

        except Exception as exc:
            logging.error(f"Error executing Wialon unit engine report for vehicle {vehicle_id}: {exc}")
            raise WialonAPIError(f"Wialon engine report failed for vehicle {vehicle_id}") from exc
        finally:
            if report_executed:
                try:
                    wialon_request("report/cleanup_result", {}, sid=sid)
                except Exception as clean_err:
                    logging.debug(f"Failed to cleanup unit engine report: {clean_err}")


def execute_wialon_group_trips_report(
    sid: str, from_time_utc: int, to_time_utc: int, group_id: int = GROUP_ID_ALL_UNITS
) -> Optional[List[Dict[str, Any]]]:
    """
    Execute Wialon's native Trips Report - All Unit template (ID 5) for a fleet unit group.
    Returns authoritative vehicle mileage and trip metrics for all units directly from Wialon.

    Guarantees thread-safe execution and cleanup in finally block.
    """
    with _report_lock:
        report_executed = False
        try:
            exec_params = {
                "reportResourceId": WIALON_RESOURCE_ID,
                "reportTemplateId": TEMPLATE_ID_GROUP_TRIPS,
                "reportObjectId": int(group_id),
                "reportObjectSecId": 0,
                "interval": {
                    "flags": 0,
                    "from": int(from_time_utc),
                    "to": int(to_time_utc),
                },
            }

            res = wialon_request("report/exec_report", exec_params, sid=sid, timeout=120)
            if not isinstance(res, dict) or "error" in res or "reportResult" not in res:
                logging.warning(
                    f"Wialon group report exec failed for group {group_id}: {res}"
                )
                raise WialonAPIError(f"Wialon group trips report failed for group {group_id}")

            report_executed = True
            rr = res["reportResult"]
            tables = rr.get("tables", [])
            unit_summaries = []

            if tables:
                total_rows = tables[0].get("rows", 0)
                if total_rows > 0:
                    row_res = wialon_request(
                        "report/get_result_rows",
                        {"tableIndex": 0, "indexFrom": 0, "indexTo": total_rows - 1},
                        sid=sid,
                    )
                    if isinstance(row_res, dict) and "error" in row_res:
                        raise WialonAPIError(f"Wialon trip rows failed for group {group_id}: {row_res}")
                    if not isinstance(row_res, list):
                        raise WialonAPIError(f"Unexpected response structure for trip rows of group {group_id}: {row_res}")
                    for r in row_res:
                        cols = r.get("c", [])
                        # Table 0 columns: ['Grouping', 'Beginning', 'Initial location', 'End', 'Final location', 'Mileage', 'Total time', 'Engine hours', 'Avg. speed', 'Max. speed', 'Driver']
                        if len(cols) >= 6:
                            vehicle_name = extract_cell_text(cols[0]).strip()
                            dist_km = parse_float_safe(extract_cell_value(cols[5]) if len(cols) > 5 else 0.0)

                            total_time_str = extract_cell_text(cols[6]) if len(cols) > 6 else "0:00:00"
                            engine_hours_str = extract_cell_text(cols[7]) if len(cols) > 7 else "0:00:00"
                            avg_spd_str = extract_cell_text(cols[8]) if len(cols) > 8 else "0 km/h"
                            max_spd_str = extract_cell_text(cols[9]) if len(cols) > 9 else "0 km/h"
                            driver_col = extract_cell_text(cols[10]).strip() if len(cols) > 10 else ""

                            unit_summaries.append({
                                "vehicle_name": vehicle_name,
                                "mileage_km": round(dist_km, 2),
                                "total_time": total_time_str,
                                "engine_hours": engine_hours_str,
                                "avg_speed": avg_spd_str,
                                "max_speed": max_spd_str,
                                "driver": driver_col,
                            })

            return unit_summaries

        except Exception as exc:
            logging.error(f"Error executing Wialon group trips report: {exc}")
            raise WialonAPIError(f"Wialon group trips report failed for group {group_id}") from exc
        finally:
            if report_executed:
                try:
                    wialon_request("report/cleanup_result", {}, sid=sid)
                except Exception as clean_err:
                    logging.debug(f"Failed to cleanup group report: {clean_err}")


def execute_wialon_group_engine_idle_report(
    sid: str,
    from_time_utc: int,
    to_time_utc: int,
    group_id: int = GROUP_ID_ALL_UNITS,
    include_events: bool = True
) -> Optional[Dict[str, Any]]:
    """
    Execute Wialon's native Engine Hours Report - All Unit template (ID 2) for a fleet group.
    Returns authoritative vehicle idling metrics, engine hours, moving durations, and discrete
    idle events across the entire fleet directly from Wialon.

    Guarantees thread-safe execution and cleanup in finally block.
    """
    with _report_lock:
        report_executed = False
        try:
            exec_params = {
                "reportResourceId": WIALON_RESOURCE_ID,
                "reportTemplateId": TEMPLATE_ID_GROUP_ENGINE,
                "reportObjectId": int(group_id),
                "reportObjectSecId": 0,
                "interval": {
                    "flags": 0,
                    "from": int(from_time_utc),
                    "to": int(to_time_utc),
                },
            }

            res = wialon_request("report/exec_report", exec_params, sid=sid, timeout=120)
            if not isinstance(res, dict) or "error" in res or "reportResult" not in res:
                logging.warning(
                    f"Wialon group engine report exec failed for group {group_id}: {res}"
                )
                raise WialonAPIError(f"Wialon group engine report failed for group {group_id}")

            report_executed = True
            rr = res["reportResult"]
            tables = rr.get("tables", [])
            unit_summaries = []
            all_idle_events = []

            if tables:
                total_rows = tables[0].get("rows", 0)
                if total_rows > 0:
                    row_res = wialon_request(
                        "report/get_result_rows",
                        {"tableIndex": 0, "indexFrom": 0, "indexTo": min(total_rows - 1, 500)},
                        sid=sid,
                    )
                    if isinstance(row_res, dict) and "error" in row_res:
                        raise WialonAPIError(f"Wialon engine rows failed for group {group_id}: {row_res}")
                    if not isinstance(row_res, list):
                        raise WialonAPIError(f"Unexpected response structure for engine rows of group {group_id}: {row_res}")
                    for row_idx, r in enumerate(row_res):
                        cols = r.get("c", [])
                        # Table 0: ['Grouping', 'Beginning', 'Initial location', 'End', 'Final location', 'Total time', 'Engine hours', 'In motion', 'Idling', 'Mileage', 'Avg. speed', 'Max. speed']
                        if len(cols) >= 9:
                            vehicle_name = str(cols[0]).strip()
                            total_time_str = str(cols[5]) if len(cols) > 5 else "0:00:00"
                            engine_hours_str = str(cols[6]) if len(cols) > 6 else "0:00:00"
                            in_motion_str = str(cols[7]) if len(cols) > 7 else "0:00:00"
                            idling_str = str(cols[8]) if len(cols) > 8 else "0:00:00"
                            mileage_str = str(cols[9]) if len(cols) > 9 else "0.00 km"

                            total_time_sec = parse_duration_to_seconds(total_time_str)
                            engine_hours_sec = parse_duration_to_seconds(engine_hours_str)
                            in_motion_sec = parse_duration_to_seconds(in_motion_str)
                            idling_sec = parse_duration_to_seconds(idling_str)

                            unit_events = []
                            # Fetch subrows for discrete idle segments if idling > 0
                            if include_events and idling_sec > 0 and r.get("d", 0) > 0:
                                sub = wialon_request(
                                    "report/get_result_subrows",
                                    {"tableIndex": 0, "rowIndex": row_idx, "indexFrom": 0, "indexTo": 200},
                                    sid=sid,
                                )
                                if isinstance(sub, list):
                                    for s in sub:
                                        sc = s.get("c", [])
                                        if len(sc) >= 9:
                                            s_idle_str = str(sc[8]).strip()
                                            s_idle_sec = parse_duration_to_seconds(s_idle_str)
                                            if s_idle_sec > 0:
                                                st_obj = sc[1] if isinstance(sc[1], dict) else {}
                                                loc_obj = sc[2] if isinstance(sc[2], dict) else (sc[4] if isinstance(sc[4], dict) else {})
                                                et_obj = sc[3] if isinstance(sc[3], dict) else {}
                                                st_ts = st_obj.get("v") or s.get("t1") or 0
                                                et_ts = et_obj.get("v") or s.get("t2") or (st_ts + s_idle_sec)
                                                lat = loc_obj.get("y") or st_obj.get("y") or et_obj.get("y")
                                                lng = loc_obj.get("x") or st_obj.get("x") or et_obj.get("x")
                                                addr = loc_obj.get("t") or "Unknown Location"

                                                ev_item = {
                                                    "vehicle_name": vehicle_name,
                                                    "start_time": st_ts,
                                                    "end_time": et_ts,
                                                    "duration": s_idle_sec,
                                                    "duration_minutes": round(s_idle_sec / 60.0, 2),
                                                    "location": {
                                                        "latitude": lat,
                                                        "longitude": lng,
                                                    },
                                                    "address": addr,
                                                }
                                                unit_events.append(ev_item)
                                                all_idle_events.append(ev_item)

                            longest_idle = max((ev["duration"] for ev in unit_events), default=idling_sec)

                            unit_summaries.append({
                                "vehicle_name": vehicle_name,
                                "total_time_str": total_time_str,
                                "total_time_seconds": total_time_sec,
                                "engine_hours_str": engine_hours_str,
                                "engine_hours_seconds": engine_hours_sec,
                                "in_motion_str": in_motion_str,
                                "in_motion_seconds": in_motion_sec,
                                "idling_str": idling_str,
                                "total_idle_seconds": idling_sec,
                                "idle_minutes": round(idling_sec / 60.0, 2),
                                "idle_events": len(unit_events) if unit_events else (1 if idling_sec > 0 else 0),
                                "longest_idle_seconds": longest_idle,
                                "mileage_str": mileage_str,
                                "events": unit_events,
                            })

            return {
                "vehicles": unit_summaries,
                "events": all_idle_events,
            }

        except Exception as exc:
            logging.error(f"Error executing Wialon group engine report: {exc}")
            raise WialonAPIError(f"Wialon group engine report failed for group {group_id}") from exc
        finally:
            if report_executed:
                try:
                    wialon_request("report/cleanup_result", {}, sid=sid)
                except Exception as clean_err:
                    logging.debug(f"Failed to cleanup group engine report: {clean_err}")
