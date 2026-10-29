"""Dynamic Excel fuel import processor, preview generator, and Supabase execution engine."""

import hashlib
import io
import logging
import math
import re
import threading
import time
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import from_excel

from app.fuel.db import (
    FuelCommitError,
    FuelDataFetchError,
    build_fingerprint,
    clear_vehicle_identifier_cache,
    commit_fuel_import,
    create_fuel_import_record,
    fetch_existing_transaction_fingerprints,
    fetch_import_rows,
    fetch_running_imports,
    fetch_visible_rows_in_range,
    find_import_by_sha256,
    get_fuel_import_record,
    ignored_counts_enabled,
    insert_fuel_transactions_batch,
    insert_fuel_transactions_ignore_duplicates,
    insert_monthly_summaries_batch,
    dated_replace_enabled,
    replace_enabled,
    schema_v2_enabled,
    summary_table_enabled,
    update_fuel_import_record,
    weak_fingerprint_key,
)
from app.fuel.mapper import (
    CANONICAL_FIELDS,
    PRICE_FIELDS,
    REQUIRED_FIELDS,
    ambiguous_mapping_fields,
    repeated_header_fields,
    date_header_candidates,
    map_headers,
    normalize_header,
)
from app.fuel.reporting_period import (
    DATE_BASIS_MONTH_END,
    DATE_BASIS_TRANSACTION,
    IMPORT_MODE_DATED,
    IMPORT_MODE_MONTHLY,
    SUMMARY_TABLE,
    is_monthly_summary,
    is_partial_month,
    is_summary_table_row,
    month_bounds,
    parse_reporting_month,
    summary_insert_row,
    tx_day,
)
from app.fuel.schemas import FuelImportPreviewResponse, FuelImportResultResponse, HeaderMappingItem
from app.utils.config import get_env_int

logger =logging.getLogger(__name__)


class FuelImportError(Exception):
    """Custom domain exception for fuel import validation errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# Summary keywords that identify subtotal/total/summary rows
SUMMARY_ROW_KEYWORDS: Set[str] = {
    "total", "grand total", "subtotal", "sub total", "summary",
    "average", "avg", "count", "page", "report total", "closing balance",
    "opening balance", "net total", "gross total"
}

# The keyword alone, optionally followed only by a currency/unit word and/or digits ("total", "grand total aed",
# "sub total 12"); "total al quoz" (a station) is not a summary label.
_SUMMARY_LABEL_RE = re.compile(r"^(grand\s*total|sub\s*total|report\s*total|net\s*total|gross\s*total|total|summary|"
                               r"closing\s*bal(ance)?|opening\s*bal(ance)?)"
                               r"(\s+(aed|amount|value|litres|liters|ltr|ltrs|qty|quantity))?[\s\d]*$")

MAX_IMPORT_WARNINGS = 50
MAX_DETAIL_ROWS = 20
# .xlsx is a zip: a few MB upload can expand to GBs in openpyxl (read_only=False). Checked before loading.
MAX_UNCOMPRESSED_BYTES = get_env_int("FUEL_IMPORT_MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024)
# Serializes the duplicate check and insert of concurrent imports in this process (without FUEL_SCHEMA_V2
# there is no database uniqueness). It does not cover several worker processes or serverless instances; across
# those, _claim_import uses the 'processing' records in fuel_imports (see there).
_IMPORT_LOCK = threading.Lock()
IMPORT_LOCK_WAIT_SECONDS = get_env_int("FUEL_IMPORT_LOCK_WAIT_SECONDS", 300)
# Time budget, measured from request entry (the auth middleware, so upload parsing and the lock wait count).
# The hosting limit is the backend service's maxDuration in vercel.json (300 s); keep this setting equal to it.
IMPORT_REQUEST_LIMIT_SECONDS = get_env_int("FUEL_IMPORT_REQUEST_LIMIT_SECONDS", 300)
# Left unused at the end for sending the response and platform overhead before the request started here.
IMPORT_RESPONSE_MARGIN_SECONDS = 10
# The final status update, and before it the re-count after uncertain batches, each get their own window.
IMPORT_STATUS_RESERVE_SECONDS = 20
IMPORT_RECOUNT_RESERVE_SECONDS = 20
# No insert, read-back or retry starts after this many seconds; capped so the reserves above always fit.
IMPORT_TIME_BUDGET_SECONDS = get_env_int("FUEL_IMPORT_TIME_BUDGET_SECONDS", 240)
# The lock is not taken when less than this is left for parsing, the duplicate check and the inserts.
IMPORT_MIN_WORK_SECONDS = 30
# A 'processing' record older than this is treated as interrupted and may be reconciled.
IMPORT_STALE_SECONDS = get_env_int("FUEL_IMPORT_STALE_SECONDS", 900)
WORKBOOK_TOO_LARGE_MESSAGE = "The workbook is too large to import. Split the statement into smaller files."
# Library error text (zip paths, parser internals) is logged server-side, never returned to the client.
UNREADABLE_WORKBOOK_MESSAGE = "Unable to read Excel file. It may be corrupt, encrypted or not a valid .xlsx workbook."

# Written into error_message of an import whose rows were not all processed or verified (interrupted, time limit,
# unknown write outcome). Reconcile keeps it, so such an import is never reported as 'completed'.
INCOMPLETE_MARKER = "Import incomplete:"
# Wording of records written before the marker existed that also means the import is incomplete.
_LEGACY_INCOMPLETE_PHRASES = ("Interrupted by an internal error", "interrupted before it finished",
                              "Time limit reached", "outcome unknown")


def _is_incomplete(error_message: Any) -> bool:
    text = str(error_message or "")
    return INCOMPLETE_MARKER in text or any(p in text for p in _LEGACY_INCOMPLETE_PHRASES)


class ImportDeadlines:
    """
    Absolute time.perf_counter() deadlines of one import request, all derived from when the request started:
    writes (inserts, read-backs, retries) < recount < final status update < request limit.
    """

    def __init__(self, request_started: float):
        status = request_started + IMPORT_REQUEST_LIMIT_SECONDS - IMPORT_RESPONSE_MARGIN_SECONDS
        self.status = status
        self.recount = status - IMPORT_STATUS_RESERVE_SECONDS
        self.writes = min(request_started + IMPORT_TIME_BUDGET_SECONDS, self.recount - IMPORT_RECOUNT_RESERVE_SECONDS)


TIMESTAMP_COMPARISON_NOTE = (
    "Transaction dates are compared as wall-clock times exactly as written on the statement "
    "(assumed UAE local time); any UTC offset returned by the database is ignored, not converted. "
    "The database column type and the session time zone of historical writes are not verified."
)


def _add_to_weak_index(index: Dict[str, Set[str]], fingerprint: str) -> None:
    key, time_part = weak_fingerprint_key(fingerprint)
    if time_part is not None:
        index.setdefault(key, set()).add(time_part)


def _build_weak_index(fingerprints: Set[str]) -> Dict[str, Set[str]]:
    index: Dict[str, Set[str]] = {}
    for fp in fingerprints:
        _add_to_weak_index(index, fp)
    return index


def _is_duplicate(fingerprint: str, strong: Set[str], weak_index: Dict[str, Set[str]]) -> bool:
    """
    Exact wall-clock match, or same date-level key when either side only has date precision
    (date-only / 00:00:00). Two rows on the same day with different real times are not duplicates.
    """
    if fingerprint in strong:
        return True
    key, time_part = weak_fingerprint_key(fingerprint)
    times = weak_index.get(key)
    if not times or time_part is None:
        return False
    return time_part == "00:00:00" or "00:00:00" in times


def load_fuel_workbook(file_bytes: bytes, context: str):
    """Open an uploaded .xlsx after checking its expanded size; client errors never carry library text."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            expanded = sum(info.file_size for info in archive.infolist())
    except (zipfile.BadZipFile, ValueError) as exc:
        logger.warning("%s: upload is not a zip-based workbook: %s", context, exc)
        raise FuelImportError(UNREADABLE_WORKBOOK_MESSAGE, status_code=422) from None
    if expanded > MAX_UNCOMPRESSED_BYTES:
        logger.warning("%s: workbook expands to %d bytes (limit %d)", context, expanded, MAX_UNCOMPRESSED_BYTES)
        raise FuelImportError(WORKBOOK_TOO_LARGE_MESSAGE, status_code=413)
    try:
        return openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=False)
    except Exception as exc:
        logger.warning("%s: workbook could not be opened: %s", context, exc)
        raise FuelImportError(UNREADABLE_WORKBOOK_MESSAGE, status_code=422) from None


_THOUSANDS_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def parse_numeric_value(val: Any) -> Tuple[Optional[float], Optional[str]]:
    """
    Safely parse numeric values from cells containing numbers, strings, or currency indicators.
    Returns: (parsed_float_or_None, warning_if_any)
    """
    if val is None:
        return None, None
    if isinstance(val, bool):
        return None, f"Could not parse numeric value '{val}'"
    if isinstance(val, (int, float)):
        number = float(val)
        return (number, None) if math.isfinite(number) else (None, f"Could not parse numeric value '{val}'")

    str_val = str(val).strip()
    if not str_val or str_val.lower() in {"-", "n/a", "na", "null", "none", "/"}:
        return None, None

    # Remove currency strings and units
    cleaned = re.sub(r"(?i)\b(aed|sar|usd|eur|gbp|dh|dhs|ltr|litres|liters|km)\b", "", str_val)
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").strip()
    # Commas are accepted only as thousands separators (1,234 / 1,234.56). A decimal comma such as
    # "12,5" or "1.234,56" would otherwise be read as 125 or 1.23456, so it is rejected, not guessed.
    if "," in cleaned:
        if not _THOUSANDS_RE.match(cleaned):
            return None, f"Could not parse numeric value '{str_val}' (ambiguous comma; use '.' for decimals)"
        cleaned = cleaned.replace(",", "")

    try:
        number = float(cleaned)
    except ValueError:
        return None, f"Could not parse numeric value '{str_val}'"
    if not math.isfinite(number):  # "nan", "inf", "1e999" would be stored as invalid JSON
        return None, f"Could not parse numeric value '{str_val}'"
    return number, None


_DMY_TEXT_RE = re.compile(r"^\s*(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})")

DATE_ORDER_DMY = "dmy"
DATE_ORDER_MDY = "mdy"
DATE_ORDER_CONFLICT = "conflict"


def infer_date_order(values: List[Any]) -> Optional[str]:
    """
    Day/month order of a statement's text dates, decided from the whole column rather than row by row:
    'dmy' when some value can only be DD/MM (first part > 12), 'mdy' when some value can only be MM/DD (second part
    > 12), 'conflict' when both occur, None when the column gives no evidence (every text date is ambiguous or none is
    text). Native Excel dates and ISO text carry no ambiguity and are ignored.
    """
    dmy = mdy = False
    for val in values:
        if val is None or isinstance(val, (datetime, date, int, float)):
            continue
        m = _DMY_TEXT_RE.match(str(val))
        if not m:
            continue
        part1, part2 = int(m.group(1)), int(m.group(2))
        if part1 > 12 and part2 <= 12:
            dmy = True
        elif part2 > 12 and part1 <= 12:
            mdy = True
    if dmy and mdy:
        return DATE_ORDER_CONFLICT
    return DATE_ORDER_DMY if dmy else DATE_ORDER_MDY if mdy else None


DATE_ORDER_CONFLICT_WARNING = (
    "The transaction date column contains both DD/MM and MM/DD dates; dates whose day and month are both 12 or less "
    "cannot be read and are reported as failed rows."
)


def statement_date_order(rows: List[Any], canonical_to_col_idx: Dict[str, int]) -> Optional[str]:
    """infer_date_order over the mapped transaction_date column of the statement rows (None when unmapped)."""
    idx = canonical_to_col_idx.get("transaction_date")
    if idx is None:
        return None
    return infer_date_order([row[idx] if row and len(row) > idx else None for row in rows])


