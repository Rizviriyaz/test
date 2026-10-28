"""Supabase PostgREST database service for Fuel Management operations."""

import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import NewConnectionError

from app.performance_diagnostics import request_trace_id
from app.utils.config import get_env_float, get_env_int
from app.utils.datetime_helpers import parse_tx_wallclock
from app.utils.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

PAGE_SIZE = 1000

# One pooled session for every PostgREST read. The Supabase edge is ~230 ms RTT away, so a fresh TCP + TLS
# connection per page cost 0.6-0.9 s before the request was even sent, and every page restarted TCP slow start.
_session = requests.Session()
_session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16))

# Page reads are idempotent, so transport failures and gateway errors are retried a bounded number of times with
# jitter. Other errors (e.g. 500 from a bad query, 4xx) fail at once. Measured: the server answers in ~10-35 ms,
# but single responses occasionally stall for 15-60 s on the network path; a fresh attempt is the fix for those.
_RETRYABLE_STATUS = frozenset({408, 429, 502, 503, 504, 520, 522, 524, 544})
_RETRYABLE_ERRORS = (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)


class FuelDataFetchError(RuntimeError):
    """Raised when fuel transactions cannot be read completely; callers must not compute on partial data."""


# Every Supabase call below can take a `deadline` (a time.perf_counter() value). Its connect and read timeouts are
# clipped to the time left, and no call or retry starts once it has passed. requests applies the read timeout per
# socket read, not to the whole body, so a server that keeps trickling bytes is the one case this does not bound.
_MIN_CALL_SECONDS = 1.0


def _remaining(deadline: Optional[float]) -> float:
    return float("inf") if deadline is None else deadline - time.perf_counter()


def _clipped_timeout(connect: float, read: float, deadline: Optional[float]) -> Tuple[float, float]:
    """(connect, read) timeouts whose sum fits in the time left (both phases can run to their limit)."""
    left = _remaining(deadline)
    if left == float("inf"):
        return connect, read
    connect = min(connect, left / 2)
    return connect, min(read, left - connect)


def _write_cycle_seconds() -> float:
    """Worst case of one insert attempt plus the settle delay and read-back that an unknown outcome needs."""
    connect = get_env_float("FUEL_SUPABASE_CONNECT_TIMEOUT_SECONDS", 5.0)
    return (connect + get_env_float("FUEL_SUPABASE_WRITE_TIMEOUT_SECONDS", 20.0)
            + get_env_float("FUEL_SUPABASE_WRITE_SETTLE_SECONDS", 2.0)
            + connect + get_env_float("FUEL_SUPABASE_READ_TIMEOUT_SECONDS", 10.0))


