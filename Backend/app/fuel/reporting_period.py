"""
Monthly-summary reporting periods.

A statement without a transaction-date column is a monthly summary: each accepted row is stored with the last
calendar day of the reporting month the user selected and date_basis 'reporting_month_end'. That date is a reporting
date, not a refuelling date: such rows are monthly totals and are never shown or analysed as refuel events.
Rows without date_basis (every row stored before Backend/sql/fuel_import_replacement.sql) are dated transactions.
With FUEL_MONTHLY_SUMMARY_TABLE summaries are stored in fuel_monthly_summaries instead (see summary_scope_row).
"""

import calendar
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from app.utils.datetime_helpers import get_uae_now, parse_tx_wallclock

DATE_BASIS_TRANSACTION = "transaction"
DATE_BASIS_MONTH_END = "reporting_month_end"

IMPORT_MODE_DATED = "dated"
IMPORT_MODE_MONTHLY = "monthly_summary"

MIN_REPORTING_YEAR = 2000
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def month_bounds(day: date) -> Tuple[date, date]:
    """First and last calendar day of the month containing `day`."""
    return day.replace(day=1), day.replace(day=calendar.monthrange(day.year, day.month)[1])


def parse_reporting_month(value: Any, today: Optional[date] = None) -> Tuple[date, date]:
    """
    'YYYY-MM' -> (first day, last day) of that month. Raises ValueError with a user-facing message for a malformed
    value, a year before MIN_REPORTING_YEAR, or a month after the current UAE month. The current month is accepted
    (see is_partial_month).
    """
    text = str(value or "").strip()
    m = _MONTH_RE.match(text)
    if not m:
        raise ValueError(f"Invalid reporting month '{text}'. Use the format YYYY-MM.")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid reporting month '{text}': month must be 01-12.")
    if year < MIN_REPORTING_YEAR:
        raise ValueError(f"Invalid reporting month '{text}': year must be {MIN_REPORTING_YEAR} or later.")
    today = today or get_uae_now().date()
    first = date(year, month, 1)
    if first > today.replace(day=1):
        raise ValueError(f"Reporting month {text} is in the future.")
    return month_bounds(first)


def is_partial_month(month_end: date, today: Optional[date] = None) -> bool:
    """True while the reporting month has not ended (UAE time)."""
    return month_end > (today or get_uae_now().date())


def is_monthly_summary(tx: Dict[str, Any]) -> bool:
    return tx.get("date_basis") == DATE_BASIS_MONTH_END


# ─── Dedicated monthly-summary table (FUEL_MONTHLY_SUMMARY_TABLE) ──────────────
# public.fuel_monthly_summaries (Backend/sql/monthly_summaries/02_...) stores a summary with its reporting_month
# and no transaction date. For the KPI code a stored summary is adapted into the in-memory row shape the legacy
# layout produced (date_basis reporting_month_end, dated on the month's last day), so the scope, KPI, trend and
# anomaly rules apply unchanged. That date is in-memory only: the API reports summaries by reporting_month.
SUMMARY_TABLE = "fuel_monthly_summaries"
RECORD_TYPE_SUMMARY = "monthly_summary"
SUMMARY_COLUMNS = ("id", "import_id", "reporting_month", "vehicle_identifier", "fuel_type", "litres", "unit_price",
                   "total_cost", "odometer_km", "station_name", "invoice_number", "card_number", "driver_name",
                   "reference_number", "notes", "source_row_number")
_SUMMARY_LIFECYCLE = ("id", "staged", "superseded_by_import_id", "superseded_at", "created_at",
                      "migrated_from_transaction_id", "migration_note", "vehicle_key")


def is_summary_table_row(tx: Dict[str, Any]) -> bool:
    """True for a row read from fuel_monthly_summaries (its id is summary_id; `id` is None)."""
    return tx.get("record_type") == RECORD_TYPE_SUMMARY


def reporting_month_label(tx: Dict[str, Any]) -> Optional[str]:
    """'YYYY-MM' of a monthly-summary row in either layout, else None."""
    if is_summary_table_row(tx):
        return tx.get("reporting_month")
    bounds = summary_month_bounds(tx)
    return bounds[0].strftime("%Y-%m") if bounds else None


