"""
Drivers directory: every Wialon driver record on the driver resource (the idle report resource, 600441379) with its
current vehicle assignment, for the Drivers page.

Driver records (core/search_item, flags 0x1 | 0x100) carry `id` (Wialon's resource-local record number), `n` (name),
`c` (driver-card code), `p` (phone), `jp` (custom fields; `jp.ID` is the Driver ID shown in Wialon), `bu` (the unit
the driver is bound to now, 0 when unbound) and `bt` (when that binding was made). The current assignment comes from
`bu` alone: binding history is not read here (thousands of entries per unit a month, with same-second bind/unbind
ties). Records are matched to units by `bu` and to each other by `id`, never by name.

Only an allow-list of fields leaves this module: the record `id`, password, description, position, checksum and other
Wialon fields are never returned. Driver personal data (names, phones, cards) is never logged.
"""

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from app.idle_events import driver_custom_id
from app.utils.datetime_helpers import to_iso_uae
from app.vehicles import extract_registration_plate, parse_wialon_epoch
from app.wialon import WialonAPIError, wialon_request
from app.wialon_reports import IDLE_REPORT_RESOURCE_ID

DRIVER_RESOURCE_ID = IDLE_REPORT_RESOURCE_ID
# Shown for an assigned vehicle that is in no Wialon group other than "All Unit"
GENERAL_FLEET = "General Fleet"

ASSIGNED, UNASSIGNED, AMBIGUOUS, VEHICLE_NOT_FOUND = "assigned", "unassigned", "ambiguous", "vehicle_not_found"


def fetch_driver_records(sid: str) -> List[Dict[str, Any]]:
    """The raw driver records of the driver resource. Raises WialonAPIError (without the payload) on failure."""
    resource = wialon_request("core/search_item", {"id": DRIVER_RESOURCE_ID, "flags": 0x1 | 0x100}, sid=sid)
    item = resource.get("item") if isinstance(resource, dict) else None
    if not isinstance(item, dict):
        raise WialonAPIError("Wialon driver records request failed")
    drivers = item.get("drvrs") or {}
    records = drivers.values() if isinstance(drivers, dict) else (drivers if isinstance(drivers, list) else [])
    return [r for r in records if isinstance(r, dict)]


def _text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    text = str(value).strip()
    return text or None


def _unit_id(value: Any) -> Optional[int]:
    """A record's `bu` as a unit id: 0 when unbound, None when malformed."""
    if value is None:
        return 0
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def unit_groups(groups: Iterable[Dict[str, Any]]) -> Dict[int, List[str]]:
    """Unit id -> names of its Wialon groups, without "All Unit" (same rule as the live fleet)."""
    by_unit: Dict[int, List[str]] = {}
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        name = str(group.get("nm") or "").strip()
        if not name or name.lower() == "all unit":
            continue
        for uid in group.get("u") or []:
            names = by_unit.setdefault(uid, [])
            if name not in names:
                names.append(name)
    return by_unit


def build_drivers_directory(records: Iterable[Dict[str, Any]], vehicles: Iterable[Dict[str, Any]],
                            groups: Iterable[Dict[str, Any]], retrieved_at: int) -> Dict[str, Any]:
    """The /api/drivers payload from driver records, the discovered units and the unit groups."""
    # One entry per record id (a repeated record is the same driver); records without a usable id are still listed
    seen_ids = set()
    unique = []
    for record in records:
        rid = record.get("id")
        if isinstance(rid, int) and not isinstance(rid, bool):
            if rid in seen_ids:
                continue
            seen_ids.add(rid)
        unique.append(record)

    units = {v.get("id"): v for v in vehicles or [] if isinstance(v, dict) and v.get("id") is not None}
    groups_by_unit = unit_groups(groups)
    bound_units = [_unit_id(r.get("bu")) for r in unique]
    drivers_per_unit = Counter(u for u in bound_units if u)
    driver_ids = [driver_custom_id(r) for r in unique]
    driver_id_counts = Counter(d for d in driver_ids if d)

    rows = []
    for record, unit_id, driver_id in zip(unique, bound_units, driver_ids):
        assignment = {"status": UNASSIGNED, "vehicle_id": None, "vehicle_name": None, "plate": None,
                      "groups": [], "since": None, "since_iso": None}
        if unit_id is None or (unit_id and unit_id not in units):
            assignment.update(status=VEHICLE_NOT_FOUND, vehicle_id=unit_id)
        elif unit_id:
            unit = units[unit_id]
            since = parse_wialon_epoch(record.get("bt"))
            assignment.update(
                status=AMBIGUOUS if drivers_per_unit[unit_id] > 1 else ASSIGNED,
                vehicle_id=unit_id,
                vehicle_name=_text(unit.get("nm")),
                plate=extract_registration_plate(unit),
                groups=list(groups_by_unit.get(unit_id) or [GENERAL_FLEET]),
                since=since,
                since_iso=to_iso_uae(since) if since else None,
            )
        driver = {
            "driver_id": driver_id,
            "name": _text(record.get("n")),
            "phone": _text(record.get("p")),
            "card": _text(record.get("c")),
            "assignment": assignment,
        }
        issues = []
        if driver["name"] is None:
            issues.append("missing_name")
        if driver["phone"] is None:
            issues.append("missing_phone")
        if driver["card"] is None:
            issues.append("missing_card")
        if driver_id is None:
            issues.append("missing_driver_id")
        elif driver_id_counts[driver_id] > 1:
            issues.append("duplicate_driver_id")
        if assignment["status"] in (AMBIGUOUS, VEHICLE_NOT_FOUND):
            issues.append(assignment["status"])
        driver["issues"] = issues
        rows.append((str(driver["name"] or "").casefold(), record.get("id") if isinstance(record.get("id"), int) else 0, driver))

    rows.sort(key=lambda row: (row[0] == "", row[0], row[1]))
    drivers = [row[2] for row in rows]
    status_counts = Counter(d["assignment"]["status"] for d in drivers)
    issue_counts = Counter(issue for d in drivers for issue in d["issues"])
    summary = {
        "total": len(drivers),
        "assigned": status_counts[ASSIGNED],
        "unassigned": status_counts[UNASSIGNED],
        "ambiguous": status_counts[AMBIGUOUS],
        "vehicle_not_found": status_counts[VEHICLE_NOT_FOUND],
        "incomplete_assignments": status_counts[AMBIGUOUS] + status_counts[VEHICLE_NOT_FOUND],
        "missing_phone": issue_counts["missing_phone"],
        "missing_card": issue_counts["missing_card"],
        "missing_driver_id": issue_counts["missing_driver_id"],
        "duplicate_driver_id": issue_counts["duplicate_driver_id"],
    }
    return {
        "count": len(drivers),
        "summary": summary,
        "drivers": drivers,
        # Groups of the drivers' vehicles, for the group filter
        "groups": sorted({g for d in drivers for g in d["assignment"]["groups"]}, key=str.casefold),
        # When the driver records and units were read from Wialon (UTC epoch seconds)
        "retrieved_at": retrieved_at,
    }
