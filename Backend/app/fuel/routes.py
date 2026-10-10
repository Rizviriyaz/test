import json
import logging
import re
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
    fetch_fuel_transactions_for_analytics,
    get_fuel_imports_list,
    get_fuel_transactions_list,
)
from app.fuel.importer import FuelImportError, execute_fuel_import, generate_fuel_preview
from app.fuel.schemas import (
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
from app.fuel.anomalies import detect_fuel_waste_and_anomalies
from app.fuel.wialon_distance import (
    get_vehicle_distance,
    get_fleet_distances_map,
    get_fleet_telematics_map,
    get_vehicle_telematics_detail,
    match_fuel_vehicle_to_wialon,
)
from app.vehicles import get_all_vehicles
from app.wialon import login

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/fuel",
    tags=["Fuel Management"],
)



def _get_bearer_token(request: Request) -> Optional[str]:
    """Extract raw bearer token from the Authorization header."""
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


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
    Requires authenticated bearer token (enforced by main API middleware).
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="An Excel file must be provided.")

    lower_filename = file.filename.lower()
    if not (lower_filename.endswith(".xlsx") or lower_filename.endswith(".xlsm") or lower_filename.endswith(".xltx")):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{file.filename}'. Please upload an Excel spreadsheet (.xlsx).",
        )

    try:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

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
    Requires authenticated bearer token (enforced by main API middleware).
    """
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
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        import_result = await run_in_threadpool(
            execute_fuel_import,
            file_bytes,
            file.filename,
            confirmed_mapping,
            imported_by,
            bearer_token,
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
    records, total = await run_in_threadpool(get_fuel_imports_list, limit, offset, bearer_token)
    return FuelImportsListResponse(
        count=len(records),
        imports=[FuelImportItem(**r) for r in records],
    )


@router.get(
    "/transactions",
    response_model=FuelTransactionsListResponse,
    summary="List fuel transactions with filtering",
    description="Retrieve paginated fuel transactions with optional filters for date range, vehicle identifier, fuel type, and import ID for strictly matched Wialon vehicles.",
)
async def list_fuel_transactions(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Vehicle name, plate, or registration filter"),
    fuel_type: Optional[str] = Query(None, description="Fuel product type filter"),
    import_id: Optional[int] = Query(None, description="Filter by specific import ID"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(50, ge=1, le=1000, description="Number of items per page"),
):
    """List and filter fuel transactions from Supabase strictly for matched Wialon vehicles."""
    bearer_token = _get_bearer_token(request)
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
        "import_id": import_id,
    }

    raw_transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_records = _filter_wialon_matched_transactions(raw_transactions, wialon_units)

    total = len(matched_records)
    offset = (page - 1) * page_size
    paged = matched_records[offset : offset + page_size]

    return FuelTransactionsListResponse(
        count=len(paged),
        page=page,
        page_size=page_size,
        total=total,
        transactions=[FuelTransactionItem(**r) for r in paged],
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


@router.get(
    "/analytics/summary",
    response_model=FuelSummaryResponse,
    summary="Fleet Fuel Summary Analytics",
    description="Calculates fleet-wide fuel summary metrics including total litres, total expenditure, transaction count, unique vehicles, and fuel type breakdown for matched Wialon vehicles.",
)
async def get_fuel_analytics_summary(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle identifier/plate"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
):
    """Calculate aggregate fleet fuel summary strictly for matched Wialon vehicles."""
    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")

    bearer_token = _get_bearer_token(request)
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    summary = await run_in_threadpool(calculate_fuel_summary, matched_transactions)
    return summary


@router.get(
    "/analytics/trend",
    response_model=FuelTrendResponse,
    summary="Fuel Consumption & Cost Trends",
    description="Chronological fuel consumption, expenditure, and transaction count aggregated by day, week, or month for matched Wialon vehicles.",
)
async def get_fuel_analytics_trend(
    request: Request,
    group_by: str = Query("day", description="Grouping interval: 'day', 'week', or 'month'"),
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle identifier/plate"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
):
    """Calculate chronological fuel trend breakdown strictly for matched Wialon vehicles."""
    clean_group_by = group_by.lower().strip()
    if clean_group_by not in VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported group_by '{group_by}'. Supported values are: 'day', 'week', 'month'.",
        )

    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")

    bearer_token = _get_bearer_token(request)
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    trend = await run_in_threadpool(calculate_fuel_trend, matched_transactions, clean_group_by)
    return trend


@router.get(
    "/analytics/vehicles",
    response_model=VehicleAnalyticsResponse,
    summary="Vehicle Fuel Analytics & Rankings",
    description="Per-vehicle aggregation of fuel consumption, cost, refuel count, and averages strictly for matched Wialon vehicles.",
)
async def get_fuel_analytics_vehicles(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle identifier/plate"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel product type"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=1000, description="Items per page (1-1000)"),
    sort_by: str = Query("litres", description="Sort field: 'litres', 'total_cost', 'transaction_count', 'average_unit_price', 'average_litres_per_transaction', 'vehicle_identifier'"),
    sort_order: str = Query("desc", description="Sort direction: 'asc' or 'desc'"),
):
    """Calculate vehicle-by-vehicle consumption and expenditure metrics strictly for matched Wialon vehicles."""
    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")

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

    bearer_token = _get_bearer_token(request)
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    result = await run_in_threadpool(
        calculate_vehicle_analytics,
        matched_transactions,
        page,
        page_size,
        clean_sort_by,
        clean_sort_order,
    )
    return result


@router.get(
    "/analytics/fuel-types",
    response_model=FuelTypeAnalyticsResponse,
    summary="Fuel Type Analytics Breakdown",
    description="Consumption and cost breakdown grouped by fuel product type (DIESEL, SPECIAL, SUPER) strictly for matched Wialon vehicles.",
)
async def get_fuel_analytics_fuel_types(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle identifier/plate"),
):
    """Calculate breakdown by fuel type strictly for matched Wialon vehicles."""
    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")

    bearer_token = _get_bearer_token(request)
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    result = await run_in_threadpool(calculate_fuel_type_analytics, matched_transactions)
    return result


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
    Dynamically resolves a fuel vehicle to an active Wialon GPS unit and retrieves its
    authoritative distance traveled over the requested date interval from Wialon's native report engine.
    """
    _ = getattr(request.state, "user", None)

    if not vehicle_identifier or not vehicle_identifier.strip():
        raise HTTPException(status_code=400, detail="vehicle_identifier cannot be empty")

    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")

    if date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")

    # 1. Dynamically connect to Wialon and fetch available units
    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)

    # 2. Dynamically resolve vehicle to Wialon unit (zero hardcoding)
    matched_unit = match_fuel_vehicle_to_wialon(vehicle_identifier, wialon_units)
    if not matched_unit:
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle '{vehicle_identifier}' does not have an active matching Wialon GPS unit.",
        )

    # 3. Authoritatively retrieve distance traveled from Wialon
    dist_info = await run_in_threadpool(
        get_vehicle_distance,
        int(matched_unit["id"]),
        date_from,
        date_to,
        sid,
    )

    return VehicleDistanceResponse(
        vehicle_identifier=vehicle_identifier,
        wialon_unit_id=int(matched_unit["id"]),
        wialon_unit_name=str(matched_unit.get("nm", "")).strip(),
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
) -> Tuple[str, str]:
    """Dynamically determine start and end dates from transactions when not provided."""
    if date_from and date_to:
        return date_from, date_to
    dates = [t.get("transaction_date", "")[:10] for t in transactions if t.get("transaction_date")]
    eff_from = date_from or (min(dates) if dates else "2026-08-01")
    eff_to = date_to or (max(dates) if dates else "2026-08-31")
    return eff_from, eff_to


