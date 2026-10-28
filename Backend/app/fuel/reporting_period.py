"""
Monthly-summary reporting periods.

A statement without a transaction-date column is a monthly summary: each accepted row is stored with the last
calendar day of the reporting month the user selected and date_basis 'reporting_month_end'. That date is a reporting
date, not a refuelling date: such rows are monthly totals and are never shown or analysed as refuel events.
Rows without date_basis (every row stored before Backend/sql/fuel_import_replacement.sql) are dated transactions.
"""

import calendar
import re
from datetime import date
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