def _not_sent(exc: requests.RequestException) -> bool:
    """True only when the failure proves the request never reached the server (no connection was established)."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    reason = getattr(exc.args[0], "reason", None) if isinstance(exc, requests.ConnectionError) and exc.args else None
    return isinstance(reason, NewConnectionError)


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


def replace_enabled() -> bool:
    """
    FUEL_IMPORT_REPLACE=1 enables latest-import-wins and monthly-summary imports, using the columns and the
    fuel_commit_import function from Backend/sql/fuel_import_replacement.sql (fuel_transactions.date_basis /
    superseded_by_import_id / superseded_at / staged, fuel_imports.import_mode / reporting_month / replace_scope /
    rows_replaced / rows_unchanged). Set it only after that SQL is applied; with it unset no request names those
    columns (PostgREST rejects unknown columns) and every read and import behaves as before.
    """
    return os.getenv("FUEL_IMPORT_REPLACE", "").strip() == "1"


def dated_replace_enabled() -> bool:
    """
    With FUEL_IMPORT_REPLACE, dated statements are latest-import-wins too: a new statement replaces the stored rows of
    its vehicles on the days it contains. FUEL_IMPORT_REPLACE_DATED=0 turns that off (dated imports then keep the
    legacy path: duplicates skipped, nothing superseded) while monthly summaries still replace their month.
    """
    return replace_enabled() and os.getenv("FUEL_IMPORT_REPLACE_DATED", "").strip() != "0"


def apply_visible_filter(params: Dict[str, str]) -> Dict[str, str]:
    """
    Restrict a fuel_transactions read to the visible dataset: not superseded by a newer import and not staged
    (rows of an import that was never committed). A no-op without FUEL_IMPORT_REPLACE.
    """
    if replace_enabled():
        params["superseded_by_import_id"] = "is.null"
        params["staged"] = "not.is.true"
    return params


def ignored_counts_enabled() -> bool:
    """
    FUEL_IMPORT_IGNORED_COUNTS=1 writes fuel_imports.rows_ignored and rows_non_fuel, the columns added by
    Backend/sql/fuel_imports_ignored_counts.sql. Independent of FUEL_SCHEMA_V2. Set it only after that SQL is
    applied; with it unset the import record payload keeps exactly the pre-existing keys.
    """
    return os.getenv("FUEL_IMPORT_IGNORED_COUNTS", "").strip() == "1"


V2_TRANSACTION_COLUMNS = ("fingerprint", "transaction_kind", "cost_derived", "unit_price_derived")


def find_import_by_sha256(file_sha256: str, bearer_token: Optional[str] = None,
                          deadline: Optional[float] = None) -> Optional[int]:
    """
    Id of an earlier completed import of the identical file (FUEL_SCHEMA_V2 only), else None. Read-only.
    An interrupted or partly failed import does not block re-importing the file to add its missing rows:
    the fingerprint index skips the rows already stored.
    """
    url = get_supabase_rest_url("fuel_imports")
    params = {"select": "id", "file_sha256": f"eq.{file_sha256}", "status": "eq.completed",
              "order": "id.asc", "limit": "1"}
    try:
        if _remaining(deadline) < _MIN_CALL_SECONDS:
            raise FuelDataFetchError("No time left to check the file hash.")
        response = _session.get(url, params=params, headers=_read_headers(bearer_token),
                                timeout=_clipped_timeout(15.0, 15.0, deadline))
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
    deadline: Optional[float] = None,
    rows_ignored: Optional[int] = None,
    rows_non_fuel: Optional[int] = None,
    replace_fields: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Create a new record in fuel_imports with status 'processing'.
    Returns the newly created import ID. rows_ignored / rows_non_fuel are final when the record is created (the
    rows are classified first) and are written only with FUEL_IMPORT_IGNORED_COUNTS=1. replace_fields
    (import_mode, reporting_month, replace_scope) are written only with FUEL_IMPORT_REPLACE=1.
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
    if ignored_counts_enabled():
        if rows_ignored is not None:
            payload["rows_ignored"] = rows_ignored
        if rows_non_fuel is not None:
            payload["rows_non_fuel"] = rows_non_fuel
    if replace_fields and replace_enabled():
        payload.update(replace_fields)

    headers = get_headers(bearer_token)
    if _remaining(deadline) < _MIN_CALL_SECONDS:
        raise RuntimeError("No time left to create the import record.")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=_clipped_timeout(15.0, 15.0, deadline))
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
    deadline: Optional[float] = None,
) -> bool:
    """
    Update an existing fuel_imports record by ID. The PATCH sets absolute values, so transient failures are
    retried (bounded, jittered backoff, only while time is left before `deadline`); returns False only when every
    attempt failed, it was rejected, or no attempt fitted before the deadline.
    """
    url = get_supabase_rest_url("fuel_imports")
    # fuel_imports has no UPDATE policy for 'authenticated': with the user's token the PATCH matches no row and the
    # record stays 'processing'. The service-role key (when configured) updates the import's own status and counts;
    # the caller's import role was checked by the route, and the commit RPC still runs with the user's token.
    headers = get_headers(None) if os.getenv("SUPABASE_SERVICE_ROLE_KEY") else get_headers(bearer_token)
    params = {"id": f"eq.{import_id}"}
    attempts = max(1, get_env_int("FUEL_SUPABASE_WRITE_ATTEMPTS", 3))

    for attempt in range(1, attempts + 1):
        if _remaining(deadline) < _MIN_CALL_SECONDS:
            logger.error("Updating import #%s: no time left before the request limit (attempt %d)", import_id, attempt)
            return False
        try:
            response = requests.patch(url, params=params, json=updates, headers=headers,
                                      timeout=_clipped_timeout(5.0, 15.0, deadline))
        except _RETRYABLE_ERRORS as exc:
            failure = type(exc).__name__
        except requests.RequestException as exc:
            logger.error("Failed to connect to Supabase fuel_imports update endpoint: %s", exc)
            return False
        else:
            if response.ok:
                try:
                    updated = response.json()
                except ValueError:
                    updated = None
                if isinstance(updated, list) and not updated:
                    # 200 with no row: row-level security (or a missing id) filtered the update out
                    logger.error("Updating import #%s matched no row (not permitted or not found)", import_id)
                    return False
                return True
            if response.status_code not in _RETRYABLE_STATUS:
                logger.error("Supabase update error on fuel_imports: %s - %s", response.status_code, response.text)
                return False
            failure = f"HTTP {response.status_code}"
        logger.warning("Updating import #%s failed (%s), attempt %d of %d", import_id, failure, attempt, attempts)
        if attempt < attempts:
            delay = _retry_delay(attempt)
            if _remaining(deadline) < delay + _MIN_CALL_SECONDS:
                return False
            time.sleep(delay)
    return False


def fetch_existing_transaction_fingerprints(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
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
    apply_visible_filter(params)

    rows = _fetch_all_pages(url, params, headers, "existing transactions for duplicate detection", deadline)
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


def _retry_delay(attempt: int) -> float:
    """Jittered backoff before retry number `attempt` (1-based): 0.25-0.5 s, then 0.5-1 s, ..."""
    cap = 0.5 * 2 ** (attempt - 1)
    return cap / 2 + random.uniform(0, cap / 2)


def _get_page(url: str, params: Dict[str, str], headers: Dict[str, str], what: str, page: int,
              read_started: float, call_deadline: Optional[float] = None) -> requests.Response:
    """
    GET one page, retrying transient failures (bounded attempts, jittered backoff, overall read deadline, and the
    caller's `call_deadline`). Returns the response (possibly a non-retryable error status) or raises FuelDataFetchError.
    """
    attempts = max(1, get_env_int("FUEL_SUPABASE_READ_ATTEMPTS", 3))
    connect_s = get_env_float("FUEL_SUPABASE_CONNECT_TIMEOUT_SECONDS", 5.0)
    read_s = get_env_float("FUEL_SUPABASE_READ_TIMEOUT_SECONDS", 10.0)
    deadline = get_env_float("FUEL_SUPABASE_READ_DEADLINE_SECONDS", 45.0)
    for attempt in range(1, attempts + 1):
        if _remaining(call_deadline) < _MIN_CALL_SECONDS:
            raise FuelDataFetchError(f"Could not read {what} completely (request time limit reached).")
        started = time.perf_counter()
        try:
            response = _session.get(url, params=params, headers=headers,
                                    timeout=_clipped_timeout(connect_s, read_s, call_deadline))
        except _RETRYABLE_ERRORS as exc:
            failure = type(exc).__name__
        except requests.RequestException as exc:
            logger.error("Network failure reading %s: %s", what, exc)
            raise FuelDataFetchError(f"Could not read {what} completely.") from None
        else:
            if response.ok or response.status_code not in _RETRYABLE_STATUS:
                total_ms = (time.perf_counter() - started) * 1000
                elapsed = getattr(response, "elapsed", None)
                # wait_ms: request sent -> headers parsed (connection setup + network + server_ms);
                # body_ms: body download + decompression. server_ms is Supabase's own upstream time.
                wait_ms = elapsed.total_seconds() * 1000 if elapsed is not None else total_ms
                logger.debug(
                    "PERF supabase_read page request_id=%s what=%s page=%d attempt=%d status=%s server_ms=%s "
                    "wait_ms=%.0f body_ms=%.0f",
                    request_trace_id.get(), what, page, attempt, response.status_code,
                    (getattr(response, "headers", None) or {}).get("x-envoy-upstream-service-time"),
                    wait_ms, max(0.0, total_ms - wait_ms),
                )
                return response
            failure = f"HTTP {response.status_code}"
        delay = _retry_delay(attempt)
        if (attempt == attempts or time.perf_counter() - read_started + delay >= deadline
                or _remaining(call_deadline) < delay + _MIN_CALL_SECONDS):
            logger.error(
                "Supabase read of %s failed: page=%d %s after %d attempt(s), %.1fs into the read",
                what, page, failure, attempt, time.perf_counter() - read_started,
            )
            raise FuelDataFetchError(f"Could not read {what} completely ({failure} after {attempt} attempt(s)).")
        logger.warning(
            "PERF supabase_read retry request_id=%s what=%s page=%d attempt=%d failure=%s duration_ms=%.0f backoff_ms=%.0f",
            request_trace_id.get(), what, page, attempt, failure, (time.perf_counter() - started) * 1000, delay * 1000,
        )
        time.sleep(delay)
    raise AssertionError("unreachable")


def _fetch_all_pages(
    url: str,
    params: Dict[str, str],
    headers: Dict[str, str],
    what: str,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Read every row matching params, PAGE_SIZE at a time, or raise FuelDataFetchError (also when `deadline` passes).
    Completion is decided by the exact count PostgREST reports (Prefer: count=exact), never by a
    short page, so a server max-rows cap below PAGE_SIZE cannot silently truncate the result.
    Without a usable count, it keeps paging until an empty page.
    """
    page_headers = dict(headers)
    page_headers["Prefer"] = "count=exact"
    page_params = dict(params)
    rows: List[Dict[str, Any]] = []
    total: Optional[int] = None
    read_started = time.perf_counter()
    parse_s = 0.0
    page = 0

    while True:
        page += 1
        page_params["offset"] = str(len(rows))
        response = _get_page(url, page_params, page_headers, what, page, read_started, deadline)
        if not response.ok:
            logger.error("Error reading %s: HTTP %s", what, response.status_code)
            raise FuelDataFetchError(f"Could not read {what} completely (HTTP {response.status_code}).")
        try:
            parse_started = time.perf_counter()
            chunk = response.json()
            parse_s += time.perf_counter() - parse_started
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

    total_s = time.perf_counter() - read_started
    logger.log(
        logging.INFO if total_s >= 5 else logging.DEBUG,
        "PERF supabase_read done request_id=%s what=%s pages=%d rows=%d total_ms=%.0f parse_ms=%.0f",
        request_trace_id.get(), what, page, len(rows), total_s * 1000, parse_s * 1000,
    )
    return rows


def fetch_import_rows(
    import_id: int,
    source_rows: Optional[List[int]] = None,
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Rows stored under one import (optionally only the given source rows): id, source_row_number,
    transaction_date (and staged with FUEL_IMPORT_REPLACE: staged rows are stored but not visible).
    (import_id, source_row_number) identifies a statement row of an import, so this is the
    read-back used to settle uncertain writes. Read-only; raises FuelDataFetchError if not read completely.
    """
    params: Dict[str, str] = {
        "select": "id,source_row_number,transaction_date" + (",staged" if replace_enabled() else ""),
        "import_id": f"eq.{int(import_id)}",
        "order": "id.asc",
        "limit": str(PAGE_SIZE),
    }
    if source_rows is not None:
        params["source_row_number"] = f"in.({','.join(str(int(r)) for r in sorted(set(source_rows)))})"
    url = get_supabase_rest_url("fuel_transactions")
    return _fetch_all_pages(url, params, _read_headers(bearer_token), f"rows stored by import #{import_id}", deadline)


def fetch_import_coverage(
    import_ids: Iterable[Any],
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Coverage metadata of the given imports by id: id, status, import_mode, replace_scope (see
    reporting_period.confirmed_coverage_period). Read-only. Empty without FUEL_IMPORT_REPLACE: before
    Backend/sql/fuel_import_replacement.sql those columns do not exist, so no import can establish coverage.
    Raises FuelDataFetchError unless read completely (an unread import must not look uncovered or covered).
    """
    ids = sorted({int(i) for i in import_ids if i is not None and str(i).strip().lstrip("-").isdigit()})
    if not ids or not replace_enabled():
        return {}
    url = get_supabase_rest_url("fuel_imports")
    out: Dict[int, Dict[str, Any]] = {}
    for start in range(0, len(ids), 200):  # bounded URL length
        chunk = ids[start:start + 200]
        params = {
            "select": "id,status,import_mode,replace_scope",
            "id": f"in.({','.join(str(i) for i in chunk)})",
            "order": "id.asc",
            "limit": str(PAGE_SIZE),
        }
        for r in _fetch_all_pages(url, params, _read_headers(bearer_token), "fuel import coverage", deadline):
            if r.get("id") is not None:
                out[int(r["id"])] = r
    return out


def fetch_visible_rows_in_range(
    date_from: str,
    date_to: str,
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Every visible transaction (FUEL_IMPORT_REPLACE) with transaction_date in [date_from, date_to] (inclusive days),
    all vehicles, with the values needed to define and fingerprint a replacement scope. Read-only; raises
    FuelDataFetchError unless read completely: a partial read would leave old rows visible next to their replacement.
    """
    params: Dict[str, str] = {
        "select": "id,import_id,transaction_date,vehicle_identifier,litres,total_cost,invoice_number,odometer_km,date_basis",
        "order": "id.asc",
        "limit": str(PAGE_SIZE),
        "and": (f"(transaction_date.gte.{normalize_date_filter(date_from, is_end=False)},"
                f"transaction_date.lte.{normalize_date_filter(date_to, is_end=True)})"),
    }
    apply_visible_filter(params)
    url = get_supabase_rest_url("fuel_transactions")
    return _fetch_all_pages(url, params, _read_headers(bearer_token), "transactions in the replacement period", deadline)


class FuelCommitError(RuntimeError):
    """The database refused to commit an import; nothing was changed by the commit."""


def commit_fuel_import(
    import_id: int,
    supersede_ids: List[int],
    expected_rows: int,
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
) -> Optional[Dict[str, int]]:
    """
    Make one import's staged rows visible and mark the rows it replaces as superseded, in ONE database transaction
    (public.fuel_commit_import, Backend/sql/fuel_import_replacement.sql). The function refuses (and changes nothing)
    unless every id in supersede_ids is still visible and exactly expected_rows rows of the import are staged.

    Returns {"superseded": n, "committed": m} on success and None when the outcome is unknown (the request was sent
    but no usable answer came back: the caller must read the import's rows to find out). Raises FuelCommitError
    when the database refused it or the request was never sent; nothing changed in either case.
    """
    url = get_supabase_rest_url("rpc/fuel_commit_import")
    payload = {"p_import_id": int(import_id), "p_supersede_ids": [int(i) for i in supersede_ids],
               "p_expected_rows": int(expected_rows)}
    if _remaining(deadline) < _MIN_CALL_SECONDS:
        raise FuelCommitError("no time left before the request limit")
    try:
        response = requests.post(url, json=payload, headers=get_headers(bearer_token),
                                 timeout=_clipped_timeout(5.0, 30.0, deadline))
    except requests.RequestException as exc:
        if _not_sent(exc):
            raise FuelCommitError("the commit request could not be sent") from None
        logger.error("Commit of fuel import #%s: outcome unknown (%s)", import_id, type(exc).__name__)
        return None
    if response.ok:
        try:
            body = response.json()
            return {"superseded": int(body["superseded"]), "committed": int(body["committed"])}
        except (ValueError, TypeError, KeyError):
            logger.error("Commit of fuel import #%s: unreadable response", import_id)
            return None
    if response.status_code in _RETRYABLE_STATUS:
        logger.error("Commit of fuel import #%s: outcome unknown (HTTP %s)", import_id, response.status_code)
        return None
    logger.error("Commit of fuel import #%s rejected: %s - %s", import_id, response.status_code, response.text[:300])
    raise FuelCommitError(f"rejected by the database (HTTP {response.status_code})")


def _row_key(tx: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    try:
        return int(tx["import_id"]), int(tx["source_row_number"])
    except (KeyError, TypeError, ValueError):
        return None


def _describe_rows(chunk: List[Dict[str, Any]]) -> str:
    rows = [tx.get("source_row_number") for tx in chunk if isinstance(tx.get("source_row_number"), int)]
    return f"source rows {min(rows)}-{max(rows)}" if rows else f"{len(chunk)} row(s)"


def _settle_rows(outcome: Dict[str, Any], state: str, rows: List[Dict[str, Any]], reason: str = "") -> None:
    """
    Count rows of a batch that ended unconfirmed: 'failed_rows' (known not stored: rejected, or the request never
    reached the server), 'unverified_rows' (sent, outcome unknown: they may be stored) or 'not_attempted_rows'
    (never sent). Failed rows also keep (source row, reason) in outcome['failed_source_rows'].
    """
    outcome[state] = outcome.get(state, 0) + len(rows)
    if state == "failed_rows":
        outcome.setdefault("failed_source_rows", []).extend((tx.get("source_row_number"), reason) for tx in rows)


def _insert_chunk(
    url: str,
    chunk: List[Dict[str, Any]],
    headers: Dict[str, str],
    params: Optional[Dict[str, str]],
    label: str,
    bearer_token: Optional[str],
    outcome: Dict[str, Any],
    deadline: Optional[float] = None,
) -> Tuple[int, int, Optional[str]]:
    """
    POST one batch. Returns (rows of the batch now stored under this import, rows not confirmed stored, error or None);
    how each unconfirmed row ended is counted in `outcome` (see _settle_rows).

    - A failure that proves the request never reached the server (connect timeout, refused connection, DNS) is
      retried: bounded attempts, jittered backoff, and only while a full attempt and its read-back fit before `deadline`.
    - A read timeout, dropped connection, gateway status or unreadable 2xx leaves the outcome unknown: the INSERT may
      have committed, even after a read-back. The batch is read back once by (import_id, source_row_number) and the
      rows found count as stored. The INSERT is atomic, so any row found means the attempt committed. Without
      on_conflict (FUEL_SCHEMA_V2 off) nothing is re-sent, since that could store a row twice: rows not found are
      reported unverified for Reconcile to count later. With on_conflict=fingerprint the unique index skips rows
      already stored, so rows not found are re-sent.
    - A definite rejection (4xx/500) is not retried: PostgREST rolls the whole INSERT back.
    """
    attempts = max(1, get_env_int("FUEL_SUPABASE_WRITE_ATTEMPTS", 3))
    connect_s = get_env_float("FUEL_SUPABASE_CONNECT_TIMEOUT_SECONDS", 5.0)
    write_s = get_env_float("FUEL_SUPABASE_WRITE_TIMEOUT_SECONDS", 20.0)
    settle = get_env_float("FUEL_SUPABASE_WRITE_SETTLE_SECONDS", 2.0)
    resend_is_safe = bool(params and params.get("on_conflict"))
    keys = [_row_key(tx) for tx in chunk]
    import_ids = {k[0] for k in keys if k is not None}
    can_read_back = None not in keys and len(import_ids) == 1
    pending = list(zip(chunk, keys))
    stored = 0
    uncertain = False  # an earlier attempt may have stored rows that are still pending
    failure = ""
    attempt = 0
    while attempt < attempts:
        attempt += 1
        rows = [tx for tx, _ in pending]
        committed = False
        try:
            response = requests.post(url, params=params, json=rows, headers=headers,
                                     timeout=_clipped_timeout(connect_s, write_s, deadline))
        except _RETRYABLE_ERRORS as exc:
            failure, sent = type(exc).__name__, not _not_sent(exc)
        except requests.RequestException as exc:
            logger.error("%s: request could not be sent: %s", label, exc)
            _settle_rows(outcome, "unverified_rows" if uncertain else "failed_rows", rows, "request could not be sent")
            return stored, len(rows), f"{label} ({_describe_rows(rows)}) not stored: request could not be sent."
        else:
            if response.ok:
                try:
                    body = response.json()
                except ValueError:
                    body = None
                if isinstance(body, list):
                    return stored + len(body), 0, None
                failure, committed, sent = "unreadable response", True, True
            elif response.status_code in _RETRYABLE_STATUS:
                failure, sent = f"HTTP {response.status_code}", True
            else:
                logger.error("%s rejected: %s - %s", label, response.status_code, response.text[:200])
                reason = f"rejected by the database: HTTP {response.status_code}"
                _settle_rows(outcome, "unverified_rows" if uncertain else "failed_rows", rows, reason)
                note = "; an earlier attempt's outcome is unknown, use Reconcile in import history" if uncertain else ""
                return stored, len(rows), f"{label} ({_describe_rows(rows)}) {reason}{note}"

        if not sent:
            # Nothing reached the server, so nothing of this attempt can be stored: trying again is safe.
            delay = _retry_delay(attempt)
            if attempt < attempts and _remaining(deadline) >= delay + _write_cycle_seconds():
                logger.warning("PERF supabase_write retry request_id=%s batch=%s attempt=%d failure=%s rows=%d (not sent)",
                               request_trace_id.get(), label, attempt, failure, len(rows))
                time.sleep(delay)
                continue
            break

        uncertain = True
        outcome["uncertain_batches"] = outcome.get("uncertain_batches", 0) + 1
        logger.warning("PERF supabase_write uncertain request_id=%s batch=%s attempt=%d failure=%s rows=%d",
                       request_trace_id.get(), label, attempt, failure, len(rows))
        if not can_read_back:
            _settle_rows(outcome, "unverified_rows", rows)
            return stored, len(rows), f"{label} ({_describe_rows(rows)}): outcome unknown after {failure}; rows cannot be read back."
        if _remaining(deadline) < settle + _MIN_CALL_SECONDS:
            _settle_rows(outcome, "unverified_rows", rows)
            return stored, len(rows), (f"{label} ({_describe_rows(rows)}): outcome unknown after {failure}; no time "
                                       "left to read it back. Use Reconcile in import history.")
        time.sleep(settle)
        try:
            found = {int(r["source_row_number"])
                     for r in fetch_import_rows(next(iter(import_ids)), [k[1] for _, k in pending], bearer_token,
                                                deadline=deadline)
                     if r.get("source_row_number") is not None}
        except FuelDataFetchError:
            _settle_rows(outcome, "unverified_rows", rows)
            return stored, len(rows), (f"{label} ({_describe_rows(rows)}): outcome unknown after {failure} and the "
                                       "read-back failed; not retried to avoid duplicates. Use Reconcile in import history.")
        stored += sum(1 for _, k in pending if k[1] in found)
        committed = committed or bool(found)
        pending = [(tx, k) for tx, k in pending if k[1] not in found]
        if not pending or (committed and resend_is_safe):
            # A committed on_conflict INSERT skipped the rows not found as already stored.
            return stored, 0, None
        if not resend_is_safe:
            rows = [tx for tx, _ in pending]
            _settle_rows(outcome, "unverified_rows", rows)
            return stored, len(rows), (f"{label} ({_describe_rows(rows)}): outcome unknown after {failure}; {len(rows)} "
                                       "row(s) not found by the read-back were not re-sent (that could store them twice). "
                                       "Use Reconcile in import history to count them later.")
        delay = _retry_delay(attempt)
        if attempt < attempts and _remaining(deadline) >= delay + _write_cycle_seconds():
            time.sleep(delay)
            continue
        break

    rows = [tx for tx, _ in pending]
    if uncertain:
        _settle_rows(outcome, "unverified_rows", rows)
        message = (f"{label} ({_describe_rows(rows)}): {len(rows)} row(s) unverified after {attempt} attempt(s) "
                   f"({failure}). Use Reconcile in import history.")
    else:
        reason = f"not stored after {attempt} attempt(s) ({failure})"
        _settle_rows(outcome, "failed_rows", rows, reason)
        message = f"{label} ({_describe_rows(rows)}): {len(rows)} row(s) {reason}."
    logger.error("%s", message)
    return stored, len(rows), message


def _insert_batches(
    transactions: List[Dict[str, Any]],
    batch_size: int,
    headers: Dict[str, str],
    params: Optional[Dict[str, str]],
    bearer_token: Optional[str],
    deadline: Optional[float],
    outcome: Optional[Dict[str, Any]],
) -> Tuple[int, int, List[str]]:
    """
    Returns (stored, unresolved, errors); unresolved rows were neither stored nor skipped by the database.
    A batch starts only if one full attempt and its read-back still fit before `deadline`.
    """
    url = get_supabase_rest_url("fuel_transactions")
    outcome = outcome if outcome is not None else {}
    stored = unresolved = 0
    errors: List[str] = []
    for i in range(0, len(transactions), batch_size):
        if deadline is not None and _remaining(deadline) < _write_cycle_seconds():
            rest = transactions[i:]
            unresolved += len(rest)
            _settle_rows(outcome, "not_attempted_rows", rest)
            errors.append(f"Time limit reached: {len(rest)} row(s) ({_describe_rows(rest)}) were not sent. "
                          "Re-import the same file to add them; rows already stored are skipped as duplicates.")
            break
        added, open_rows, error = _insert_chunk(url, transactions[i:i + batch_size], headers, params,
                                                f"Batch {i // batch_size + 1}", bearer_token, outcome, deadline)
        stored += added
        unresolved += open_rows
        if error:
            errors.append(error)
    if transactions:
        clear_vehicle_identifier_cache()
    return stored, unresolved, errors


def insert_fuel_transactions_batch(
    transactions: List[Dict[str, Any]],
    batch_size: int = 100,
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
    outcome: Optional[Dict[str, Any]] = None,
) -> Tuple[int, List[str]]:
    """
    Insert transactions in batches into fuel_transactions table (see _insert_chunk for retries).
    Batches are not started after `deadline` (a time.perf_counter() value). `outcome`, when given, receives
    uncertain_batches / failed_rows / unverified_rows / not_attempted_rows counters and failed_source_rows.
    Returns: (inserted_count, list_of_error_messages)
    """
    if not transactions:
        return 0, []
    headers = get_headers(bearer_token)
    headers["Prefer"] = "return=representation"
    inserted, _, errors = _insert_batches(transactions, batch_size, headers, None, bearer_token, deadline, outcome)
    return inserted, errors


def insert_fuel_transactions_ignore_duplicates(
    transactions: List[Dict[str, Any]],
    batch_size: int = 100,
    bearer_token: Optional[str] = None,
    deadline: Optional[float] = None,
    outcome: Optional[Dict[str, Any]] = None,
) -> Tuple[int, int, List[str]]:
    """
    FUEL_SCHEMA_V2 insert: on_conflict=fingerprint with ignore-duplicates, so rows already stored (also by a
    concurrent import) are skipped by the database. Returns (inserted, ignored_as_duplicate, errors);
    the counts come from the rows PostgREST returns (or the read-back), never assumed.
    """
    if not transactions:
        return 0, 0, []
    headers = get_headers(bearer_token)
    headers["Prefer"] = "resolution=ignore-duplicates,return=representation"
    inserted, unresolved, errors = _insert_batches(transactions, batch_size, headers, {"on_conflict": "fingerprint"},
                                                   bearer_token, deadline, outcome)
    return inserted, len(transactions) - inserted - unresolved, errors


def get_fuel_import_record(import_id: int, bearer_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """One fuel_imports row by id, or None if it does not exist. Raises FuelDataFetchError on failure."""
    url = get_supabase_rest_url("fuel_imports")
    params = {"select": "*", "id": f"eq.{int(import_id)}", "limit": "1"}
    response = _get_page(url, params, _read_headers(bearer_token), f"import #{import_id}", 1, time.perf_counter())
    if not response.ok:
        raise FuelDataFetchError(f"Could not read import #{import_id} (HTTP {response.status_code}).")
    try:
        rows = response.json()
    except ValueError:
        raise FuelDataFetchError(f"Invalid response while reading import #{import_id}.") from None
    return rows[0] if isinstance(rows, list) and rows else None


def fetch_running_imports(since_iso: str, bearer_token: Optional[str] = None,
                          deadline: Optional[float] = None) -> List[Dict[str, Any]]:
    """
    fuel_imports rows still 'processing' that were created at or after `since_iso` (id, created_at, status).
    Read with the service-role key when configured, so other users' imports are not hidden. Read-only; raises
    FuelDataFetchError on failure.
    """
    url = get_supabase_rest_url("fuel_imports")
    params = {"select": "id,created_at,status", "status": "eq.processing", "created_at": f"gte.{since_iso}",
              "order": "id.asc", "limit": "20"}
    response = _get_page(url, params, _read_headers(bearer_token), "running imports", 1, time.perf_counter(), deadline)
    if not response.ok:
        raise FuelDataFetchError(f"Could not read running imports (HTTP {response.status_code}).")
    try:
        rows = response.json()
    except ValueError:
        raise FuelDataFetchError("Invalid response while reading running imports.") from None
    if not isinstance(rows, list):
        raise FuelDataFetchError("Unexpected response while reading running imports.")
    return rows


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
        response = _session.get(url, params=params, headers=headers, timeout=15)
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
    apply_visible_filter(params)

    try:
        response = _session.get(url, params=params, headers=headers, timeout=15)
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
        + (",cost_derived" if schema_v2_enabled() else "")
        + (",date_basis" if replace_enabled() else ""),
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
    apply_visible_filter(params)

    return _fetch_all_pages(url, params, headers, "fuel transactions")


def fetch_fuel_vehicle_identifiers(bearer_token: Optional[str] = None) -> List[str]:
    """
    Every distinct vehicle_identifier ever imported (all dates), for the number-only plate conflict check.
    Reads every page (see _fetch_all_pages) or raises FuelDataFetchError; never returns a partial list.
    """
    url = get_supabase_rest_url("fuel_transactions")
    params = apply_visible_filter({"select": "vehicle_identifier", "order": "id.asc", "limit": str(PAGE_SIZE)})
    rows = _fetch_all_pages(url, params, _read_headers(bearer_token), "fuel vehicle identifiers")
    return sorted({str(r.get("vehicle_identifier") or "").strip() for r in rows} - {""})


# Identifier registry per reader (service role, or the caller's token since row-level security may differ).
# Cleared whenever rows are written, so a new import's identifiers are checked on the next request.
IDENTIFIER_REGISTRY_TTL_S = 300
_IDENTIFIER_CACHE = TTLCache(max_entries=16)


def get_fuel_vehicle_identifiers(bearer_token: Optional[str] = None) -> List[str]:
    """Cached fetch_fuel_vehicle_identifiers (IDENTIFIER_REGISTRY_TTL_S); failures propagate and are not cached."""
    auth_key = None if os.getenv("SUPABASE_SERVICE_ROLE_KEY") else bearer_token
    return _IDENTIFIER_CACHE.get_or_compute(
        ("identifiers", auth_key),
        lambda: fetch_fuel_vehicle_identifiers(bearer_token),
        lambda _ids: IDENTIFIER_REGISTRY_TTL_S,
    )


def clear_vehicle_identifier_cache() -> None:
    _IDENTIFIER_CACHE.clear()


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
    l_norm = fingerprint_number(litres, 2)
    c_norm = fingerprint_number(total_cost, 2)
    inv_norm = str(invoice_number or "").strip().lower()
    odo_norm = fingerprint_number(odometer_km, 0)

    return f"{v_norm}|{d_norm}|{l_norm}|{c_norm}|{inv_norm}|{odo_norm}"


def fingerprint_number(value: Any, places: int) -> str:
    """
    `value` as fixed-point text rounded half away from zero to `places`, the rule PostgreSQL applies when it stores
    a value in numeric(p, places) and in round(numeric, places); "" for None.

    The value is read as a decimal, never as a binary float: a float is taken from its shortest repr, which is the
    text json.dumps sends to PostgREST and the text a numeric column returns. So a statement value and the same
    value read back from the database give one string (binary formatting gave 2.675 -> "2.67" while numeric(p, 2)
    stores 2.68). Negative zero is printed as zero, as numeric has none. Matching across a column whose scale is
    finer than `places` but coarser than the statement value is not guaranteed (double rounding).
    """
    if value is None:
        return ""
    number = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value).strip())
    if not number.is_finite():
        return f"{float(value):.{places}f}"
    rounded = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return format(rounded if rounded else abs(rounded), "f")


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
