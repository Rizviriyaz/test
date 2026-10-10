"""Fuel Analytics Aggregation and Business Logic Module."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.fuel.schemas import (
    FuelSummaryResponse,
    FuelTrendItem,
    FuelTrendResponse,
    FuelTypeAnalyticsResponse,
    FuelTypeBreakdownItem,
    VehicleAnalyticsItem,
    VehicleAnalyticsResponse,
)

VALID_GROUP_BY = {"day", "week", "month"}
VALID_SORT_FIELDS = {
    "litres",
    "total_cost",
    "transaction_count",
    "average_unit_price",
    "average_litres_per_transaction",
    "vehicle_identifier",
}
VALID_SORT_ORDERS = {"asc", "desc"}


def calculate_fuel_summary(transactions: List[Dict[str, Any]]) -> FuelSummaryResponse:
    """Calculate fleet-wide summary metrics and fuel-type distribution."""
    if not transactions:
        return FuelSummaryResponse(
            total_litres=0.0,
            total_cost=0.0,
            transaction_count=0,
            vehicle_count=0,
            average_unit_price=0.0,
            fuel_type_breakdown=[],
        )

    total_litres = 0.0
    total_cost = 0.0
    vehicles = set()
    ft_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"litres": 0.0, "cost": 0.0, "count": 0})

    for t in transactions:
        litres = float(t.get("litres") or 0)
        cost = float(t.get("total_cost") or 0)
        v_ident = t.get("vehicle_identifier")
        ft = str(t.get("fuel_type") or "UNKNOWN").strip().upper()

        total_litres += litres
        total_cost += cost
        if v_ident and str(v_ident).strip():
            vehicles.add(str(v_ident).strip())

        ft_map[ft]["litres"] += litres
        ft_map[ft]["cost"] += cost
        ft_map[ft]["count"] += 1

    total_litres_rounded = round(total_litres, 2)
    total_cost_rounded = round(total_cost, 2)
    avg_price = round(total_cost_rounded / total_litres_rounded, 2) if total_litres_rounded > 0 else 0.0

    breakdown: List[FuelTypeBreakdownItem] = []
    for ft_name, data in ft_map.items():
        l_round = round(data["litres"], 2)
        c_round = round(data["cost"], 2)
        breakdown.append(
            FuelTypeBreakdownItem(
                fuel_type=ft_name,
                litres=l_round,
                cost=c_round,
                transactions=data["count"],
                average_unit_price=round(c_round / l_round, 2) if l_round > 0 else 0.0,
            )
        )

    # Sort breakdown by litres descending
    breakdown.sort(key=lambda x: x.litres, reverse=True)

    return FuelSummaryResponse(
        total_litres=total_litres_rounded,
        total_cost=total_cost_rounded,
        transaction_count=len(transactions),
        vehicle_count=len(vehicles),
        average_unit_price=avg_price,
        fuel_type_breakdown=breakdown,
    )


def calculate_fuel_trend(transactions: List[Dict[str, Any]], group_by: str = "day") -> FuelTrendResponse:
    """Group transactions chronologically by day, week, or month."""
    clean_group_by = group_by.lower().strip()
    if clean_group_by not in VALID_GROUP_BY:
        raise ValueError(f"Unsupported group_by '{group_by}'. Supported values: 'day', 'week', 'month'.")

    if not transactions:
        return FuelTrendResponse(group_by=clean_group_by, data=[])

    period_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"litres": 0.0, "cost": 0.0, "count": 0})

    for t in transactions:
        raw_date = t.get("transaction_date")
        if not raw_date:
            continue

        date_str = str(raw_date)[:10]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if clean_group_by == "day":
            period_key = dt.strftime("%Y-%m-%d")
        elif clean_group_by == "week":
            # Start of the week (Monday)
            period_key = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        elif clean_group_by == "month":
            period_key = dt.strftime("%Y-%m")
        else:
            period_key = dt.strftime("%Y-%m-%d")

        litres = float(t.get("litres") or 0)
        cost = float(t.get("total_cost") or 0)

        period_map[period_key]["litres"] += litres
        period_map[period_key]["cost"] += cost
        period_map[period_key]["count"] += 1

    # Sort periods chronologically
    sorted_periods = sorted(period_map.keys())

    items: List[FuelTrendItem] = []
    for p in sorted_periods:
        l_round = round(period_map[p]["litres"], 2)
        c_round = round(period_map[p]["cost"], 2)
        items.append(
            FuelTrendItem(
                period=p,
                litres=l_round,
                cost=c_round,
                transactions=period_map[p]["count"],
                average_unit_price=round(c_round / l_round, 2) if l_round > 0 else 0.0,
            )
        )

    return FuelTrendResponse(group_by=clean_group_by, data=items)


def calculate_vehicle_analytics(
    transactions: List[Dict[str, Any]],
    page: int = 1,
    page_size: int = 25,
    sort_by: str = "litres",
    sort_order: str = "desc",
) -> VehicleAnalyticsResponse:
    """Aggregate consumption and cost by vehicle with sorting and pagination."""
    if page < 1:
        raise ValueError("Page number must be greater than or equal to 1.")
    if page_size < 1 or page_size > 1000:
        raise ValueError("Page size must be between 1 and 1000.")

    clean_sort_by = sort_by.lower().strip()
    clean_sort_order = sort_order.lower().strip()

    if clean_sort_by not in VALID_SORT_FIELDS:
        raise ValueError(f"Invalid sort_by '{sort_by}'. Supported fields: {', '.join(sorted(VALID_SORT_FIELDS))}.")
    if clean_sort_order not in VALID_SORT_ORDERS:
        raise ValueError(f"Invalid sort_order '{sort_order}'. Supported values: 'asc', 'desc'.")

    if not transactions:
        return VehicleAnalyticsResponse(page=page, page_size=page_size, total=0, data=[])

    veh_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"litres": 0.0, "cost": 0.0, "count": 0})

    for t in transactions:
        v_ident = str(t.get("vehicle_identifier") or "").strip()
        if not v_ident:
            continue

        litres = float(t.get("litres") or 0)
        cost = float(t.get("total_cost") or 0)

        veh_map[v_ident]["litres"] += litres
        veh_map[v_ident]["cost"] += cost
        veh_map[v_ident]["count"] += 1

    records: List[VehicleAnalyticsItem] = []
    for v_ident, data in veh_map.items():
        l_round = round(data["litres"], 2)
        c_round = round(data["cost"], 2)
        count = data["count"]
        records.append(
            VehicleAnalyticsItem(
                vehicle_identifier=v_ident,
                litres=l_round,
                total_cost=c_round,
                transaction_count=count,
                average_unit_price=round(c_round / l_round, 2) if l_round > 0 else 0.0,
                average_litres_per_transaction=round(l_round / count, 2) if count > 0 else 0.0,
            )
        )

    # Sorting
    reverse = clean_sort_order == "desc"
    if clean_sort_by == "vehicle_identifier":
        records.sort(key=lambda x: x.vehicle_identifier.lower(), reverse=reverse)
    else:
        records.sort(key=lambda x: getattr(x, clean_sort_by), reverse=reverse)

    total_count = len(records)
    offset = (page - 1) * page_size
    paged_records = records[offset : offset + page_size]

    return VehicleAnalyticsResponse(
        page=page,
        page_size=page_size,
        total=total_count,
        data=paged_records,
    )


def calculate_fuel_type_analytics(transactions: List[Dict[str, Any]]) -> FuelTypeAnalyticsResponse:
    """Aggregate fuel type breakdown records."""
    if not transactions:
        return FuelTypeAnalyticsResponse(data=[])

    ft_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"litres": 0.0, "cost": 0.0, "count": 0})

    for t in transactions:
        ft = str(t.get("fuel_type") or "UNKNOWN").strip().upper()
        litres = float(t.get("litres") or 0)
        cost = float(t.get("total_cost") or 0)

        ft_map[ft]["litres"] += litres
        ft_map[ft]["cost"] += cost
        ft_map[ft]["count"] += 1

    items: List[FuelTypeBreakdownItem] = []
    for ft_name, data in ft_map.items():
        l_round = round(data["litres"], 2)
        c_round = round(data["cost"], 2)
        items.append(
            FuelTypeBreakdownItem(
                fuel_type=ft_name,
                litres=l_round,
                cost=c_round,
                transactions=data["count"],
                average_unit_price=round(c_round / l_round, 2) if l_round > 0 else 0.0,
            )
        )

    items.sort(key=lambda x: x.litres, reverse=True)
    return FuelTypeAnalyticsResponse(data=items)
