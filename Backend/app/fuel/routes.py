import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from app.fuel.analytics import (
    VALID_GROUP_BY,
    VALID_SORT_FIELDS,
    VALID_SORT_ORDERS,
    calculate_fuel_summary,
    calculate_fuel_trend,
    calculate_fuel_type_analytics,
    calculate_vehicle_analytics,
)
from app.fuel.db import (
    FuelDataFetchError,
    fetch_fuel_transactions_for_analytics,
    get_fuel_imports_list,
    get_fuel_vehicle_identifiers,
    get_fuel_transactions_list,
)
from app.fuel import classification
from app.fuel.importer import FuelImportError, execute_fuel_import, generate_fuel_preview, reconcile_fuel_import
from app.fuel.scope import (
    EXCLUDED_REASONS,
    NO_TRANSACTIONS_WARNING,
    AMBIGUITY_PLATE_CODE_CONFLICT,
    REASON_AMBIGUOUS,
    ScopeContext,
    UnitResolver,
    build_scope,
    resolve_effective_dates,
    summarize_excluded,
)
from app.fuel.schemas import (
    ExcludedTransactionGroup,
    ExcludedTransactionsResponse,
    FuelImportItem,
    FuelImportPreviewResponse,
    FuelImportResultResponse,
    FuelImportsListResponse,
    FuelSummaryResponse,
    FuelTransactionItem,
    FuelTransactionsListResponse,
    FuelTrendResponse,
    FuelTypeAnalyticsResponse,
    VehicleAnalyticsResponse,
    VehicleDistanceResponse,
    FleetEfficiencyResponse,
    VehicleEfficiencyItem,
    VehicleEfficiencyResponse,
    EfficiencyTrendItem,
    EfficiencyTrendResponse,
    FuelAnomalyItem,
    FuelWasteResponse,
    VehicleFuelDetailResponse,
)
from app.fuel.efficiency import (
    VALID_EFFICIENCY_SORT_FIELDS,
    VALID_EFFICIENCY_STATUSES,
    calculate_fleet_efficiency,
    calculate_vehicle_efficiency_records,
    paginate_and_sort_vehicle_efficiency,
    calculate_efficiency_trend,
)
from app.fuel.anomalies import IDLE_THRESHOLD_MODES, detect_fuel_waste_and_anomalies
from app.fuel.wialon_distance import (
    get_vehicle_distance,
    get_fleet_distances_map,
    get_fleet_telematics_bundle,
    get_fleet_telematics_map,
    get_fleet_trips,
    get_units_trips_for_range,
    get_vehicle_telematics_detail,
    match_fuel_vehicle_to_wialon,
)
from app.performance_diagnostics import request_trace_id
from app.utils.config import get_env_int
from app.utils.ttl_cache import InFlight
from app.wialon import WialonAPIError
from app.vehicles import get_all_vehicles, extract_registration_plate
from app.wialon import login

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/fuel",
    tags=["Fuel Management"],
)



MAX_IMPORT_FILE_BYTES = get_env_int("FUEL_IMPORT_MAX_FILE_BYTES", 10 * 1024 * 1024)


async def _read_upload(file: UploadFile) -> bytes:
    """Read an uploaded statement up to MAX_IMPORT_FILE_BYTES; 413 above it, 400 when empty."""
    file_bytes = await file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(file_bytes) > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The file exceeds the {MAX_IMPORT_FILE_BYTES // (1024 * 1024)} MB import limit. Split the statement into smaller files.",
        )
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return file_bytes


# Roles allowed to write fuel data (import and its preview). Read-only roles such as 'viewer' get 403.
FUEL_IMPORT_ROLES = frozenset({"admin", "manager", "fleet_manager"})


def _require_fuel_import_role(request: Request) -> None:
    """Deny fuel import unless the authenticated profile (set by the auth middleware) has a write role."""
    user = getattr(request.state, "user", None)
    role = user.get("role") if isinstance(user, dict) else None
    if role not in FUEL_IMPORT_ROLES:
        raise HTTPException(status_code=403, detail="Your role does not permit importing fuel statements.")


def _get_bearer_token(request: Request) -> Optional[str]:
    """Extract raw bearer token from the Authorization header."""
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


FUEL_DATA_UNAVAILABLE_DETAIL = (
    "Fuel data source unavailable; results withheld to avoid partial KPIs. The read was retried and still failed; "
    "try again in a moment."
)

_shared_reads = InFlight()


async def _fetch_transactions(filters: Dict[str, Any], bearer_token: Optional[str]) -> List[Dict[str, Any]]:
    """Read all matching fuel transactions, or fail with 503 rather than compute on partial data."""
    try:
        return await run_in_threadpool(fetch_fuel_transactions_for_analytics, filters, bearer_token)
    except FuelDataFetchError:
        raise HTTPException(status_code=503, detail=FUEL_DATA_UNAVAILABLE_DETAIL)


def _bundle_warnings(bundle: Dict[str, Any]) -> List[str]:
    return list(bundle.get("warnings") or [])


