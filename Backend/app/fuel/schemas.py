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
    rows_candidate: int
    rows_ignored: int
    detected_columns: List[str]
    suggested_mapping: Dict[str, Optional[str]]
    mapping_details: List[HeaderMappingItem]
    unmapped_columns: List[str]
    missing_required_fields: List[str]
    validation_warnings: List[str]
    sample_rows: List[Dict[str, Any]]


class FuelImportResultResponse(BaseModel):
    import_id: int
    file_name: str
    status: str
    rows_found: int
    rows_imported: int
    rows_duplicate: int
    rows_failed: int
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    error_message: Optional[str] = None
    failed_row_details: Optional[List[Dict[str, Any]]] = None


class FuelImportItem(BaseModel):
    id: int
    file_name: str
    imported_by: Optional[str] = None
    status: str
    rows_found: int
    rows_imported: int
    rows_duplicate: int
    rows_failed: int
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


class FuelTransactionsListResponse(BaseModel):
    count: int
    page: int
    page_size: int
    total: int
    transactions: List[FuelTransactionItem]


# ─── Analytics Schemas ─────────────────────────────────────────────────────────

class FuelTypeBreakdownItem(BaseModel):
    fuel_type: str = Field(..., description="Fuel type category (e.g. DIESEL, SPECIAL, SUPER)")
    litres: float = Field(..., description="Total fuel volume in litres")
    cost: float = Field(..., description="Total fuel expenditure")
    transactions: int = Field(..., description="Number of refuel transactions")
    average_unit_price: float = Field(..., description="Weighted average unit price per litre")


class FuelSummaryResponse(BaseModel):
    total_litres: float = Field(..., description="Total fleet fuel consumption in litres")
    total_cost: float = Field(..., description="Total fleet fuel expenditure")
    transaction_count: int = Field(..., description="Total number of fuel transactions")
    vehicle_count: int = Field(..., description="Number of unique vehicles refueled")
    average_unit_price: float = Field(..., description="Fleet-wide average unit price per litre")
    fuel_type_breakdown: List[FuelTypeBreakdownItem] = Field(default_factory=list, description="Breakdown by fuel type")


class FuelTrendItem(BaseModel):
    period: str = Field(..., description="Period identifier (e.g. YYYY-MM-DD for day, YYYY-MM-DD for week start, YYYY-MM for month)")
    litres: float = Field(..., description="Fuel volume in litres for the period")
    cost: float = Field(..., description="Total expenditure for the period")
    transactions: int = Field(..., description="Transaction count for the period")
    average_unit_price: float = Field(..., description="Average unit price per litre for the period")


class FuelTrendResponse(BaseModel):
    group_by: str = Field(..., description="Grouping interval used: 'day', 'week', or 'month'")
    data: List[FuelTrendItem] = Field(default_factory=list, description="Chronological trend data points")


class VehicleAnalyticsItem(BaseModel):
    vehicle_identifier: str = Field(..., description="Registration plate or vehicle identifier")
    litres: float = Field(..., description="Total fuel consumption in litres for this vehicle")
    total_cost: float = Field(..., description="Total fuel expenditure for this vehicle")
    transaction_count: int = Field(..., description="Number of refuels for this vehicle")
    average_unit_price: float = Field(..., description="Average unit price per litre paid for this vehicle")
    average_litres_per_transaction: float = Field(..., description="Average litres dispensed per refuel")


class VehicleAnalyticsResponse(BaseModel):
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Page size limit")
    total: int = Field(..., description="Total number of unique vehicles matching filters")
    data: List[VehicleAnalyticsItem] = Field(default_factory=list, description="Paginated vehicle analytics records")


class FuelTypeAnalyticsResponse(BaseModel):
    data: List[FuelTypeBreakdownItem] = Field(default_factory=list, description="Fuel type aggregation records")


class VehicleDistanceResponse(BaseModel):
    vehicle_identifier: str = Field(..., description="Fuel vehicle registration identifier")
    wialon_unit_id: int = Field(..., description="Dynamically resolved Wialon unit ID")
    wialon_unit_name: str = Field(..., description="Dynamically resolved Wialon unit name")
    date_from: str = Field(..., description="Start date (YYYY-MM-DD)")
    date_to: str = Field(..., description="End date (YYYY-MM-DD)")
    distance: float = Field(..., description="Authoritative distance traveled in kilometers")
    unit: str = Field(default="km", description="Distance measurement unit")
    source: str = Field(default="wialon_native_trips_report", description="Authoritative data source")
    trip_count: int = Field(default=0, description="Total number of trips recorded in interval")
    has_data: bool = Field(default=True, description="Whether GPS/trip telemetry was recorded in interval")


