import logging
from datetime import datetime, timezone, timedelta, time as dtime

# UAE Timezone: UTC+04:00 (Fixed offset, no DST) - Asia/Dubai
UAE_TZ = timezone(timedelta(hours=4))

SUPPORTED_TIMEFRAMES = {
    "today",
    "yesterday",
    "week",
    "this_week",
    "7d",
    "last_1_week",
    "1w",
    "month",
    "this_month",
    "30d",
    "last_1_month",
    "1m",
    "year",
    "this_year",
    "1y",
    "last_1_year",
    "365d",
    "24h",
    "last_24_hours",
    "24_hours",
    "all",
    "all_time",
    "odometer",
}


def get_uae_now() -> datetime:
    """Return current timezone-aware datetime in UAE time (Asia/Dubai)."""
    return datetime.now(UAE_TZ)


def now_timestamp() -> int:
    """Return the current UNIX timestamp in UTC seconds."""
    return int(datetime.now(timezone.utc).timestamp())


def to_iso_utc(timestamp: int) -> str:
    """Convert a UNIX timestamp (seconds) to an ISO-8601 UTC string."""
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def to_iso_uae(timestamp: int) -> str:
    """Convert a UNIX timestamp (seconds) to an ISO-8601 string in UAE time (+04:00)."""
    if not timestamp:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(UAE_TZ).isoformat()


def parse_date_to_uae_bounds(from_date: str, to_date: str = None) -> tuple:
    """
    Parses date strings (YYYY-MM-DD) into exact UAE start and end UNIX timestamps.
    - Start date starts at 00:00:00 UAE time.
    - End date: if end date is current UAE date, ends at current time.
      Otherwise ends at 23:59:59.999 UAE time.
    """
    uae_now = get_uae_now()
    now_ts = int(uae_now.timestamp())

    try:
        from_dt_base = datetime.strptime(from_date[:10], "%Y-%m-%d")
        target_to_date_str = to_date[:10] if to_date else from_date[:10]
        to_dt_base = datetime.strptime(target_to_date_str, "%Y-%m-%d").date()
    except ValueError as e:
        logging.error(f"Error parsing date bounds {from_date}..{to_date}: {e}")
        raise ValueError(f"Invalid date format: {from_date}..{to_date}. Expected YYYY-MM-DD")

    if to_dt_base < from_dt_base.date():
        raise ValueError("End date must be on or after start date")

    from_dt = datetime.combine(from_dt_base.date(), dtime.min, tzinfo=UAE_TZ)
    from_ts = int(from_dt.timestamp())

    if to_dt_base == uae_now.date():
        to_ts = now_ts
    else:
        to_dt = datetime.combine(to_dt_base, dtime(23, 59, 59, 999000), tzinfo=UAE_TZ)
        to_ts = int(to_dt.timestamp())

    return from_ts, to_ts


def get_timeframe_timestamps(
    timeframe: str = None,
    from_time: int = None,
    to_time: int = None,
    from_date: str = None,
    to_date: str = None,
    hours: int = None
) -> tuple:
    """
    Centralized timeframe & date range resolver returning (from_time, to_time) UNIX timestamps.
    Strictly uses UAE Time (Asia/Dubai, UTC+4).

    Rules:
    - 'today': 00:00:00 UAE today -> current UAE time.
    - 'yesterday': 00:00:00 UAE yesterday -> 23:59:59.999 UAE yesterday.
    - 'week' / 'this_week' / '7d': 00:00:00 UAE start of current week (Monday) -> current UAE time.
    - 'month' / 'this_month' / '30d': 00:00:00 UAE first day of month -> current UAE time.
    - 'year' / 'this_year' / '1y': 00:00:00 UAE Jan 1st of current year -> current UAE time.
    - '24h' / 'last_24_hours': current time - 24h -> current UAE time.
    - Specific from_date / to_date: exact UAE calendar start/end bounds.
    - A positive 'hours' value: rolling interval ending at the current time.
    """
    # Direct numeric timestamp overrides
    if from_time is not None and to_time is not None:
        try:
            ft, tt = int(from_time), int(to_time)
            if ft > tt:
                raise ValueError("End timestamp must be on or after start timestamp")
            return ft, tt
        except (ValueError, TypeError):
            raise ValueError(f"Invalid numeric timestamps: from_time={from_time}, to_time={to_time}")

    # Date string overrides (YYYY-MM-DD)
    if from_date:
        return parse_date_to_uae_bounds(from_date, to_date)

    uae_now = get_uae_now()
    now_ts = int(uae_now.timestamp())

    if hours is not None:
        try:
            interval_hours = int(hours)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid hours value: {hours}")
        if interval_hours <= 0:
            raise ValueError("Hours must be a positive integer")
        if not timeframe:
            return now_ts - interval_hours * 3600, now_ts
    
    if not timeframe:
        timeframe = "today"

    tf = timeframe.lower().strip()

    if tf not in SUPPORTED_TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe: '{timeframe}'. Supported: {sorted(list(SUPPORTED_TIMEFRAMES))}")

    if tf in ["today"]:
        start_today = datetime.combine(uae_now.date(), dtime.min, tzinfo=UAE_TZ)
        return int(start_today.timestamp()), now_ts

    elif tf in ["yesterday"]:
        yesterday_date = uae_now.date() - timedelta(days=1)
        start_yest = datetime.combine(yesterday_date, dtime.min, tzinfo=UAE_TZ)
        end_yest = datetime.combine(yesterday_date, dtime(23, 59, 59, 999000), tzinfo=UAE_TZ)
        return int(start_yest.timestamp()), int(end_yest.timestamp())

    elif tf in ["week", "this_week", "7d", "last_1_week", "1w"]:
        # Start of current calendar week (Monday 00:00 UAE)
        monday_date = uae_now.date() - timedelta(days=uae_now.weekday())
        start_week = datetime.combine(monday_date, dtime.min, tzinfo=UAE_TZ)
        return int(start_week.timestamp()), now_ts

    elif tf in ["month", "this_month", "30d", "last_1_month", "1m"]:
        first_day_month = uae_now.date().replace(day=1)
        start_month = datetime.combine(first_day_month, dtime.min, tzinfo=UAE_TZ)
        return int(start_month.timestamp()), now_ts

    elif tf in ["year", "this_year", "1y", "last_1_year", "365d"]:
        first_day_year = uae_now.date().replace(month=1, day=1)
        start_year = datetime.combine(first_day_year, dtime.min, tzinfo=UAE_TZ)
        return int(start_year.timestamp()), now_ts

    elif tf in ["24h", "last_24_hours", "24_hours"]:
        return now_ts - 86400, now_ts

    elif tf in ["all", "all_time", "odometer"]:
        # From beginning of epoch to now
        return 0, now_ts

    raise ValueError(f"Unhandled timeframe: {tf}")