def _attach_telematics_status(response: Any, bundle: Dict[str, Any], items: Optional[List[Any]] = None) -> None:
    """Copy fleet telematics completeness onto a response; partial if any listed vehicle lacks Wialon values."""
    status = bundle.get("status", "complete")
    warnings = _bundle_warnings(bundle)
    if items is not None:
        unknown = [i for i in items if getattr(i, "status", None) in ("matched", "no_distance_data")
                   and getattr(i, "telemetry_status", None) in ("partial", "unavailable")]
        if unknown:
            status = "partial"
            warnings.append(f"{len(unknown)} vehicle(s) have unavailable or partial Wialon telematics; affected values are null.")
    response.telematics_status = status
    response.data_warnings = warnings


def _to_tx_item(record: Dict[str, Any]) -> FuelTransactionItem:
    """Build a transaction item, labelling negative-litre rows as refund adjustments."""
    item = FuelTransactionItem(**record)
    if item.litres < 0:
        item.transaction_kind = "refund_adjustment"
    return item


def _filter_wialon_matched_transactions(
    transactions: List[Dict[str, Any]],
    wialon_units: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Strictly filter transactions to include ONLY records belonging to confidently matched Wialon units."""
    if not transactions or not wialon_units:
        return []
    matched = []
    for tx in transactions:
        vid = tx.get("vehicle_identifier")
        m = match_fuel_vehicle_to_wialon(vid, wialon_units)
        if m and not m.get("ambiguous"):
            matched.append(tx)
    return matched


@router.post(
    "/import/preview",
    response_model=FuelImportPreviewResponse,
    summary="Preview Excel fuel import file",
    description="Analyzes an uploaded .xlsx fuel statement, automatically detects headers, suggests canonical mappings, filters non-data rows, and returns sample candidate rows without inserting into the database.",
)
async def preview_fuel_import(
    request: Request,
    file: UploadFile = File(..., description="Excel fuel statement file (.xlsx)"),
):
    """
    Preview and dynamically map fuel spreadsheet data before database commit.
    Requires authenticated bearer token (enforced by main API middleware) and an import role.
    """
    _require_fuel_import_role(request)
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="An Excel file must be provided.")

    lower_filename = file.filename.lower()
    if not (lower_filename.endswith(".xlsx") or lower_filename.endswith(".xlsm") or lower_filename.endswith(".xltx")):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{file.filename}'. Please upload an Excel spreadsheet (.xlsx).",
        )

    try:
        file_bytes = await _read_upload(file)

        preview_data = await run_in_threadpool(generate_fuel_preview, file_bytes, file.filename)
        return preview_data

    except FuelImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected error during fuel preview: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to parse the fuel statement. Please ensure the file is a valid, unencrypted Excel workbook.",
        )


@router.post(
    "/import",
    response_model=FuelImportResultResponse,
    summary="Execute Excel fuel import into Supabase",
    description="Imports validated fuel transactions into Supabase using the user-confirmed column mapping, creates fuel_imports audit record, deduplicates rows, and returns complete import statistics.",
)
async def import_fuel_statement(
    request: Request,
    file: UploadFile = File(..., description="Excel fuel statement file (.xlsx)"),
    mapping: str = Form(..., description="JSON string of confirmed canonical field mappings"),
):
    """
    Execute full fuel statement ingestion.
    Requires authenticated bearer token (enforced by main API middleware) and an import role.
    """
    _require_fuel_import_role(request)
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="An Excel file must be provided.")

    lower_filename = file.filename.lower()
    if not (lower_filename.endswith(".xlsx") or lower_filename.endswith(".xlsm") or lower_filename.endswith(".xltx")):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{file.filename}'. Please upload an Excel spreadsheet (.xlsx).",
        )

    # Parse JSON mapping string
    try:
        confirmed_mapping = json.loads(mapping)
        if not isinstance(confirmed_mapping, dict):
            raise ValueError()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid mapping parameter. Expected a valid JSON object of canonical-to-header mappings.",
        )

    # User ID from authentication middleware
    user = getattr(request.state, "user", None)
    imported_by = user.get("id") if user and isinstance(user, dict) else "system"
    bearer_token = _get_bearer_token(request)

    try:
        file_bytes = await _read_upload(file)

        import_result = await run_in_threadpool(
            execute_fuel_import,
            file_bytes,
            file.filename,
            confirmed_mapping,
            imported_by,
            bearer_token,
            getattr(request.state, "started", None),
        )
        return import_result

    except FuelImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Unexpected error during fuel import: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="An error occurred while importing the fuel statement into the database.",
        )


@router.get(
    "/imports",
    response_model=FuelImportsListResponse,
    summary="Get recent fuel imports history",
    description="Retrieve a paginated list of previous fuel statement imports and their status.",
)
async def list_fuel_imports(
    request: Request,
    limit: int = Query(50, ge=1, le=100, description="Max number of records to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """List recent fuel imports from Supabase."""
    bearer_token = _get_bearer_token(request)
    try:
        records, total = await run_in_threadpool(get_fuel_imports_list, limit, offset, bearer_token)
    except FuelDataFetchError:
        raise HTTPException(status_code=503, detail="Fuel import history unavailable; please retry.") from None
    return FuelImportsListResponse(
        count=len(records),
        imports=[FuelImportItem(**r) for r in records],
    )


@router.post(
    "/imports/{import_id}/reconcile",
    response_model=FuelImportItem,
    summary="Reconcile a fuel import with the stored transactions",
    description="Recounts the transactions stored under one import and writes the corrected counts and a terminal status to its history record (e.g. an import left 'processing' by an interrupted run). Inserts and deletes nothing.",
)
async def reconcile_import(request: Request, import_id: int):
    """Requires an import role, like the import itself."""
    _require_fuel_import_role(request)
    try:
        record = await run_in_threadpool(reconcile_fuel_import, import_id, _get_bearer_token(request))
    except FuelImportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return FuelImportItem(**record)


async def _build_scope(filters: Dict[str, Any], bearer_token: Optional[str],
                       login_fn: Optional[Any] = None, units_fn: Optional[Any] = None) -> ScopeContext:
    """
    Shared KPI scope for every analytics endpoint: fetch -> exact vehicle filter -> partition -> fuel type.
    Fuel read failures are 503 (no partial KPIs). Wialon is contacted only when there are transactions.
    """
    # One Fuel Performance load fires ~8 analytics requests at once with mostly identical filters: concurrent identical
    # reads share one Supabase read / Wialon unit search. Nothing is retained, so no request sees older data than before.
    # Reads with the caller's token (no service-role key) are keyed by token, since row-level security may differ.
    read_ms = [0.0]

    def fetch(f: Dict[str, Any]) -> List[Dict[str, Any]]:
        auth_key = None if os.getenv("SUPABASE_SERVICE_ROLE_KEY") else bearer_token
        started = time.perf_counter()
        try:
            rows = _shared_reads.run(
                ("transactions", auth_key, tuple(sorted(f.items()))),
                lambda: fetch_fuel_transactions_for_analytics(f, bearer_token),
            )
        finally:
            read_ms[0] = (time.perf_counter() - started) * 1000
        return [dict(r) for r in rows or []]

    def units(sid: str) -> List[Dict[str, Any]]:
        return list(_shared_reads.run(("units", sid), lambda: get_all_vehicles(sid)) or [])

    started = time.perf_counter()
    try:
        scope = await run_in_threadpool(
            build_scope,
            filters,
            fetch,
            login_fn or (lambda: login()),
            units_fn or units,
            lambda: get_fuel_vehicle_identifiers(bearer_token),
        )
    except FuelDataFetchError as exc:
        # The cause (which page, which failure, how many attempts) is logged by the read itself
        logger.warning("Fuel scope unavailable request_id=%s read_ms=%.0f: %s", request_trace_id.get(), read_ms[0], exc)
        raise HTTPException(status_code=503, detail=FUEL_DATA_UNAVAILABLE_DETAIL)
    # read_ms includes waiting on a shared read another request started; scope_ms adds Wialon units + classification
    logger.debug(
        "PERF fuel_scope request_id=%s read_ms=%.0f scope_ms=%.0f rows=%d",
        request_trace_id.get(), read_ms[0], (time.perf_counter() - started) * 1000, scope.fetched_count,
    )
    return scope


async def _resolve_single_vehicle(identifier: str, wialon_units: List[Dict[str, Any]],
                                  bearer_token: Optional[str]) -> Dict[str, Any]:
    """
    Resolve one identifier with the KPI scope's rules, including the plate-code conflict check against every
    imported identifier. Raises 404 (no unit), 409 (several units or a plate-code conflict) or 503 (fuel read failed).
    """
    resolver = UnitResolver(wialon_units, registry_fn=lambda: get_fuel_vehicle_identifiers(bearer_token))
    try:
        res = await run_in_threadpool(resolver.resolve, identifier)
    except FuelDataFetchError:
        raise HTTPException(status_code=503, detail=FUEL_DATA_UNAVAILABLE_DETAIL)
    if res["status"] == REASON_AMBIGUOUS:
        if res.get("ambiguity") == AMBIGUITY_PLATE_CODE_CONFLICT:
            detail = (f"Vehicle '{identifier}' matches Wialon unit {', '.join(res['candidates'])} by registration number "
                      f"only, and other plate codes share that number ({', '.join(res.get('conflicting_identifiers') or [])}); "
                      "not attributed.")
        else:
            detail = f"Vehicle '{identifier}' matches several Wialon units ({', '.join(res['candidates'])}); not attributed."
        raise HTTPException(status_code=409, detail=detail)
    return res


def _validate_range(date_from: Optional[str], date_to: Optional[str]) -> None:
    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")


def _period_days(date_from: str, date_to: str) -> int:
    try:
        d0 = datetime.strptime(str(date_from)[:10], "%Y-%m-%d").date()
        d1 = datetime.strptime(str(date_to)[:10], "%Y-%m-%d").date()
        return max(1, (d1 - d0).days + 1)
    except ValueError:
        return 1


def _validate_idle_mode(mode: str) -> str:
    clean = (mode or "").strip().lower()
    if clean not in IDLE_THRESHOLD_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid idle_threshold_mode '{mode}'. Supported: {list(IDLE_THRESHOLD_MODES)}")
    return clean


def _with_period(scope: ScopeContext, dates: Optional[Tuple[str, str]]) -> Dict[str, Any]:
    """excluded_scope plus the effective period, so drill-downs (vehicle detail) use the fleet's window."""
    out = dict(scope.excluded_scope)
    out["period"] = {"date_from": dates[0], "date_to": dates[1]} if dates else None
    return out


def _apply_excluded_to_efficiency(result: FleetEfficiencyResponse, scope: ScopeContext) -> None:
    """Legacy unmatched_* fields come from the shared partition (unmatched + ambiguous identifiers)."""
    ex = scope.excluded_scope
    um, amb = ex["unmatched"], ex["ambiguous"]
    result.unmatched_transactions_count = um["transaction_count"] + amb["transaction_count"]
    result.unmatched_litres = round(um["litres"] + amb["litres"], 2)
    result.unmatched_cost = round(um["cost"] + amb["cost"], 2)
    result.unmatched_vehicle_count = um["identifier_count"] + amb["identifier_count"]
    result.excluded_scope = ex
    result.value_classification = classification.FLEET_EFFICIENCY


@router.get(
    "/transactions",
    response_model=FuelTransactionsListResponse,
    summary="List fuel transactions with filtering",
    description="Paginated fuel transactions in the KPI scope (resolving to exactly one Wialon unit; suspected cross-import duplicates excluded). excluded_scope reconciles what was left out.",
)
async def list_fuel_transactions(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter (same Wialon unit or identical identifier)"),
    fuel_type: Optional[str] = Query(None, description="Fuel product type filter"),
    import_id: Optional[int] = Query(None, description="Filter by specific import ID"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=1000, description="Number of items per page"),
):
    _validate_range(date_from, date_to)
    bearer_token = _get_bearer_token(request)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier,
               "fuel_type": fuel_type, "import_id": import_id}
    scope = await _build_scope(filters, bearer_token)
    # Newest first (as the previous database listing returned them)
    matched_records = sorted(scope.matched, key=lambda r: (str(r.get("transaction_date") or ""), r.get("id") or 0), reverse=True)

    total = len(matched_records)
    offset = (page - 1) * page_size
    paged = matched_records[offset : offset + page_size]

    return FuelTransactionsListResponse(
        count=len(paged),
        page=page,
        page_size=page_size,
        total=total,
        transactions=[_to_tx_item(r) for r in paged],
        excluded_scope=scope.excluded_scope,
    )


