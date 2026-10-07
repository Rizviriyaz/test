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
    total_litres: float = Field(..., description="Total fuel volume across all filtered refuel records")
    total_cost: float = Field(..., description="Total fuel expenditure across all filtered refuel records")
    transaction_count: int = Field(..., description="Total refuel transactions recorded")
    vehicle_count: int = Field(..., description="Total unique vehicles in the filtered fuel data")
    matched_vehicle_count: int = Field(..., description="Vehicles successfully matched to an active Wialon GPS unit")
    unmatched_vehicle_count: int = Field(..., description="Vehicles not tracked by Wialon GPS")
    vehicles_with_distance: int = Field(..., description="Tracked vehicles with recorded distance > 0")
    vehicles_without_distance: int = Field(..., description="Tracked vehicles with 0 recorded distance / no trips")
    tracked_litres: float = Field(..., description="Fuel volume dispensed specifically to vehicles with tracked distance")
    tracked_cost: float = Field(..., description="Fuel cost incurred specifically by vehicles with tracked distance")
    total_distance_km: Optional[float] = Field(None, description="Total distance traveled by tracked vehicles in kilometers")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Weighted fleet fuel efficiency in L/100 km (tracked_litres / total_distance * 100)")
    fuel_cost_per_km: Optional[float] = Field(None, description="Weighted fleet fuel cost per km (tracked_cost / total_distance)")


class VehicleEfficiencyItem(BaseModel):
    vehicle_identifier: str = Field(..., description="Registration plate / vehicle identifier")
    wialon_unit_id: Optional[int] = Field(None, description="Resolved Wialon unit ID, null if not tracked")
    wialon_unit_name: Optional[str] = Field(None, description="Resolved Wialon unit name, null if not tracked")
    litres: float = Field(..., description="Total fuel dispensed in litres")
    total_cost: float = Field(..., description="Total fuel expenditure in AED")
    transaction_count: int = Field(..., description="Number of refuels recorded")
    distance_km: Optional[float] = Field(None, description="Total distance traveled in km, null if not tracked")
    fuel_efficiency_l_per_100km: Optional[float] = Field(None, description="Fuel consumption in L/100 km, null if distance unavailable")
    fuel_cost_per_km: Optional[float] = Field(None, description="Fuel cost per km, null if distance unavailable")
    status: str = Field(..., description="Data status: 'matched', 'not_tracked', 'no_distance_data', or 'ambiguous'")


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