@router.get(
    "/analytics/efficiency",
    response_model=FleetEfficiencyResponse,
    summary="Get overall fleet fuel efficiency, telematics activity, and driving vs idling estimates",
    description="Calculates weighted fleet efficiency (L/100 km), cost/km, qualifying driving hours, idle hours, and estimated idle fuel waste using Supabase transactions and Wialon native telematics strictly for matched Wialon vehicles.",
)
async def get_fleet_fuel_efficiency(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle registration"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
):
    _ = getattr(request.state, "user", None)
    bearer_token = _get_bearer_token(request)

    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")

    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    eff_from, eff_to = _resolve_effective_dates(matched_transactions, date_from, date_to)
    telematics_map = await run_in_threadpool(get_fleet_telematics_map, eff_from, eff_to, sid)

    result = await run_in_threadpool(
        calculate_fleet_efficiency,
        matched_transactions,
        telematics_map,
        wialon_units,
        idle_rate_lph,
    )
    return result


@router.get(
    "/analytics/efficiency/vehicles",
    response_model=VehicleEfficiencyResponse,
    summary="Get per-vehicle fuel efficiency and performance rankings",
    description="Returns paginated vehicle efficiency records with driving/idling hours, estimated idle fuel, L/100 km, cost/km, tracking status, and multi-field sorting strictly for matched Wialon vehicles.",
)
async def get_vehicles_fuel_efficiency(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle registration substring"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
    status: Optional[str] = Query(None, description="Filter by status ('matched', 'not_tracked', 'no_distance_data', 'ambiguous')"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=500, description="Items per page"),
    sort_by: str = Query("litres", description="Sort field ('litres', 'cost', 'distance', 'efficiency', 'cost_per_km', 'transactions', 'idle_hours', 'driving_hours', 'estimated_idle_fuel', 'vehicle_identifier')"),
    sort_order: str = Query("desc", description="Sort direction ('asc' or 'desc')"),
):
    _ = getattr(request.state, "user", None)
    bearer_token = _get_bearer_token(request)

    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")

    sort_field = sort_by.strip().lower()
    if sort_field not in VALID_EFFICIENCY_SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid sort_by '{sort_by}'. Supported fields: {sorted(list(VALID_EFFICIENCY_SORT_FIELDS))}",
        )

    sort_ord = sort_order.strip().lower()
    if sort_ord not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="Invalid sort_order. Must be 'asc' or 'desc'.")

    if status and status.strip().lower() not in VALID_EFFICIENCY_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Supported statuses: {sorted(list(VALID_EFFICIENCY_STATUSES))}",
        )

    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": None,  # Filter vehicle at post-aggregation level if substring
        "fuel_type": fuel_type,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    eff_from, eff_to = _resolve_effective_dates(matched_transactions, date_from, date_to)
    telematics_map = await run_in_threadpool(get_fleet_telematics_map, eff_from, eff_to, sid)

    items = await run_in_threadpool(
        calculate_vehicle_efficiency_records,
        matched_transactions,
        telematics_map,
        wialon_units,
        idle_rate_lph,
    )

    result = await run_in_threadpool(
        paginate_and_sort_vehicle_efficiency,
        items,
        page,
        page_size,
        sort_field,
        sort_ord,
        status,
        vehicle_identifier,
    )
    return result


