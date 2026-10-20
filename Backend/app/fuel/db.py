"""Supabase PostgREST database service for Fuel Management operations."""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
import requests

from app.utils.datetime_helpers import parse_tx_wallclock

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000


class FuelDataFetchError(RuntimeError):
    """Raised when fuel transactions cannot be read completely; callers must not compute on partial data."""


class FingerprintSet(set):
    """Set of existing fingerprints, plus the shapes of the stored transaction_date values seen."""

    def __init__(self, *args, stored_value_shapes: Optional[Dict[str, int]] = None, rows_scanned: int = 0):
        super().__init__(*args)
        self.stored_value_shapes: Dict[str, int] = stored_value_shapes or {}
        self.rows_scanned = rows_scanned


_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}(?::?\d{2})?)$", re.IGNORECASE)


def describe_stored_date_shape(value: Any) -> str:
    """
    Classify a stored transaction_date as 'date_only', 'naive', 'offset:<offset>' or 'unparseable'.
    Evidence only for the (unverified) DB column type; never used to convert values.
    """
    s = str(value or "").strip()
    if not s or parse_tx_wallclock(s) is None:
        return "unparseable"
    if len(s) == 10:
        return "date_only"
    m = _OFFSET_RE.search(s)
    if m:
        off = m.group(1).upper()
        return "offset:+00:00" if off == "Z" else f"offset:{off}"
    return "naive"


def _get_supabase_config() -> Tuple[str, str]:
    """Retrieve Supabase URL and key from environment variables."""
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
    return url, key


def get_headers(bearer_token: Optional[str] = None) -> Dict[str, str]:
    """Build authenticated PostgREST headers without exposing secrets."""
    url, key = _get_supabase_config()
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def get_supabase_rest_url(endpoint: str) -> str:
    """Return the absolute PostgREST endpoint URL."""
    url, _ = _get_supabase_config()
    if not url:
        raise RuntimeError("SUPABASE_URL is not configured in the environment.")
    return f"{url}/rest/v1/{endpoint.lstrip('/')}"


def schema_v2_enabled() -> bool:
    """
    FUEL_SCHEMA_V2=1 enables the optional columns from Backend/sql/proposed_fuel_dedup.sql
    (fuel_transactions.fingerprint/transaction_kind/cost_derived/unit_price_derived and
    fuel_imports.file_sha256). Set it only after that SQL has been reviewed and applied; with it unset
    every payload keeps exactly the pre-existing keys (PostgREST rejects unknown columns).
    """
    return os.getenv("FUEL_SCHEMA_V2", "").strip() == "1"


V2_TRANSACTION_COLUMNS = ("fingerprint", "transaction_kind", "cost_derived", "unit_price_derived")


def find_import_by_sha256(file_sha256: str, bearer_token: Optional[str] = None) -> Optional[int]:
    """Id of an earlier import of the identical file (FUEL_SCHEMA_V2 only), else None. Read-only."""
    url = get_supabase_rest_url("fuel_imports")
    params = {"select": "id", "file_sha256": f"eq.{file_sha256}", "order": "id.asc", "limit": "1"}
    try:
        response = requests.get(url, params=params, headers=_read_headers(bearer_token), timeout=15)
    except requests.RequestException as exc:
        raise FuelDataFetchError(f"Network failure checking file hash: {exc}") from None
    if not response.ok:
        raise FuelDataFetchError(f"Error checking file hash: HTTP {response.status_code}")
    rows = response.json() or []
    return int(rows[0]["id"]) if rows else None


