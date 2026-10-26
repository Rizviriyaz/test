"""
Wialon Native Report Integration Module for ATS Fleet Management.

Provides high-performance, authoritative native report execution directly through Wialon's report engine:
  1. Unit Trips Report (Distance, Duration, Engine Hours, Avg/Max Speed, Start/End Locations)
  2. Unit Engine & Idle Report (Engine Hours, In Motion, Idle Duration)
  3. Group Trips & Utilization Rollups (All Units or Specific Groups)
  4. Group Engine & Idle Rollups (Engine Hours, Motion, Idling, Discrete Idle Events)

Completeness contract (shared by fuel, idle analytics, dashboard and fleet analytics):
  - Every report row is read in pages; a page shorter than requested raises WialonAPIError instead of
    silently truncating totals. Sub-rows (discrete idle events) are paged too; if Wialon returns an
    error for them the unit's events are flagged via `incomplete_fields` ("idle_events") and the
    report-level `events_incomplete` list rather than failing the whole report.
  - Existing keys keep their historical types and values (ints default to 0) so existing callers are
    unaffected. Cells that could not be read are additionally listed in `incomplete_fields`, so callers
    that must not treat "unreadable" as zero (fuel efficiency) can tell the difference.
  - Columns are located by header name when Wialon sends a header; if a name is not found the
    historical column position is used and the fact is reported in `column_warnings`. Every value read
    from a guessed position (name not found, or no header at all) is listed in the additive
    `unlocated_fields`, so callers can report it as unknown instead of trusting the guessed column.
"""

import json
import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from typing import Collection, Dict, List, Any, Optional, Tuple
from app.wialon import WialonAPIError, wialon_request, get_vehicle_groups, session_name  # imported first: it loads Backend/.env
from app.utils.config import get_env_int
from app.performance_diagnostics import request_trace_id

# Wialon resource holding the report templates, and the template IDs.
# The defaults are the ATS production account; override them per environment (e.g. a staging account).
WIALON_RESOURCE_ID = get_env_int("WIALON_REPORT_RESOURCE_ID", 601814494)
TEMPLATE_ID_UNIT_TRIPS = get_env_int("WIALON_TEMPLATE_UNIT_TRIPS", 4)
TEMPLATE_ID_UNIT_ENGINE = get_env_int("WIALON_TEMPLATE_UNIT_ENGINE", 1)
TEMPLATE_ID_GROUP_TRIPS = get_env_int("WIALON_TEMPLATE_GROUP_TRIPS", 5)
TEMPLATE_ID_GROUP_ENGINE = get_env_int("WIALON_TEMPLATE_GROUP_ENGINE", 2)

# Optional explicit 'All Unit' group ID. 0 (the default) means it is discovered from the
# authenticated account by name (see resolve_all_units_group_id).
GROUP_ID_ALL_UNITS = get_env_int("WIALON_GROUP_ALL_UNITS", 0)
ALL_UNITS_GROUP_NAMES = ("all unit", "all units")

# Rows requested per report/get_result_rows call, and sub-rows per report/get_result_subrows call
REPORT_ROWS_CHUNK = 1000
REPORT_SUBROWS_CHUNK = 200
# Safety bound on sub-row pages for a single row (200 * 500 = 100k idle events)
REPORT_SUBROWS_MAX_PAGES = 500
# Rows whose first sub-row page is requested together in one core/batch call (see _fetch_rows_subrows)
REPORT_SUBROWS_BATCH = 50

# Wialon keeps one report result per session, so reports are serialized per session: `_report_lock` for
# the shared session, and one lock per named secondary session (app.wialon.login(session=...)).
_report_lock = threading.Lock()
_session_report_locks: Dict[str, threading.Lock] = {}
_session_report_locks_guard = threading.Lock()


def _report_lock_for(sid: str):
    session = session_name(sid)
    if session is None:
        return _report_lock
    with _session_report_locks_guard:
        return _session_report_locks.setdefault(session, threading.Lock())


@contextmanager
def _holding_report_lock(report: str, sid: str):
    """Hold `sid`'s session report lock (one Wialon report result per session), logging PERF wait and hold times."""
    wait_started = time.perf_counter()
    with _report_lock_for(sid):
        held_started = time.perf_counter()
        try:
            yield
        finally:
            logging.debug(
                "PERF report_lock request_id=%s report=%s session=%s wait_ms=%.1f held_ms=%.1f",
                request_trace_id.get(), report, session_name(sid) or "shared",
                (held_started - wait_started) * 1000, (time.perf_counter() - held_started) * 1000,
            )


def resolve_all_units_group_id(sid: str, groups: Optional[List[Dict[str, Any]]] = None) -> int:
    """
    Return the fleet-wide 'All Unit' group ID of the authenticated Wialon account.
    WIALON_GROUP_ALL_UNITS wins when set; otherwise the group is found by name among `groups`
    (fetched from Wialon when not supplied). Raises WialonAPIError when the account has no such group.
    """
    if GROUP_ID_ALL_UNITS:
        return GROUP_ID_ALL_UNITS
    if groups is None:
        groups = get_vehicle_groups(sid)
    matches = [
        g for g in groups
        if g.get("id") and str(g.get("nm", "")).strip().lower() in ALL_UNITS_GROUP_NAMES
    ]
    if not matches:
        raise WialonAPIError(
            "No 'All Unit' group found in the Wialon account; create it or set WIALON_GROUP_ALL_UNITS"
        )
    # Several same-named groups: the one with the most members covers the fleet best
    return int(max(matches, key=lambda g: len(g.get("u") or []))["id"])


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