# ─── Fuel Analytics Endpoints ──────────────────────────────────────────────────


def _validate_date_param(val: Optional[str], param_name: str) -> None:
    """Validate date parameter format."""
    if val and str(val).strip():
        s = str(val).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?)?$", s):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format for '{param_name}': '{val}'. Expected format 'YYYY-MM-DD'.",
            )
        # The pattern accepts impossible dates (2026-02-31, 2026-13-45); reject them here as 400 instead of
        # letting them reach PostgREST (503) or the UAE-bounds parser (500).
        try:
            datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid calendar date for '{param_name}': '{val}'.",
            ) from None


@router.get(
    "/analytics/summary",
    response_model=FuelSummaryResponse,
    summary="Fleet Fuel Summary Analytics",
    description="Net fuel purchased and expenditure for transactions resolving to exactly one Wialon unit. Unmatched, ambiguous and suspected duplicate rows are excluded and reconciled in excluded_scope.",
)
async def get_fuel_analytics_summary(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
):
    _validate_range(date_from, date_to)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    summary = await run_in_threadpool(calculate_fuel_summary, scope.matched)
    summary.excluded_scope = scope.excluded_scope
    summary.value_classification = classification.FUEL_SUMMARY
    return summary


@router.get(
    "/analytics/trend",
    response_model=FuelTrendResponse,
    summary="Fuel Purchase & Cost Trends",
    description="Chronological fuel purchased, expenditure and transaction count by day, week or month, in the KPI scope (matched Wialon units only).",
)
async def get_fuel_analytics_trend(
    request: Request,
    group_by: str = Query("day", description="Grouping interval: 'day', 'week', or 'month'"),
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
):
    clean_group_by = group_by.lower().strip()
    if clean_group_by not in VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported group_by '{group_by}'. Supported values are: 'day', 'week', 'month'.",
        )
    _validate_range(date_from, date_to)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    trend = await run_in_threadpool(calculate_fuel_trend, scope.matched, clean_group_by)
    trend.excluded_scope = scope.excluded_scope
    return trend


