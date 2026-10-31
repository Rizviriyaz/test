"""
Data freshness block shared by API responses (additive: existing fields such as retrieved_at and stale are unchanged).

    "freshness": {
        "fetched_at":          when the backend read the data from its source (UTC epoch seconds), or None
        "data_age_seconds":    seconds since fetched_at at the time of this response (a cached result keeps ageing)
        "target_age_seconds":  the age up to which this category counts as current, or None (closed windows: the data
                               no longer changes, so its age does not make it out of date)
        "is_stale":            True only when the last good result is served because the source failed
        "refresh_status":      fresh (read for this response) | cached (an earlier read within its TTL) |
                               stale (last good result after a failure) | partial | unavailable
        "refresh_error":       a client-safe reason when stale or unavailable, else None
        "source_updated_at":   the source's own time for the data, only when the source provides one, else None
    }

Built at response time from the result's own retrieved_at, so it never mutates a cached result. Source timestamps are
never invented: fetched_at is the backend read time, and source_updated_at stays None unless the caller passes a time
the source reported (e.g. a Wialon message time).
"""
import time
from typing import Any, Dict, Optional

from app.utils.datetime_helpers import get_uae_now

# A result read within this many seconds of the response counts as read for it ("fresh"), not served from a cache
FRESH_READ_SECONDS = 2

# Age up to which each category counts as current (its cache TTL plus its usual client refresh interval)
TARGET_LIVE_FLEET_S = 30            # /api/vehicles: 8 s cache, 10 s polls
TARGET_GROUPS_S = 120
TARGET_TODAY_REPORT_S = 120         # today's mileage and trips: 60 s report snapshot, 30 s polls
TARGET_TODAY_IDLE_S = 240           # today's engine/idle report: 180 s cache, 30 s polls
TARGET_FLEET_ANALYTICS_S = 360      # fleet health, utilization, alerts, group rollups: 180-300 s caches
TARGET_VEHICLE_TRIPS_S = 120        # vehicle drawer: report per minute, 60 s refresh
TARGET_FUEL_TELEMATICS_S = 240      # fuel telematics of a range reaching today: 120 s live TTL

STALE_ERROR = "The data source could not be reached; showing the last good result."


def _epoch(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def window_target(eff_to: Any, open_target: int) -> Optional[int]:
    """open_target while a report window reaches into today (UAE); None once it ended before today's UAE midnight."""
    end = _epoch(eff_to)
    if end is None:
        return open_target
    today_start = int(get_uae_now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    return None if end < today_start else open_target


def freshness_block(fetched_at: Any, *, target_age_seconds: Optional[int], stale: bool = False, partial: bool = False,
                    unavailable: bool = False, refresh_error: Optional[str] = None,
                    source_updated_at: Any = None, now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    fetched = _epoch(fetched_at)
    age = None if fetched is None else max(0, int(now - fetched))
    if unavailable or fetched is None:
        status = "unavailable"
    elif stale:
        status = "stale"
    elif partial:
        status = "partial"
    elif age <= FRESH_READ_SECONDS:
        status = "fresh"
    else:
        status = "cached"
    if refresh_error is None and status == "stale":
        refresh_error = STALE_ERROR
    return {
        "fetched_at": fetched,
        "data_age_seconds": age,
        "target_age_seconds": target_age_seconds,
        "is_stale": bool(stale),
        "refresh_status": status,
        "refresh_error": refresh_error,
        "source_updated_at": _epoch(source_updated_at),
    }


def with_freshness(result: Any, *, target_age_seconds: Optional[int], fetched_at: Any = None, partial: bool = False,
                   unavailable: bool = False, refresh_error: Optional[str] = None, source_updated_at: Any = None,
                   now: Optional[float] = None) -> Any:
    """A copy of a result dict with its freshness block (from fetched_at, else its own retrieved_at and stale)."""
    if not isinstance(result, dict):
        return result
    block = freshness_block(
        fetched_at if fetched_at is not None else result.get("retrieved_at"),
        target_age_seconds=target_age_seconds, stale=result.get("stale") is True, partial=partial,
        unavailable=unavailable, refresh_error=refresh_error, source_updated_at=source_updated_at, now=now,
    )
    return {**result, "freshness": block}