def _cell_str(cell: Any) -> str:
    """Text of a cell; identical to str(cell) for plain values (historical behaviour), text for dict cells."""
    return extract_cell_text(cell) if isinstance(cell, dict) else str(cell)


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


_COMMA_GROUPED_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")  # 1,234,567 / 1,234.5
_DOT_GROUPED_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")  # 1.234.567 / 1.234,5
_COMMA_DECIMAL_RE = re.compile(r"\d+,\d+")
_DOT_DECIMAL_RE = re.compile(r"\d+(?:\.\d+)?")
_AMBIGUOUS_COMMA_RE = re.compile(r"[1-9]\d{0,2},\d{3}")  # 1,234: 1234 or 1.234?


def parse_mileage_km(val: Any) -> Optional[float]:
    """
    Strictly parse a Trips Report mileage cell (number, dict cell, or text such as '12.34 km'); None when unknown.
    Unlike parse_float_safe, a text whose separators can be read two ways is unknown rather than guessed:
    '1,234' (1234 or 1.234?) is None, as is any text that is not a single well-formed number. Separators are
    read only when the digit grouping leaves one reading: '1,234.5' and '1.234,5' are 1234.5, '12,5' is 12.5,
    '0,500' is 0.5 (no grouped number starts with 0). A lone dot is a decimal point ('1.234' is 1.234), Wialon's
    own format. One leading '-' is a sign ('-12.5'); a repeated one ('--12') is malformed.
    """
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val) if math.isfinite(val) else None
    if isinstance(val, dict):
        return parse_mileage_km(val.get("v") if val.get("v") is not None else val.get("t"))

    s = str(val).replace("\u00a0", " ").strip()
    if s.lower().endswith("km"):
        s = s[:-2].strip()
    s = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", s)  # a space between digit groups: '1 234.5'
    sign = -1.0 if s.startswith("-") else 1.0
    s = s[1:] if s.startswith("-") else s  # one sign only: '--12' is malformed
    # A grouped thousands number never starts with a 0 group, so '0,500' has one reading (0.5)
    if _COMMA_GROUPED_RE.fullmatch(s) and not s.startswith("0") and not _AMBIGUOUS_COMMA_RE.fullmatch(s):
        s = s.replace(",", "")
    elif _DOT_GROUPED_RE.fullmatch(s) and "," in s or re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}", s):
        s = s.replace(".", "").replace(",", ".")
    elif _COMMA_DECIMAL_RE.fullmatch(s) and not _AMBIGUOUS_COMMA_RE.fullmatch(s):
        s = s.replace(",", ".")
    elif not _DOT_DECIMAL_RE.fullmatch(s):
        return None
    return sign * float(s)


_DAY_PREFIX_RE = re.compile(r"^(\d+)\s*days?\b\s*(.*)$", re.IGNORECASE)


def _split_day_prefix(s: str) -> Tuple[int, str]:
    """Wialon prints durations of a day or more as '1 days 02:03:04'; return (days, remaining text)."""
    m = _DAY_PREFIX_RE.match(s)
    if not m:
        return 0, s
    return int(m.group(1)), m.group(2).strip() or "0"


def parse_duration_to_seconds(dur_str: Any) -> int:
    """Parse Wialon duration string ('N days HH:MM:SS', HH:MM:SS or MM:SS) to total seconds."""
    if isinstance(dur_str, (int, float)):
        return int(dur_str)
    if isinstance(dur_str, dict):
        dur_str = dur_str.get("v") if dur_str.get("v") is not None else dur_str.get("t", "0")
    try:
        days, s = _split_day_prefix(str(dur_str).strip())
        parts = s.split(":")
        if len(parts) == 3:
            return days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        elif len(parts) == 2:
            return days * 86400 + int(parts[0]) * 60 + int(float(parts[1]))
        return days * 86400 + int(float(s))
    except (ValueError, TypeError):
        return 0


def parse_duration_strict(cell: Any) -> Optional[int]:
    """Like parse_duration_to_seconds, but None when the cell is missing or unreadable (never 0 by default)."""
    if cell is None or isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return int(cell)
    if isinstance(cell, dict):
        cell = cell.get("v") if cell.get("v") is not None else cell.get("t")
        if cell is None:
            return None
        if isinstance(cell, (int, float)):
            return int(cell)
    s = str(cell).strip()
    if not s:
        return None
    try:
        days, s = _split_day_prefix(s)
        parts = s.split(":")
        if len(parts) == 3:
            return days * 86400 + int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
        if len(parts) == 2:
            return days * 86400 + int(parts[0]) * 60 + int(float(parts[1]))
        return days * 86400 + int(float(s))
    except (ValueError, TypeError):
        return None


