"""Supabase PostgREST database service for Fuel Management operations."""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
import requests

logger = logging.getLogger(__name__)


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


def create_fuel_import_record(
    file_name: str,
    imported_by: str,
    rows_found: int,
    bearer_token: Optional[str] = None,
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
    Fetch existing fuel transactions to identify duplicates.
    Returns a set of composite fingerprint strings.
    """
    url = get_supabase_rest_url("fuel_transactions")
    headers = get_headers(bearer_token)
    params = {
        "select": "transaction_date,vehicle_identifier,litres,total_cost,invoice_number,odometer_km",
        "limit": "10000",
    }
    if date_from:
        params["transaction_date"] = f"gte.{date_from}"
    if date_to:
        # If date_to is specified, combine with gte or use PostgREST and filter
        if "transaction_date" in params:
            params["and"] = f"(transaction_date.gte.{date_from},transaction_date.lte.{date_to})"
            del params["transaction_date"]
        else:
            params["transaction_date"] = f"lte.{date_to}"

    try:
        response = requests.get(url, params=params, headers=headers, timeout=15)
        if response.ok:
            records = response.json()
            fingerprints = set()
            for r in records:
                fp = build_fingerprint(
                    vehicle_identifier=r.get("vehicle_identifier"),
                    transaction_date=r.get("transaction_date"),
                    litres=r.get("litres"),
                    total_cost=r.get("total_cost"),
                    invoice_number=r.get("invoice_number"),
                    odometer_km=r.get("odometer_km"),
                )
                fingerprints.add(fp)
            return fingerprints
    except Exception as exc:
        logger.warning("Could not fetch existing transactions for duplicate detection: %s", exc)

    return set()


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
            return [], 0
    except Exception as exc:
        logger.error("Error fetching fuel_imports: %s", exc)
        return [], 0


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
        else:
            logger.error("Failed fetching fuel_transactions: %s", response.status_code)
            return [], 0
    except Exception as exc:
        logger.error("Error fetching fuel_transactions: %s", exc)
        return [], 0


def fetch_fuel_transactions_for_analytics(
    filters: Dict[str, Any],
    bearer_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch lightweight transaction records for analytics calculations.
    Paginates in 1000-row chunks to overcome PostgREST server max-row limits.
    Selects only: transaction_date, vehicle_identifier, fuel_type, litres, total_cost, unit_price.
    """
    url = get_supabase_rest_url("fuel_transactions")
    # Use server service role key when available for backend analytics aggregations
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    headers = get_headers(None) if service_key else get_headers(bearer_token)

    params: Dict[str, str] = {
        "select": "transaction_date,vehicle_identifier,fuel_type,litres,total_cost,unit_price",
        "order": "transaction_date.asc,id.asc",
        "limit": "1000",
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

    all_records: List[Dict[str, Any]] = []
    offset = 0

    while True:
        params["offset"] = str(offset)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            if not response.ok:
                logger.error("Error querying transactions for analytics: HTTP %s", response.status_code)
                break
            chunk = response.json()
            if not chunk or not isinstance(chunk, list):
                break
            all_records.extend(chunk)
            offset += len(chunk)
            if len(chunk) < 1000:
                break
        except requests.RequestException as exc:
            logger.error("Network failure during analytics data query: %s", exc)
            break

    return all_records


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
    d_norm = str(transaction_date or "").strip()[:10]  # Date portion YYYY-MM-DD
    # Normalize liters to 2 decimal places
    l_norm = f"{float(litres):.2f}" if litres is not None else ""
    # Normalize cost to 2 decimal places if available
    c_norm = f"{float(total_cost):.2f}" if total_cost is not None else ""
    inv_norm = str(invoice_number or "").strip().lower()
    odo_norm = f"{float(odometer_km):.0f}" if odometer_km is not None else ""

    return f"{v_norm}|{d_norm}|{l_norm}|{c_norm}|{inv_norm}|{odo_norm}"