@router.get(
    "/analytics/vehicles",
    response_model=VehicleAnalyticsResponse,
    summary="Vehicle Fuel Analytics & Rankings",
    description="Per-Wialon-unit fuel purchased, cost, refuel count and averages (identifiers of one unit combined), in the KPI scope.",
)
async def get_fuel_analytics_vehicles(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=1000, description="Items per page (1-1000)"),
    sort_by: str = Query("litres", description="Sort field: 'litres', 'total_cost', 'transaction_count', 'average_unit_price', 'average_litres_per_transaction', 'vehicle_identifier'"),
    sort_order: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
):
    _validate_range(date_from, date_to)
    clean_sort_by = sort_by.lower().strip()
    clean_sort_order = sort_order.lower().strip()
    if clean_sort_by not in VALID_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by '{sort_by}'. Supported fields: {', '.join(sorted(VALID_SORT_FIELDS))}.",
        )
    if clean_sort_order not in VALID_SORT_ORDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_order '{sort_order}'. Supported values: 'asc', 'desc'.",
        )
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    result = await run_in_threadpool(
        calculate_vehicle_analytics, scope.matched, page, page_size, clean_sort_by, clean_sort_order,
    )
    result.excluded_scope = scope.excluded_scope
    return result


@router.get(
    "/analytics/fuel-types",
    response_model=FuelTypeAnalyticsResponse,
    summary="Fuel Type Analytics Breakdown",
    description="Fuel purchased and cost by fuel product type (DIESEL, SPECIAL, SUPER), in the KPI scope.",
)
async def get_fuel_analytics_fuel_types(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
):
    _validate_range(date_from, date_to)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier}
    scope = await _build_scope(filters, _get_bearer_token(request))
    result = await run_in_threadpool(calculate_fuel_type_analytics, scope.matched)
    result.excluded_scope = scope.excluded_scope
    return result