# Wialon writes a run of dashes ("-----") in a cell when the value does not exist for the interval (e.g. the Mileage of
# a 2 s engine-on interval without motion). The value stays unknown (None) and listed in `incomplete_fields`; unlike an
# unreadable cell it is what Wialon reports for that interval, so re-reading a closed window returns it again.
_DASH_PLACEHOLDER = re.compile(r"-{2,}")
_ENGINE_FIELD_TEXT = {"total_time_seconds": "total_time_str", "engine_hours_seconds": "engine_hours_str",
                      "in_motion_seconds": "in_motion_str", "total_idle_seconds": "idling_str", "mileage_km": "mileage_str"}


def engine_row_unstable_incomplete(row: Dict[str, Any]) -> List[str]:
    """A group engine report row's `incomplete_fields` other than dash-only placeholders (see above)."""
    return [
        f for f in row.get("incomplete_fields") or []
        if not (f in _ENGINE_FIELD_TEXT and _DASH_PLACEHOLDER.fullmatch(str(row.get(_ENGINE_FIELD_TEXT[f]) or "").strip()))
    ]


def parse_duration_to_minutes(dur_str: Any) -> float:
    """Parse Wialon duration string to minutes rounded to 2 decimal places."""
    sec = parse_duration_to_seconds(dur_str)
    return round(sec / 60.0, 2)


def _normalize_header_name(name: Any) -> str:
    return " ".join(extract_cell_text(name).replace(".", " ").split()).lower()


def _column_index(header: Any, names: Tuple[str, ...], default: int, warnings: List[str],
                  unlocated: Optional[List[str]] = None) -> int:
    """
    Locate a report column by header name. Falls back to the historical position `default` when no
    header is sent or the name is absent, recording why in `warnings`. Whenever the position is a guess,
    `names[0]` is also appended to `unlocated` (when given) so callers can treat the value as unknown.
    """
    if not isinstance(header, list) or not header:
        if unlocated is not None:
            unlocated.append(names[0])
        return default
    wanted = {_normalize_header_name(n) for n in names}
    for idx, col in enumerate(header):
        if _normalize_header_name(col) in wanted:
            return idx
    warnings.append(f"Column '{names[0]}' not found in report header; using position {default}")
    if unlocated is not None:
        unlocated.append(names[0])
    return default


def _fetch_all_rows(sid: str, total_rows: int, what: str, table_index: int = 0) -> List[Dict[str, Any]]:
    """Read all `total_rows` rows of a report table in pages; raise if any page comes back short."""
    rows: List[Dict[str, Any]] = []
    start = 0
    while start < total_rows:
        end = min(start + REPORT_ROWS_CHUNK, total_rows) - 1
        page = wialon_request(
            "report/get_result_rows",
            {"tableIndex": table_index, "indexFrom": start, "indexTo": end},
            sid=sid,
        )
        if isinstance(page, dict) and "error" in page:
            raise WialonAPIError(f"Wialon {what} rows failed: {page}")
        if not isinstance(page, list):
            raise WialonAPIError(f"Unexpected response structure for {what} rows: {page}")
        expected = end - start + 1
        if len(page) != expected:
            raise WialonAPIError(
                f"Wialon {what} returned {len(page)} of {expected} rows for rows {start}-{end}; report incomplete"
            )
        rows.extend(page)
        start = end + 1
    return rows


def _subrow_key(subrow: Any) -> str:
    """Identity of one sub-row, used to recognise intervals Wialon has already returned."""
    return json.dumps(subrow, sort_keys=True, default=str)