class FleetEfficiencyResponse(BaseModel):
    total_litres: float = Field(..., description="Total fuel volume across all filtered refuel records [Recorded]")
    total_cost: float = Field(..., description="Total fuel expenditure across all filtered refuel records [Recorded]")
    transaction_count: int = Field(..., description="Total refuel transactions recorded [Recorded]")
    vehicle_count: int = Field(..., description="Total unique vehicles in the filtered fuel data [Recorded]")
    matched_vehicle_count: int = Field(..., description="Vehicles successfully matched to an active Wialon GPS unit")
    unmatched_vehicle_count: int = Field(..., description="Vehicles not tracked by Wialon GPS")
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
    estimated_idle_percentage: Optional[float] = Field(None, description="Estimated idle fuel percentage of tracked fuel [Estimated]")
    estimated_driving_percentage: Optional[float] = Field(None, description="Estimated driving fuel percentage of tracked fuel [Estimated]")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Weighted fleet fuel efficiency in L/100 km (tracked_litres / total_distance * 100)")
    fuel_cost_per_km: Optional[float] = Field(None, description="Weighted fleet fuel cost per km (tracked_cost / total_distance)")
    unmatched_transactions_count: int = Field(default=0, description="Number of fuel transactions with unmatched vehicle identifier")
    unmatched_litres: float = Field(default=0.0, description="Fuel volume from unmatched transactions")
    unmatched_cost: float = Field(default=0.0, description="Fuel cost from unmatched transactions")


class VehicleEfficiencyItem(BaseModel):
    vehicle_identifier: str = Field(..., description="Registration plate / vehicle identifier")
    wialon_unit_id: Optional[int] = Field(None, description="Resolved Wialon unit ID, null if not tracked")
    wialon_unit_name: Optional[str] = Field(None, description="Resolved Wialon unit name, null if not tracked")
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
    estimation_status: str = Field(default="unavailable", description="Estimation confidence: 'measured', 'estimated', 'provisional', 'unavailable'")
    quality_warnings: List[str] = Field(default_factory=list, description="Data quality issues or reconciliation flags")


class VehicleEfficiencyResponse(BaseModel):
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Page size limit")
    total: int = Field(..., description="Total unique vehicles matching filters")
    data: List[VehicleEfficiencyItem] = Field(default_factory=list, description="Paginated vehicle efficiency records")


class EfficiencyTrendItem(BaseModel):
    period: str = Field(..., description="Chronological period (YYYY-MM-DD for day, YYYY-MM-DD for week start, YYYY-MM for month)")
    litres: float = Field(..., description="Fuel volume dispensed in the period")
    cost: float = Field(..., description="Fuel cost incurred in the period")
    transaction_count: int = Field(..., description="Transactions in the period")
    distance_km: Optional[float] = Field(None, description="Distance traveled in the period in km")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Weighted efficiency for the period in L/100 km")
    fuel_cost_per_km: Optional[float] = Field(None, description="Weighted fuel cost per km for the period")


class EfficiencyTrendResponse(BaseModel):
    group_by: str = Field(..., description="Grouping interval: 'day', 'week', or 'month'")
    data: List[EfficiencyTrendItem] = Field(default_factory=list, description="Chronological efficiency trend records")


# ─── Fuel Waste & Anomaly Schemas ──────────────────────────────────────────────

class FuelAnomalyItem(BaseModel):
    id: str = Field(..., description="Unique identifier for the anomaly flag")
    anomaly_type: str = Field(..., description="Category: 'excessive_idling', 'rapid_refuel', 'volume_spike', 'odometer_inconsistency', 'duplicate_candidate', 'unmatched_vehicle'")
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


class VehicleFuelDetailResponse(BaseModel):
    vehicle_identifier: str = Field(..., description="Vehicle registration plate")
    vehicle_summary: VehicleEfficiencyItem = Field(..., description="Comprehensive vehicle performance metrics")
    transactions: List[FuelTransactionItem] = Field(default_factory=list, description="Chronological transaction history")
    activity_breakdown: Dict[str, Any] = Field(default_factory=dict, description="Driving vs Idling duration and fuel breakdown")
    anomalies: List[FuelAnomalyItem] = Field(default_factory=list, description="Specific anomalies identified for this vehicle")
    calculation_transparency: Dict[str, Any] = Field(default_factory=dict, description="Disclosed formulas, coefficient, and data sources")