def summary_scope_row(stored: Dict[str, Any]) -> Dict[str, Any]:
    """
    A fuel_monthly_summaries row as an in-memory KPI row. `id` is None so a summary id can never be taken for a
    fuel_transactions id; the summary's own id is summary_id. Raises ValueError for an unreadable reporting_month.
    """
    first = date.fromisoformat(str(stored.get("reporting_month"))[:10])
    if first.day != 1:
        raise ValueError(f"reporting_month {first.isoformat()} is not the first day of a month")
    row = {k: v for k, v in stored.items() if k not in _SUMMARY_LIFECYCLE}
    row.update(id=None, summary_id=stored.get("id"), record_type=RECORD_TYPE_SUMMARY,
               date_basis=DATE_BASIS_MONTH_END, reporting_month=first.strftime("%Y-%m"),
               transaction_date=month_bounds(first)[1].isoformat())
    return row


def summary_insert_row(tx: Dict[str, Any], reporting_month_first: date) -> Dict[str, Any]:
    """A staged-insert payload for fuel_transactions turned into one for fuel_monthly_summaries (no date columns)."""
    out = {k: v for k, v in tx.items() if k not in ("transaction_date", "date_basis", "vehicle_id")}
    out["reporting_month"] = reporting_month_first.isoformat()
    return out


def summary_months_for_range(date_from: Optional[str], date_to: Optional[str]) -> Optional[Tuple[Optional[date], Optional[date]]]:
    """
    First days of the earliest and latest reporting months whose month-end reporting date (00:00 wall clock) lies in
    [date_from, date_to], the exact window a legacy summary row dated on that day was selected by. A bound is None
    when not given; returns None when no month qualifies. Bounds are 'YYYY-MM-DD' or wall-clock timestamps.
    """
    def wallclock(value: Optional[str], is_end: bool):
        if not value or not str(value).strip():
            return None
        parsed = parse_tx_wallclock(str(value).strip())
        if parsed is None:
            raise ValueError(f"Unreadable date bound '{value}'")
        stamp, precision = parsed
        return stamp.replace(hour=23, minute=59, second=59) if is_end and precision == "date" and len(str(value).strip()) == 10 else stamp

    lo, hi = wallclock(date_from, False), wallclock(date_to, True)
    first = last = None
    if lo is not None:
        month_end = month_bounds(lo.date())[1]
        start = lo.date().replace(day=1)
        # The month-end reporting date is 00:00 on its last day: a bound later that day misses it
        first = start if lo <= datetime.combine(month_end, datetime.min.time()) else month_bounds(month_end + timedelta(days=1))[0]
    if hi is not None:
        month_end = month_bounds(hi.date())[1]
        if hi >= datetime.combine(month_end, datetime.min.time()):
            last = hi.date().replace(day=1)
        else:
            last = month_bounds(hi.date().replace(day=1) - timedelta(days=1))[0]
    if first is not None and last is not None and first > last:
        return None
    return first, last


def tx_day(tx: Dict[str, Any]) -> Optional[date]:
    """Wall-clock calendar day of a stored row's transaction_date, or None when unparseable."""
    parsed = parse_tx_wallclock(tx.get("transaction_date"))
    return parsed[0].date() if parsed else None


def summary_month_bounds(tx: Dict[str, Any]) -> Optional[Tuple[date, date]]:
    """Reporting month of a monthly-summary row, or None for dated rows and unparseable dates."""
    if not is_monthly_summary(tx):
        return None
    day = tx_day(tx)
    return month_bounds(day) if day else None


# ─── Coverage evidence ─────────────────────────────────────────────────────────
# An import proves that its statement covers every day of a period (so a day without a row is a real zero for the
# vehicles in that statement) only when its stored metadata says so: a committed, fully stored dated import whose
# replace_scope the user confirmed as whole calendar months (extended_to_full_months). The first and last transaction
# dates of a file never prove coverage. Metadata exists only after Backend/sql/fuel_import_replacement.sql.
COVERAGE_STATUS = "completed"


def confirmed_coverage_period(import_meta: Optional[Dict[str, Any]]) -> Optional[Tuple[date, date]]:
    """(first day, last day) an import's statement is confirmed to cover, or None when nothing is established."""
    if not isinstance(import_meta, dict):
        return None
    if import_meta.get("status") != COVERAGE_STATUS or import_meta.get("import_mode") != IMPORT_MODE_DATED:
        return None
    scope = import_meta.get("replace_scope")
    if not isinstance(scope, dict) or scope.get("extended_to_full_months") is not True:
        return None
    try:
        first = date.fromisoformat(str(scope.get("period_from"))[:10])
        last = date.fromisoformat(str(scope.get("period_to"))[:10])
    except ValueError:
        return None
    return (first, last) if first <= last else None


def covers(import_meta: Optional[Dict[str, Any]], first: date, last: date) -> bool:
    """True when the import's confirmed coverage includes every day of [first, last]."""
    period = confirmed_coverage_period(import_meta)
    return period is not None and period[0] <= first and last <= period[1]