def _fetch_all_subrows(
    sid: str, row_index: int, what: str, table_index: int = 0, expected_count: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Read every sub-row (e.g. discrete idle intervals) of one report row, page by page.
    Returns (subrows, complete). An error/unexpected sub-row response does not fail the whole report
    (historically such rows were skipped and existing pages rely on that); instead complete=False so
    the caller can flag the unit's event list as incomplete. Transport errors still propagate.

    Wialon can ignore indexFrom/indexTo and return the row's whole sub-row list for every page (seen live:
    the same 313 intervals for 0-199, 200-399 and 400-599), so a sub-row already read is never added again,
    and paging stops at a page that brings nothing new or ignores the range. `expected_count` is the row's
    sub-row count ("d"); when given, complete=True only if exactly that many distinct sub-rows were read.
    Without it, only a short page proves the end of the list.
    """
    subrows: List[Dict[str, Any]] = []
    seen = set()
    for page_no in range(REPORT_SUBROWS_MAX_PAGES):
        start = page_no * REPORT_SUBROWS_CHUNK
        page = wialon_request(
            "report/get_result_subrows",
            {"tableIndex": table_index, "rowIndex": row_index,
             "indexFrom": start, "indexTo": start + REPORT_SUBROWS_CHUNK - 1},
            sid=sid,
        )
        if not isinstance(page, list):
            logging.warning("Wialon %s sub-rows unavailable for row %s: %s", what, row_index, page)
            return subrows, False
        new_rows = 0
        for s in page:
            key = _subrow_key(s)
            if key not in seen:
                seen.add(key)
                subrows.append(s)
                new_rows += 1
        if expected_count is not None and len(subrows) >= expected_count:
            break
        if len(page) < REPORT_SUBROWS_CHUNK:
            if expected_count is None:
                return subrows, True
            break
        if new_rows == 0 or len(page) > REPORT_SUBROWS_CHUNK:
            # The requested range was ignored: further pages would only repeat sub-rows already read
            logging.warning(
                "Wialon %s row %s ignored sub-row range %s-%s (%s returned, %s new); stopped paging",
                what, row_index, start, start + REPORT_SUBROWS_CHUNK - 1, len(page), new_rows,
            )
            break
    else:
        logging.warning(
            "Wialon %s row %s has more than %s sub-rows; event list truncated",
            what, row_index, REPORT_SUBROWS_CHUNK * REPORT_SUBROWS_MAX_PAGES,
        )
        return subrows, False
    if expected_count is None or len(subrows) != expected_count:
        logging.warning(
            "Wialon %s row %s: read %s distinct sub-rows, expected %s; event list incomplete",
            what, row_index, len(subrows), expected_count,
        )
        return subrows, False
    return subrows, True


def _fetch_rows_subrows(
    sid: str, rows: List[Dict[str, Any]], what: str, table_index: int = 0, only: Optional[Collection[int]] = None
) -> Dict[int, Tuple[List[Dict[str, Any]], bool]]:
    """
    Sub-rows of every row with sub-rows ("d" > 0), or of the row indexes in `only`, as {row_index: (subrows, complete)}.

    The first sub-row page of up to REPORT_SUBROWS_BATCH rows goes out as one core/batch call instead of one
    round trip per row (a multi-day trips report has one day row per active day). A batched page is accepted
    only when it holds exactly the row's "d" distinct sub-rows; any other row, or a failed batch, is read by
    _fetch_all_subrows exactly as before, so completeness is checked the same way.
    """
    wanted = [(idx, int(r["d"])) for idx, r in enumerate(rows) if r.get("d", 0) > 0 and (only is None or idx in only)]
    out: Dict[int, Tuple[List[Dict[str, Any]], bool]] = {}
    for start in range(0, len(wanted), REPORT_SUBROWS_BATCH):
        chunk = wanted[start:start + REPORT_SUBROWS_BATCH]
        try:
            pages = wialon_request(
                "core/batch",
                {"params": [
                    {"svc": "report/get_result_subrows",
                     "params": {"tableIndex": table_index, "rowIndex": idx, "indexFrom": 0, "indexTo": REPORT_SUBROWS_CHUNK - 1}}
                    for idx, _ in chunk
                ], "flags": 0},
                sid=sid,
            )
        except WialonAPIError as exc:
            logging.warning("Wialon %s sub-row batch failed (%s); reading rows one by one", what, exc)
            pages = None
        if not isinstance(pages, list) or len(pages) != len(chunk):
            logging.debug("Wialon %s sub-row batch unavailable (%s); reading rows one by one", what, type(pages).__name__)
            pages = [None] * len(chunk)
        for (idx, expected), page in zip(chunk, pages):
            if isinstance(page, list):
                distinct = list({_subrow_key(s): s for s in page}.values())
                if len(distinct) == expected:
                    out[idx] = (distinct, True)
                    continue
            out[idx] = _fetch_all_subrows(sid, idx, what, table_index=table_index, expected_count=expected)
    return out


def execute_wialon_unit_trips_report(
    sid: str, vehicle_id: int, from_time_utc: int, to_time_utc: int
) -> Optional[Dict[str, Any]]:
    """
    Execute Wialon's native Trips Report template (ID 4) for an individual unit.
    Returns authoritative trip list and summary metrics directly from Wialon.

    Note: column 6 of this template is "Engine hours" (not moving time). It is exposed as
    `engine_hours_str`; `moving_duration` keeps the same value only for backward compatibility.

    Guarantees report cleanup inside finally block and thread-safe execution.
    """
    with _holding_report_lock("unit_trips", sid):
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
            column_warnings: List[str] = []
            unlocated: List[str] = []
            incomplete_fields = set()
            total_rows = 0

            if tables:
                total_rows = tables[0].get("rows", 0)
                header = tables[0].get("header")
                mileage_idx = _column_index(header, ("Mileage",), 9, column_warnings, unlocated)
                if total_rows > 0:
                    row_res = _fetch_all_rows(sid, total_rows, f"trip report of vehicle {vehicle_id}")
                    # The template groups trips by day: a row with sub-rows ("d") is a day summary whose
                    # sub-rows are its trips (verified live). Its trips replace it; a row without sub-rows is a trip.
                    trip_rows = []
                    day_trips = _fetch_rows_subrows(sid, row_res, f"trip report of vehicle {vehicle_id}")
                    for row_idx, r in enumerate(row_res):
                        if r.get("d", 0) > 0:
                            sub, sub_complete = day_trips[row_idx]
                            if not sub_complete:
                                # A partial list would undercount trips and distance; fail like an unread report
                                raise WialonAPIError(f"Wialon trips of vehicle {vehicle_id} could not be read completely")
                            trip_rows.extend(sub)
                        else:
                            trip_rows.append(r)
                    for r in trip_rows:
                        cols = r.get("c", [])
                        if len(cols) >= 10:
                            start_obj = cols[1] if isinstance(cols[1], dict) else {}
                            start_loc_obj = cols[2] if isinstance(cols[2], dict) else {}
                            end_obj = cols[3] if isinstance(cols[3], dict) else {}
                            end_loc_obj = cols[4] if isinstance(cols[4], dict) else {}
                            max_spd_obj = cols[8] if isinstance(cols[8], dict) else {}

                            trip_incomplete = []
                            raw_mileage = extract_cell_value(cols[mileage_idx]) if len(cols) > mileage_idx else None
                            dist_strict = parse_mileage_km(raw_mileage)
                            if dist_strict is None:
                                trip_incomplete.append("distance_km")
                            dist_km = dist_strict if dist_strict is not None else 0.0

                            dur_str = extract_cell_text(cols[5])
                            mov_str = extract_cell_text(cols[6])
                            dur_mins = parse_duration_to_minutes(dur_str)

                            trip = {
                                # A start without a position comes as plain text; the row's t1/t2 still carry the times
                                "start_time": start_obj.get("v") or r.get("t1"),
                                "start_time_str": start_obj.get("t") or (extract_cell_text(cols[1]) or None),
                                "start_location": start_loc_obj.get("t") or "Unknown",
                                "start_latitude": start_loc_obj.get("y"),
                                "start_longitude": start_loc_obj.get("x"),
                                "end_time": end_obj.get("v") or r.get("t2"),
                                "end_time_str": end_obj.get("t") or (extract_cell_text(cols[3]) or None),
                                "end_location": end_loc_obj.get("t") or "Unknown",
                                "end_latitude": end_loc_obj.get("y"),
                                "end_longitude": end_loc_obj.get("x"),
                                "duration": dur_str,
                                "duration_str": dur_str,
                                "duration_minutes": dur_mins,
                                "moving_duration": mov_str,
                                "moving_duration_str": mov_str,
                                "engine_hours_str": mov_str,
                                "average_speed": str(cols[7]),
                                "max_speed": (
                                    max_spd_obj.get("t")
                                    if isinstance(max_spd_obj, dict)
                                    else str(max_spd_obj)
                                ),
                                "distance_km": round(dist_km, 2),
                            }
                            if trip_incomplete:
                                trip["incomplete_fields"] = trip_incomplete
                                incomplete_fields.update(trip_incomplete)
                            trips.append(trip)
                        else:
                            # A dropped trip row leaves the distance sum (and trip list) short: never a complete total
                            incomplete_fields.update(("row_shape", "distance_km"))

            return {
                "stats": stats,
                "trips": trips,
                "rows_total": total_rows,
                "incomplete_fields": sorted(incomplete_fields),
                "column_warnings": column_warnings,
                "unlocated_fields": ["distance_km"] if "Mileage" in unlocated else [],
            }

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

    `total_in_motion_seconds` is None when the "In motion" value could not be read for every row.
    """
    with _holding_report_lock("unit_engine", sid):
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
            total_in_motion_sec: Optional[int] = 0
            column_warnings: List[str] = []
            unlocated: List[str] = []
            incomplete_fields = set()
            total_rows = 0

            if tables:
                total_rows = tables[0].get("rows", 0)
                header = tables[0].get("header")
                engine_idx = _column_index(header, ("Engine hours",), 6, column_warnings, unlocated)
                motion_idx = _column_index(header, ("In motion",), 7, column_warnings, unlocated)
                idling_idx = _column_index(header, ("Idling",), 8, column_warnings, unlocated)
                if total_rows > 0:
                    row_res = _fetch_all_rows(sid, total_rows, f"engine report of vehicle {vehicle_id}")
                    for row_idx, r in enumerate(row_res):
                        cols = r.get("c", [])
                        if len(cols) >= 9:
                            engine_cell = cols[engine_idx] if len(cols) > engine_idx else None
                            idling_cell = cols[idling_idx] if len(cols) > idling_idx else None
                            motion_cell = cols[motion_idx] if len(cols) > motion_idx else None
                            total_engine_sec += parse_duration_to_seconds(_cell_str(engine_cell))
                            row_idle_sec = parse_duration_to_seconds(_cell_str(idling_cell))
                            total_idle_sec += row_idle_sec
                            if parse_duration_strict(engine_cell) is None:
                                incomplete_fields.add("engine_hours")
                            if parse_duration_strict(idling_cell) is None:
                                incomplete_fields.add("idle_seconds")
                            motion_sec = parse_duration_strict(motion_cell)
                            if motion_sec is None:
                                incomplete_fields.add("in_motion_seconds")
                                total_in_motion_sec = None
                            elif total_in_motion_sec is not None:
                                total_in_motion_sec += motion_sec

                            # Fetch subrows for discrete idle segments
                            if r.get("d", 0) > 0 and row_idle_sec > 0:
                                sub, sub_complete = _fetch_all_subrows(
                                    sid, row_idx, f"engine report of vehicle {vehicle_id}", expected_count=int(r["d"])
                                )
                                if not sub_complete:
                                    incomplete_fields.add("idle_events")
                                for s in sub:
                                    sc = s.get("c", [])
                                    if len(sc) >= 9:
                                        s_idle_sec = parse_duration_to_seconds(_cell_str(sc[8]).strip())
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
                        else:
                            incomplete_fields.add("row_shape")

            return {
                "total_idle_seconds": total_idle_sec,
                "total_idle_minutes": round(total_idle_sec / 60.0, 2),
                "total_engine_seconds": total_engine_sec,
                "total_engine_hours": round(total_engine_sec / 3600.0, 2),
                "idle_events": idle_events,
                "total_in_motion_seconds": total_in_motion_sec,
                "rows_total": total_rows,
                "incomplete_fields": sorted(incomplete_fields),
                "column_warnings": column_warnings,
                "unlocated_fields": sorted(
                    {"Engine hours": "engine_hours", "In motion": "in_motion_seconds", "Idling": "idle_seconds"}[n]
                    for n in unlocated
                ),
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


def _group_row_trips(
    subrows: List[Dict[str, Any]], mileage_idx: int, beginning_idx: int, end_idx: int
) -> Tuple[List[Dict[str, Any]], List[int], bool]:
    """
    One unit row's trips from its template-5 sub-rows (one sub-row per trip, verified live), as
    (trips[{start_time, end_time, distance_km}], unit ids seen in the cells, complete). The "-----" placeholder
    of a unit without trips is not a trip. complete is False when a sub-row is malformed, a start time is
    missing or a distance unreadable (that trip's distance_km is None, never 0).
    """
    trips: List[Dict[str, Any]] = []
    unit_ids = set()
    complete = True
    for s in subrows:
        cols = s.get("c", [])
        if len(cols) <= max(mileage_idx, beginning_idx, end_idx):
            complete = False
            continue
        for c in cols:
            if isinstance(c, dict) and c.get("u"):
                unit_ids.add(int(c["u"]))
        if extract_cell_text(cols[beginning_idx]).strip() in ("", "-----"):
            continue
        start_obj = cols[beginning_idx] if isinstance(cols[beginning_idx], dict) else {}
        end_obj = cols[end_idx] if isinstance(cols[end_idx], dict) else {}
        dist = parse_mileage_km(extract_cell_value(cols[mileage_idx]))
        start = start_obj.get("v") or s.get("t1")
        if dist is None or not start:
            complete = False
        trips.append({
            "start_time": start,
            "end_time": end_obj.get("v") or s.get("t2"),
            "distance_km": round(dist, 2) if dist is not None else None,
        })
    return trips, sorted(unit_ids), complete


def execute_wialon_group_trips_report(
    sid: str, from_time_utc: int, to_time_utc: int, group_id: Optional[int] = None, include_trips: bool = False
) -> Optional[List[Dict[str, Any]]]:
    """
    Execute Wialon's native Trips Report - All Unit template (ID 5) for a fleet unit group.
    Returns authoritative vehicle mileage and trip metrics for all units directly from Wialon.
    Rows whose mileage cell could not be read carry incomplete_fields=["mileage_km"].
    A falsy group_id means the account's 'All Unit' group.

    include_trips additionally reads every unit row's sub-rows (its individual trips) and adds to each row
    `trips` (start_time, end_time, distance_km), `trip_unit_ids` (unit ids in the trip cells) and
    `trips_complete` (False when sub-rows were incomplete, malformed or unreadable, or a column was not
    located by header name). Without it the output is unchanged.

    Guarantees thread-safe execution and cleanup in finally block.
    """
    if not group_id:
        group_id = resolve_all_units_group_id(sid)
    with _holding_report_lock("group_trips", sid):
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
                column_warnings: List[str] = []
                unlocated: List[str] = []
                mileage_idx = _column_index(tables[0].get("header"), ("Mileage",), 5, column_warnings, unlocated)
                beginning_idx = _column_index(tables[0].get("header"), ("Beginning",), 1, column_warnings, unlocated)
                trip_unlocated: List[str] = []
                end_idx = (_column_index(tables[0].get("header"), ("End",), 3, column_warnings, trip_unlocated)
                           if include_trips else 3)
                if total_rows > 0:
                    row_res = _fetch_all_rows(sid, total_rows, f"group trips report of group {group_id}")
                    row_subrows = (_fetch_rows_subrows(sid, row_res, f"group trips report of group {group_id}")
                                   if include_trips else {})
                    for row_idx, r in enumerate(row_res):
                        cols = r.get("c", [])
                        # Table 0 columns: ['Grouping', 'Beginning', 'Initial location', 'End', 'Final location', 'Mileage', 'Total time', 'Engine hours', 'Avg. speed', 'Max. speed', 'Driver']
                        if len(cols) >= 6:
                            vehicle_name = extract_cell_text(cols[0]).strip()
                            raw_mileage = extract_cell_value(cols[mileage_idx]) if len(cols) > mileage_idx else None
                            dist_strict = parse_mileage_km(raw_mileage)
                            dist_km = dist_strict if dist_strict is not None else 0.0

                            total_time_str = extract_cell_text(cols[6]) if len(cols) > 6 else "0:00:00"
                            engine_hours_str = extract_cell_text(cols[7]) if len(cols) > 7 else "0:00:00"
                            avg_spd_str = extract_cell_text(cols[8]) if len(cols) > 8 else "0 km/h"
                            max_spd_str = extract_cell_text(cols[9]) if len(cols) > 9 else "0 km/h"
                            driver_col = extract_cell_text(cols[10]).strip() if len(cols) > 10 else ""

                            # The template is detailed: each unit row's sub-rows ("d") are its trips, and a unit
                            # without trips gets one "-----" placeholder sub-row (verified live against every
                            # sub-row). A row with a trip start but no sub-rows means detail is off: unknown (None).
                            beginning = extract_cell_text(cols[beginning_idx]).strip() if len(cols) > beginning_idx else ""
                            sub_count = r.get("d")
                            if "Beginning" in unlocated or not isinstance(sub_count, int):
                                trip_count = None
                            elif beginning in ("", "-----"):
                                trip_count = 0
                            else:
                                trip_count = sub_count if sub_count > 0 else None

                            summary = {
                                "vehicle_name": vehicle_name,
                                "trip_count": trip_count,
                                "mileage_km": round(dist_km, 2),
                                "total_time": total_time_str,
                                "engine_hours": engine_hours_str,
                                "avg_speed": avg_spd_str,
                                "max_speed": max_spd_str,
                                "driver": driver_col,
                            }
                            if dist_strict is None:
                                summary["incomplete_fields"] = ["mileage_km"]
                            if column_warnings:
                                summary["column_warnings"] = list(column_warnings)
                            if "Mileage" in unlocated:
                                summary["unlocated_fields"] = ["mileage_km"]
                            if include_trips:
                                sub, sub_complete = row_subrows.get(row_idx, ([], True))
                                trips, trip_unit_ids, trips_ok = _group_row_trips(sub, mileage_idx, beginning_idx, end_idx)
                                summary["trips"] = trips
                                summary["trip_unit_ids"] = trip_unit_ids
                                summary["trips_complete"] = bool(
                                    sub_complete and trips_ok and not unlocated and not trip_unlocated
                                    and isinstance(sub_count, int) and sub_count > 0
                                )
                            unit_summaries.append(summary)

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
    group_id: Optional[int] = None,
    include_events: bool = True,
    event_vehicle_names: Optional[Collection[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Execute Wialon's native Engine Hours Report - All Unit template (ID 2) for a fleet group.
    Returns authoritative vehicle idling metrics, engine hours, moving durations, and discrete
    idle events across the entire fleet directly from Wialon.
    A falsy group_id means the account's 'All Unit' group.

    Each vehicle row lists unreadable cells in `incomplete_fields` (the corresponding legacy integer
    keys stay 0 for existing callers) and carries a parsed `mileage_km` (None when unreadable).

    `event_vehicle_names`, when given with include_events, limits discrete idle events to those units:
    other rows keep their totals but are treated as if events were not requested (`idle_events` None).
    Sub-row paging dominates report time (and holds the report lock), so callers that use only some
    units' events should pass it.

    Guarantees thread-safe execution and cleanup in finally block.
    """
    if not group_id:
        group_id = resolve_all_units_group_id(sid)
    with _holding_report_lock("group_engine", sid):
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
            column_warnings: List[str] = []
            unlocated: List[str] = []
            events_incomplete: List[str] = []
            total_rows = 0

            if tables:
                total_rows = tables[0].get("rows", 0)
                header = tables[0].get("header")
                total_idx = _column_index(header, ("Total time",), 5, column_warnings, unlocated)
                engine_idx = _column_index(header, ("Engine hours",), 6, column_warnings, unlocated)
                motion_idx = _column_index(header, ("In motion",), 7, column_warnings, unlocated)
                idling_idx = _column_index(header, ("Idling",), 8, column_warnings, unlocated)
                mileage_idx = _column_index(header, ("Mileage",), 9, column_warnings, unlocated)
                if total_rows > 0:
                    row_res = _fetch_all_rows(sid, total_rows, f"group engine report of group {group_id}")
                    # Sub-rows (discrete idle events) of exactly the rows read below, fetched up front in core/batch
                    # calls instead of one round trip per unit; checked and completed per row as before
                    event_rows = set()
                    for row_idx, r in enumerate(row_res):
                        cols = r.get("c", [])
                        if (include_events and len(cols) >= 9 and r.get("d", 0) > 0
                                and (event_vehicle_names is None or _cell_str(cols[0]).strip() in event_vehicle_names)
                                and parse_duration_to_seconds(_cell_str(cols[idling_idx]) if len(cols) > idling_idx else "0:00:00") > 0):
                            event_rows.add(row_idx)
                    row_subrows = (_fetch_rows_subrows(sid, row_res, f"group engine report of group {group_id}", only=event_rows)
                                   if event_rows else {})
                    for row_idx, r in enumerate(row_res):
                        cols = r.get("c", [])
                        # Table 0: ['Grouping', 'Beginning', 'Initial location', 'End', 'Final location', 'Total time', 'Engine hours', 'In motion', 'Idling', 'Mileage', 'Avg. speed', 'Max. speed']
                        if len(cols) >= 9:
                            vehicle_name = _cell_str(cols[0]).strip()

                            def cell(idx):
                                return cols[idx] if len(cols) > idx else None

                            total_time_str = _cell_str(cell(total_idx)) if len(cols) > total_idx else "0:00:00"
                            engine_hours_str = _cell_str(cell(engine_idx)) if len(cols) > engine_idx else "0:00:00"
                            in_motion_str = _cell_str(cell(motion_idx)) if len(cols) > motion_idx else "0:00:00"
                            idling_str = _cell_str(cell(idling_idx)) if len(cols) > idling_idx else "0:00:00"
                            mileage_str = _cell_str(cell(mileage_idx)) if len(cols) > mileage_idx else "0.00 km"

                            total_time_sec = parse_duration_to_seconds(total_time_str)
                            engine_hours_sec = parse_duration_to_seconds(engine_hours_str)
                            in_motion_sec = parse_duration_to_seconds(in_motion_str)
                            idling_sec = parse_duration_to_seconds(idling_str)

                            row_incomplete = []
                            for field, idx in (("total_time_seconds", total_idx), ("engine_hours_seconds", engine_idx),
                                               ("in_motion_seconds", motion_idx), ("total_idle_seconds", idling_idx)):
                                if parse_duration_strict(cell(idx)) is None:
                                    row_incomplete.append(field)
                            mileage_km = parse_float_safe(cell(mileage_idx), default=None)
                            if mileage_km is None:
                                row_incomplete.append("mileage_km")

                            unit_events = []
                            row_events = include_events and (
                                event_vehicle_names is None or vehicle_name in event_vehicle_names
                            )
                            # Fetch subrows for discrete idle segments if idling > 0
                            if row_events and idling_sec > 0 and r.get("d", 0) > 0:
                                sub, sub_complete = row_subrows[row_idx]
                                if not sub_complete:
                                    row_incomplete.append("idle_events")
                                    events_incomplete.append(vehicle_name)
                                for s in sub:
                                    sc = s.get("c", [])
                                    if len(sc) >= 9:
                                        s_idle_str = _cell_str(sc[8]).strip()
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
                            elif row_events and idling_sec > 0 and r.get("d", 0) == 0:
                                # When a vehicle has idling recorded in a single non-nested interval
                                st_obj = cols[1] if len(cols) > 1 and isinstance(cols[1], dict) else {}
                                loc_obj = cols[2] if len(cols) > 2 and isinstance(cols[2], dict) else (cols[4] if len(cols) > 4 and isinstance(cols[4], dict) else {})
                                et_obj = cols[3] if len(cols) > 3 and isinstance(cols[3], dict) else {}
                                st_ts = st_obj.get("v") or r.get("t1") or 0
                                et_ts = et_obj.get("v") or r.get("t2") or (st_ts + idling_sec)
                                lat = loc_obj.get("y") or st_obj.get("y") or et_obj.get("y")
                                lng = loc_obj.get("x") or st_obj.get("x") or et_obj.get("x")
                                addr = loc_obj.get("t") or "Unknown Location"

                                ev_item = {
                                    "vehicle_name": vehicle_name,
                                    "start_time": st_ts,
                                    "end_time": et_ts,
                                    "duration": idling_sec,
                                    "duration_minutes": round(idling_sec / 60.0, 2),
                                    "location": {
                                        "latitude": lat,
                                        "longitude": lng,
                                    },
                                    "address": addr,
                                }
                                unit_events.append(ev_item)
                                all_idle_events.append(ev_item)

                            # Only consider discrete individual qualifying events; never substitute cumulative total idle
                            longest_idle = max((ev["duration"] for ev in unit_events), default=(idling_sec if len(unit_events) == 1 else 0))

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
                                # Count only discrete events actually read; unknown (None) when they were not requested or incomplete
                                "idle_events": len(unit_events) if row_events and "idle_events" not in row_incomplete else None,
                                "longest_idle_seconds": longest_idle,
                                "mileage_str": mileage_str,
                                "mileage_km": round(mileage_km, 2) if mileage_km is not None else None,
                                "incomplete_fields": row_incomplete,
                                "events": unit_events,
                            })

            return {
                "vehicles": unit_summaries,
                "events": all_idle_events,
                "rows_total": total_rows,
                "column_warnings": column_warnings,
                "events_incomplete": events_incomplete,
                # Row fields read from a guessed column position (see the module docstring)
                "unlocated_fields": sorted(
                    {"Total time": "total_time_seconds", "Engine hours": "engine_hours_seconds",
                     "In motion": "in_motion_seconds", "Idling": "total_idle_seconds", "Mileage": "mileage_km"}[n]
                    for n in unlocated
                ),
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