def create_fuel_import_record(
    file_name: str,
    imported_by: str,
    rows_found: int,
    bearer_token: Optional[str] = None,
    file_sha256: Optional[str] = None,
) -> int:
    """
    Create a new record in fuel_imports with status 'processing'.
    Returns the newly created import ID.
    """
    url = get_supabase_rest_url("fuel_imports")
    clean_imported_by = None
    if imported_by:
        try:
            import uuid
            clean_imported_by = str(uuid.UUID(str(imported_by)))
        except Exception:
            clean_imported_by = None

    payload = {
        "file_name": file_name,
        "status": "processing",
        "rows_found": rows_found,
        "rows_imported": 0,
        "rows_duplicate": 0,
        "rows_failed": 0,
    }
    if clean_imported_by:
        payload["imported_by"] = clean_imported_by
    if file_sha256 and schema_v2_enabled():
        payload["file_sha256"] = file_sha256

    headers = get_headers(bearer_token)
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as exc:
        logger.error("Failed to connect to Supabase fuel_imports endpoint: %s", exc)
        raise RuntimeError("Database service is currently unavailable.") from None

    if not response.ok:
        logger.error("Supabase insert error on fuel_imports: %s - %s", response.status_code, response.text)
        raise RuntimeError(f"Database error ({response.status_code}) while creating import record.")

    try:
        created_records = response.json()
        if isinstance(created_records, list) and created_records:
            return created_records[0]["id"]
        elif isinstance(created_records, dict) and "id" in created_records:
            return created_records["id"]
        raise ValueError("Invalid response payload from database")
    except Exception as exc:
        logger.error("Failed to parse response from fuel_imports insert: %s", exc)
        raise RuntimeError("Failed to obtain ID for the new fuel import record.") from None


def update_fuel_import_record(
    import_id: int,
    updates: Dict[str, Any],
    bearer_token: Optional[str] = None,
) -> bool:
    """Update an existing fuel_imports record by ID."""
    url = get_supabase_rest_url("fuel_imports")
    headers = get_headers(bearer_token)
    params = {"id": f"eq.{import_id}"}

    try:
        response = requests.patch(url, params=params, json=updates, headers=headers, timeout=15)
    except requests.RequestException as exc:
        logger.error("Failed to connect to Supabase fuel_imports update endpoint: %s", exc)
        return False

    if not response.ok:
        logger.error("Supabase update error on fuel_imports: %s - %s", response.status_code, response.text)
        return False

    return True


def fetch_existing_transaction_fingerprints(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    bearer_token: Optional[str] = None,
) -> Set[str]:
    """
    Fetch fingerprints of ALL existing fuel transactions in [date_from, date_to] (inclusive days).
    Pages through PostgREST in PAGE_SIZE chunks ordered by id so no rows are skipped.
    Raises FuelDataFetchError on any failure: an incomplete set would let duplicates through.
    Returns a FingerprintSet (a set) that also reports the stored date shapes it observed.
    """
    url = get_supabase_rest_url("fuel_transactions")
    headers = _read_headers(bearer_token)
    params: Dict[str, str] = {
        "select": "id,transaction_date,vehicle_identifier,litres,total_cost,invoice_number,odometer_km",
        "order": "id.asc",
        "limit": str(PAGE_SIZE),
    }
    df = normalize_date_filter(date_from, is_end=False)
    dt = normalize_date_filter(date_to, is_end=True)
    if df and dt:
        params["and"] = f"(transaction_date.gte.{df},transaction_date.lte.{dt})"
    elif df:
        params["transaction_date"] = f"gte.{df}"
    elif dt:
        params["transaction_date"] = f"lte.{dt}"

    rows = _fetch_all_pages(url, params, headers, "existing transactions for duplicate detection")
    fingerprints: Set[str] = set()
    shapes: Dict[str, int] = {}
    for r in rows:
        shape = describe_stored_date_shape(r.get("transaction_date"))
        shapes[shape] = shapes.get(shape, 0) + 1
        fingerprints.add(build_fingerprint(
            vehicle_identifier=r.get("vehicle_identifier"),
            transaction_date=r.get("transaction_date"),
            litres=r.get("litres"),
            total_cost=r.get("total_cost"),
            invoice_number=r.get("invoice_number"),
            odometer_km=r.get("odometer_km"),
        ))

    return FingerprintSet(fingerprints, stored_value_shapes=shapes, rows_scanned=len(rows))


def _read_headers(bearer_token: Optional[str]) -> Dict[str, str]:
    """
    Headers for backend-side full-table reads. Uses the service-role key when configured (same as
    analytics) so row-level security cannot silently hide rows; otherwise the caller's token.
    """
    return get_headers(None) if os.getenv("SUPABASE_SERVICE_ROLE_KEY") else get_headers(bearer_token)