@router.get(
    "/analytics/excluded",
    response_model=ExcludedTransactionsResponse,
    summary="Transactions excluded from fuel KPIs",
    description="Unmatched, ambiguous and suspected cross-import duplicate transactions, grouped by identifier and reason. Nothing is deleted.",
)
async def get_fuel_excluded_transactions(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
    reason: Optional[str] = Query(None, description="unmatched | ambiguous | suspected_duplicate"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=500, description="Items per page"),
):
    _validate_range(date_from, date_to)
    clean_reason = reason.strip().lower() if reason and reason.strip() else None
    if clean_reason and clean_reason not in EXCLUDED_REASONS:
        raise HTTPException(status_code=400, detail=f"Invalid reason '{reason}'. Supported: {list(EXCLUDED_REASONS)}")
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    groups = summarize_excluded(scope.partition, clean_reason)
    offset = (page - 1) * page_size
    return ExcludedTransactionsResponse(
        page=page,
        page_size=page_size,
        total=len(groups),
        data=[ExcludedTransactionGroup(**g) for g in groups[offset: offset + page_size]],
        excluded_scope=scope.excluded_scope,
    )


@router.get(
    "/analytics/distance",
    response_model=VehicleDistanceResponse,
    summary="Get authoritative Wialon distance traveled for a vehicle over a date range",
)
async def get_fuel_vehicle_distance(
    request: Request,
    vehicle_identifier: str = Query(..., description="Vehicle registration plate identifier"),
    date_from: str = Query(..., description="Start date (YYYY-MM-DD)"),
    date_to: str = Query(..., description="End date (YYYY-MM-DD)"),
):
    """
    Resolves a fuel vehicle to exactly one Wialon unit and returns its Wialon trip distance for the interval.
    An identifier matching several units is a 409 (never guessed).
    """
    _ = getattr(request.state, "user", None)

    if not vehicle_identifier or not vehicle_identifier.strip():
        raise HTTPException(status_code=400, detail="vehicle_identifier cannot be empty")
    _validate_range(date_from, date_to)

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)

    resolved = await _resolve_single_vehicle(vehicle_identifier, wialon_units, _get_bearer_token(request))
    if resolved["status"] != "matched":
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle '{vehicle_identifier}' does not have an active matching Wialon GPS unit.",
        )
    matched_unit = resolved["unit"]

    dist_info = await run_in_threadpool(get_vehicle_distance, int(matched_unit["id"]), date_from, date_to, sid)

    return VehicleDistanceResponse(
        vehicle_identifier=vehicle_identifier,
        wialon_unit_id=int(matched_unit["id"]),
        wialon_unit_name=str(matched_unit.get("nm", "")).strip(),
        registration_plate=extract_registration_plate(matched_unit),
        date_from=date_from,
        date_to=date_to,
        distance=dist_info["distance"],
        unit=dist_info["unit"],
        source=dist_info["source"],
        trip_count=dist_info["trip_count"],
        has_data=dist_info["has_data"],
    )


def _resolve_effective_dates(
    transactions: List[Dict[str, Any]],
    date_from: Optional[str],
    date_to: Optional[str],
) -> Optional[Tuple[str, str]]:
    """Requested dates completed from the transactions; None when unknown (no hardcoded fallback)."""
    return resolve_effective_dates(transactions, date_from, date_to)


@router.get(
    "/analytics/efficiency",
    response_model=FleetEfficiencyResponse,
    summary="Fleet purchase-based efficiency, telematics activity, and driving vs idling estimates",
    description="Purchase-based L/100 km and cost/km (derived, not measured consumption), Wialon driving/idle hours and estimated idle fuel for vehicles resolving to exactly one Wialon unit. Excluded rows are reconciled in excluded_scope.",
)
async def get_fleet_fuel_efficiency(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
):
    _ = getattr(request.state, "user", None)
    _validate_range(date_from, date_to)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    dates = _resolve_effective_dates(scope.matched, date_from, date_to)

    if scope.is_empty or dates is None:
        result = calculate_fleet_efficiency([], {}, [], idle_rate_lph, data_warnings=list(scope.warnings))
    else:
        bundle = await run_in_threadpool(get_fleet_telematics_bundle, dates[0], dates[1], scope.sid)
        result = await run_in_threadpool(
            calculate_fleet_efficiency,
            scope.matched,
            bundle["by_name"],
            scope.units,
            idle_rate_lph,
            telematics_status=bundle["status"],
            data_warnings=_bundle_warnings(bundle),
            duplicate_names=bundle["duplicate_names"],
            mixed_fuel_unit_ids=scope.mixed_fuel_unit_ids,
        )
    _apply_excluded_to_efficiency(result, scope)
    result.excluded_scope = _with_period(scope, dates)
    return result


