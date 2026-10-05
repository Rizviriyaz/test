import hashlib
import json
from typing import Dict, Any, List, Optional

def _hash_filters(filters: Dict[str, Any]) -> str:
    """Create a stable hash for a filter dict.
    The dict is sorted to ensure identical content produces the same hash.
    """
    serialized = json.dumps(filters, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

def build_cache_key(base: str, filters: Dict[str, Any]) -> str:
    """Construct a cache key that uniquely identifies a request.

    Args:
        base: A string identifying the endpoint (e.g. "fleet_health").
        filters: Dictionary of filter parameters (date range, vehicle ids, etc.).
    """
    filter_hash = _hash_filters(filters)
    return f"{base}:{filter_hash}"

def parse_date_range(params: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Extract ``from`` and ``to`` timestamps from request query parameters.

    Expected param names: ``from_time`` and ``to_time`` (UNIX seconds). If missing,
    returns ``None`` indicating the caller should use a default range.
    """
    try:
        from_ts = int(params.get("from_time")) if params.get("from_time") is not None else None
        to_ts = int(params.get("to_time")) if params.get("to_time") is not None else None
        if from_ts is not None and to_ts is not None and from_ts > to_ts:
            from_ts, to_ts = to_ts, from_ts
        if from_ts is None or to_ts is None:
            return None
        return {"from": from_ts, "to": to_ts}
    except (ValueError, TypeError):
        return None

def normalize_filters(params: Dict[str, Any]) -> Dict[str, Any]:
    """Return a canonical filter dict.

    Supported filters:
        - ``vehicle_id`` (single int or list of ints)
        - ``group_id`` (single int)
        - ``from_time`` / ``to_time`` (UNIX seconds)
        - ``hours`` (fallback range)
    """
    filters: Dict[str, Any] = {}
    vid = params.get("vehicle_id")
    if vid is not None:
        if isinstance(vid, list):
            filters["vehicle_ids"] = [int(v) for v in vid]
        else:
            filters["vehicle_ids"] = [int(vid)]
    gid = params.get("group_id")
    if gid is not None:
        filters["group_id"] = int(gid)
    dr = parse_date_range(params)
    if dr:
        filters.update(dr)
    else:
        hrs = params.get("hours")
        try:
            hrs = int(hrs) if hrs is not None else 24
        except (ValueError, TypeError):
            hrs = 24
        now_ts = int(__import__("time").time())
        filters["from"] = now_ts - hrs * 3600
        filters["to"] = now_ts
    return filters