@router.get(
    "/analytics/waste",
    response_model=FuelWasteResponse,
    summary="Detect fuel waste, excessive idling, and refuelling anomalies",
    description="Analyzes fuel transactions and Wialon activity to identify excessive idling, rapid consecutive refuels, volume spikes, and odometer regressions strictly for matched Wialon vehicles.",
)
async def get_fuel_waste_and_anomalies(
    request: Request,
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle registration"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
    idle_threshold_hours: float = Query(5.0, ge=0.5, le=100.0, description="Excessive idle threshold in hours"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
):
    _ = getattr(request.state, "user", None)
    bearer_token = _get_bearer_token(request)

    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")

    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
    }

    transactions_full = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions_full, wialon_units)

    eff_from, eff_to = _resolve_effective_dates(matched_transactions, date_from, date_to)
    telematics_map = await run_in_threadpool(get_fleet_telematics_map, eff_from, eff_to, sid)

    vehicle_items = await run_in_threadpool(
        calculate_vehicle_efficiency_records,
        matched_transactions,
        telematics_map,
        wialon_units,
        idle_rate_lph,
    )

    waste_report = await run_in_threadpool(
        detect_fuel_waste_and_anomalies,
        matched_transactions,
        vehicle_items,
        idle_threshold_hours,
        idle_rate_lph,
    )
    return waste_report