@router.get(
    "/analytics/efficiency/vehicles",
    response_model=VehicleEfficiencyResponse,
    summary="Get per-vehicle fuel efficiency and performance rankings",
    description="Paginated per-Wialon-unit records with driving/idling hours, estimated idle fuel, purchase-based L/100 km and cost/km. The default list is the KPI scope; status=not_tracked or ambiguous lists excluded identifiers instead.",
)
async def get_vehicles_fuel_efficiency(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Search box: substring of identifier, plate or unit name"),
    vehicle_filter: Optional[str] = Query(None, description="Exact vehicle filter, the same KPI scope rule as the other analytics endpoints (same Wialon unit or identical identifier)"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
    status: Optional[str] = Query(None, description="Filter by status ('matched', 'not_tracked', 'no_distance_data', 'ambiguous')"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=500, description="Items per page"),
    sort_by: str = Query("litres", description="Sort field ('litres', 'cost', 'distance', 'efficiency', 'cost_per_km', 'transactions', 'idle_hours', 'driving_hours', 'estimated_idle_fuel', 'vehicle_identifier')"),
    sort_order: str = Query("desc", description="Sort direction ('asc' or 'desc')"),
):
    _ = getattr(request.state, "user", None)
    _validate_range(date_from, date_to)

    sort_field = sort_by.strip().lower()
    if sort_field not in VALID_EFFICIENCY_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by '{sort_by}'. Supported fields: {sorted(list(VALID_EFFICIENCY_SORT_FIELDS))}",
        )
    sort_ord = sort_order.strip().lower()
    if sort_ord not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="Invalid sort_order. Must be 'asc' or 'desc'.")
    status_clean = status.strip().lower() if status and status.strip() else None
    if status_clean and status_clean not in VALID_EFFICIENCY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Supported statuses: {sorted(list(VALID_EFFICIENCY_STATUSES))}",
        )

    # The search box stays a substring match over the aggregated records; vehicle_filter is the exact KPI-scope filter,
    # so the table under a selected vehicle shows exactly the rows the KPI cards count.
    filters = {"date_from": date_from, "date_to": date_to,
               "vehicle_identifier": vehicle_filter.strip() if vehicle_filter and vehicle_filter.strip() else None,
               "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    bundle = None

    if status_clean in ("not_tracked", "ambiguous"):
        # Excluded identifiers, listed for review (never in KPIs); no telematics needed.
        rows = scope.partition.unmatched + scope.partition.ambiguous
        items = await run_in_threadpool(calculate_vehicle_efficiency_records, rows, {}, scope.units, idle_rate_lph)
    else:
        dates = _resolve_effective_dates(scope.matched, date_from, date_to)
        items = []
        if not scope.is_empty and dates is not None:
            bundle = await run_in_threadpool(get_fleet_telematics_bundle, dates[0], dates[1], scope.sid)
            items = await run_in_threadpool(
                calculate_vehicle_efficiency_records,
                scope.matched,
                bundle["by_name"],
                scope.units,
                idle_rate_lph,
                duplicate_names=bundle["duplicate_names"],
                mixed_fuel_unit_ids=scope.mixed_fuel_unit_ids,
            )

    result = await run_in_threadpool(
        paginate_and_sort_vehicle_efficiency, items, page, page_size, sort_field, sort_ord, status_clean, vehicle_identifier,
    )
    if bundle is not None:
        _attach_telematics_status(result, bundle, items)
    elif scope.is_empty:
        result.data_warnings = list(scope.warnings)
    result.excluded_scope = scope.excluded_scope
    result.value_classification = classification.VEHICLE_EFFICIENCY
    return result


@router.get(
    "/analytics/waste",
    response_model=FuelWasteResponse,
    summary="Detect fuel waste, excessive idling, and refuelling anomalies",
    description="Excessive idling (per-day threshold by default), rapid refuels, volume spikes, odometer issues and duplicates for vehicles resolving to exactly one Wialon unit, plus info items for excluded identifiers.",
)
async def get_fuel_waste_and_anomalies(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
    idle_threshold_hours: float = Query(5.0, ge=0.5, le=100.0, description="Excessive idle threshold in hours"),
    idle_threshold_mode: str = Query("per_day", description="per_day (threshold x days in period) or total (whole period)"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
):
    _ = getattr(request.state, "user", None)
    _validate_range(date_from, date_to)
    mode = _validate_idle_mode(idle_threshold_mode)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    dates = _resolve_effective_dates(scope.matched, date_from, date_to)

    bundle = None
    vehicle_items: List[VehicleEfficiencyItem] = []
    if not scope.is_empty and dates is not None:
        bundle = await run_in_threadpool(get_fleet_telematics_bundle, dates[0], dates[1], scope.sid)
        vehicle_items = await run_in_threadpool(
            calculate_vehicle_efficiency_records,
            scope.matched,
            bundle["by_name"],
            scope.units,
            idle_rate_lph,
            duplicate_names=bundle["duplicate_names"],
            mixed_fuel_unit_ids=scope.mixed_fuel_unit_ids,
        )

    waste_report = await run_in_threadpool(
        detect_fuel_waste_and_anomalies,
        scope.matched,
        vehicle_items,
        idle_threshold_hours,
        idle_rate_lph,
        summarize_excluded(scope.partition),
        mode,
        _period_days(*dates) if dates else None,
    )
    if bundle is not None:
        _attach_telematics_status(waste_report, bundle, vehicle_items)
    else:
        waste_report.data_warnings = list(scope.warnings)
    waste_report.excluded_scope = scope.excluded_scope
    waste_report.value_classification = classification.WASTE
    return waste_report


def _unavailable_activity(warning: str) -> Dict[str, Any]:
    return {
        "distance_km": None,
        "trip_count": 0,
        "driving_duration_hours": None,
        "idle_duration_hours": None,
        "engine_hours": None,
        "stopped_hours": None,
        "idle_events_count": None,
        "idle_events_complete": False,
        "idle_events": [],
        "trips": [],
        "has_data": False,
        "status": "unavailable",
        "driving_basis": "unavailable",
        "distance_source": "unavailable",
        "warnings": [warning],
    }


@router.get(
    "/analytics/vehicle-detail",
    response_model=VehicleFuelDetailResponse,
    summary="Get complete vehicle fuel intelligence drill-down",
    description="Performance breakdown, activity, transactions, anomalies and disclosed formulas for one vehicle resolving to exactly one Wialon unit (all identifiers of that unit combined).",
)
async def get_vehicle_fuel_detail(
    request: Request,
    vehicle_identifier: str = Query(..., description="Vehicle registration plate identifier"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
    idle_threshold_hours: float = Query(5.0, ge=0.5, le=100.0, description="Excessive idle threshold in hours"),
    idle_threshold_mode: str = Query("per_day", description="per_day (threshold x days in period) or total (whole period)"),
):
    _ = getattr(request.state, "user", None)
    bearer_token = _get_bearer_token(request)

    if not vehicle_identifier or not vehicle_identifier.strip():
        raise HTTPException(status_code=400, detail="vehicle_identifier is required")
    clean_vid = vehicle_identifier.strip()
    _validate_range(date_from, date_to)
    mode = _validate_idle_mode(idle_threshold_mode)

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    resolved = await _resolve_single_vehicle(clean_vid, wialon_units, bearer_token)
    if resolved["status"] != "matched":
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle '{clean_vid}' is not an active matched Wialon GPS fleet vehicle.",
        )
    matched_unit = resolved["unit"]
    unit_id = int(matched_unit["id"])
    unit_name = str(matched_unit.get("nm", "")).strip()

    # Same scope rules as the KPIs, reusing the unit list fetched above (no second Wialon round trip).
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": clean_vid, "fuel_type": None}
    scope = await _build_scope(filters, bearer_token, login_fn=lambda: sid, units_fn=lambda _sid: wialon_units)
    raw_records = [r for r in scope.matched if r.get("_unit_id") == unit_id]
    raw_records.sort(key=lambda r: str(r.get("transaction_date") or ""), reverse=True)
    tx_items = [_to_tx_item(r) for r in raw_records]

    dates = _resolve_effective_dates(raw_records, date_from, date_to)
    if raw_records and dates is not None:
        telematics_detail = await run_in_threadpool(get_vehicle_telematics_detail, unit_id, dates[0], dates[1], sid)
    else:
        telematics_detail = _unavailable_activity(NO_TRANSACTIONS_WARNING)

    # Telemetry is keyed to this unit only, so a name shared with another unit cannot withhold it.
    telematics_map = {
        unit_name: {
            "vehicle_name": unit_name,
            "mileage_km": telematics_detail["distance_km"],
            "driving_hours": telematics_detail["driving_duration_hours"],
            "idle_hours": telematics_detail["idle_duration_hours"],
            "engine_hours": telematics_detail["engine_hours"],
            "has_telemetry": telematics_detail["has_data"],
        }
    } if raw_records else {}

    v_records = await run_in_threadpool(
        calculate_vehicle_efficiency_records, raw_records, telematics_map, [matched_unit], idle_rate_lph,
    )
    v_summary = next((r for r in v_records if r.wialon_unit_id == unit_id), None) or VehicleEfficiencyItem(
        vehicle_identifier=clean_vid,
        wialon_unit_id=unit_id,
        wialon_unit_name=unit_name,
        litres=0.0,
        total_cost=0.0,
        transaction_count=0,
        status="matched",
        estimation_status="unavailable",
        telemetry_status="unavailable",
        quality_warnings=list(scope.warnings),
    )

    waste_res = await run_in_threadpool(
        detect_fuel_waste_and_anomalies,
        raw_records,
        [v_summary],
        idle_threshold_hours,
        idle_rate_lph,
        None,
        mode,
        _period_days(*dates) if dates else None,
    )

    calculation_transparency = {
        "period_date_from": dates[0] if dates else date_from,
        "period_date_to": dates[1] if dates else date_to,
        "idle_coefficient_lph": idle_rate_lph,
        "idle_threshold_hours": idle_threshold_hours,
        "idle_threshold_mode": mode,
        "idle_formula": "Estimated Idle Fuel (L) = Wialon Idle Hours × Idle Coefficient (L/h) [estimated]",
        "driving_formula": "Estimated Driving Fuel (L) = Fuel Purchased (L) − Estimated Idle Fuel (L) [estimated]",
        "efficiency_formula": "Purchase-based L/100 km = fuel purchased ÷ Wialon trip distance × 100 (derived; not measured consumption)",
        "telematics_source": "Wialon Native Reports (Template 4 Trips & Template 1 Engine Hours)",
        "transaction_source": "Supabase public.fuel_transactions table",
        "disclaimer": "Purchased fuel reflects refuelling events in the selected window, not measured consumption. The driving/idling split is an estimate from Wialon idle hours and the configured idle rate.",
    }

    return VehicleFuelDetailResponse(
        vehicle_identifier=clean_vid,
        vehicle_summary=v_summary,
        transactions=tx_items,
        activity_breakdown=telematics_detail,
        anomalies=waste_res.anomalies,
        calculation_transparency=calculation_transparency,
        excluded_scope=scope.excluded_scope,
        value_classification=classification.VEHICLE_EFFICIENCY,
    )


@router.get(
    "/analytics/efficiency/trend",
    response_model=EfficiencyTrendResponse,
    summary="Get chronological purchase-based efficiency with actual Wialon distance per period",
    description="Periods grouped by day, week or month. Distance is the sum of Wialon trips started in each period (never apportioned by fuel); L/100 km is purchase-based and shown only above the minimum distance.",
)
async def get_fuel_efficiency_trend(
    request: Request,
    group_by: str = Query("day", description="Grouping interval: 'day', 'week', or 'month'"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Exact vehicle filter"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
):
    _ = getattr(request.state, "user", None)
    group_clean = group_by.strip().lower()
    if group_clean not in VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid group_by '{group_by}'. Supported values: {sorted(list(VALID_GROUP_BY))}",
        )
    _validate_range(date_from, date_to)
    filters = {"date_from": date_from, "date_to": date_to, "vehicle_identifier": vehicle_identifier, "fuel_type": fuel_type}
    scope = await _build_scope(filters, _get_bearer_token(request))
    dates = _resolve_effective_dates(scope.matched, date_from, date_to)

    if scope.is_empty or dates is None:
        result = EfficiencyTrendResponse(group_by=group_clean, data=[], data_warnings=list(scope.warnings))
    else:
        trend_warnings: List[str] = []
        bundle = None
        unit_trips = None
        unit_ids = sorted({int(r["_unit_id"]) for r in scope.matched})
        max_units = get_env_int("FUEL_TREND_MAX_UNITS", 200)

        async def fetch_bundle():
            try:
                return await run_in_threadpool(get_fleet_telematics_bundle, dates[0], dates[1], scope.sid)
            except WialonAPIError:
                return None

        if len(unit_ids) <= max_units:
            # The fleet trips report (own session) runs while the bundle's reports do; its result is cached and
            # read by get_units_trips_for_range below. A failure there only means per-unit reports instead.
            bundle, _ = await asyncio.gather(fetch_bundle(), run_in_threadpool(get_fleet_trips, dates[0], dates[1]))
        else:
            bundle = await fetch_bundle()
        if bundle is None:
            # Fuel and cost per period need no telematics: keep them, mark distance unavailable.
            trend_warnings.append("Wialon reports unavailable; distance and efficiency withheld for this request.")
        elif len(unit_ids) > max_units:
            trend_warnings.append(
                f"{len(unit_ids)} units exceed FUEL_TREND_MAX_UNITS={max_units}; per-period distance withheld (never partial)."
            )
        else:
            unit_trips = await run_in_threadpool(
                get_units_trips_for_range, unit_ids, dates[0], dates[1], scope.sid, scope.units
            )
        result = await run_in_threadpool(
            calculate_efficiency_trend,
            scope.matched,
            (bundle or {}).get("by_name", {}),
            scope.units,
            group_clean,
            duplicate_names=(bundle or {}).get("duplicate_names"),
            unit_trips=unit_trips,
            mixed_fuel_unit_ids=scope.mixed_fuel_unit_ids,
        )
        trend_warnings = list(result.data_warnings) + trend_warnings
        if bundle is not None:
            _attach_telematics_status(result, bundle)
            result.data_warnings = list(result.data_warnings) + trend_warnings
        else:
            result.data_warnings = trend_warnings
        if result.units_failed or unit_trips is None:
            result.telematics_status = "partial"
    result.excluded_scope = scope.excluded_scope
    result.value_classification = classification.EFFICIENCY_TREND
    return result