def _parse_content_range_total(value: Optional[str]) -> Optional[int]:
    """Total row count from a PostgREST Content-Range header such as '0-999/2335' or '*/0'."""
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


def _fetch_all_pages(
    url: str,
    params: Dict[str, str],
    headers: Dict[str, str],
    what: str,
) -> List[Dict[str, Any]]:
    """
    Read every row matching params, PAGE_SIZE at a time, or raise FuelDataFetchError.
    Completion is decided by the exact count PostgREST reports (Prefer: count=exact), never by a
    short page, so a server max-rows cap below PAGE_SIZE cannot silently truncate the result.
    Without a usable count, it keeps paging until an empty page.
    """
    page_headers = dict(headers)
    page_headers["Prefer"] = "count=exact"
    page_params = dict(params)
    rows: List[Dict[str, Any]] = []
    total: Optional[int] = None

    while True:
        page_params["offset"] = str(len(rows))
        try:
            response = requests.get(url, params=page_params, headers=page_headers, timeout=20)
        except requests.RequestException as exc:
            logger.error("Network failure reading %s: %s", what, exc)
            raise FuelDataFetchError(f"Could not read {what} completely.") from None
        if not response.ok:
            logger.error("Error reading %s: HTTP %s", what, response.status_code)
            raise FuelDataFetchError(f"Could not read {what} completely (HTTP {response.status_code}).")
        try:
            chunk = response.json()
        except ValueError:
            raise FuelDataFetchError(f"Invalid response while reading {what}.") from None
        if not isinstance(chunk, list):
            raise FuelDataFetchError(f"Unexpected response while reading {what}.")

        reported = _parse_content_range_total(response.headers.get("content-range"))
        if reported is not None:
            total = reported

        if not chunk:
            if total is not None and len(rows) < total:
                raise FuelDataFetchError(
                    f"Could not read {what} completely ({len(rows)} of {total} rows; data changed during read)."
                )
            break
        rows.extend(chunk)
        if total is not None and len(rows) >= total:
            break

    return rows


def insert_fuel_transactions_batch(
    transactions: List[Dict[str, Any]],
    batch_size: int = 100,
    bearer_token: Optional[str] = None,
) -> Tuple[int, List[str]]:
    """
    Insert transactions in batches into fuel_transactions table.
    Returns: (inserted_count, list_of_error_messages)
    """
    if not transactions:
        return 0, []

    url = get_supabase_rest_url("fuel_transactions")
    headers = get_headers(bearer_token)
    headers["Prefer"] = "return=representation"

    inserted_count = 0
    errors: List[str] = []

    for i in range(0, len(transactions), batch_size):
        chunk = transactions[i : i + batch_size]
        try:
            response = requests.post(url, json=chunk, headers=headers, timeout=30)
            if response.ok:
                res_json = response.json()
                if isinstance(res_json, list):
                    inserted_count += len(res_json)
                else:
                    inserted_count += len(chunk)
            else:
                err_detail = response.text[:200]
                logger.error("Failed inserting batch of %d transactions: %s - %s", len(chunk), response.status_code, err_detail)
                errors.append(f"Batch {i // batch_size + 1} failed: HTTP {response.status_code}")
        except requests.RequestException as exc:
            logger.error("Request failed during transaction batch insert: %s", exc)
            errors.append(f"Batch {i // batch_size + 1} network failure.")

    return inserted_count, errors