@router.get(
    "/analytics/vehicle-detail",
    response_model=VehicleFuelDetailResponse,
    summary="Get complete vehicle fuel intelligence drill-down",
    description="Returns detailed performance breakdown, activity segregation, full transaction history, specific anomalies, and disclosed formulas for a single matched Wialon vehicle.",
)
async def get_vehicle_fuel_detail(
    request: Request,
    vehicle_identifier: str = Query(..., description="Vehicle registration plate identifier"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    idle_rate_lph: float = Query(2.0, ge=0.1, le=20.0, description="Configured idle consumption coefficient in L/h"),
):
    _ = getattr(request.state, "user", None)
    bearer_token = _get_bearer_token(request)

    if not vehicle_identifier or not vehicle_identifier.strip():
        raise HTTPException(status_code=400, detail="vehicle_identifier is required")

    clean_vid = vehicle_identifier.strip()
    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_unit = match_fuel_vehicle_to_wialon(clean_vid, wialon_units)

    if not matched_unit or matched_unit.get("ambiguous"):
        raise HTTPException(
            status_code=404,
            detail=f"Vehicle '{clean_vid}' is not an active matched Wialon GPS fleet vehicle.",
        )

    # Fetch all transactions for this specific vehicle
    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": clean_vid,
        "fuel_type": None,
    }

    raw_records, total_txns = await run_in_threadpool(
        get_fuel_transactions_list,
        filters,
        500,
        0,
        bearer_token,
    )
    tx_items = [FuelTransactionItem(**r) for r in raw_records]

    eff_from, eff_to = _resolve_effective_dates(raw_records, date_from, date_to)

    telematics_detail = {
        "distance_km": 0.0,
        "trip_count": 0,
        "driving_duration_hours": 0.0,
        "idle_duration_hours": 0.0,
        "engine_hours": 0.0,
        "stopped_hours": 0.0,
        "idle_events_count": 0,
        "idle_events": [],
        "trips": [],
        "has_data": False,
    }

    if matched_unit and matched_unit.get("id"):
        telematics_detail = await run_in_threadpool(
            get_vehicle_telematics_detail,
            int(matched_unit["id"]),
            eff_from,
            eff_to,
            sid,
        )

    # Compute vehicle summary item
    telematics_map = {
        str(matched_unit.get("nm", "")).strip(): {
            "vehicle_name": str(matched_unit.get("nm", "")).strip(),
            "mileage_km": telematics_detail["distance_km"],
            "driving_hours": telematics_detail["driving_duration_hours"],
            "idle_hours": telematics_detail["idle_duration_hours"],
            "engine_hours": telematics_detail["engine_hours"],
            "has_telemetry": telematics_detail["has_data"],
        }
    }

    v_records = await run_in_threadpool(
        calculate_vehicle_efficiency_records,
        raw_records,
        telematics_map,
        wialon_units,
        idle_rate_lph,
    )

    v_summary = v_records[0] if v_records else VehicleEfficiencyItem(
        vehicle_identifier=clean_vid,
        litres=0.0,
        total_cost=0.0,
        transaction_count=0,
        status="matched",
        estimation_status="unavailable",
    )

    # Run anomaly detection for this vehicle
    waste_res = await run_in_threadpool(
        detect_fuel_waste_and_anomalies,
        raw_records,
        [v_summary],
        5.0,
        idle_rate_lph,
    )

    calculation_transparency = {
        "period_date_from": eff_from,
        "period_date_to": eff_to,
        "idle_coefficient_lph": idle_rate_lph,
        "idle_formula": "Estimated Idle Fuel (L) = Qualifying Idle Duration (hours) × Idle Coefficient (L/h)",
        "driving_formula": "Estimated Driving Fuel (L) = Fuel Purchased (L) - Estimated Idle Fuel (L)",
        "efficiency_formula": "Fuel Efficiency (L/100km) = (Fuel Purchased / GPS Distance km) × 100",
        "telematics_source": "Wialon Native Reports (Template 4 Trips & Template 1 Engine Hours)",
        "transaction_source": "Supabase public.fuel_transactions table",
        "disclaimer": "Purchased fuel reflects refuelling events during the selected window. Instantaneous consumption split is estimated based on verified stationary engine runtime.",
    }

    return VehicleFuelDetailResponse(
        vehicle_identifier=clean_vid,
        vehicle_summary=v_summary,
        transactions=tx_items,
        activity_breakdown=telematics_detail,
        anomalies=waste_res.anomalies,
        calculation_transparency=calculation_transparency,
    )


@router.get(
    "/analytics/efficiency/trend",
    response_model=EfficiencyTrendResponse,
    summary="Get chronological fuel efficiency trends over time",
    description="Returns chronological periods grouped by day, week, or month with weighted L/100 km and cost/km strictly for matched Wialon vehicles.",
)
async def get_fuel_efficiency_trend(
    request: Request,
    group_by: str = Query("day", description="Grouping interval: 'day', 'week', or 'month'"),
    date_from: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    vehicle_identifier: Optional[str] = Query(None, description="Filter by vehicle registration"),
    fuel_type: Optional[str] = Query(None, description="Filter by fuel type"),
):
    _ = getattr(request.state, "user", None)
    bearer_token = _get_bearer_token(request)

    group_clean = group_by.strip().lower()
    if group_clean not in VALID_GROUP_BY:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid group_by '{group_by}'. Supported values: {sorted(list(VALID_GROUP_BY))}",
        )

    _validate_date_param(date_from, "date_from")
    _validate_date_param(date_to, "date_to")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")

    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "vehicle_identifier": vehicle_identifier,
        "fuel_type": fuel_type,
    }

    transactions = await run_in_threadpool(
        fetch_fuel_transactions_for_analytics,
        filters,
        bearer_token,
    )

    sid = await run_in_threadpool(login)
    wialon_units = await run_in_threadpool(get_all_vehicles, sid)
    matched_transactions = _filter_wialon_matched_transactions(transactions, wialon_units)

    eff_from, eff_to = _resolve_effective_dates(matched_transactions, date_from, date_to)
    telematics_map = await run_in_threadpool(get_fleet_telematics_map, eff_from, eff_to, sid)

    result = await run_in_threadpool(
        calculate_efficiency_trend,
        matched_transactions,
        telematics_map,
        wialon_units,
        group_clean,
    )
    return result



