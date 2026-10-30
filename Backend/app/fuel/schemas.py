"""Pydantic schemas for Fuel Management endpoints."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HeaderMappingItem(BaseModel):
    canonical_field: str = Field(..., description="Standardized target database field")
    excel_header: Optional[str] = Field(None, description="Matched Excel column header")
    confidence: str = Field("exact", description="Confidence level: exact, alias, fuzzy, or manual")
    required: bool = Field(False, description="Whether this field is mandatory")


class FuelImportPreviewResponse(BaseModel):
    file_name: str
    worksheet_name: str
    detected_header_row: int
    rows_found: int
    rows_candidate: int = Field(..., description="Rows that will be validated as fuel transactions (rows_fuel + rows_invalid)")
    rows_ignored: int = Field(..., description="Blank, summary/total and non-fuel rows skipped, as the import counts them")
    rows_fuel: Optional[int] = Field(None, description="Valid fuel transaction rows (before the duplicate check); null when required columns are unmapped")
    rows_invalid: Optional[int] = Field(None, description="Rows the import will report as failed; null when required columns are unmapped")
    rows_non_fuel: int = Field(0, description="Part of rows_ignored: payments, charges or fees without litres")
    invalid_row_details: Optional[List[Dict[str, Any]]] = Field(None, description="Source row and reason of invalid rows (capped)")
    non_fuel_row_details: Optional[List[Dict[str, Any]]] = Field(None, description="Source row and reason of non-fuel rows (capped)")
    detected_columns: List[str]
    suggested_mapping: Dict[str, Optional[str]]
    mapping_details: List[HeaderMappingItem]
    unmapped_columns: List[str]
    missing_required_fields: List[str]
    validation_warnings: List[str]
    sample_rows: List[Dict[str, Any]]
    active_mapping: Dict[str, Optional[str]] = Field(default_factory=dict, description="Mapping the counts were computed with (the confirmed mapping, else the suggestion); send it to the import")
    import_mode: Optional[str] = Field(None, description="'dated' (individual refuels), 'monthly_summary' (no date column; reporting month selected) or null (no date column and no month yet)")
    reporting_month: Optional[str] = Field(None, description="Selected reporting month (YYYY-MM) of a monthly summary")
    assigned_transaction_date: Optional[str] = Field(None, description="Month-end reporting date assigned to every row of a monthly summary (not a refuelling date)")
    partial_month: bool = Field(False, description="The reporting month has not ended yet")
    monthly_summary_enabled: bool = Field(False, description="Statements without a date column can be imported (FUEL_IMPORT_REPLACE)")
    monthly_summary_table: bool = Field(False, description="Monthly summaries are stored in fuel_monthly_summaries by reporting month, without any date (FUEL_MONTHLY_SUMMARY_TABLE); assigned_transaction_date is then null")
    date_column_candidates: List[str] = Field(default_factory=list, description="Headers that could hold the transaction date")
    mapping_confirmation_required: List[str] = Field(default_factory=list, description="Fields whose mapping is ambiguous and must be confirmed (transaction_date, unit_price, total_cost)")
    mapping_confirmed: bool = False
    rows_identical_in_file: int = Field(0, description="Rows identical to another row of the file; all are imported as separate refuels")
    rows_price_mismatch: int = Field(0, description="Rows whose total cost differs from litres x unit price (total kept)")
    rows_cost_derived: int = Field(0, description="Valid rows whose total cost will be derived as litres x unit price")
    rows_unit_price_derived: int = Field(0, description="Valid rows whose unit price will be derived as total cost / litres")
    replacement: Optional[Dict[str, Any]] = Field(None, description="Latest-import-wins preview: period, rows to supersede (exact records, capped), per-vehicle totals, conflicts, expected_replace_count")
    single_date: Optional[str] = Field(None, description="The one date every valid row of a dated statement carries (YYYY-MM-DD); null when dates differ")
    single_date_confirmation_required: bool = Field(False, description="All rows share single_date, which is not a month end: the user must select the reporting month (or choose single_date_mode 'dated')")
    single_date_mode: Optional[str] = Field(None, description="How the single-date statement is treated: 'monthly_summary' (automatic) or 'dated' (the user's override)")
    rows_skipped_monthly_summary: int = Field(0, description="Valid rows that will not be imported because a monthly summary is stored for their vehicle and month")
    monthly_summary_scopes_kept: List[Dict[str, Any]] = Field(default_factory=list, description="Per vehicle-month: the kept monthly summary's totals and the file rows skipped (capped)")
    ready_to_import: bool = Field(False, description="True when the import would be accepted as previewed")
    blocking_reasons: List[str] = Field(default_factory=list, description="Why the import is not ready")


class FuelImportResultResponse(BaseModel):
    import_id: int
    file_name: str
    status: str
    rows_found: int
    rows_imported: int
    rows_duplicate: int
    rows_failed: int
    rows_unverified: int = Field(0, description="Rows sent whose write outcome is unknown (they may be stored); not counted as failed. Reconcile counts them later")
    rows_not_processed: int = Field(0, description="Rows never sent because the request time limit was reached; re-import the file to add them")
    rows_ignored: int = Field(0, description="Blank, repeated-header, summary/total or non-fuel rows skipped; with the counts above they add up to rows_found")
    rows_non_fuel: int = Field(0, description="Part of rows_ignored: statement entries typed as payments, charges or fees with no litres")
    ignored_row_details: Optional[List[Dict[str, Any]]] = Field(None, description="Source row and reason of skipped non-fuel rows (capped)")
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    error_message: Optional[str] = None
    failed_row_details: Optional[List[Dict[str, Any]]] = None
    rows_refund: int = Field(0, description="Rows imported as signed refund/reversal adjustments (negative litres)")
    rows_cost_derived: int = Field(0, description="Rows whose total_cost was derived as unit_price x litres (not on the statement)")
    rows_unit_price_derived: int = Field(0, description="Rows whose unit_price was derived as total_cost / litres (not on the statement)")
    rows_date_ambiguous: int = Field(0, description="Rows whose date was ambiguous and interpreted as DD/MM/YYYY")
    rows_optional_parse_failed: int = Field(0, description="Rows where a non-empty optional numeric cell could not be parsed and was left empty")
    warnings: List[str] = Field(default_factory=list, description="Row-level import warnings (capped)")
    duplicate_row_details: Optional[List[Dict[str, Any]]] = None
    file_sha256: Optional[str] = Field(None, description="SHA-256 of the uploaded file (reported only; not stored)")
    duplicate_check_scope: Optional[Dict[str, Any]] = Field(
        None,
        description="Date window, existing rows scanned and stored transaction_date shapes used for duplicate detection",
    )
    import_mode: Optional[str] = Field(None, description="'dated' or 'monthly_summary'")
    reporting_month: Optional[str] = None
    assigned_transaction_date: Optional[str] = Field(None, description="Month-end reporting date of a monthly summary (null when monthly_summary_table)")
    monthly_summary_table: bool = Field(False, description="The monthly summary was stored in fuel_monthly_summaries by reporting month, without any date")
    rows_price_mismatch: int = 0
    rows_identical_in_file: int = 0
    replacement_enabled: bool = False
    committed: Optional[bool] = Field(None, description="Latest-import-wins: whether the new rows were made visible (null when replacement is off)")
    rows_replaced: int = Field(0, description="Older visible rows superseded by this import (kept in the database, hidden from KPIs)")
    rows_unchanged: int = Field(0, description="Rows identical to a stored row in the replaced period; the stored row was kept (counted in rows_duplicate)")
    replacement_period: Optional[Dict[str, Any]] = None
    rows_skipped_monthly_summary: int = Field(0, description="Rows not imported because a monthly summary is stored for their vehicle and month (the summary is kept); with the counts above they add up to rows_found")
    monthly_summary_scopes_kept: List[Dict[str, Any]] = Field(default_factory=list, description="Per vehicle-month: the kept monthly summary's totals and the file rows skipped (capped)")
    single_date: Optional[str] = Field(None, description="The one date every row carried, when the user confirmed what it means")


class FuelImportItem(BaseModel):
    id: int
    file_name: str
    imported_by: Optional[str] = None
    status: str
    rows_found: int
    rows_imported: int
    rows_duplicate: int
    rows_failed: int
    # Stored only after Backend/sql/fuel_imports_ignored_counts.sql (FUEL_IMPORT_IGNORED_COUNTS=1); null for older records
    rows_ignored: Optional[int] = None
    rows_non_fuel: Optional[int] = None
    # Stored only after Backend/sql/fuel_import_replacement.sql (FUEL_IMPORT_REPLACE=1)
    import_mode: Optional[str] = None
    reporting_month: Optional[str] = None
    rows_replaced: Optional[int] = None
    rows_unchanged: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class FuelImportsListResponse(BaseModel):
    count: int
    imports: List[FuelImportItem]


class FuelTransactionItem(BaseModel):
    id: int
    vehicle_id: Optional[int] = None
    import_id: int
    transaction_date: str
    vehicle_identifier: str
    fuel_type: Optional[str] = None
    litres: float
    unit_price: Optional[float] = None
    total_cost: Optional[float] = None
    odometer_km: Optional[float] = None
    station_name: Optional[str] = None
    invoice_number: Optional[str] = None
    card_number: Optional[str] = None
    driver_name: Optional[str] = None
    reference_number: Optional[str] = None
    notes: Optional[str] = None
    source_row_number: Optional[int] = None
    created_at: Optional[str] = None
    transaction_kind: str = Field("purchase", description="'purchase' or 'refund_adjustment' (negative litres)")
    date_basis: Optional[str] = Field(None, description="'reporting_month_end' for monthly-summary rows (the date is a reporting date, not a refuel); else a dated transaction")


class FuelTransactionsListResponse(BaseModel):
    count: int
    page: int
    page_size: int
    total: int
    transactions: List[FuelTransactionItem]
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class FuelMonthlySummaryItem(BaseModel):
    """A monthly fuel total of one vehicle line of a statement. It has a reporting month and no refuelling date."""
    id: int = Field(..., description="Row id in its source table")
    source_table: str = Field(..., description="'fuel_monthly_summaries', or 'fuel_transactions' for a summary stored before FUEL_MONTHLY_SUMMARY_TABLE")
    import_id: Optional[int] = None
    reporting_month: str = Field(..., description="Reporting month (YYYY-MM) the total covers")
    vehicle_identifier: str
    fuel_type: Optional[str] = None
    litres: float
    unit_price: Optional[float] = None
    total_cost: Optional[float] = None
    station_name: Optional[str] = None
    invoice_number: Optional[str] = None
    card_number: Optional[str] = None
    driver_name: Optional[str] = None
    source_row_number: Optional[int] = None


class FuelMonthlySummariesListResponse(BaseModel):
    count: int
    page: int
    page_size: int
    total: int
    monthly_summaries: List[FuelMonthlySummaryItem]
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation for the same filters")


# ─── Analytics Schemas ─────────────────────────────────────────────────────────

class ExcludedTransactionGroup(BaseModel):
    vehicle_identifier: str = Field(..., description="Fuel statement identifier as recorded")
    reason: str = Field(..., description="unmatched | ambiguous | suspected_duplicate")
    candidate_unit_names: List[str] = Field(default_factory=list, description="Wialon units the identifier matched (ambiguous only)")
    ambiguity_reason: Optional[str] = Field(None, description="plate_code_conflict when the identifier matched one unit by registration number only and other plate codes share that number; null otherwise")
    transaction_count: int = Field(..., description="Excluded transactions in this group")
    litres: float = Field(..., description="Signed litres (refund adjustments negative) [Recorded]")
    cost: float = Field(..., description="Signed cost [Recorded]")
    first_date: Optional[str] = Field(None, description="Earliest transaction date (YYYY-MM-DD)")
    last_date: Optional[str] = Field(None, description="Latest transaction date (YYYY-MM-DD)")
    transaction_ids: List[int] = Field(default_factory=list, description="Up to 50 transaction ids, for review")
    summary_ids: List[int] = Field(default_factory=list, description="Up to 50 fuel_monthly_summaries ids (FUEL_MONTHLY_SUMMARY_TABLE), for review")


class ExcludedTransactionsResponse(BaseModel):
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Page size limit")
    total: int = Field(..., description="Total excluded groups matching filters")
    data: List[ExcludedTransactionGroup] = Field(default_factory=list, description="Excluded transaction groups")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="Scope reconciliation for the same filters")


class FuelTypeBreakdownItem(BaseModel):
    fuel_type: str = Field(..., description="Fuel type category (e.g. DIESEL, SPECIAL, SUPER)")
    litres: float = Field(..., description="Total fuel volume in litres")
    cost: float = Field(..., description="Total fuel expenditure")
    transactions: int = Field(..., description="Number of refuel transactions")
    average_unit_price: float = Field(..., description="Weighted average unit price per litre")


class FuelSummaryResponse(BaseModel):
    total_litres: float = Field(..., description="Net fuel purchased in litres (purchases minus refund adjustments); not measured consumption")
    total_cost: float = Field(..., description="Net fuel expenditure (purchases minus refund adjustments)")
    transaction_count: int = Field(..., description="All source rows in the KPI scope: dated_purchase_count + refund_count + monthly_summary_rows (+ any legacy zero-litre rows)")
    vehicle_count: int = Field(..., description="Number of unique vehicles refueled")
    average_unit_price: float = Field(..., description="Average unit price per litre over purchases only")
    fuel_type_breakdown: List[FuelTypeBreakdownItem] = Field(default_factory=list, description="Breakdown by fuel type")
    purchase_count: int = Field(0, description="Rows that are not refund adjustments (litres >= 0), monthly summaries included; refuelling events are dated_purchase_count")
    gross_purchased_litres: float = Field(0.0, description="Litres on purchase transactions only")
    gross_purchased_cost: float = Field(0.0, description="Cost on purchase transactions only")
    refund_count: int = Field(0, description="Number of refund/reversal adjustments (negative litres)")
    refund_litres: float = Field(0.0, description="Signed litres on refund adjustments (negative)")
    refund_cost: float = Field(0.0, description="Signed cost on refund adjustments (zero or negative)")
    dated_purchase_count: int = Field(0, description="Dated purchase rows (litres > 0, not monthly summaries): refuelling events [Recorded]")
    monthly_summary_rows: int = Field(0, description="Monthly-summary rows (statement totals for a month, not refuelling events) [Recorded]")
    value_classification: Dict[str, str] = Field(default_factory=dict, description="Field -> recorded | derived | estimated")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class FuelTrendItem(BaseModel):
    period: str = Field(..., description="Period identifier (e.g. YYYY-MM-DD for day, YYYY-MM-DD for week start, YYYY-MM for month)")
    litres: float = Field(..., description="Fuel volume in litres for the period")
    cost: float = Field(..., description="Total expenditure for the period")
    transactions: int = Field(..., description="All source rows in the period (purchases, refund adjustments and, in the monthly view, monthly summaries)")
    average_unit_price: float = Field(..., description="Average unit price per litre for the period")
    dated_purchase_count: int = Field(0, description="Dated purchase rows (litres > 0) in the period: refuelling events [Recorded]")
    refund_count: int = Field(0, description="Dated refund adjustments (negative litres) in the period [Recorded]")
    monthly_summary_rows: int = Field(0, description="Monthly-summary rows in the period (monthly view only) [Recorded]")


class FuelTrendResponse(BaseModel):
    group_by: str = Field(..., description="Grouping interval used: 'day', 'week', or 'month'")
    data: List[FuelTrendItem] = Field(default_factory=list, description="Chronological trend data points")
    monthly_summary_excluded: Optional[Dict[str, Any]] = Field(None, description="Day/week trends: monthly-summary rows left out (rows, litres, cost, months); they appear only in the monthly view")
    data_warnings: List[str] = Field(default_factory=list)
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class VehicleAnalyticsItem(BaseModel):
    vehicle_identifier: str = Field(..., description="Registration plate or vehicle identifier")
    litres: float = Field(..., description="Total fuel consumption in litres for this vehicle")
    total_cost: float = Field(..., description="Total fuel expenditure for this vehicle")
    transaction_count: int = Field(..., description="Number of refuels for this vehicle")
    average_unit_price: float = Field(..., description="Average unit price per litre paid for this vehicle")
    average_litres_per_transaction: float = Field(..., description="Average litres dispensed per refuel")
    wialon_unit_id: Optional[int] = Field(None, description="Wialon unit the identifier(s) resolve to")
    merged_identifiers: List[str] = Field(default_factory=list, description="All fuel identifiers combined into this unit record")


class VehicleAnalyticsResponse(BaseModel):
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Page size limit")
    total: int = Field(..., description="Total number of unique vehicles matching filters")
    data: List[VehicleAnalyticsItem] = Field(default_factory=list, description="Paginated vehicle analytics records")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class FuelTypeAnalyticsResponse(BaseModel):
    data: List[FuelTypeBreakdownItem] = Field(default_factory=list, description="Fuel type aggregation records")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class VehicleDistanceResponse(BaseModel):
    vehicle_identifier: str = Field(..., description="Fuel vehicle registration identifier")
    wialon_unit_id: int = Field(..., description="Dynamically resolved Wialon unit ID")
    wialon_unit_name: str = Field(..., description="Dynamically resolved Wialon unit name")
    registration_plate: Optional[str] = Field(None, description="Verified Wialon profile registration plate, null if not available")
    date_from: str = Field(..., description="Start date (YYYY-MM-DD)")
    date_to: str = Field(..., description="End date (YYYY-MM-DD)")
    distance: float = Field(..., description="Authoritative distance traveled in kilometers")
    unit: str = Field(default="km", description="Distance measurement unit")
    source: str = Field(default="wialon_native_trips_report", description="Authoritative data source")
    trip_count: int = Field(default=0, description="Total number of trips recorded in interval")
    has_data: bool = Field(default=True, description="Whether GPS/trip telemetry was recorded in interval")


class FleetEfficiencyResponse(BaseModel):
    total_litres: float = Field(..., description="Fleet net fuel: vehicles matched to one Wialon unit plus unmapped records (unmatched_litres) [Recorded]")
    total_cost: float = Field(..., description="Fleet net fuel expenditure: matched vehicles plus unmapped records (unmatched_cost) [Recorded]")
    transaction_count: int = Field(..., description="All source rows in the fleet totals (KPI scope plus unmapped records): dated_purchase_count + refund_count + monthly_summary_rows (+ any legacy zero-litre rows) [Recorded]")
    dated_purchase_count: int = Field(0, description="Dated purchase rows (litres > 0, not monthly summaries): refuelling events [Recorded]")
    monthly_summary_rows: int = Field(0, description="Monthly-summary rows (statement totals for a month, not refuelling events) [Recorded]")
    refund_count: int = Field(0, description="Refund adjustments (negative litres) in the KPI scope [Recorded]")
    vehicle_count: int = Field(..., description="Wialon units in the KPI scope (identifiers of one unit counted once) [Recorded]")
    matched_vehicle_count: int = Field(..., description="Vehicles successfully matched to an active Wialon GPS unit")
    unmatched_vehicle_count: int = Field(..., description="Fuel identifiers excluded from KPIs (no Wialon unit, or several)")
    vehicles_with_distance: int = Field(..., description="Tracked vehicles with recorded distance > 0")
    vehicles_without_distance: int = Field(..., description="Tracked vehicles with 0 recorded distance / no trips")
    tracked_litres: float = Field(..., description="Fuel volume dispensed specifically to vehicles with tracked distance")
    tracked_cost: float = Field(..., description="Fuel cost incurred specifically by vehicles with tracked distance")
    total_distance_km: Optional[float] = Field(None, description="Total distance traveled by tracked vehicles in kilometers [Recorded]")
    total_driving_hours: Optional[float] = Field(None, description="Total qualifying moving duration across tracked vehicles in hours [Recorded]")
    total_idle_hours: Optional[float] = Field(None, description="Total qualifying idle duration across tracked vehicles in hours [Recorded]")
    total_engine_hours: Optional[float] = Field(None, description="Total engine on hours across tracked vehicles [Recorded]")
    idle_rate_lph: float = Field(default=2.0, description="Configured idle consumption coefficient in L/h [Assumption]")
    estimated_idle_fuel_litres: Optional[float] = Field(None, description="Estimated total fuel consumed while idling across fleet [Estimated]")
    estimated_idle_cost: Optional[float] = Field(None, description="Estimated total cost of idle fuel across fleet [Estimated]")
    estimated_driving_fuel_litres: Optional[float] = Field(None, description="Estimated total fuel consumed while driving across fleet [Estimated]")
    estimated_idle_percentage: Optional[float] = Field(None, description="Estimated idle fuel as a percentage of idle_basis_litres (litres of vehicles with known idle hours, recorded 0 h included); a fuel share, not a share of engine time [Estimated]")
    estimated_driving_percentage: Optional[float] = Field(None, description="Estimated driving fuel as a percentage of idle_basis_litres (litres of vehicles with known idle hours, recorded 0 h included); a fuel share, not a share of engine time [Estimated]")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Weighted fleet fuel efficiency in L/100 km (tracked_litres / total_distance * 100)")
    fuel_cost_per_km: Optional[float] = Field(None, description="Weighted fleet fuel cost per km (tracked_cost / total_distance)")
    unmatched_transactions_count: int = Field(default=0, description="Number of fuel transactions with unmatched vehicle identifier")
    unmatched_litres: float = Field(default=0.0, description="Fuel volume from unmatched transactions")
    unmatched_cost: float = Field(default=0.0, description="Fuel cost from unmatched transactions")
    telematics_status: str = Field("complete", description="complete | partial: whether every Wialon value used was reported readably; unavailable: Wialon reports failed or timed out (telematics values null)")
    telematics_pending: bool = Field(False, description="True when the Wialon reports did not answer within FUEL_TELEMATICS_WAIT_S but are still running (shared, cached on completion): asking again later returns them. False when they failed or answered")
    data_warnings: List[str] = Field(default_factory=list, description="Telematics completeness warnings (missing units, unreadable cells, column fallbacks)")
    vehicles_telemetry_unknown: int = Field(0, description="Matched vehicles whose Wialon distance is unknown; excluded from L/100 km")
    telemetry_litres: float = Field(0.0, description="Net litres of vehicles with distance or engine hours (basis of driving/engine hour totals)")
    idle_basis_litres: float = Field(0.0, description="Net litres of vehicles eligible for an idle estimate (idle hours known; not mixed-fuel under a fuel-type filter): basis of the idle/driving fuel split")
    vehicles_hours_known: int = Field(0, description="Vehicles in the KPI scope with Wialon driving, idle and engine hours all reported")
    vehicles_hours_unknown: int = Field(0, description="Vehicles in the KPI scope missing any of those hours; their hours are not counted (never as 0)")
    vehicles_idle_estimated: int = Field(0, description="Vehicles whose estimated idle fuel is included in estimated_idle_fuel_litres (sum of their per-vehicle estimates)")
    vehicles_engine_hours_only: int = Field(0, description="Vehicles with engine hours but no distance; in hour totals, not in L/100 km")
    engine_only_litres: float = Field(0.0, description="Net litres of engine-hours-only vehicles (not in tracked_litres)")
    vehicles_fuel_type_mixed: int = Field(0, description="Vehicles with several fuel types under a fuel-type filter; excluded from L/100 km")
    fuel_type_mixed_excluded_litres: float = Field(0.0, description="Filtered-fuel litres of mixed-fuel vehicles, excluded from L/100 km")
    min_efficiency_distance_km: float = Field(25.0, description="Minimum Wialon distance for a purchase-based ratio (FUEL_MIN_EFFICIENCY_KM)")
    efficiency_suppressed_reason: Optional[str] = Field(None, description="insufficient_distance | non_positive_net_fuel when the fleet ratio is withheld")
    efficiency_basis: str = Field("purchase_based", description="L/100 km = fuel purchased / Wialon distance x 100 (derived; not measured consumption)")
    value_classification: Dict[str, str] = Field(default_factory=dict, description="Field -> recorded | derived | estimated")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class VehicleEfficiencyItem(BaseModel):
    vehicle_identifier: str = Field(..., description="Registration plate / vehicle identifier")
    wialon_unit_id: Optional[int] = Field(None, description="Resolved Wialon unit ID, null if not tracked")
    wialon_unit_name: Optional[str] = Field(None, description="Resolved Wialon unit name, null if not tracked")
    registration_plate: Optional[str] = Field(None, description="Resolved Wialon profile registration plate, null if not available")
    litres: float = Field(..., description="Total fuel dispensed in litres [Recorded]")
    total_cost: float = Field(..., description="Total fuel expenditure in AED [Recorded]")
    transaction_count: int = Field(..., description="Number of refuels recorded [Recorded]")
    distance_km: Optional[float] = Field(None, description="Total distance traveled in km, null if not tracked [Recorded]")
    driving_duration_hours: Optional[float] = Field(None, description="Total qualifying driving time in hours [Recorded]")
    idle_duration_hours: Optional[float] = Field(None, description="Total qualifying idle time in hours [Recorded]")
    engine_hours: Optional[float] = Field(None, description="Total engine operation hours [Recorded]")
    idle_rate_lph: float = Field(default=2.0, description="Idle consumption coefficient applied (L/h)")
    estimated_idle_fuel_litres: Optional[float] = Field(None, description="Estimated idle fuel consumed (L) = idle_hours * idle_rate_lph [Estimated]")
    estimated_idle_cost: Optional[float] = Field(None, description="Estimated idle fuel expenditure in AED [Estimated]")
    estimated_driving_fuel_litres: Optional[float] = Field(None, description="Estimated driving fuel (L) = litres - estimated_idle_fuel [Estimated]")
    estimated_idle_percentage: Optional[float] = Field(None, description="Estimated idle fuel percentage of fuel purchased [Estimated]")
    estimated_driving_percentage: Optional[float] = Field(None, description="Estimated driving fuel percentage of fuel purchased [Estimated]")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Fuel consumption in L/100 km, null if distance unavailable")
    fuel_cost_per_km: Optional[float] = Field(None, description="Fuel cost per km, null if distance unavailable")
    status: str = Field(..., description="Data status: 'matched', 'not_tracked', 'no_distance_data', or 'ambiguous'")
    estimation_status: str = Field(default="unavailable", description="Idle/driving split confidence: 'estimated', 'provisional', 'unavailable' (never measured)")
    quality_warnings: List[str] = Field(default_factory=list, description="Data quality issues or reconciliation flags")
    distance_source: Optional[str] = Field(None, description="wialon_report | unavailable (distance unknown, never substituted with 0)")
    telemetry_status: Optional[str] = Field(None, description="complete | partial | unavailable for this unit's Wialon values")
    merged_identifiers: List[str] = Field(default_factory=list, description="All fuel identifiers combined into this unit record (distance counted once)")
    efficiency_suppressed_reason: Optional[str] = Field(None, description="insufficient_distance | non_positive_net_fuel | mixed_fuel_types when L/100 km is withheld")


class VehicleEfficiencyResponse(BaseModel):
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Page size limit")
    total: int = Field(..., description="Total unique vehicles matching filters")
    data: List[VehicleEfficiencyItem] = Field(default_factory=list, description="Paginated vehicle efficiency records")
    value_classification: Dict[str, str] = Field(default_factory=dict, description="Field -> recorded | derived | estimated")
    telematics_status: str = Field("complete", description="complete | partial: whether every Wialon value used was reported readably; unavailable: Wialon reports failed or timed out (telematics values null)")
    telematics_pending: bool = Field(False, description="True when the Wialon reports did not answer within FUEL_TELEMATICS_WAIT_S but are still running (shared, cached on completion): asking again later returns them. False when they failed or answered")
    data_warnings: List[str] = Field(default_factory=list, description="Telematics completeness warnings (missing units, unreadable cells, column fallbacks)")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class EfficiencyTrendItem(BaseModel):
    period: str = Field(..., description="Chronological period (YYYY-MM-DD for day, YYYY-MM-DD for week start, YYYY-MM for month)")
    litres: float = Field(..., description="Fuel volume dispensed in the period")
    cost: float = Field(..., description="Fuel cost incurred in the period")
    transaction_count: int = Field(..., description="Transactions in the period")
    distance_km: Optional[float] = Field(None, description="Distance traveled in the period in km")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Weighted efficiency for the period in L/100 km")
    fuel_cost_per_km: Optional[float] = Field(None, description="Weighted fuel cost per km for the period")
    tracked_litres: float = Field(0.0, description="Litres of units with Wialon trip distance in the period (ratio numerator)")
    distance_source: str = Field("unavailable", description="wialon_unit_trips (actual trips started in the period) | unavailable")
    efficiency_basis: str = Field("purchase_based", description="Fuel purchased / Wialon distance (derived; not measured consumption)")
    efficiency_status: str = Field("unavailable", description="ok | no_fuel | no_distance | insufficient_distance | non_positive_net_fuel | unavailable")
    cumulative_l_per_100km: Optional[float] = Field(None, description="Running ratio of tracked litres to distance up to this period")
    boundary_trip_km: float = Field(0.0, description="Km of trips that started in this period and ended in a later one (kept in the start period)")
    units_with_distance: int = Field(0, description="Units contributing trip distance to the period")


class EfficiencyTrendResponse(BaseModel):
    group_by: str = Field(..., description="Grouping interval: 'day', 'week', or 'month'")
    data: List[EfficiencyTrendItem] = Field(default_factory=list, description="Chronological efficiency trend records")
    distance_reconciliation: Dict[str, Any] = Field(default_factory=dict, description="Per-unit trip totals vs group-report distance (mismatches above max(1 km, 2%))")
    units_failed: List[Dict[str, Any]] = Field(default_factory=list, description="Units whose trips report failed or was incomplete (excluded, never 0 km)")
    min_distance_km: float = Field(25.0, description="Minimum period distance for a per-period ratio")
    monthly_summary_excluded: Optional[Dict[str, Any]] = Field(None, description="Day/week trends: monthly-summary fuel (and those units' distance in the same months) left out; shown only in the monthly view")
    value_classification: Dict[str, str] = Field(default_factory=dict, description="Field -> recorded | derived | estimated")
    telematics_status: str = Field("complete", description="complete | partial: whether every Wialon value used was reported readably; unavailable: Wialon reports failed or timed out (telematics values null)")
    data_warnings: List[str] = Field(default_factory=list, description="Telematics completeness warnings (missing units, unreadable cells, column fallbacks)")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


# ─── Fuel Waste & Anomaly Schemas ──────────────────────────────────────────────

class FuelAnomalyItem(BaseModel):
    id: str = Field(..., description="Unique identifier for the anomaly flag")
    anomaly_type: str = Field(..., description="Category: 'excessive_idling', 'rapid_refuel', 'same_day_multiple_refuels', 'volume_spike', 'odometer_inconsistency', 'missing_odometer', 'invalid_transaction_date', 'duplicate_candidate', 'unmatched_vehicle', 'summary_dated_overlap'")
    severity: str = Field(..., description="Severity level: 'critical', 'warning', 'info'")
    finding_type: str = Field(..., description="Evidence classification: 'measured', 'estimated', 'provisional'")
    vehicle_identifier: str = Field(..., description="Vehicle registration plate affected")
    transaction_id: Optional[int] = Field(None, description="Transaction ID if specific to a single record")
    transaction_date: Optional[str] = Field(None, description="Date or timestamp of incident")
    title: str = Field(..., description="Concise title summarizing the anomaly")
    description: str = Field(..., description="Detailed explanation with transparent numbers and reason")
    recorded_value: Optional[str] = Field(None, description="Actual recorded value that triggered the flag")
    threshold_or_baseline: Optional[str] = Field(None, description="Comparison threshold or baseline used")
    wasted_litres: Optional[float] = Field(None, description="Estimated fuel wasted in litres if applicable")
    wasted_cost: Optional[float] = Field(None, description="Estimated expenditure wasted in AED if applicable")
    details: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional context metadata")
    classification: str = Field("recorded", description="recorded (from statements/Wialon records) | estimated (rate x hours)")


class FuelWasteResponse(BaseModel):
    total_anomalies_count: int = Field(..., description="Total number of active anomaly findings")
    excessive_idle_vehicles_count: int = Field(..., description="Vehicles exceeding idle duration threshold")
    total_idle_waste_litres: float = Field(..., description="Fleet total estimated idle fuel waste in litres")
    total_idle_waste_cost: float = Field(..., description="Fleet total estimated idle fuel waste in AED")
    refuel_anomalies_count: int = Field(..., description="Number of unusual refuelling patterns detected")
    data_quality_issues_count: int = Field(..., description="Number of data quality inconsistencies found")
    idle_threshold_hours: float = Field(default=5.0, description="Configured idle threshold in hours")
    idle_rate_lph: float = Field(default=2.0, description="Idle consumption coefficient in L/h")
    anomalies: List[FuelAnomalyItem] = Field(default_factory=list, description="Prioritized list of anomalies")
    top_idle_wasters: List[VehicleEfficiencyItem] = Field(default_factory=list, description="Top vehicles ranked by estimated idle cost")
    fleet_estimated_idle_fuel_litres: Optional[float] = Field(None, description="Idle hours x rate across all matched vehicles with known idle hours (same basis as the efficiency KPI) [Estimated]")
    idle_waste_basis: str = Field("vehicles_at_or_above_threshold", description="total_idle_waste_* sums only vehicles at or above the effective idle threshold")
    idle_threshold_mode: str = Field("per_day", description="per_day: threshold x period_days | total: threshold for the whole period")
    effective_idle_threshold_hours: Optional[float] = Field(None, description="Threshold actually applied to the period's idle hours")
    period_days: Optional[int] = Field(None, description="Days in the analysed period")
    value_classification: Dict[str, str] = Field(default_factory=dict, description="Field -> recorded | derived | estimated")
    telematics_status: str = Field("complete", description="complete | partial: whether every Wialon value used was reported readably; unavailable: Wialon reports failed or timed out (telematics values null)")
    telematics_pending: bool = Field(False, description="True when the Wialon reports did not answer within FUEL_TELEMATICS_WAIT_S but are still running (shared, cached on completion): asking again later returns them. False when they failed or answered")
    data_warnings: List[str] = Field(default_factory=list, description="Telematics completeness warnings (missing units, unreadable cells, column fallbacks)")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")


class VehicleFuelDetailResponse(BaseModel):
    vehicle_identifier: str = Field(..., description="Vehicle registration plate")
    vehicle_summary: VehicleEfficiencyItem = Field(..., description="Comprehensive vehicle performance metrics")
    transactions: List[FuelTransactionItem] = Field(default_factory=list, description="Chronological transaction history")
    monthly_summaries: List[FuelMonthlySummaryItem] = Field(default_factory=list, description="Monthly totals in scope, by reporting month (FUEL_MONTHLY_SUMMARY_TABLE; they are then not in transactions)")
    activity_breakdown: Dict[str, Any] = Field(default_factory=dict, description="Driving vs Idling duration and fuel breakdown")
    anomalies: List[FuelAnomalyItem] = Field(default_factory=list, description="Specific anomalies identified for this vehicle")
    calculation_transparency: Dict[str, Any] = Field(default_factory=dict, description="Disclosed formulas, coefficient, and data sources")
    value_classification: Dict[str, str] = Field(default_factory=dict, description="vehicle_summary field -> recorded | derived | estimated")
    excluded_scope: Optional[Dict[str, Any]] = Field(None, description="KPI scope reconciliation: kpi / unmatched / ambiguous / suspected_duplicates / all_* (count, litres, cost; refunds signed)")