def insert_fuel_transactions_ignore_duplicates(
    transactions: List[Dict[str, Any]],
    batch_size: int = 100,
    bearer_token: Optional[str] = None,
) -> Tuple[int, int, List[str]]:
    """
    FUEL_SCHEMA_V2 insert: on_conflict=fingerprint with ignore-duplicates, so rows already stored (also by a
    concurrent import) are skipped by the database. Returns (inserted, ignored_as_duplicate, errors);
    the counts come from the rows PostgREST returns, never assumed.
    """
    if not transactions:
        return 0, 0, []
    url = get_supabase_rest_url("fuel_transactions")
    headers = get_headers(bearer_token)
    headers["Prefer"] = "resolution=ignore-duplicates,return=representation"
    inserted = ignored = 0
    errors: List[str] = []
    for i in range(0, len(transactions), batch_size):
        chunk = transactions[i : i + batch_size]
        try:
            response = requests.post(url, params={"on_conflict": "fingerprint"}, json=chunk, headers=headers, timeout=30)
        except requests.RequestException as exc:
            logger.error("Request failed during transaction batch insert: %s", exc)
            errors.append(f"Batch {i // batch_size + 1} network failure.")
            continue
        if not response.ok:
            logger.error("Failed inserting batch of %d transactions: %s - %s", len(chunk), response.status_code, response.text[:200])
            errors.append(f"Batch {i // batch_size + 1} failed: HTTP {response.status_code}")
            continue
        body = response.json()
        returned = len(body) if isinstance(body, list) else len(chunk)
        inserted += returned
        ignored += len(chunk) - returned
    return inserted, ignored, errors