def parse_date_value(val: Any, order: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Safely parse date/datetime values from Excel cells into ISO formatted strings.
    Returns: (iso_date_string_or_None, warning_if_any)

    `order` is the column's day/month order from infer_date_order. An ambiguous text date (both parts <= 12) follows
    it ('mdy' reads MM/DD); without it the UAE default DD/MM applies with a warning, as before. Under 'conflict' the
    column contains both orders, so an ambiguous value cannot be read and is rejected (None) with a warning.
    """
    if val is None:
        return None, None

    # Native openpyxl datetime or date
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%dT%H:%M:%S") if (val.hour or val.minute or val.second) else val.strftime("%Y-%m-%d"), None
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d"), None

    # Excel numeric date (serial number)
    if isinstance(val, (int, float)) and val > 20000:
        try:
            converted = from_excel(val)
            if isinstance(converted, datetime):
                return converted.strftime("%Y-%m-%dT%H:%M:%S") if (converted.hour or converted.minute or converted.second) else converted.strftime("%Y-%m-%d"), None
            if isinstance(converted, date):
                return converted.strftime("%Y-%m-%d"), None
        except Exception:
            pass

    str_val = str(val).strip()
    if not str_val or str_val.lower() in {"-", "n/a", "na", "null", "none"}:
        return None, None

    # ISO formats with optional time and fractional seconds (e.g. '2026-08-31 23:39:39.7478736')
    iso_match = re.match(
        r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?$",
        str_val
    )
    if iso_match:
        y = int(iso_match.group(1))
        m = int(iso_match.group(2))
        d = int(iso_match.group(3))
        hh = int(iso_match.group(4) or 0)
        mm = int(iso_match.group(5) or 0)
        ss = int(iso_match.group(6) or 0)
        try:
            dt = datetime(y, m, d, hh, mm, ss)
            if hh or mm or ss:
                return dt.strftime("%Y-%m-%dT%H:%M:%S"), None
            return dt.strftime("%Y-%m-%d"), None
        except ValueError:
            return None, f"Invalid ISO date values in '{str_val}'"

    # Common UAE / European format: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    dmy_match = re.match(
        r"^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?$",
        str_val
    )
    if dmy_match:
        part1 = int(dmy_match.group(1))
        part2 = int(dmy_match.group(2))
        year = int(dmy_match.group(3))
        hh = int(dmy_match.group(4) or 0)
        mm = int(dmy_match.group(5) or 0)
        ss = int(dmy_match.group(6) or 0)

        # Disambiguation logic: if first part > 12, it must be DD/MM/YYYY
        if part1 > 12 and part2 <= 12:
            try:
                dt = datetime(year, part2, part1, hh, mm, ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d"), None
            except ValueError:
                return None, f"Invalid date values in '{str_val}'"
        # If second part > 12, it must be MM/DD/YYYY
        elif part2 > 12 and part1 <= 12:
            try:
                dt = datetime(year, part1, part2, hh, mm, ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d"), None
            except ValueError:
                return None, f"Invalid date values in '{str_val}'"
        elif order == DATE_ORDER_CONFLICT:
            return None, (f"Ambiguous date '{str_val}' rejected: this column contains both DD/MM and MM/DD dates, "
                          "so the day and month cannot be told apart")
        elif order == DATE_ORDER_MDY:
            # Both <= 12, but other dates in this column can only be MM/DD
            try:
                dt = datetime(year, part1, part2, hh, mm, ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d"), None
            except ValueError:
                return None, f"Invalid date values in '{str_val}'"
        elif order == DATE_ORDER_DMY:
            # Both <= 12, but other dates in this column can only be DD/MM
            try:
                dt = datetime(year, part2, part1, hh, mm, ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d"), None
            except ValueError:
                return None, f"Invalid date values in '{str_val}'"
        else:
            # Both <= 12 and no evidence in the column: default to DD/MM/YYYY (standard in UAE/Middle East) with a warning
            try:
                dt = datetime(year, part2, part1, hh, mm, ss)
                res_str = dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d")
                warning = f"Ambiguous date '{str_val}' interpreted as DD/MM/YYYY ({res_str})"
                return res_str, warning
            except ValueError:
                return None, f"Could not parse date '{str_val}'"

    return None, f"Unrecognized date format '{str_val}'"


# Statement entries that are not fuel purchases: account payments, service/tag charges and fees.
NON_FUEL_TYPE_PATTERN = re.compile(r"payment|charge|fee", re.IGNORECASE)


def is_non_fuel_entry(raw_type: Any, raw_litres: Any) -> bool:
    """
    True for a row whose mapped transaction type names a non-fuel entry and that carries no fuel quantity (empty or
    zero litres). A typed row with a non-zero or unparseable quantity is not skipped: it is validated as fuel.
    """
    if raw_type is None or not NON_FUEL_TYPE_PATTERN.search(str(raw_type)):
        return False
    if raw_litres is None or str(raw_litres).strip() == "":
        return True
    litres, _ = parse_numeric_value(raw_litres)
    return litres == 0


def is_summary_or_blank_row(row_values: List[Any], header_count: int) -> Tuple[bool, str]:
    """
    Detect whether a row is blank, a header repetition, or a summary/total row.
    Returns: (is_ignored, reason)
    """
    non_empty = [v for v in row_values if v is not None and str(v).strip() != ""]
    if not non_empty:
        return True, "blank row"

    # Check if first 3 populated values match any summary row keyword
    # A summary label is a cell that IS a keyword, optionally with a currency/unit word or a number ("Total",
    # "Grand Total:", "Sub-total AED"). A keyword inside other text ("TotalEnergies", "Total Al Quoz", "Monthly total")
    # is data: such a row is validated as fuel (and reported as invalid if it is not), never silently dropped.
    first_few_strs = [str(v).strip().lower() for v in non_empty[:3]]
    for s in first_few_strs:
        norm_s = normalize_header(s)
        if norm_s in SUMMARY_ROW_KEYWORDS or _SUMMARY_LABEL_RE.match(norm_s):
            return True, f"summary row: '{s}'"

    # If row only contains 1 populated value across a table with 4+ columns
    if header_count >= 4 and len(non_empty) == 1:
        return True, "lone single-value row (likely title/note/separator)"

    return False, ""


def read_table_rows(sheet: Any, header_row_idx: int) -> List[Tuple[Any, ...]]:
    """Rows below the header row, without trailing blank rows."""
    all_rows = list(sheet.iter_rows(min_row=header_row_idx + 1, values_only=True))
    while all_rows:
        last_r = all_rows[-1]
        if not last_r or all(v is None or str(v).strip() == "" for v in last_r):
            all_rows.pop()
        else:
            break
    return all_rows


def column_indexes(raw_headers: List[str], mapping: Dict[str, Optional[str]]) -> Dict[str, int]:
    """Canonical field -> column index for the mapped headers (case- and whitespace-insensitive)."""
    header_to_col_idx = {str(h).strip().lower(): idx for idx, h in enumerate(raw_headers) if h and str(h).strip()}
    result: Dict[str, int] = {}
    for canonical, mapped_hdr in mapping.items():
        idx = header_to_col_idx.get(str(mapped_hdr).strip().lower()) if mapped_hdr else None
        if idx is not None:
            result[canonical] = idx
    return result


# Total cost and litres x unit price may differ by VAT, discounts or rounding: beyond this the row is accepted with
# a warning (the statement's total is kept; KPIs use total_cost), never rejected.
PRICE_MISMATCH_ABS_AED = 0.05
PRICE_MISMATCH_REL = 0.01


def _price_mismatch(litres: float, unit_price: float, total_cost: float) -> bool:
    return abs(total_cost - litres * unit_price) > max(PRICE_MISMATCH_ABS_AED, PRICE_MISMATCH_REL * abs(total_cost))


def _parse_price(row_slice: List[Any], canonical_to_col_idx: Dict[str, int], key: str,
                 label: str) -> Tuple[Optional[float], Optional[str]]:
    """
    (value, rejection reason) of a pricing cell. A missing cell (column unmapped, blank, '-', 'n/a') is (None, None);
    text or a negative value is a rejection. A literal zero is a genuine price (e.g. a free or fully discounted
    fill) and is kept as 0, never turned into missing.
    """
    if key not in canonical_to_col_idx:
        return None, None
    raw = row_slice[canonical_to_col_idx[key]]
    value, warn = parse_numeric_value(raw)
    if value is None:
        return None, (f"Invalid {label} ('{raw}')" if warn else None)
    if value < 0:
        return None, f"Negative {label} ('{raw}')"
    return value, None


def classify_statement_row(row: Any, canonical_to_col_idx: Dict[str, int], row_idx: int,
                           total_cols: int, date_order: Optional[str] = None,
                           assigned_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Classify and validate one statement row. The single rule set shared by the import preview and the import, so
    both count the same rows the same way. Returns a dict:
      kind                  'ignored' (blank/summary), 'non_fuel' (payment/charge/fee without litres),
                            'failed' (invalid fuel row) or 'fuel' (valid fuel transaction)
      reason                why the row was skipped or failed ('' for 'fuel')
      warnings              row-level warnings (ambiguous date, unparseable optional cells, price mismatch)
      date_ambiguous, optional_parse_failed, price_mismatch
      candidate             for 'fuel': fingerprint, derived flags, date_basis and the transaction fields

    `assigned_date` (YYYY-MM-DD, the reporting month's last day) makes the row a monthly-summary row: the caller
    passes it only when no date column is mapped, so a blank or invalid cell of a mapped date column always fails.
    Dated rows need a unit price or a total cost (each derives the other); monthly-summary rows need the total cost.
    Litres must be numeric and >= 0, and a price that is present must be numeric and >= 0: zero is a genuine value,
    stored as 0. Negative and non-numeric values are rejected.
    """
    res: Dict[str, Any] = {"kind": "fuel", "reason": "", "warnings": [], "date_ambiguous": False,
                           "optional_parse_failed": False, "price_mismatch": False, "candidate": None}
    monthly_summary = assigned_date is not None

    def done(kind: str, reason: str) -> Dict[str, Any]:
        res.update(kind=kind, reason=reason)
        return res

    row_slice = list(row[:total_cols]) if row else []
    while len(row_slice) < total_cols:
        row_slice.append(None)

    is_ignored, reason = is_summary_or_blank_row(row_slice, total_cols)
    if is_ignored:
        return done("ignored", reason)

    # Extract values
    raw_date = row_slice[canonical_to_col_idx["transaction_date"]] if "transaction_date" in canonical_to_col_idx else None
    raw_vehicle = row_slice[canonical_to_col_idx["vehicle_identifier"]] if "vehicle_identifier" in canonical_to_col_idx else None
    raw_litres = row_slice[canonical_to_col_idx["litres"]] if "litres" in canonical_to_col_idx else None
    raw_type = row_slice[canonical_to_col_idx["transaction_type"]] if "transaction_type" in canonical_to_col_idx else None

    # Payments, service charges and fees on the statement are not fuel transactions: skipped and reported
    if is_non_fuel_entry(raw_type, raw_litres):
        return done("non_fuel", f"Non-fuel entry (type '{str(raw_type).strip()}', no litres)")

    # Validate required fields
    if monthly_summary:
        parsed_dt, date_err = assigned_date, None
    else:
        parsed_dt, date_err = parse_date_value(raw_date, date_order)
    if not parsed_dt:
        return done("failed", date_err or f"Invalid or missing transaction date ('{raw_date}')")
    if date_err:
        res["date_ambiguous"] = True
        res["warnings"].append(f"Row {row_idx}: {date_err}")

    if not raw_vehicle or not str(raw_vehicle).strip():
        return done("failed", "Missing vehicle identifier")
    clean_vehicle = str(raw_vehicle).strip()

    parsed_litres, _ = parse_numeric_value(raw_litres)
    if parsed_litres is None:
        return done("failed", f"Missing or invalid fuel litres ('{raw_litres}')")
    if parsed_litres < 0:
        return done("failed", f"Fuel litres cannot be negative ('{raw_litres}')")

    # Parse optional fields; a non-empty cell that cannot be parsed is left empty and reported
    row_parse_failures: List[str] = []

    def get_opt_numeric(canonical_key: str) -> Optional[float]:
        if canonical_key in canonical_to_col_idx:
            raw_val = row_slice[canonical_to_col_idx[canonical_key]]
            val, warn = parse_numeric_value(raw_val)
            if warn:
                row_parse_failures.append(f"{canonical_key} '{raw_val}'")
            return val
        return None

    def get_opt_str(canonical_key: str) -> Optional[str]:
        if canonical_key in canonical_to_col_idx:
            raw_s = row_slice[canonical_to_col_idx[canonical_key]]
            return str(raw_s).strip() if raw_s is not None and str(raw_s).strip() else None
        return None

    unit_price, price_err = _parse_price(row_slice, canonical_to_col_idx, "unit_price", "unit price")
    if price_err:
        return done("failed", price_err)
    total_cost, cost_err = _parse_price(row_slice, canonical_to_col_idx, "total_cost", "total cost")
    if cost_err:
        return done("failed", cost_err)
    if monthly_summary and total_cost is None:
        return done("failed", "Missing total cost (AED): a monthly summary needs the total cost; unit price alone is not enough")
    if unit_price is None and total_cost is None:
        return done("failed", "Missing unit price per litre (and no total cost to derive it from)")
    odometer_km = get_opt_numeric("odometer_km")

    if row_parse_failures:
        res["optional_parse_failed"] = True
        res["warnings"].append(f"Row {row_idx}: could not parse {', '.join(row_parse_failures)}; left empty")

    # Derive the missing one of total_cost / unit_price from the other (derived, not recorded). With 0 litres a unit
    # price cannot be derived from the total: it stays empty (unknown), never invented.
    cost_derived = False
    unit_price_derived = False
    if total_cost is None:
        total_cost = round(unit_price * parsed_litres, 2)
        cost_derived = True
    elif unit_price is None:
        if parsed_litres > 0:
            unit_price = round(total_cost / parsed_litres, 4)
            unit_price_derived = True
    elif _price_mismatch(parsed_litres, unit_price, total_cost):
        res["price_mismatch"] = True
        res["warnings"].append(f"Row {row_idx}: total cost {total_cost} differs from litres x unit price "
                               f"({parsed_litres} x {unit_price} = {round(parsed_litres * unit_price, 2)}); "
                               "the statement's total cost is kept")

    invoice_number = get_opt_str("invoice_number")
    fp = build_fingerprint(
        vehicle_identifier=clean_vehicle,
        transaction_date=parsed_dt,
        litres=parsed_litres,
        total_cost=total_cost,
        invoice_number=invoice_number,
        odometer_km=odometer_km,
    )

    # Vehicle ID: public.vehicles is empty in Supabase and Wialon IDs do not belong in that FK.
    # vehicle_id is kept NULL while vehicle_identifier stores the full registration plate.
    res["candidate"] = {
        "fingerprint": fp,
        "cost_derived": cost_derived,
        "unit_price_derived": unit_price_derived,
        "date_basis": DATE_BASIS_MONTH_END if monthly_summary else DATE_BASIS_TRANSACTION,
        "fields": {
            "vehicle_id": None,
            "transaction_date": parsed_dt,
            "vehicle_identifier": clean_vehicle,
            "fuel_type": get_opt_str("fuel_type"),
            "litres": parsed_litres,
            "unit_price": unit_price,
            "total_cost": total_cost,
            "odometer_km": odometer_km,
            "station_name": get_opt_str("station_name"),
            "invoice_number": invoice_number,
            "card_number": get_opt_str("card_number"),
            "driver_name": get_opt_str("driver_name"),
            "reference_number": get_opt_str("reference_number"),
            "notes": get_opt_str("notes"),
            "source_row_number": row_idx,
        },
    }
    return res


def detect_header_row(sheet: openpyxl.worksheet.worksheet.Worksheet, max_scan_rows: int = 25) -> Tuple[int, List[str]]:
    """
    Scan the first max_scan_rows of a worksheet to identify the most probable header row.
    Returns: (header_row_1_indexed, detected_headers)
    """
    best_row_idx = 1
    best_score = -1
    best_headers: List[str] = []

    all_known_aliases: Set[str] = set()
    for field_info in CANONICAL_FIELDS.values():
        for alias in field_info["aliases"]:
            all_known_aliases.add(normalize_header(alias))

    for row_idx, row in enumerate(sheet.iter_rows(min_row=1, max_row=max_scan_rows, values_only=True), start=1):
        if not row:
            continue

        row_headers = [str(cell).strip() if cell is not None else "" for cell in row]
        while row_headers and row_headers[-1] == "":
            row_headers.pop()

        if not row_headers or len([h for h in row_headers if h]) < 2:
            continue

        score = 0
        populated_count = 0
        for h in row_headers:
            if not h:
                continue
            populated_count += 1
            norm = normalize_header(h)
            if norm in all_known_aliases:
                score += 10
            elif len(norm) > 2 and not norm.replace(".", "").isdigit():
                score += 1

        score += populated_count

        if score > best_score:
            best_score = score
            best_row_idx = row_idx
            best_headers = row_headers

    if best_score <= 0 or not best_headers:
        raise FuelImportError("Could not detect a valid header row with column titles in the Excel sheet.", status_code=422)

    return best_row_idx, label_repeated_headers(best_headers)


def label_repeated_headers(headers: List[str]) -> List[str]:
    """
    A header text that appears more than once (repeated column groups, e.g. 'Quantity | Amount' per product) gets
    its column letter on every occurrence: 'Amount (col F)', 'Amount (col J)'. Each column then has its own label, so
    a mapping names exactly one column and no value is read from another column of the same name.
    """
    counts = Counter(h.strip().lower() for h in headers if h)
    return [f"{h} (col {get_column_letter(i + 1)})" if h and counts[h.strip().lower()] > 1 else h
            for i, h in enumerate(headers)]


def validate_confirmed_mapping(raw_headers: List[str], confirmed_mapping: Dict[str, str],
                               date_assigned: bool = False, require_fields: bool = True) -> Dict[str, str]:
    """
    Validate that:
    1. Required fields are present (skipped with require_fields=False; transaction_date is not required when the
       date is assigned from a reporting month).
    2. Mapped Excel headers actually exist in raw_headers.
    3. No Excel header is assigned to multiple incompatible canonical fields.
    """
    if not isinstance(confirmed_mapping, dict) or (require_fields and not confirmed_mapping):
        raise FuelImportError("A valid mapping object must be provided.", status_code=400)

    # Clean mapping
    clean_mapping: Dict[str, str] = {}
    for canonical, excel_hdr in confirmed_mapping.items():
        if canonical not in CANONICAL_FIELDS:
            raise FuelImportError(f"Unknown mapping field '{canonical}'.", status_code=422)
        if excel_hdr and str(excel_hdr).strip():
            clean_mapping[canonical] = str(excel_hdr).strip()

    # 1. Required fields check
    for req in REQUIRED_FIELDS if require_fields else []:
        if date_assigned and req == "transaction_date":
            continue
        if req not in clean_mapping or not clean_mapping[req]:
            raise FuelImportError(
                f"Missing required mapping for '{req}'. Required fields: {', '.join(REQUIRED_FIELDS)}",
                status_code=422,
            )

    # 2. Check header existence in Excel
    raw_header_set = {h.strip().lower(): h for h in raw_headers if h and str(h).strip()}
    for canonical, excel_hdr in clean_mapping.items():
        if excel_hdr.strip().lower() not in raw_header_set:
            raise FuelImportError(
                f"Mapped column '{excel_hdr}' for field '{canonical}' does not exist in the uploaded Excel file.",
                status_code=422,
            )

    # 3. Duplicate header assignment check
    seen_excel_headers: Dict[str, str] = {}
    for canonical, excel_hdr in clean_mapping.items():
        hdr_key = excel_hdr.strip().lower()
        if hdr_key in seen_excel_headers:
            prev_canonical = seen_excel_headers[hdr_key]
            raise FuelImportError(
                f"Excel header '{excel_hdr}' cannot be mapped to multiple canonical fields ('{prev_canonical}' and '{canonical}').",
                status_code=422,
            )
        seen_excel_headers[hdr_key] = canonical

    return clean_mapping


MONTHLY_SUMMARY_DISABLED_MESSAGE = (
    "Statements without a transaction date column (monthly summaries) cannot be imported on this server yet: "
    "the database migration for monthly-summary imports has not been enabled."
)


def resolve_import_mode(mapping: Dict[str, Optional[str]], reporting_month: Optional[str],
                        today: Optional[date] = None) -> Dict[str, Any]:
    """
    Import format from the mapping and the reporting month:
      dated            a date column is mapped; each row is a refuel with its own date (no reporting month allowed)
      monthly_summary  no date column and a valid reporting month; every row gets the month's last day
      None             no date column and no reporting month yet (the user must select one)
    Raises FuelImportError (422) for a reporting month sent with a date column, a malformed or future month, or a
    monthly summary while FUEL_IMPORT_REPLACE is off (such rows could not be marked as summaries).
    """
    info: Dict[str, Any] = {"mode": None, "assigned_date": None, "reporting_month": None,
                            "month_from": None, "month_to": None, "partial_month": False}
    month_given = bool(str(reporting_month or "").strip())
    if mapping.get("transaction_date"):
        if month_given:
            raise FuelImportError("This statement has a transaction date column, so a reporting month is not used. "
                                  "Clear the reporting month, or set the date column to 'No date column'.", status_code=422)
        info["mode"] = IMPORT_MODE_DATED
        return info
    if not month_given:
        return info
    if not replace_enabled():
        raise FuelImportError(MONTHLY_SUMMARY_DISABLED_MESSAGE, status_code=422)
    try:
        first, last = parse_reporting_month(reporting_month, today)
    except ValueError as exc:
        raise FuelImportError(str(exc), status_code=422) from None
    info.update(mode=IMPORT_MODE_MONTHLY, assigned_date=last.isoformat(), reporting_month=first.strftime("%Y-%m"),
                month_from=first, month_to=last, partial_month=is_partial_month(last, today))
    return info


def missing_price_fields(mapping: Dict[str, Optional[str]]) -> List[str]:
    """Pricing columns the format needs: dated -> unit price or total cost; no date column -> total cost."""
    if not mapping.get("transaction_date"):
        return [] if mapping.get("total_cost") else ["total_cost"]
    return [] if mapping.get("unit_price") or mapping.get("total_cost") else ["unit_price"]


def _prices_consistent(rows: List[Any], canonical_to_col_idx: Dict[str, int], total_cols: int) -> bool:
    """True when unit price and total cost are both mapped and every data row has total = litres x unit price."""
    keys = ("litres", "unit_price", "total_cost")
    if any(k not in canonical_to_col_idx for k in keys):
        return False
    checked = 0
    for row in rows:
        cells = list(row[:total_cols]) if row else []
        cells += [None] * (total_cols - len(cells))
        if is_summary_or_blank_row(cells, total_cols)[0]:
            continue
        vals = [parse_numeric_value(cells[canonical_to_col_idx[k]])[0] for k in keys]
        if any(v is None or v <= 0 for v in vals):
            continue
        if _price_mismatch(*vals):
            return False
        checked += 1
    return checked > 0


def mapping_confirmation_fields(raw_headers: List[str], mapping: Dict[str, Optional[str]],
                                mapping_details: List[HeaderMappingItem], rows: List[Any],
                                canonical_to_col_idx: Dict[str, int]) -> List[str]:
    """
    ambiguous_mapping_fields, except price columns whose values prove which is which (see _prices_consistent), plus
    every field mapped to a column whose title is repeated: consistent prices cannot tell which group is meant.
    """
    need = ambiguous_mapping_fields(raw_headers, mapping, mapping_details)
    if any(f in need for f in PRICE_FIELDS) and _prices_consistent(rows, canonical_to_col_idx, len(raw_headers)):
        need = [f for f in need if f not in PRICE_FIELDS]
    return need + [f for f in repeated_header_fields(raw_headers, mapping) if f not in need]


# ─── Latest import wins: replacement scope ─────────────────────────────────────

def replacement_vehicle_key(value: Any) -> str:
    """Vehicle identity for the replacement scope: letters and digits only, lowercase ('A-12345' == 'a 12345')."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def replacement_period(candidates: List[Dict[str, Any]], mode_info: Dict[str, Any],
                       extend_to_full_months: bool = False) -> Optional[Tuple[date, date]]:
    """
    Period a new import replaces: the reporting month for a monthly summary; for a dated statement the first to the
    last transaction day of the file, widened to whole calendar months when the user confirms the statement covers
    them (extend_to_full_months). None when there are no valid rows.
    """
    if mode_info.get("mode") == IMPORT_MODE_MONTHLY:
        return mode_info["month_from"], mode_info["month_to"]
    days = [date.fromisoformat(c["fields"]["transaction_date"][:10]) for c in candidates]
    if not days:
        return None
    lo, hi = min(days), max(days)
    if extend_to_full_months:
        lo, hi = month_bounds(lo)[0], month_bounds(hi)[1]
    return lo, hi


def replacement_fetch_window(period: Tuple[date, date]) -> Tuple[str, str]:
    """Stored rows to read: the period, extended to the end of its last month to see monthly summaries it overlaps."""
    return period[0].isoformat(), month_bounds(period[1])[1].isoformat()


CONFLICT_UNREADABLE_DATE = "unreadable stored date"


def _vehicle_month(vehicle_identifier: Any, day: date) -> Tuple[str, str]:
    return replacement_vehicle_key(vehicle_identifier), day.strftime("%Y-%m")


def _row_ref(row: Dict[str, Any]) -> Tuple[str, int]:
    """(table, id) of a stored row: ids of fuel_transactions and fuel_monthly_summaries may coincide."""
    if is_summary_table_row(row):
        return SUMMARY_TABLE, int(row["summary_id"])
    return "fuel_transactions", int(row["id"])


def split_supersede_ids(rows: List[Dict[str, Any]]) -> Tuple[List[int], List[int]]:
    """(fuel_transactions ids, fuel_monthly_summaries ids) of the rows a plan supersedes."""
    refs = [_row_ref(r) for r in rows]
    return [i for t, i in refs if t != SUMMARY_TABLE], [i for t, i in refs if t == SUMMARY_TABLE]


def plan_replacement(candidates: List[Dict[str, Any]], period: Tuple[date, date],
                     visible_rows: List[Dict[str, Any]], whole_period: bool = False) -> Dict[str, Any]:
    """
    Which visible rows a new import replaces, and which of its rows it must not store. Pure: `visible_rows` comes
    from fetch_visible_rows_in_range over replacement_fetch_window(period). Only rows of the vehicles in the new
    file (replacement_vehicle_key) are considered, whatever import or provider they came from.

    Daily transactions and a monthly summary never count together for one vehicle and month, and the monthly
    summary takes precedence. This is decided per vehicle-month, never for the whole file:
      - monthly-summary import: the vehicle-month's earlier monthly summary and its daily transactions are replaced
        (superseded, never deleted);
      - dated import, vehicle-month with a stored monthly summary: the summary is kept and the file's rows for that
        vehicle-month are skipped (returned in "skipped"); nothing stored for that vehicle-month is touched;
      - dated import, any other vehicle-month: stored dated rows on a day the new file has rows for that vehicle are
        replaced (a corrected statement replaces the vehicle-days it contains); with whole_period (the user confirmed
        the file covers whole months), every dated row of the vehicle in those months.
    A stored row whose date cannot be read is a conflict that refuses the import (its month cannot be checked). Rows
    of other vehicles, and dates outside the period, are never touched.

    An in-scope row identical to a new row (same fingerprint and date basis) is paired with it one-to-one and kept as
    is: the new copy is not inserted. Repeated identical refuels pair one each, so none is lost or doubled.
    Stored rows are told apart by _row_ref: with FUEL_MONTHLY_SUMMARY_TABLE `visible_rows` also holds
    fuel_monthly_summaries rows (summary_scope_row), whose ids are a separate sequence.
    Returns {"in_scope", "supersede", "retained_for" (candidate index -> kept row id), "conflicts", "vehicles",
    "skipped" (candidate indexes not to store), "kept_summaries" ((vehicle key, YYYY-MM) -> kept summary rows)}.
    """
    lo, hi = period
    vehicles = {replacement_vehicle_key(c["fields"]["vehicle_identifier"]) for c in candidates}
    monthly_import = any(c["date_basis"] == DATE_BASIS_MONTH_END for c in candidates)
    vehicle_days = {(replacement_vehicle_key(c["fields"]["vehicle_identifier"]), tx_day(c["fields"])) for c in candidates}
    conflicts: List[Dict[str, Any]] = []
    readable: List[Tuple[Dict[str, Any], date]] = []
    for r in sorted(visible_rows, key=_row_ref):
        if replacement_vehicle_key(r.get("vehicle_identifier")) not in vehicles:
            continue
        day = tx_day(r)
        if day is None:
            conflicts.append({**r, "_conflict": CONFLICT_UNREADABLE_DATE})
        else:
            readable.append((r, day))

    # Vehicle-months a stored monthly summary already reports: a dated import leaves them exactly as they are
    kept_summaries: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    if not monthly_import:
        for r, day in readable:
            first, last = month_bounds(day)
            if is_monthly_summary(r) and not (last < lo or first > hi):
                kept_summaries[_vehicle_month(r.get("vehicle_identifier"), day)].append(r)

    in_scope: List[Dict[str, Any]] = []
    for r, day in readable:
        if is_monthly_summary(r):
            first, last = month_bounds(day)
            if monthly_import and lo <= first and last <= hi:
                in_scope.append(r)
        elif lo <= day <= hi:
            if monthly_import:
                in_scope.append(r)
            elif _vehicle_month(r.get("vehicle_identifier"), day) in kept_summaries:
                continue
            elif whole_period or (replacement_vehicle_key(r.get("vehicle_identifier")), day) in vehicle_days:
                in_scope.append(r)

    skipped = {i for i, c in enumerate(candidates)
               if not monthly_import and tx_day(c["fields"]) is not None
               and _vehicle_month(c["fields"]["vehicle_identifier"], tx_day(c["fields"])) in kept_summaries}

    available: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in in_scope:
        fp = build_fingerprint(r.get("vehicle_identifier"), r.get("transaction_date"), r.get("litres"),
                               r.get("total_cost"), r.get("invoice_number"), r.get("odometer_km"))
        available[(fp, r.get("date_basis") or DATE_BASIS_TRANSACTION)].append(r)
    retained_for: Dict[int, int] = {}
    kept: Set[Tuple[str, int]] = set()
    for i, cand in enumerate(candidates):
        same = None if i in skipped else available.get((cand["fingerprint"], cand["date_basis"]))
        if same:
            ref = _row_ref(same.pop(0))
            retained_for[i] = ref[1]
            kept.add(ref)
    return {
        "in_scope": in_scope,
        "supersede": [r for r in in_scope if _row_ref(r) not in kept],
        "retained_for": retained_for,
        "conflicts": conflicts,
        "vehicles": vehicles,
        "skipped": skipped,
        "kept_summaries": dict(kept_summaries),
    }


def describe_kept_summaries(plan: Dict[str, Any], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per vehicle-month whose stored monthly summary is kept: the summary's totals and the file rows skipped."""
    skipped_by_scope: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for i in sorted(plan.get("skipped", ())):
        f = candidates[i]["fields"]
        skipped_by_scope[_vehicle_month(f["vehicle_identifier"], tx_day(f))].append(f)
    out = []
    for key in sorted(plan.get("kept_summaries", {})):
        rows, file_rows = plan["kept_summaries"][key], skipped_by_scope.get(key, [])
        out.append({
            "vehicle_identifier": str(rows[0].get("vehicle_identifier") or "").strip(),
            "month": key[1],
            "summary_import_ids": sorted({r.get("import_id") for r in rows if r.get("import_id") is not None}),
            "summary_litres": round(sum(float(r.get("litres") or 0) for r in rows), 2),
            "summary_cost": round(sum(float(r.get("total_cost") or 0) for r in rows), 2),
            "rows_skipped": len(file_rows),
            "skipped_source_rows": [f["source_row_number"] for f in file_rows][:MAX_DETAIL_ROWS],
        })
    return [g for g in out if g["rows_skipped"]]


def kept_summary_message(scopes: List[Dict[str, Any]]) -> Optional[str]:
    if not scopes:
        return None
    pairs = [f"{g['vehicle_identifier']} {g['month']}" for g in scopes]
    shown = ", ".join(pairs[:8]) + (f" and {len(pairs) - 8} more" if len(pairs) > 8 else "")
    total = sum(g["rows_skipped"] for g in scopes)
    return (f"{total} row(s) not imported: a monthly summary is already stored for {shown}, and daily transactions "
            "are never counted together with it. The monthly summary is kept; other vehicles and months are imported.")


MAX_REPLACEMENT_RECORDS = 200


def describe_replacement(plan: Dict[str, Any], period: Tuple[date, date], extended: bool) -> Dict[str, Any]:
    """Preview of a replacement plan: the exact rows that would be superseded (capped), per-vehicle totals, conflicts."""
    by_vehicle: Dict[str, Dict[str, Any]] = {}
    for r in plan["supersede"]:
        ident = str(r.get("vehicle_identifier") or "").strip()
        g = by_vehicle.setdefault(ident, {"vehicle_identifier": ident, "rows": 0, "monthly_summary_rows": 0,
                                          "litres": 0.0, "cost": 0.0, "first_date": None, "last_date": None})
        g["rows"] += 1
        g["monthly_summary_rows"] += int(is_monthly_summary(r))
        g["litres"] += float(r.get("litres") or 0)
        g["cost"] += float(r.get("total_cost") or 0)
        day = (tx_day(r) or date.min).isoformat()
        g["first_date"] = min(filter(None, [g["first_date"], day]))
        g["last_date"] = max(filter(None, [g["last_date"], day]))
    for g in by_vehicle.values():
        g["litres"], g["cost"] = round(g["litres"], 2), round(g["cost"], 2)

    def record(r: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: r.get(k) for k in ("id", "import_id", "vehicle_identifier", "transaction_date", "litres",
                                     "total_cost", "invoice_number")} | {
            "date_basis": r.get("date_basis") or DATE_BASIS_TRANSACTION}
        if is_summary_table_row(r):
            # A stored monthly summary has no transaction date: it is shown by its reporting month
            out.update(id=None, summary_id=r.get("summary_id"), record_type=r.get("record_type"),
                       reporting_month=r.get("reporting_month"), transaction_date=None)
        return out

    conflicts = [{"vehicle_identifier": r.get("vehicle_identifier"), "transaction_date": r.get("transaction_date"),
                  "import_id": r.get("import_id"), "reason": r["_conflict"]} for r in plan["conflicts"]]
    return {
        "period_from": period[0].isoformat(),
        "period_to": period[1].isoformat(),
        "extended_to_full_months": extended,
        "vehicles_in_file": len(plan["vehicles"]),
        "rows_to_replace": len(plan["supersede"]),
        "rows_unchanged": len(plan["retained_for"]),
        "litres_to_replace": round(sum(float(r.get("litres") or 0) for r in plan["supersede"]), 2),
        "cost_to_replace": round(sum(float(r.get("total_cost") or 0) for r in plan["supersede"]), 2),
        "by_vehicle": sorted(by_vehicle.values(), key=lambda g: g["vehicle_identifier"]),
        "records": [record(r) for r in plan["supersede"][:MAX_REPLACEMENT_RECORDS]],
        "records_truncated": len(plan["supersede"]) > MAX_REPLACEMENT_RECORDS,
        "partial_month_conflicts": conflicts[:MAX_DETAIL_ROWS],
        "expected_replace_count": len(plan["supersede"]),
    }


def replacement_conflict_message(plan: Dict[str, Any]) -> str:
    pairs = sorted({f"{r.get('vehicle_identifier')} {str(r.get('transaction_date'))[:7]}" for r in plan["conflicts"]})
    shown = ", ".join(pairs[:8]) + (f" and {len(pairs) - 8} more" if len(pairs) > 8 else "")
    return f"A stored row for {shown} has an unreadable date, so its period cannot be checked. Nothing was imported."


# ─── Statements whose rows all share one date ──────────────────────────────────
# A statement whose valid rows all carry one identical date is a monthly summary, classified automatically. Its
# reporting month is that date's month only when the date is the month's last day; any other date (a statement printed
# on the 1st for the month before, or mid-month) is not guessed: the user selects the reporting month.
# single_date_mode IMPORT_MODE_DATED is the user's explicit override (the rows are individual refuels on that day).
SINGLE_DATE_MODES = (IMPORT_MODE_DATED, IMPORT_MODE_MONTHLY)


def single_statement_day(candidates: List[Dict[str, Any]]) -> Optional[date]:
    """
    The calendar day of a dated statement whose valid rows (two or more) all carry the identical date value, else
    None. Rows on one day with different times of day are distinct refuel timestamps, not a reporting date; a single
    row gives no evidence either way and keeps its date column's meaning.
    """
    stamps = {str(c["fields"]["transaction_date"]) for c in candidates}
    return tx_day(candidates[0]["fields"]) if len(candidates) >= 2 and len(stamps) == 1 else None


def single_date_message(day: date) -> str:
    return (f"Every row of this statement has the same date ({day.isoformat()}), so it is treated as a monthly "
            "summary. That date is not the last day of a month, so its reporting month is not certain: select the "
            "reporting month, or mark the rows as individual refuels on that day.")


def needs_single_date_month(day: Optional[date], mode_info: Dict[str, Any], single_date_mode: Optional[str]) -> bool:
    """True while a single-date statement still waits for the user's reporting month (nothing can be imported)."""
    return day is not None and mode_info.get("mode") == IMPORT_MODE_DATED and single_date_mode != IMPORT_MODE_DATED


def effective_single_date_mode(day: Optional[date], single_date_mode: Optional[str]) -> Optional[str]:
    """How a single-date statement is treated: monthly unless the user chose individual refuels; None otherwise."""
    if day is None:
        return None
    return IMPORT_MODE_DATED if single_date_mode == IMPORT_MODE_DATED else IMPORT_MODE_MONTHLY


def apply_single_date_mode(candidates: List[Dict[str, Any]], mode_info: Dict[str, Any], single_date_mode: Optional[str],
                           reporting_month: Optional[str] = None, today: Optional[date] = None) -> Optional[date]:
    """
    For a dated statement whose valid rows all share one day: returns that day and, unless single_date_mode is
    IMPORT_MODE_DATED, makes the rows a monthly summary of `reporting_month`, or of the day's month when the day is
    the month's last day. Each row is then dated on the month's last day exactly as a statement without a date
    column; mode_info and the candidates are updated in place. Rows are never split or spread over days. Without a
    reporting month for any other day nothing changes (needs_single_date_month: the user selects the month).
    Returns None (nothing to confirm) for other statements. Raises FuelImportError (422) for an unknown mode, a
    reporting month on an ordinary dated statement, an invalid month, a monthly summary while FUEL_IMPORT_REPLACE is
    off, or a monthly summary with rows whose total cost was not given (it is never derived from a unit price).
    """
    month_given = bool(str(reporting_month or "").strip())
    if single_date_mode and single_date_mode not in SINGLE_DATE_MODES:
        raise FuelImportError(f"Unknown single_date_mode '{single_date_mode}'. Use 'dated' or 'monthly_summary'.",
                              status_code=422)
    day = single_statement_day(candidates) if mode_info.get("mode") == IMPORT_MODE_DATED else None
    if day is None or single_date_mode == IMPORT_MODE_DATED:
        if month_given and mode_info.get("mode") == IMPORT_MODE_DATED:
            raise FuelImportError("This statement has a transaction date column, so a reporting month is not used. "
                                  "Clear the reporting month, or set the date column to 'No date column'.", status_code=422)
        return day
    if not month_given and day != month_bounds(day)[1]:
        return day
    if not replace_enabled():
        raise FuelImportError(MONTHLY_SUMMARY_DISABLED_MESSAGE, status_code=422)
    no_cost = sum(1 for c in candidates if c.get("cost_derived"))
    if no_cost:
        raise FuelImportError(f"{no_cost} row(s) of this monthly summary have no total cost. A monthly summary needs "
                              "every row's total cost (AED); it is never derived from a unit price.", status_code=422)
    try:
        first, last = parse_reporting_month(reporting_month if month_given else day.strftime("%Y-%m"), today)
    except ValueError as exc:
        raise FuelImportError(str(exc), status_code=422) from None
    assigned = last.isoformat()
    mode_info.update(mode=IMPORT_MODE_MONTHLY, assigned_date=assigned, reporting_month=first.strftime("%Y-%m"),
                     month_from=first, month_to=last, partial_month=is_partial_month(last, today))
    for c in candidates:
        f = c["fields"]
        f["transaction_date"] = assigned
        c["date_basis"] = DATE_BASIS_MONTH_END
        c["fingerprint"] = build_fingerprint(f["vehicle_identifier"], assigned, f["litres"], f["total_cost"],
                                             f["invoice_number"], f["odometer_km"])
    return day


def _monthly_summary_warnings(mode_info: Dict[str, Any]) -> List[str]:
    if mode_info.get("mode") != IMPORT_MODE_MONTHLY:
        return []
    month_label = mode_info["month_from"].strftime("%B %Y")
    if summary_table_enabled():
        out = [f"Monthly summary for {month_label}: every accepted row is stored as a monthly total of that "
               "reporting month, without a refuelling date, and is never shown as daily refuelling."]
    else:
        out = [f"Monthly summary for {month_label}: every accepted row is dated {mode_info['assigned_date']} as the "
               "reporting date. That is not when the vehicle refuelled; these rows are monthly totals and are never "
               "shown as daily refuelling."]
    if mode_info.get("partial_month"):
        out.append(f"{month_label} has not ended yet, so these totals may be incomplete. Importing the final "
                   "statement for the month later replaces them.")
    return out


def _identical_rows(candidates: List[Dict[str, Any]]) -> int:
    """Rows identical to an earlier row of the same file (same fingerprint); all of them are imported."""
    seen: Dict[str, int] = defaultdict(int)
    for c in candidates:
        seen[c["fingerprint"]] += 1
    return sum(n - 1 for n in seen.values())


def resolve_fleet_vehicles() -> Dict[str, int]:
    """
    Attempt to fetch fleet vehicle units to match vehicle_identifier with Wialon unit IDs.
    First indexes verified Wialon profile registration plates.
    Keeps unit name matching as an explicit backward-compatibility fallback.
    Returns normalized plate/name -> vehicle_id mapping.
    """
    try:
        from app.wialon import login
        from app.vehicles import get_all_vehicles, extract_registration_plate

        sid = login()
        vehicles = get_all_vehicles(sid)
        lookup: Dict[str, int] = {}
        fallback_lookup: Dict[str, int] = {}

        for v in vehicles:
            v_id = v.get("id")
            if not v_id:
                continue
            v_id_int = int(v_id)
            lookup[str(v_id)] = v_id_int

            # 1. Primary: Verified Profile Registration Plate
            v_plate = extract_registration_plate(v)
            if v_plate:
                plate_clean = str(v_plate).strip().lower()
                lookup[plate_clean] = v_id_int
                plate_alpha = re.sub(r"[^a-zA-Z0-9]", "", plate_clean)
                if plate_alpha:
                    lookup[plate_alpha] = v_id_int

            # 2. Explicit Fallback: Unit Name (General -> Name) for backward compatibility
            v_name = v.get("nm")
            if v_name:
                name_clean = str(v_name).strip().lower()
                fallback_lookup[name_clean] = v_id_int
                name_alpha = re.sub(r"[^a-zA-Z0-9]", "", name_clean)
                if name_alpha:
                    fallback_lookup[name_alpha] = v_id_int

        # Add fallback mappings only if not already claimed by a verified plate
        for k, vid in fallback_lookup.items():
            if k not in lookup:
                lookup[k] = vid

        return lookup
    except Exception as exc:
        logger.warning("Could not pre-fetch Wialon vehicles for ID resolution: %s", exc)
        return {}


def generate_fuel_preview(
    file_bytes: bytes,
    file_name: str,
    mapping: Optional[Dict[str, Optional[str]]] = None,
    reporting_month: Optional[str] = None,
    mapping_confirmed: bool = False,
    extend_to_full_months: bool = False,
    bearer_token: Optional[str] = None,
    today: Optional[date] = None,
    single_date_mode: Optional[str] = None,
) -> FuelImportPreviewResponse:
    """
    Parse an uploaded Excel workbook, detect structure, map headers, and generate a safe preview.

    `mapping` (the user's column choices; default: the suggested mapping), `reporting_month` (statements without
    a date column, or a single-date statement confirmed as a monthly summary) and `single_date_mode` (see
    apply_single_date_mode) are applied exactly as the import applies them, so the counts match. With
    FUEL_IMPORT_REPLACE the preview also reads (never writes) the rows the import would supersede and the rows it
    would skip because a monthly summary is kept. ready_to_import is False, with blocking_reasons, whenever the
    import would be refused.
    """
    lower_name = file_name.lower()
    if not (lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm") or lower_name.endswith(".xltx")):
        raise FuelImportError("Unsupported file type. Please upload a modern Excel file (.xlsx).", status_code=400)

    wb = load_fuel_workbook(file_bytes, "Fuel preview")

    if not wb.sheetnames:
        raise FuelImportError("Excel workbook contains no worksheets.", status_code=422)

    sheet = wb.active or wb[wb.sheetnames[0]]
    worksheet_name = sheet.title

    header_row_idx, raw_headers = detect_header_row(sheet)
    suggested_mapping, mapping_details, unmapped_columns, _ = map_headers(raw_headers)
    if mapping is not None:
        chosen = validate_confirmed_mapping(raw_headers, mapping, require_fields=False)
        active_mapping: Dict[str, Optional[str]] = {k: chosen.get(k) for k in CANONICAL_FIELDS}
        used = {h.strip().lower() for h in chosen.values()}
        unmapped_columns = [h for h in raw_headers if h and h.strip() and h.strip().lower() not in used]
    else:
        active_mapping = dict(suggested_mapping)

    blocking_reasons: List[str] = []
    try:
        # With a date column a reporting month only applies to a confirmed single-date summary (apply_single_date_mode)
        mode_info = resolve_import_mode(active_mapping, None if active_mapping.get("transaction_date") else reporting_month,
                                        today)
    except FuelImportError as exc:
        mode_info = {"mode": None}
        blocking_reasons.append(exc.message)
    monthly = mode_info.get("mode") == IMPORT_MODE_MONTHLY
    assigned_date = mode_info.get("assigned_date") if monthly else None

    missing_required_fields = [f for f in REQUIRED_FIELDS
                               if not active_mapping.get(f) and not (f == "transaction_date" and monthly)]
    missing_required_fields += missing_price_fields(active_mapping)

    # The import receives this mapping, so classifying with it gives the counts the import will report
    canonical_to_col_idx = column_indexes(raw_headers, active_mapping)
    col_idx_to_canonical: Dict[int, str] = {idx: canonical for canonical, idx in canonical_to_col_idx.items()}

    rows_found = 0
    rows_candidate = 0
    rows_ignored = 0
    rows_fuel = 0
    rows_non_fuel = 0
    rows_invalid = 0
    invalid_row_details: List[Dict[str, Any]] = []
    non_fuel_row_details: List[Dict[str, Any]] = []
    validation_warnings: List[str] = _monthly_summary_warnings(mode_info)
    sample_rows: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    rows_price_mismatch = 0

    all_rows = read_table_rows(sheet, header_row_idx)
    date_order = statement_date_order(all_rows, canonical_to_col_idx)
    if date_order == DATE_ORDER_CONFLICT:
        validation_warnings.append(DATE_ORDER_CONFLICT_WARNING)

    total_cols = len(raw_headers)
    for offset, row in enumerate(all_rows, start=1):
        row_idx = header_row_idx + offset
        rows_found += 1
        res = classify_statement_row(row, canonical_to_col_idx, row_idx, total_cols, date_order, assigned_date)
        if res["kind"] == "fuel":
            candidates.append(res["candidate"])
            rows_price_mismatch += int(res["price_mismatch"])
        if res["kind"] == "ignored":
            rows_ignored += 1
            continue
        if res["kind"] == "non_fuel":
            rows_ignored += 1
            rows_non_fuel += 1
            if len(non_fuel_row_details) < MAX_DETAIL_ROWS:
                non_fuel_row_details.append({"source_row": row_idx, "reason": res["reason"]})
            continue

        rows_candidate += 1
        if res["kind"] == "fuel":
            rows_fuel += 1
        else:
            rows_invalid += 1
            if len(invalid_row_details) < MAX_DETAIL_ROWS:
                invalid_row_details.append({"source_row": row_idx, "reason": res["reason"]})

        row_slice = list(row[:total_cols]) if row else []
        while len(row_slice) < total_cols:
            row_slice.append(None)

        if len(sample_rows) < 5:
            sample_record: Dict[str, Any] = {
                "_source_row": row_idx,
            }
            for canonical in CANONICAL_FIELDS:
                sample_record[canonical] = None

            for col_idx, cell_value in enumerate(row_slice):
                canonical = col_idx_to_canonical.get(col_idx)
                if not canonical:
                    continue

                if canonical == "transaction_date":
                    parsed_dt, warn = parse_date_value(cell_value, date_order)
                    sample_record[canonical] = parsed_dt
                    if warn and len(validation_warnings) < 10:
                        validation_warnings.append(f"Row {row_idx}: {warn}")
                elif canonical in {"litres", "unit_price", "total_cost", "odometer_km"}:
                    parsed_num, warn = parse_numeric_value(cell_value)
                    sample_record[canonical] = parsed_num
                    if warn and len(validation_warnings) < 10:
                        validation_warnings.append(f"Row {row_idx} ({canonical}): {warn}")
                else:
                    sample_record[canonical] = str(cell_value).strip() if cell_value is not None else None

            if monthly:
                sample_record["transaction_date"] = assigned_date
                sample_record["date_basis"] = DATE_BASIS_MONTH_END
            sample_rows.append(sample_record)

    if missing_required_fields:
        validation_warnings.insert(
            0,
            f"Missing required columns: {', '.join(missing_required_fields)}. Please map these before final import."
        )

    if rows_candidate == 0:
        validation_warnings.append("No transaction data rows were found below the header row.")

    single_day: Optional[date] = None
    try:
        single_day = apply_single_date_mode(candidates, mode_info, single_date_mode, reporting_month, today)
    except FuelImportError as exc:
        blocking_reasons.append(exc.message)
    if single_day and mode_info.get("mode") == IMPORT_MODE_MONTHLY:
        monthly = True
        assigned_date = mode_info["assigned_date"]
        validation_warnings[:0] = _monthly_summary_warnings(mode_info)
        for sample in sample_rows:
            sample.update(transaction_date=assigned_date, date_basis=DATE_BASIS_MONTH_END)
    single_date_pending = needs_single_date_month(single_day, mode_info, single_date_mode)
    if single_date_pending:
        blocking_reasons.append(single_date_message(single_day))
    summary_table = summary_table_enabled()
    if monthly and summary_table:
        # Stored without a date: the sample shows the reporting month, never a month-end date
        for sample in sample_rows:
            sample.update(transaction_date=None, reporting_month=mode_info.get("reporting_month"))

    identical = _identical_rows(candidates)
    if identical:
        validation_warnings.append(f"{identical} row(s) are identical to another row of this file (same vehicle, date, "
                                   "litres, cost, invoice and odometer). They are kept as separate refuels.")
    if rows_price_mismatch:
        validation_warnings.append(f"{rows_price_mismatch} row(s) have a total cost that differs from litres x unit "
                                   "price; the statement's total cost is kept.")

    confirmation_fields = mapping_confirmation_fields(raw_headers, active_mapping, mapping_details, all_rows,
                                                      canonical_to_col_idx)
    pending_confirmation = [] if mapping_confirmed else confirmation_fields

    # Without a date column the date comes from the reporting month, so ask for that instead of a column
    month_needed = not active_mapping.get("transaction_date") and mode_info.get("mode") is None
    missing_columns = [f for f in missing_required_fields if not (month_needed and f == "transaction_date")]
    if missing_columns:
        blocking_reasons.append(f"Required columns not mapped: {', '.join(missing_columns)}.")
    if month_needed and not str(reporting_month or "").strip():
        blocking_reasons.append("This statement has no transaction date column: select the reporting month."
                                if replace_enabled() else MONTHLY_SUMMARY_DISABLED_MESSAGE)
    if pending_confirmation:
        blocking_reasons.append(f"Confirm the column mapping for: {', '.join(pending_confirmation)}.")
    if not missing_required_fields and not candidates:
        blocking_reasons.append("No valid fuel rows to import.")

    replacement: Optional[Dict[str, Any]] = None
    kept_scopes: List[Dict[str, Any]] = []
    # With FUEL_IMPORT_REPLACE monthly summaries can exist, so every import checks them; dated statements are only
    # planned as replacements with FUEL_IMPORT_REPLACE_DATED (else just the monthly-summary check applies)
    if replace_enabled() and mode_info.get("mode") and candidates and not missing_required_fields and not single_date_pending:
        period = replacement_period(candidates, mode_info, extend_to_full_months)
        try:
            plan = plan_replacement(candidates, period, fetch_visible_rows_in_range(
                *replacement_fetch_window(period), bearer_token=bearer_token), whole_period=extend_to_full_months)
        except FuelDataFetchError:
            blocking_reasons.append("The rows this import would replace could not be read; please retry the preview.")
        else:
            if monthly or dated_replace_enabled():
                replacement = describe_replacement(plan, period, extend_to_full_months and not monthly)
            if plan["conflicts"]:
                blocking_reasons.append(replacement_conflict_message(plan))
            kept_scopes = describe_kept_summaries(plan, candidates)
            if kept_scopes:
                validation_warnings.insert(0, kept_summary_message(kept_scopes))
                if len(plan["skipped"]) == len(candidates):
                    blocking_reasons.append("Every valid row of this file is for a vehicle and month that already has "
                                            "a monthly summary; nothing would be imported.")

    return FuelImportPreviewResponse(
        file_name=file_name,
        worksheet_name=worksheet_name,
        detected_header_row=header_row_idx,
        rows_found=rows_found,
        rows_candidate=rows_candidate,
        rows_ignored=rows_ignored,
        # Without the required columns the import is refused, so no fuel/invalid split is reported
        rows_fuel=None if missing_required_fields else rows_fuel,
        rows_invalid=None if missing_required_fields else rows_invalid,
        rows_non_fuel=rows_non_fuel,
        invalid_row_details=(invalid_row_details or None) if not missing_required_fields else None,
        non_fuel_row_details=non_fuel_row_details or None,
        detected_columns=raw_headers,
        suggested_mapping=suggested_mapping,
        mapping_details=mapping_details,
        unmapped_columns=unmapped_columns,
        missing_required_fields=missing_required_fields,
        validation_warnings=validation_warnings,
        sample_rows=sample_rows,
        active_mapping=active_mapping,
        import_mode=mode_info.get("mode"),
        reporting_month=mode_info.get("reporting_month"),
        assigned_transaction_date=None if summary_table else assigned_date,
        partial_month=bool(mode_info.get("partial_month")),
        monthly_summary_enabled=replace_enabled(),
        monthly_summary_table=summary_table,
        date_column_candidates=date_header_candidates(raw_headers),
        mapping_confirmation_required=confirmation_fields,
        mapping_confirmed=bool(mapping_confirmed),
        rows_identical_in_file=identical,
        rows_price_mismatch=rows_price_mismatch,
        rows_cost_derived=sum(int(c["cost_derived"]) for c in candidates),
        rows_unit_price_derived=sum(int(c["unit_price_derived"]) for c in candidates),
        replacement=replacement,
        single_date=single_day.isoformat() if single_day else None,
        single_date_confirmation_required=single_date_pending,
        single_date_mode=effective_single_date_mode(single_day, single_date_mode),
        rows_skipped_monthly_summary=sum(g["rows_skipped"] for g in kept_scopes),
        monthly_summary_scopes_kept=kept_scopes[:MAX_DETAIL_ROWS],
        ready_to_import=not blocking_reasons,
        blocking_reasons=blocking_reasons,
    )


def _summarize_stored_rows(rows: List[Dict[str, Any]], include_staged: bool = True) -> Dict[str, Any]:
    """
    Distinct source rows stored under one import, extra copies of any source row, and their date range.
    include_staged=False counts only visible rows (staged rows of an uncommitted import are stored but not shown).
    """
    if not include_staged:
        rows = [r for r in rows if r.get("staged") is not True]
    copies: Dict[Any, int] = {}
    days: List[str] = []
    for r in rows:
        copies[r.get("source_row_number")] = copies.get(r.get("source_row_number"), 0) + 1
        if r.get("transaction_date"):
            days.append(str(r["transaction_date"])[:10])
        elif r.get("reporting_month"):
            # fuel_monthly_summaries: the reporting month, never a refuelling day
            first = date.fromisoformat(str(r["reporting_month"])[:10])
            days.extend(d.isoformat() for d in month_bounds(first))
    days.sort()
    repeated = sorted(k for k, n in copies.items() if n > 1 and k is not None)
    return {
        "stored": len(copies),
        "extra_copies": len(rows) - len(copies),
        "repeated_source_rows": repeated,
        "date_from": days[0] if days else None,
        "date_to": days[-1] if days else None,
    }


def _extra_copies_message(stored: Dict[str, Any]) -> str:
    shown = ", ".join(str(r) for r in stored["repeated_source_rows"][:MAX_DETAIL_ROWS])
    return (f"{stored['extra_copies']} extra stored copy(ies) of source row(s) {shown}; review them before removing "
            "any (nothing was deleted).")


def _parse_record_time(value: Any) -> Optional[datetime]:
    """created_at of a fuel_imports row (ISO 8601 with offset, any fraction length) as aware UTC, else None."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?$", str(value or "").strip())
    if not m:
        return None
    offset = (m.group(3) or "+00:00").replace("Z", "+00:00")
    if ":" not in offset:
        offset = offset[:3] + ":" + offset[3:]
    try:
        return datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}{offset}").astimezone(timezone.utc)
    except ValueError:
        return None


def _fetch_rows_of_import(import_id: int, table: str, **kwargs: Any) -> List[Dict[str, Any]]:
    """fetch_import_rows from the table the import stores its rows in (fuel_transactions or SUMMARY_TABLE)."""
    if table == SUMMARY_TABLE:
        kwargs["table"] = SUMMARY_TABLE
    return fetch_import_rows(import_id, **kwargs)


def _finalize_interrupted_import(import_id: int, bearer_token: Optional[str],
                                 deadlines: Optional[ImportDeadlines] = None,
                                 table: str = "fuel_transactions") -> str:
    """
    Give an import that stopped after its record was created a terminal status from what is actually stored
    under it. Returns the client message. Never raises: the original error is what the caller reports.
    """
    try:
        stored: Optional[Dict[str, Any]] = _summarize_stored_rows(_fetch_rows_of_import(
            import_id, table, bearer_token=bearer_token, deadline=deadlines.recount if deadlines else None),
            include_staged=False)
    except Exception as exc:
        logger.error("Could not count rows stored by interrupted import #%s: %s", import_id, exc)
        stored = None
    if stored is None:
        updates: Dict[str, Any] = {
            "status": "failed",
            "error_message": f"{INCOMPLETE_MARKER} interrupted by an internal error; the rows stored before it could "
                             "not be counted. Use Reconcile in import history.",
        }
        message = f"The import stopped after an internal error. Use Reconcile on import #{import_id} to count the rows it stored."
    else:
        note = (f"{INCOMPLETE_MARKER} interrupted by an internal error after {stored['stored']} row(s) were stored. "
                "Re-import the same file to add the rest; rows already stored are skipped as duplicates.")
        updates = {
            "status": "completed_with_errors" if stored["stored"] else "failed",
            "rows_imported": stored["stored"],
            "date_from": stored["date_from"],
            "date_to": stored["date_to"],
            "error_message": note,
        }
        message = f"The import stopped after an internal error (import #{import_id}). {note}"
    updates["completed_at"] = datetime.now(timezone.utc).isoformat()
    try:
        if not update_fuel_import_record(import_id=import_id, updates=updates, bearer_token=bearer_token,
                                         deadline=deadlines.status if deadlines else None):
            message += " Its history record could not be updated; use Reconcile in import history."
    except Exception as exc:
        logger.error("Could not finalize interrupted import #%s: %s", import_id, exc)
        message += " Its history record could not be updated; use Reconcile in import history."
    return message


def _claim_import(import_id: int, bearer_token: Optional[str], deadlines: ImportDeadlines) -> None:
    """
    Proceed only if no other import is running, in any process or instance. Called after this import's
    'processing' record is committed and before the duplicate check reads existing rows. Each import commits its
    record before it looks for others, so of two overlapping imports at least the later reader sees the other and
    stops (both may stop; neither wrote anything and a retry succeeds). Without FUEL_SCHEMA_V2 this is what keeps
    two instances from storing the same rows. 'processing' records older than IMPORT_STALE_SECONDS are ignored as
    interrupted (Reconcile repairs them). Raises FuelImportError (409 busy, 503 unreadable); nothing is written.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=IMPORT_STALE_SECONDS)
    try:
        rows = fetch_running_imports(cutoff.isoformat(), bearer_token, deadline=deadlines.writes)
    except FuelDataFetchError:
        raise FuelImportError("Could not check for other running imports; nothing was imported. Please retry.",
                              status_code=503) from None
    # Filtered again here: the query asks for exactly this, but a looser answer must not block or pass imports.
    others = [r.get("id") for r in rows
              if isinstance(r, dict) and r.get("status") == "processing" and str(r.get("id")) != str(import_id)
              and (_parse_record_time(r.get("created_at")) or cutoff) >= cutoff]
    if others:
        minutes = max(1, IMPORT_STALE_SECONDS // 60)
        raise FuelImportError(f"Another fuel import (#{others[0]}) is still running; nothing was imported. Retry after "
                              f"it finishes (if it was interrupted, Reconcile it after {minutes} minutes).",
                              status_code=409)


def _close_unstarted_import(import_id: int, message: str, bearer_token: Optional[str],
                            deadlines: ImportDeadlines) -> str:
    """Mark an import refused before it wrote any row as failed with `message`. Returns the client message."""
    updated = update_fuel_import_record(
        import_id=import_id,
        updates={"status": "failed", "rows_imported": 0, "rows_duplicate": 0, "rows_failed": 0,
                 "error_message": message, "completed_at": datetime.now(timezone.utc).isoformat()},
        bearer_token=bearer_token,
        deadline=deadlines.status,
    )
    return message if updated else f"{message} History record #{import_id} could not be updated; use Reconcile on it."


def reconcile_fuel_import(import_id: int, bearer_token: Optional[str] = None) -> Dict[str, Any]:
    """
    Recount one import from the rows actually stored under it (import_id) and write the corrected counts and a
    terminal status to its history record. Repairs a record left 'processing' by an interrupted run and counts
    that a lost batch response under-reported. Reads fuel_transactions (and, for a monthly summary with
    FUEL_MONTHLY_SUMMARY_TABLE, fuel_monthly_summaries) and updates only that fuel_imports row: nothing is inserted
    or deleted.

    Idempotent: a repeat gives the same record. An import that was interrupted or left rows unprocessed or
    unverified (INCOMPLETE_MARKER) stays incomplete and is never upgraded to 'completed': the file's rows that are
    not stored can only be added by re-importing it, and its original error text is kept for that.
    """
    try:
        record = get_fuel_import_record(import_id, bearer_token)
    except FuelDataFetchError:
        raise FuelImportError("Import history unavailable; nothing was changed. Please retry.", status_code=503) from None
    if record is None:
        raise FuelImportError(f"Import #{import_id} was not found.", status_code=404)

    interrupted = record.get("status") == "processing"
    if interrupted:
        created = _parse_record_time(record.get("created_at"))
        if created is not None and (datetime.now(timezone.utc) - created).total_seconds() < IMPORT_STALE_SECONDS:
            raise FuelImportError(f"Import #{import_id} may still be running; retry in a few minutes.", status_code=409)

    try:
        rows: List[Dict[str, Any]] = []
        if summary_table_enabled() and record.get("import_mode") == IMPORT_MODE_MONTHLY:
            rows = fetch_import_rows(import_id, bearer_token=bearer_token, table=SUMMARY_TABLE)
        # A monthly summary imported before FUEL_MONTHLY_SUMMARY_TABLE (and not yet copied) has its rows here
        rows = rows or fetch_import_rows(import_id, bearer_token=bearer_token)
        stored = _summarize_stored_rows(rows, include_staged=False)
    except FuelDataFetchError:
        raise FuelImportError("Stored transactions could not be read completely; nothing was changed. Please retry.",
                              status_code=503) from None

    recorded = int(record.get("rows_imported") or 0)
    delta = stored["stored"] - recorded
    rows_failed_recorded = int(record.get("rows_failed") or 0)
    rows_duplicate = int(record.get("rows_duplicate") or 0)
    # Rows stored beyond the history count first account for rows the history left uncounted (unverified or not
    # processed); only the rest can have been wrongly recorded as failed.
    # rows_ignored is null on records from before fuel_imports_ignored_counts.sql; those keep the older estimate.
    uncounted = max(0, int(record.get("rows_found") or 0) - recorded - rows_duplicate - rows_failed_recorded
                    - int(record.get("rows_ignored") or 0))
    rows_failed = max(0, rows_failed_recorded - max(0, delta - uncounted))
    incomplete = interrupted or _is_incomplete(record.get("error_message"))
    # Also true on a repeat: the earlier Reconciled note carries it.
    was_interrupted = interrupted or "interrupted before it finished" in str(record.get("error_message") or "")
    # A repeat that finds nothing new keeps the count shown before the first reconcile, so the record is unchanged.
    shown = recorded
    earlier = re.search(r"Reconciled: \d+ row\(s\) of this import are stored \(history showed (\d+)\)",
                        str(record.get("error_message") or ""))
    if earlier and delta == 0:
        shown = int(earlier.group(1))
    note = f"Reconciled: {stored['stored']} row(s) of this import are stored (history showed {shown})."
    if incomplete:
        note += (f" {INCOMPLETE_MARKER} " + ("the import was interrupted before it finished; " if was_interrupted else "")
                 + "rows of the file not stored here were not imported. Re-import the same file to add them; rows "
                 "already stored are skipped as duplicates.")
    if stored["extra_copies"]:
        note += " " + _extra_copies_message(stored)

    if stored["stored"] == 0 and (incomplete or recorded == 0):
        status = "failed"
    elif incomplete or rows_failed or rows_duplicate or stored["extra_copies"]:
        status = "completed_with_errors"
    else:
        status = "completed"

    # Keep the original error text for audit; a repeated reconcile replaces its own earlier note.
    previous = str(record.get("error_message") or "").split(" | Reconciled:", 1)[0]
    previous = "" if previous.startswith("Reconciled:") else previous
    updates: Dict[str, Any] = {
        "status": status,
        "rows_imported": stored["stored"],
        "rows_failed": rows_failed,
        "error_message": f"{previous} | {note}" if previous else note,
    }
    if stored["stored"]:
        updates.update(date_from=stored["date_from"], date_to=stored["date_to"])
    if interrupted or not record.get("completed_at"):
        updates["completed_at"] = datetime.now(timezone.utc).isoformat()
    if not update_fuel_import_record(import_id=import_id, updates=updates, bearer_token=bearer_token):
        raise FuelImportError("The import history record could not be updated; nothing was changed. Please retry.",
                              status_code=503)
    logger.info("Reconciled fuel import #%s: stored=%d recorded=%d status=%s", import_id, stored["stored"], recorded, status)
    return {**record, **updates}


def _commit_replacement(import_id: int, supersede_ids: List[int], expected_rows: int,
                        bearer_token: Optional[str], deadlines: ImportDeadlines, table: str = "fuel_transactions",
                        supersede_summary_ids: Optional[List[int]] = None) -> Tuple[str, int, Optional[str]]:
    """
    Run commit_fuel_import. Returns (state, rows superseded, note): 'committed', 'not_committed' (refused: nothing
    changed, the new rows stay hidden) or 'unknown' (sent, no answer, and reading the import back could not settle
    it). An unknown outcome is settled from the import's own rows: the commit is one transaction, so they are either
    all still staged or all visible. `table` holds the import's rows; supersede_summary_ids are
    fuel_monthly_summaries ids (FUEL_MONTHLY_SUMMARY_TABLE only).
    """
    unchanged = ("the new rows stay hidden and the dashboard still shows the previously imported data. "
                 "Preview and import the file again.")
    summary_ids = list(supersede_summary_ids or [])
    try:
        result = commit_fuel_import(import_id, supersede_ids, expected_rows, bearer_token, deadline=deadlines.recount,
                                    **({"supersede_summary_ids": summary_ids} if summary_table_enabled() else {}))
    except FuelCommitError as exc:
        return "not_committed", 0, f"Not committed ({exc}): {unchanged}"
    if result is not None:
        return "committed", result["superseded"], None
    if expected_rows:
        try:
            rows = _fetch_rows_of_import(import_id, table, bearer_token=bearer_token, deadline=deadlines.recount)
        except FuelDataFetchError:
            rows = None
        if rows is not None and len(rows) == expected_rows:
            if not any(r.get("staged") is True for r in rows):
                return ("committed", len(supersede_ids) + len(summary_ids),
                        "Commit outcome verified by reading the import back.")
            if all(r.get("staged") is True for r in rows):
                return "not_committed", 0, f"Not committed (no answer from the database): {unchanged}"
    return "unknown", 0, (f"{INCOMPLETE_MARKER} the commit outcome is unknown; the import is either fully visible or "
                          "fully hidden. Use Reconcile in import history to find out.")


def execute_fuel_import(
    file_bytes: bytes,
    file_name: str,
    confirmed_mapping: Dict[str, str],
    imported_by: str,
    bearer_token: Optional[str] = None,
    request_started: Optional[float] = None,
    reporting_month: Optional[str] = None,
    mapping_confirmed: bool = False,
    extend_to_full_months: bool = False,
    expected_replace_count: Optional[int] = None,
    single_date_mode: Optional[str] = None,
) -> FuelImportResultResponse:
    """
    Run one import at a time, so a concurrent import of the same rows sees the first one's inserts.
    `request_started` (time.perf_counter() at request entry) anchors the time budget; the lock wait counts against it.
    """
    deadlines = ImportDeadlines(time.perf_counter() if request_started is None else request_started)
    wait = min(IMPORT_LOCK_WAIT_SECONDS, deadlines.writes - IMPORT_MIN_WORK_SECONDS - time.perf_counter())
    if wait < 0 or not _IMPORT_LOCK.acquire(timeout=wait):
        raise FuelImportError("Another fuel import is still running; nothing was imported. Please retry.", status_code=409)
    try:
        return _execute_fuel_import_locked(file_bytes, file_name, confirmed_mapping, imported_by, bearer_token,
                                           deadlines=deadlines, reporting_month=reporting_month,
                                           mapping_confirmed=mapping_confirmed,
                                           extend_to_full_months=extend_to_full_months,
                                           expected_replace_count=expected_replace_count,
                                           single_date_mode=single_date_mode)
    finally:
        _IMPORT_LOCK.release()


def _execute_fuel_import_locked(
    file_bytes: bytes,
    file_name: str,
    confirmed_mapping: Dict[str, str],
    imported_by: str,
    bearer_token: Optional[str] = None,
    deadlines: Optional[ImportDeadlines] = None,
    reporting_month: Optional[str] = None,
    mapping_confirmed: bool = False,
    extend_to_full_months: bool = False,
    expected_replace_count: Optional[int] = None,
    single_date_mode: Optional[str] = None,
) -> FuelImportResultResponse:
    """
    Execute full fuel import:
    1. Parse workbook & detect headers.
    2. Validate confirmed mapping against actual headers, and the format (dated / monthly summary).
    3. Validate candidate rows and parse values. A dated statement whose rows all share one date needs the user's
       single_date_mode (apply_single_date_mode); it is never guessed.
    4. Create initial 'processing' record in fuel_imports and claim the import (see _claim_import).
    5. Without FUEL_IMPORT_REPLACE: skip rows already stored (duplicate check against the database).
       With it: plan the replacement (plan_replacement) - the visible rows of the file's vehicles in its period -
       and skip the rows of vehicle-months whose stored monthly summary is kept (also on the legacy dated path).
    6. Batch insert valid records into fuel_transactions (staged, i.e. hidden, with FUEL_IMPORT_REPLACE).
    7. With FUEL_IMPORT_REPLACE, only when every new row is stored: commit_fuel_import makes them visible and
       supersedes the replaced rows in one database transaction. Otherwise nothing visible changes.
    8. Update fuel_imports with final statistics and status.
    Once the record exists, every exit leaves it in a terminal status (see _finalize_interrupted_import).
    Every database call is bounded by `deadlines` (see ImportDeadlines).
    Identical rows of one file are all imported (repeated refuels); they are never collapsed.
    """
    deadlines = deadlines or ImportDeadlines(time.perf_counter())
    # 1. Load Excel
    lower_name = file_name.lower()
    if not (lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm") or lower_name.endswith(".xltx")):
        raise FuelImportError("Unsupported file type. Please upload a modern Excel file (.xlsx).", status_code=400)

    wb = load_fuel_workbook(file_bytes, "Fuel import")

    if not wb.sheetnames:
        raise FuelImportError("Excel workbook contains no worksheets.", status_code=422)

    sheet = wb.active or wb[wb.sheetnames[0]]
    header_row_idx, raw_headers = detect_header_row(sheet)

    # 2. Validate confirmed mapping and the import format
    month_given = bool(str(reporting_month or "").strip())
    valid_mapping = validate_confirmed_mapping(raw_headers, confirmed_mapping, date_assigned=month_given)
    # With a date column a reporting month only applies to a confirmed single-date summary (apply_single_date_mode)
    mode_info = resolve_import_mode(valid_mapping, None if valid_mapping.get("transaction_date") else reporting_month)
    missing_prices = missing_price_fields(valid_mapping)
    if missing_prices:
        raise FuelImportError(
            "Missing required mapping for 'total_cost': a statement without a transaction date column needs the total "
            "cost (AED)." if missing_prices == ["total_cost"] else
            "Missing required mapping for 'unit_price': a dated statement needs the unit price per litre or the "
            "total cost.", status_code=422)
    monthly = mode_info["mode"] == IMPORT_MODE_MONTHLY
    assigned_date = mode_info["assigned_date"] if monthly else None
    # Monthly summaries always replace (staged, then committed); dated statements only with FUEL_IMPORT_REPLACE_DATED
    replace = replace_enabled() and (monthly or dated_replace_enabled())

    # Map column index -> canonical field
    canonical_to_col_idx = column_indexes(raw_headers, valid_mapping)

    # 3. Read table rows
    all_rows = read_table_rows(sheet, header_row_idx)
    full_mapping = {k: valid_mapping.get(k) for k in CANONICAL_FIELDS}
    unconfirmed = mapping_confirmation_fields(raw_headers, full_mapping, map_headers(raw_headers)[1], all_rows,
                                              canonical_to_col_idx)
    if unconfirmed and not mapping_confirmed:
        raise FuelImportError(f"The column mapping for {', '.join(unconfirmed)} is ambiguous and must be confirmed in "
                              "the preview before importing; nothing was imported.", status_code=422)
    date_order = statement_date_order(all_rows, canonical_to_col_idx)

    total_rows_found = len(all_rows)
    total_cols = len(raw_headers)

    # 4. Pass 1: parse and validate every row (no database access yet)
    rows_ignored = 0
    rows_non_fuel = 0
    rows_failed = 0
    rows_date_ambiguous = 0
    rows_optional_parse_failed = 0
    rows_price_mismatch = 0
    candidates: List[Dict[str, Any]] = []
    failed_row_details: List[Dict[str, Any]] = []
    ignored_row_details: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def add_warning(message: str) -> None:
        if len(warnings) < MAX_IMPORT_WARNINGS:
            warnings.append(message)

    if date_order == DATE_ORDER_CONFLICT:
        add_warning(DATE_ORDER_CONFLICT_WARNING)

    def fail_row(source_row: int, reason: str) -> None:
        nonlocal rows_failed
        rows_failed += 1
        failed_row_details.append({"source_row": source_row, "reason": reason})

    for offset, row in enumerate(all_rows, start=1):
        row_idx = header_row_idx + offset
        res = classify_statement_row(row, canonical_to_col_idx, row_idx, total_cols, date_order, assigned_date)
        kind = res["kind"]
        if kind == "ignored":
            rows_ignored += 1
            continue
        if kind == "non_fuel":
            rows_ignored += 1
            rows_non_fuel += 1
            if len(ignored_row_details) < MAX_DETAIL_ROWS:
                ignored_row_details.append({"source_row": row_idx, "reason": res["reason"]})
            continue
        rows_date_ambiguous += int(res["date_ambiguous"])
        rows_optional_parse_failed += int(res["optional_parse_failed"])
        for message in res["warnings"]:
            add_warning(message)
        if kind == "failed":
            fail_row(row_idx, res["reason"])
            continue
        rows_price_mismatch += int(res["price_mismatch"])
        candidates.append(res["candidate"])

    single_day = apply_single_date_mode(candidates, mode_info, single_date_mode, reporting_month)
    if needs_single_date_month(single_day, mode_info, single_date_mode):
        raise FuelImportError(f"{single_date_message(single_day)} Nothing was imported.", status_code=422)
    monthly = mode_info["mode"] == IMPORT_MODE_MONTHLY
    assigned_date = mode_info["assigned_date"] if monthly else None
    replace = replace_enabled() and (monthly or dated_replace_enabled())
    # With FUEL_MONTHLY_SUMMARY_TABLE a monthly summary is stored in fuel_monthly_summaries, without a date
    stored_table = SUMMARY_TABLE if monthly and summary_table_enabled() else "fuel_transactions"

    for message in _monthly_summary_warnings(mode_info):
        add_warning(message)
    identical = _identical_rows(candidates)
    if identical:
        add_warning(f"{identical} row(s) are identical to another row of this file; all were kept as separate refuels.")
    period = replacement_period(candidates, mode_info, extend_to_full_months) if replace and candidates else None

    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    v2 = schema_v2_enabled()
    if v2:
        try:
            earlier = find_import_by_sha256(file_sha256, bearer_token, deadline=deadlines.writes)
        except FuelDataFetchError:
            raise FuelImportError("Duplicate-file check unavailable; nothing was imported. Please retry.", status_code=503) from None
        if earlier is not None:
            raise FuelImportError(f"Identical file already imported as #{earlier}; nothing was imported.", status_code=409)

    # 5. Create initial fuel_imports record, unless the time budget is already used up (nothing written yet)
    if time.perf_counter() >= deadlines.writes:
        raise FuelImportError("The request time limit was reached before any row was written; nothing was imported. "
                              "Please retry, or split the statement into smaller files.", status_code=503)
    import_id = create_fuel_import_record(
        file_name=file_name,
        imported_by=imported_by,
        rows_found=total_rows_found,
        bearer_token=bearer_token,
        deadline=deadlines.writes,
        **({"file_sha256": file_sha256} if v2 else {}),
        **({"rows_ignored": rows_ignored, "rows_non_fuel": rows_non_fuel} if ignored_counts_enabled() else {}),
        replace_fields={
            "import_mode": mode_info["mode"],
            "reporting_month": mode_info["month_from"].isoformat() if monthly else None,
            "replace_scope": ({"period_from": period[0].isoformat(), "period_to": period[1].isoformat(),
                               "extended_to_full_months": bool(extend_to_full_months and not monthly),
                               # fuel_commit_import_v2 bounds what may be superseded by the file's vehicles
                               **({"vehicle_keys": sorted({replacement_vehicle_key(c["fields"]["vehicle_identifier"])
                                                           for c in candidates})} if summary_table_enabled() else {})}
                              if period else None),
        },
    )

    # 6. Claim the import across instances, then fetch existing fingerprints for the file's date window (±1 day).
    # Both run after the record exists, so the duplicate check sees every row of an import that finished before
    # the claim. If either fails, the import stops before writing anything: a partial set lets duplicates through.
    check_from: Optional[str] = None
    check_to: Optional[str] = None
    existing_fingerprints: Set[str] = set()
    plan: Optional[Dict[str, Any]] = None
    summary_plan: Optional[Dict[str, Any]] = None  # legacy dated path: only its monthly-summary precedence is used
    try:
        _claim_import(import_id, bearer_token, deadlines)
        # Read after the claim, so no other import can change these rows before this one finishes
        check_period = period or (replacement_period(candidates, mode_info) if replace_enabled() and candidates else None)
        if check_period:
            try:
                scoped = plan_replacement(candidates, check_period, fetch_visible_rows_in_range(
                    *replacement_fetch_window(check_period), bearer_token=bearer_token, deadline=deadlines.writes),
                    whole_period=extend_to_full_months and replace)
            except FuelDataFetchError:
                raise FuelImportError("The rows this import replaces could not be read completely; nothing was "
                                      "imported. Please retry.", status_code=503) from None
            if scoped["conflicts"]:
                raise FuelImportError(replacement_conflict_message(scoped), status_code=409)
            if len(scoped["skipped"]) == len(candidates):
                raise FuelImportError(f"{kept_summary_message(describe_kept_summaries(scoped, candidates))} "
                                      "Nothing was imported.", status_code=409)
            if replace:
                plan = scoped
            else:
                summary_plan = scoped
        if plan and expected_replace_count is not None and int(expected_replace_count) != len(plan["supersede"]):
            raise FuelImportError(
                f"The stored data changed since the preview ({len(plan['supersede'])} row(s) would now be "
                f"replaced, the preview showed {int(expected_replace_count)}). Nothing was imported; preview the "
                "file again.", status_code=409)
        if candidates and not replace:
            candidate_days = [c["fields"]["transaction_date"][:10] for c in candidates]
            check_from = (date.fromisoformat(min(candidate_days)) - timedelta(days=1)).isoformat()
            check_to = (date.fromisoformat(max(candidate_days)) + timedelta(days=1)).isoformat()
            try:
                existing_fingerprints = fetch_existing_transaction_fingerprints(
                    date_from=check_from,
                    date_to=check_to,
                    bearer_token=bearer_token,
                    deadline=deadlines.writes,
                )
            except FuelDataFetchError:
                raise FuelImportError(
                    "Duplicate check unavailable: existing transactions could not be read completely. "
                    "Nothing was imported; please retry.",
                    status_code=503,
                ) from None
    except FuelImportError as exc:
        raise FuelImportError(_close_unstarted_import(import_id, exc.message, bearer_token, deadlines),
                              status_code=exc.status_code) from None

    stored_value_shapes = getattr(existing_fingerprints, "stored_value_shapes", None)
    offsets_seen = sorted(k for k in (stored_value_shapes or {}) if k.startswith("offset:"))
    if len(offsets_seen) > 1:
        add_warning(
            "Stored transaction dates carry different UTC offsets "
            f"({', '.join(o.split(':', 1)[1] for o in offsets_seen)}); wall-clock duplicate comparison "
            "across them is unreliable."
        )

    try:
        # 7. Pass 2: rows already stored are not stored again - an identical visible row in the replacement scope
        # (FUEL_IMPORT_REPLACE), else a fingerprint match in the database. Identical rows within the file are all
        # kept: they are repeated refuels, and re-import protection comes from the database side.
        existing_weak = _build_weak_index(existing_fingerprints)
        retained_for: Dict[int, int] = plan["retained_for"] if plan else {}
        skipped: Set[int] = (plan or summary_plan or {}).get("skipped", set())
        kept_scopes = describe_kept_summaries(plan or summary_plan, candidates) if skipped else []
        file_copies: Dict[str, int] = defaultdict(int)
        rows_unchanged = 0
        rows_skipped_monthly = 0
        rows_duplicate = 0
        rows_cost_derived = 0
        rows_unit_price_derived = 0
        duplicate_row_details: List[Dict[str, Any]] = []
        purchases: List[Dict[str, Any]] = []
        refunds: List[Dict[str, Any]] = []
        parsed_dates: List[str] = []

        for i, cand in enumerate(candidates):
            fp = cand["fingerprint"]
            source_row = cand["fields"]["source_row_number"]
            if i in skipped:
                rows_skipped_monthly += 1  # the stored monthly summary of this vehicle-month is kept
                continue
            if i in retained_for:
                dup_reason = f"Identical to stored transaction #{retained_for[i]}, which is kept as is"
                rows_unchanged += 1
            elif not replace and _is_duplicate(fp, existing_fingerprints, existing_weak):
                dup_reason = "Matches an existing transaction"
            else:
                dup_reason = None
            if dup_reason:
                rows_duplicate += 1
                if len(duplicate_row_details) < MAX_DETAIL_ROWS:
                    duplicate_row_details.append({"source_row": source_row, "reason": dup_reason})
                continue

            file_copies[fp] += 1
            rows_cost_derived += int(cand["cost_derived"])
            rows_unit_price_derived += int(cand["unit_price_derived"])
            parsed_dates.append(cand["fields"]["transaction_date"][:10])

            tx = {"import_id": import_id, **cand["fields"]}
            if replace:
                tx.update({"date_basis": cand["date_basis"], "staged": True})
            if stored_table == SUMMARY_TABLE:
                tx = summary_insert_row(tx, mode_info["month_from"])
            elif v2:
                # The unique fingerprint index must accept repeated refuels (n-th copy suffixed) and, with
                # replacement, rows equal to ones a later import superseded (suffixed with this import's row).
                if replace:
                    stored_fp = f"{fp}|imp:{import_id}:{source_row}"
                else:
                    stored_fp = fp if file_copies[fp] == 1 else f"{fp}|rep:{file_copies[fp]}"
                tx.update({
                    "fingerprint": stored_fp,
                    "transaction_kind": "refund_adjustment" if tx["litres"] < 0 else "purchase",
                    "cost_derived": bool(cand["cost_derived"]),
                    "unit_price_derived": bool(cand["unit_price_derived"]),
                })
            (refunds if tx["litres"] < 0 else purchases).append(tx)

        # 8. Insert batches into Supabase. Refunds go in their own request so that a database
        # constraint on negative litres (unverified) can only reject the refunds. Each group is counted from its
        # own outcome, so every row lands in exactly one bucket: stored, skipped as duplicate by the database,
        # failed (known not stored), unverified (sent, outcome unknown) or not processed (never sent).
        rows_imported = 0
        rows_refund = 0
        rows_unverified = 0
        rows_not_processed = 0
        db_ignored = 0
        uncertain_batches = 0
        batch_errors: List[str] = []
        refund_rejection_details: List[Dict[str, Any]] = []
        for group, is_refund in ((purchases, False), (refunds, True)):
            if not group:
                continue
            part: Dict[str, Any] = {}
            batch_size = len(group) if is_refund else 100
            if v2 and not replace:
                # The unique fingerprint index skips rows already stored, including by a concurrent import.
                inserted, ignored, errs = insert_fuel_transactions_ignore_duplicates(
                    group, batch_size=batch_size, bearer_token=bearer_token, deadline=deadlines.writes, outcome=part,
                )
            elif stored_table == SUMMARY_TABLE:
                inserted, errs = insert_monthly_summaries_batch(
                    group, batch_size=batch_size, bearer_token=bearer_token, deadline=deadlines.writes, outcome=part,
                )
                ignored = 0
            else:
                inserted, errs = insert_fuel_transactions_batch(
                    transactions=group, batch_size=batch_size, bearer_token=bearer_token,
                    deadline=deadlines.writes, outcome=part,
                )
                ignored = 0
            unverified = part.get("unverified_rows", 0)
            not_sent = part.get("not_attempted_rows", 0)
            failed = max(0, len(group) - inserted - ignored - unverified - not_sent)
            rows_imported += inserted
            rows_duplicate += ignored
            db_ignored += ignored
            rows_failed += failed
            rows_unverified += unverified
            rows_not_processed += not_sent
            uncertain_batches += part.get("uncertain_batches", 0)
            if ignored and len(duplicate_row_details) < MAX_DETAIL_ROWS:
                duplicate_row_details.append({"source_row": None, "reason": f"{ignored} row(s) already stored (database fingerprint index)"})
            if not is_refund:
                batch_errors.extend(errs)
                continue
            rows_refund = inserted
            batch_errors.extend(f"Refund adjustments: {e}" for e in errs)
            # Only refund rows that are known not stored are failed rows; without row-level detail from the
            # insert, a wholly failed group names its rows and a partial one is reported as a count.
            failed_rows = part.get("failed_source_rows")
            if failed_rows is None and failed:
                failed_rows = ([(tx["source_row_number"], "") for tx in group] if failed == len(group)
                               else [(None, f"{failed} refund adjustment row(s) not stored")])
            for source_row, reason in failed_rows or []:
                if not reason or reason.startswith("rejected by the database"):
                    reason = "Database rejected refund adjustment (negative litres); the table may not allow negative values"
                elif source_row is not None:
                    reason = f"Refund adjustment {reason}"
                refund_rejection_details.append({"source_row": source_row, "reason": reason})

        # 8b. A batch whose response was lost was settled by reading it back; re-count the whole import from the
        # database so the totals are what is actually stored.
        if uncertain_batches or rows_unverified:
            try:
                stored = _summarize_stored_rows(_fetch_rows_of_import(import_id, stored_table, bearer_token=bearer_token,
                                                                      deadline=deadlines.recount))
            except FuelDataFetchError:
                add_warning(f"Rows stored by import #{import_id} could not be re-counted after uncertain batch "
                            "results; use Reconcile in import history to verify the counts.")
            else:
                delta = stored["stored"] - rows_imported
                rows_imported = stored["stored"]
                # Rows found beyond those counted settle unverified rows first, then rows the fingerprint index
                # skipped because an earlier attempt of theirs committed late; anything left was counted failed.
                extra = max(0, delta)
                settled = min(extra, rows_unverified)
                rows_unverified -= settled
                extra -= settled
                settled = min(extra, db_ignored)
                rows_duplicate -= settled
                extra -= settled
                rows_failed = max(0, rows_failed - extra) + max(0, -delta)
                if delta:
                    batch_errors.append(f"Verified against the database: {stored['stored']} row(s) of this import are stored.")
                if stored["extra_copies"]:
                    batch_errors.append(_extra_copies_message(stored))

        # 8c. Latest import wins: commit only when every new row is stored. The commit makes them visible and
        # supersedes the replaced rows in one transaction, so the visible data is either the old set or the new one.
        rows_replaced = 0
        commit_state: Optional[str] = None
        if replace:
            to_store = len(purchases) + len(refunds)
            supersede_ids, supersede_summary_ids = split_supersede_ids((plan or {}).get("supersede", []))
            if rows_imported != to_store or rows_unverified or rows_not_processed:
                commit_state = "not_committed"
                batch_errors.insert(0, "Not committed: not every new row was stored, so the new rows stay hidden and "
                                       "the dashboard still shows the previously imported data. Import the file again.")
            elif to_store or supersede_ids or supersede_summary_ids:
                commit_state, rows_replaced, note = _commit_replacement(import_id, supersede_ids, to_store,
                                                                        bearer_token, deadlines, stored_table,
                                                                        supersede_summary_ids)
                if note:
                    batch_errors.insert(0, note)
            elif rows_unchanged:
                commit_state = "committed"  # every valid row is already stored unchanged: nothing to change
            if commit_state is None:
                pass  # no valid rows: nothing was written
            elif commit_state == "unknown":
                rows_unverified += rows_imported
                rows_imported = 0
            elif commit_state != "committed":
                # Stored but hidden (staged): nothing of this import is visible
                rows_failed += rows_imported + rows_unverified
                rows_imported = rows_unverified = 0
                parsed_dates = []
            else:
                clear_vehicle_identifier_cache()
    except Exception as exc:
        logger.error("Fuel import #%s stopped after an internal error: %s", import_id, exc, exc_info=True)
        raise FuelImportError(_finalize_interrupted_import(import_id, bearer_token, deadlines, stored_table),
                              status_code=500) from None

    # 9. Date ranges and final status. Rows never sent or with an unknown outcome make the import incomplete: its
    # status cannot be 'completed', and the marker keeps Reconcile from upgrading it later.
    date_from: Optional[str] = min(parsed_dates) if parsed_dates else None
    date_to: Optional[str] = max(parsed_dates) if parsed_dates else None
    if stored_table == SUMMARY_TABLE and parsed_dates:
        # A monthly summary covers its reporting month; it has no refuelling dates
        date_from, date_to = mode_info["month_from"].isoformat(), mode_info["month_to"].isoformat()

    if rows_not_processed or rows_unverified:
        open_rows = []
        if rows_not_processed:
            open_rows.append(f"{rows_not_processed} row(s) were not processed (not sent before the time limit)")
        if rows_unverified:
            open_rows.append(f"{rows_unverified} row(s) have an unknown write outcome and may be stored")
        batch_errors.insert(0, f"{INCOMPLETE_MARKER} {'; '.join(open_rows)}. Use Reconcile in import history to count "
                               "the stored rows, then re-import the same file to add the missing ones.")

    # Every row of the sheet ends in exactly one bucket; a gap means a counting bug, so it is reported, not hidden.
    accounted = (rows_ignored + rows_imported + rows_duplicate + rows_failed + rows_unverified + rows_not_processed
                 + rows_skipped_monthly)
    if accounted != total_rows_found:
        logger.error("Fuel import #%s row counts do not add up: %d accounted of %d found", import_id, accounted,
                     total_rows_found)
        batch_errors.append(f"Row counts do not add up ({accounted} accounted of {total_rows_found} found); "
                            "use Reconcile in import history to verify the stored rows.")

    error_message: Optional[str] = None
    if batch_errors:
        error_message = "; ".join(batch_errors)

    if rows_imported == 0 and not rows_unverified and total_rows_found > 0 and commit_state != "committed":
        final_status = "failed"
        if not error_message:
            error_message = "No valid transactions were imported."
    elif rows_failed > 0 or rows_duplicate - rows_unchanged > 0 or batch_errors:
        final_status = "completed_with_errors"
    else:
        final_status = "completed"

    # Rows skipped for a kept monthly summary follow the import rule, so they do not change the status; the note is
    # stored in the import history for audit
    kept_note = kept_summary_message(kept_scopes)
    if kept_note:
        add_warning(kept_note)
        error_message = f"{error_message}; {kept_note}" if error_message else kept_note

    completed_at = datetime.now(timezone.utc).isoformat()

    # 10. Update fuel_imports record
    record_updated = update_fuel_import_record(
        import_id=import_id,
        updates={
            "status": final_status,
            "rows_imported": rows_imported,
            "rows_duplicate": rows_duplicate,
            "rows_failed": rows_failed,
            "date_from": date_from,
            "date_to": date_to,
            "error_message": error_message,
            "completed_at": completed_at,
            **({"rows_replaced": rows_replaced, "rows_unchanged": rows_unchanged} if replace else {}),
        },
        bearer_token=bearer_token,
        deadline=deadlines.status,
    )
    if not record_updated:
        add_warning(f"Import history record #{import_id} could not be updated; it may still show 'processing'.")

    return FuelImportResultResponse(
        import_id=import_id,
        file_name=file_name,
        status=final_status,
        rows_found=total_rows_found,
        rows_imported=rows_imported,
        rows_duplicate=rows_duplicate,
        rows_failed=rows_failed,
        rows_unverified=rows_unverified,
        rows_not_processed=rows_not_processed,
        rows_ignored=rows_ignored,
        rows_non_fuel=rows_non_fuel,
        ignored_row_details=ignored_row_details or None,
        date_from=date_from,
        date_to=date_to,
        error_message=error_message,
        # Refund rejections first so the cap cannot hide them behind parse failures
        failed_row_details=(refund_rejection_details + failed_row_details)[:MAX_DETAIL_ROWS] or None,
        rows_refund=rows_refund,
        rows_cost_derived=rows_cost_derived,
        rows_unit_price_derived=rows_unit_price_derived,
        rows_date_ambiguous=rows_date_ambiguous,
        rows_optional_parse_failed=rows_optional_parse_failed,
        warnings=warnings,
        duplicate_row_details=duplicate_row_details or None,
        file_sha256=file_sha256,
        duplicate_check_scope={
            "date_from": check_from,
            "date_to": check_to,
            "existing_rows_scanned": getattr(existing_fingerprints, "rows_scanned", None),
            "stored_value_shapes": stored_value_shapes,
            "timestamp_note": TIMESTAMP_COMPARISON_NOTE,
        },
        import_mode=mode_info["mode"],
        reporting_month=mode_info["reporting_month"],
        assigned_transaction_date=None if stored_table == SUMMARY_TABLE else assigned_date,
        monthly_summary_table=stored_table == SUMMARY_TABLE,
        rows_price_mismatch=rows_price_mismatch,
        rows_identical_in_file=identical,
        replacement_enabled=replace,
        committed=(commit_state == "committed") if replace else None,
        rows_replaced=rows_replaced,
        rows_unchanged=rows_unchanged,
        replacement_period=({"period_from": period[0].isoformat(), "period_to": period[1].isoformat()}
                            if period else None),
        rows_skipped_monthly_summary=rows_skipped_monthly,
        monthly_summary_scopes_kept=kept_scopes[:MAX_DETAIL_ROWS],
        single_date=single_day.isoformat() if single_day else None,
    )