def get_fuel_imports_list(
    limit: int = 50,
    offset: int = 0,
    bearer_token: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Retrieve list of fuel imports with total count."""
    url = get_supabase_rest_url("fuel_imports")
    headers = get_headers(bearer_token)
    headers["Prefer"] = "count=exact"
    params = {
        "select": "*",
        "order": "created_at.desc,id.desc",
        "limit": str(min(limit, 100)),
        "offset": str(offset),
    }

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if response.ok:
            total_count = 0
            content_range = response.headers.get("content-range", "")
            if "/" in content_range:
                try:
                    total_count = int(content_range.split("/")[-1])
                except ValueError:
                    total_count = len(response.json())
            else:
                total_count = len(response.json())

            return response.json(), total_count
        else:
            logger.error("Failed fetching fuel_imports: %s", response.status_code)
    except Exception as exc:
        logger.error("Error fetching fuel_imports: %s", exc)
    # An outage must not look like "no imports yet".
    raise FuelDataFetchError("Could not read fuel import history.")


def normalize_date_filter(date_str: Optional[str], is_end: bool = False) -> Optional[str]:
    """
    Ensure date filter covers full day timestamp range.
    If '2026-08-31' is passed as end date, converts to '2026-08-31T23:59:59.999999'
    so transactions occurring throughout that day are not accidentally excluded.
    """
    if not date_str or not str(date_str).strip():
        return None
    val = str(date_str).strip()
    if is_end:
        if len(val) == 10 and "T" not in val and " " not in val:
            return f"{val}T23:59:59.999999"
    else:
        if len(val) == 10 and "T" not in val and " " not in val:
            return f"{val}T00:00:00"
    return val


def get_fuel_transactions_list(
    filters: Dict[str, Any],
    limit: int = 50,
    offset: int = 0,
    bearer_token: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Retrieve list of fuel transactions with filtering and pagination."""
    url = get_supabase_rest_url("fuel_transactions")
    headers = get_headers(bearer_token)
    headers["Prefer"] = "count=exact"

    params: Dict[str, str] = {
        "select": "*",
        "order": "transaction_date.desc,id.desc",
        "limit": str(min(limit, 100)),
        "offset": str(offset),
    }

    # Apply filters with safe timestamp bounds
    df = normalize_date_filter(filters.get("date_from"), is_end=False)
    dt = normalize_date_filter(filters.get("date_to"), is_end=True)

    if df and dt:
        params["and"] = f"(transaction_date.gte.{df},transaction_date.lte.{dt})"
    elif df:
        params["transaction_date"] = f"gte.{df}"
    elif dt:
        params["transaction_date"] = f"lte.{dt}"

    if filters.get("vehicle_identifier"):
        params["vehicle_identifier"] = f"ilike.*{filters['vehicle_identifier']}*"
    if filters.get("fuel_type"):
        params["fuel_type"] = f"ilike.*{filters['fuel_type']}*"
    if filters.get("import_id"):
        params["import_id"] = f"eq.{filters['import_id']}"

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if response.ok:
            total_count = 0
            content_range = response.headers.get("content-range", "")
            if "/" in content_range:
                try:
                    total_count = int(content_range.split("/")[-1])
                except ValueError:
                    total_count = len(response.json())
            else:
                total_count = len(response.json())

            return response.json(), total_count
        logger.error("Failed fetching fuel_transactions: %s", response.status_code)
        raise FuelDataFetchError(f"Fuel transactions could not be read (HTTP {response.status_code}).")
    except FuelDataFetchError:
        raise
    except Exception as exc:
        logger.error("Error fetching fuel_transactions: %s", exc)
        raise FuelDataFetchError("Fuel transactions could not be read.") from None


def fetch_fuel_transactions_for_analytics(
    filters: Dict[str, Any],
    bearer_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch lightweight transaction records for analytics calculations.
    Reads every page (see _fetch_all_pages) or raises FuelDataFetchError; never returns partial data.
    Selects only: transaction_date, vehicle_identifier, fuel_type, litres, total_cost, unit_price.
    """
    url = get_supabase_rest_url("fuel_transactions")
    # Use server service role key when available for backend analytics aggregations
    headers = _read_headers(bearer_token)

    params: Dict[str, str] = {
        "select": "id,import_id,transaction_date,vehicle_identifier,fuel_type,litres,total_cost,unit_price,odometer_km,station_name,invoice_number,driver_name,card_number"
        + (",cost_derived" if schema_v2_enabled() else ""),
        "order": "transaction_date.asc,id.asc",
        "limit": str(PAGE_SIZE),
    }


    # Apply normalized filters
    df = normalize_date_filter(filters.get("date_from"), is_end=False)
    dt = normalize_date_filter(filters.get("date_to"), is_end=True)

    if df and dt:
        params["and"] = f"(transaction_date.gte.{df},transaction_date.lte.{dt})"
    elif df:
        params["transaction_date"] = f"gte.{df}"
    elif dt:
        params["transaction_date"] = f"lte.{dt}"

    if filters.get("vehicle_identifier"):
        params["vehicle_identifier"] = f"ilike.*{filters['vehicle_identifier']}*"
    if filters.get("fuel_type"):
        params["fuel_type"] = f"ilike.*{filters['fuel_type']}*"
    if filters.get("import_id") is not None:
        params["import_id"] = f"eq.{filters['import_id']}"

    return _fetch_all_pages(url, params, headers, "fuel transactions")


def build_fingerprint(
    vehicle_identifier: Optional[str],
    transaction_date: Optional[str],
    litres: Optional[float],
    total_cost: Optional[float] = None,
    invoice_number: Optional[str] = None,
    odometer_km: Optional[float] = None,
) -> str:
    """
    Build a conservative composite fingerprint for duplicate detection.
    Prevents false duplicates when a vehicle refuels multiple times per day.
    """
    v_norm = str(vehicle_identifier or "").strip().lower().replace("-", "").replace(" ", "")
    # Wall-clock timestamp with any offset discarded (see parse_tx_wallclock for the unverified-type caveat).
    parsed = parse_tx_wallclock(transaction_date)
    d_norm = parsed[0].strftime("%Y-%m-%dT%H:%M:%S") if parsed else str(transaction_date or "").strip()
    # Normalize liters to 2 decimal places
    l_norm = f"{float(litres):.2f}" if litres is not None else ""
    # Normalize cost to 2 decimal places if available
    c_norm = f"{float(total_cost):.2f}" if total_cost is not None else ""
    inv_norm = str(invoice_number or "").strip().lower()
    odo_norm = f"{float(odometer_km):.0f}" if odometer_km is not None else ""

    return f"{v_norm}|{d_norm}|{l_norm}|{c_norm}|{inv_norm}|{odo_norm}"


def weak_fingerprint_key(fingerprint: str) -> Tuple[str, Optional[str]]:
    """
    Split a fingerprint into (date-level key, time part "HH:MM:SS").
    The date-level key is compared when either side only has date precision (a date-only value,
    or 00:00:00, which the importer cannot distinguish). time part is None when the date field
    is not a full wall-clock timestamp.
    """
    parts = fingerprint.split("|")
    if len(parts) < 2:
        return fingerprint, None
    stamp = parts[1]
    if len(stamp) == 19 and stamp[10] == "T":
        parts[1] = stamp[:10]
        return "|".join(parts), stamp[11:]
    return fingerprint, None
