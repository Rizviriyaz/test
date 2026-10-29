"""Fuel Analytics Aggregation and Business Logic Module."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.fuel.reporting_period import confirmed_coverage_period, is_monthly_summary
from app.fuel.scope import REASON_DATED_SUPERSEDED, _q2, tx_local_date, unit_key
from app.fuel.schemas import (
    DailyFuelItem,
    DailyFuelResponse,
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

    # Totals are net: refund adjustments (negative litres) reduce them. The average price uses purchases only.
    # Sums use Decimal (like scope.excluded_scope) so the KPI and excluded totals reconcile to the cent.
    total_litres = Decimal(0)
    total_cost = Decimal(0)
    purchase_count = 0
    purchase_litres = Decimal(0)
    purchase_cost = Decimal(0)
    refund_count = 0
    refund_litres = Decimal(0)
    refund_cost = Decimal(0)
    dated_purchase_count = 0
    summary_rows = 0
    vehicles = set()
    ft_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": 0})

    for t in transactions:
        litres = Decimal(str(t.get("litres") or 0))
        cost = Decimal(str(t.get("total_cost") or 0))
        v_ident = t.get("vehicle_identifier")
        ft = str(t.get("fuel_type") or "UNKNOWN").strip().upper()

        total_litres += litres
        total_cost += cost
        if litres < 0:
            refund_count += 1
            refund_litres += litres
            refund_cost += cost
        else:
            purchase_count += 1
            purchase_litres += litres
            purchase_cost += cost
        if is_monthly_summary(t):
            summary_rows += 1
        elif litres > 0:
            dated_purchase_count += 1
        if t.get("_unit_id") is not None:
            vehicles.add(unit_key(t))  # identifiers of one Wialon unit count once
        elif v_ident and str(v_ident).strip():
            vehicles.add(("id", str(v_ident).strip()))

        ft_map[ft]["litres"] += litres
        ft_map[ft]["cost"] += cost
        ft_map[ft]["count"] += 1

    total_litres_rounded = _q2(total_litres)
    total_cost_rounded = _q2(total_cost)
    purchase_litres_rounded = _q2(purchase_litres)
    purchase_cost_rounded = _q2(purchase_cost)
    avg_price = round(purchase_cost_rounded / purchase_litres_rounded, 2) if purchase_litres_rounded > 0 else 0.0

    breakdown: List[FuelTypeBreakdownItem] = []
    for ft_name, data in ft_map.items():
        l_round = _q2(data["litres"])
        c_round = _q2(data["cost"])
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
        purchase_count=purchase_count,
        gross_purchased_litres=purchase_litres_rounded,
        gross_purchased_cost=purchase_cost_rounded,
        refund_count=refund_count,
        refund_litres=_q2(refund_litres),
        refund_cost=_q2(refund_cost),
        dated_purchase_count=dated_purchase_count,
        monthly_summary_rows=summary_rows,
    )


def monthly_summary_excluded(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Totals of monthly-summary rows left out of a day/week view, or None when there are none."""
    if not rows:
        return None
    return {
        "rows": len(rows),
        "litres": _q2(sum((Decimal(str(r.get("litres") or 0)) for r in rows), Decimal(0))),
        "cost": _q2(sum((Decimal(str(r.get("total_cost") or 0)) for r in rows), Decimal(0))),
        "months": sorted({str(r.get("transaction_date"))[:7] for r in rows}),
    }


MONTHLY_SUMMARY_TREND_WARNING = (
    "Monthly-summary fuel is not shown in the daily or weekly trend: those rows are monthly totals with a month-end "
    "reporting date, not refuelling events. Use the monthly view to include them."
)


def calculate_fuel_trend(transactions: List[Dict[str, Any]], group_by: str = "day") -> FuelTrendResponse:
    """
    Group transactions chronologically by day, week, or month. Monthly-summary rows are included only when grouping
    by month; for day/week they are reported in monthly_summary_excluded, never placed on their reporting date.
    """
    clean_group_by = group_by.lower().strip()
    if clean_group_by not in VALID_GROUP_BY:
        raise ValueError(f"Unsupported group_by '{group_by}'. Supported values: 'day', 'week', 'month'.")

    if not transactions:
        return FuelTrendResponse(group_by=clean_group_by, data=[])

    summaries: List[Dict[str, Any]] = []
    if clean_group_by != "month":
        summaries = [t for t in transactions if is_monthly_summary(t)]
        transactions = [t for t in transactions if not is_monthly_summary(t)]

    # Decimal sums (rounded once per period with _q2), so periods reconcile with the summary to the cent
    period_map: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": 0, "purchases": 0, "refunds": 0, "summaries": 0})

    for t in transactions:
        raw_date = t.get("transaction_date")
        if not raw_date:
            continue

        date_str = tx_local_date(raw_date) or str(raw_date)[:10]
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

        litres = Decimal(str(t.get("litres") or 0))
        cost = Decimal(str(t.get("total_cost") or 0))

        period_map[period_key]["litres"] += litres
        period_map[period_key]["cost"] += cost
        period_map[period_key]["count"] += 1
        if is_monthly_summary(t):
            period_map[period_key]["summaries"] += 1
        elif litres < 0:
            period_map[period_key]["refunds"] += 1
        elif litres > 0:
            period_map[period_key]["purchases"] += 1

    # Sort periods chronologically
    sorted_periods = sorted(period_map.keys())

    items: List[FuelTrendItem] = []
    for p in sorted_periods:
        l_round = _q2(period_map[p]["litres"])
        c_round = _q2(period_map[p]["cost"])
        items.append(
            FuelTrendItem(
                period=p,
                litres=l_round,
                cost=c_round,
                transactions=period_map[p]["count"],
                average_unit_price=round(c_round / l_round, 2) if l_round > 0 else 0.0,
                dated_purchase_count=period_map[p]["purchases"],
                refund_count=period_map[p]["refunds"],
                monthly_summary_rows=period_map[p]["summaries"],
            )
        )

    return FuelTrendResponse(group_by=clean_group_by, data=items,
                             monthly_summary_excluded=monthly_summary_excluded(summaries),
                             data_warnings=[MONTHLY_SUMMARY_TREND_WARNING] if summaries else [])


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

    # Grouped per Wialon unit when rows are scope-tagged (identifiers of one unit combined), else per identifier.
    veh_map: Dict[Any, Dict[str, Any]] = defaultdict(
        lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": 0, "ids": defaultdict(int)})

    for t in transactions:
        v_ident = str(t.get("vehicle_identifier") or "").strip()
        if not v_ident:
            continue

        litres = Decimal(str(t.get("litres") or 0))
        cost = Decimal(str(t.get("total_cost") or 0))

        key = unit_key(t) if t.get("_unit_id") is not None else ("id", v_ident)
        veh_map[key]["litres"] += litres
        veh_map[key]["cost"] += cost
        veh_map[key]["count"] += 1
        veh_map[key]["ids"][v_ident] += 1

    records: List[VehicleAnalyticsItem] = []
    for key, data in veh_map.items():
        l_round = _q2(data["litres"])
        c_round = _q2(data["cost"])
        count = data["count"]
        ids = data["ids"]
        primary = sorted(ids.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        records.append(
            VehicleAnalyticsItem(
                vehicle_identifier=primary,
                litres=l_round,
                total_cost=c_round,
                transaction_count=count,
                average_unit_price=round(c_round / l_round, 2) if l_round > 0 else 0.0,
                average_litres_per_transaction=round(l_round / count, 2) if count > 0 else 0.0,
                wialon_unit_id=key[1] if key[0] == "unit" else None,
                merged_identifiers=sorted(ids) if len(ids) > 1 else [],
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

    ft_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"litres": Decimal(0), "cost": Decimal(0), "count": 0})

    for t in transactions:
        ft = str(t.get("fuel_type") or "UNKNOWN").strip().upper()
        litres = Decimal(str(t.get("litres") or 0))
        cost = Decimal(str(t.get("total_cost") or 0))

        ft_map[ft]["litres"] += litres
        ft_map[ft]["cost"] += cost
        ft_map[ft]["count"] += 1

    items: List[FuelTypeBreakdownItem] = []
    for ft_name, data in ft_map.items():
        l_round = _q2(data["litres"])
        c_round = _q2(data["cost"])
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


# ─── Daily fuel with explicit statement coverage ───────────────────────────────
# Coverage is proven per imported statement, never per supplier: a confirmed statement shows that ITS rows for a vehicle
# are complete for the period, but fuel bought from a supplier (card provider) whose statement was not imported is
# invisible. fuel_imports stores no supplier/provider identity, so completeness across suppliers cannot be established
# (it would need e.g. a provider column on fuel_imports and the expected providers per vehicle). The values are named
# for what they prove: statement_covered / statement_verified, with supplier_completeness "not_established".
COVERAGE_COVERED = "statement_covered"
COVERAGE_NOT_COVERED = "not_covered"
COVERAGE_FUTURE = "future"
SUPPLIER_COMPLETENESS_NOT_ESTABLISHED = "not_established"
COVERAGE_NOTE = ("Coverage means each vehicle's imported dated statement is confirmed complete for the day. Fuel from a "
                 "supplier whose statement was not imported cannot be detected, so a zero day is a zero within the "
                 "imported statements, not proof that no supplier sold fuel.")


def unit_coverage_days(
    rows: List[Dict[str, Any]], import_meta: Dict[int, Dict[str, Any]], first: date, last: date,
) -> Dict[int, set]:
    """
    Wialon unit id -> the days of [first, last] its fuel statements are confirmed to cover: days inside the confirmed
    period (reporting_period.confirmed_coverage_period) of an import that holds one of the unit's dated rows. Every
    fuel type counts (a statement covers a vehicle, not a product). Monthly-summary rows never establish coverage, and
    a unit-month with a monthly summary has no covered day: its fuel is a monthly total, so a day without dated rows
    is unknown, never a zero. Dated rows a summary superseded establish nothing either.
    """
    covered: Dict[int, set] = defaultdict(set)
    summary_months: Dict[int, set] = defaultdict(set)
    for r in rows:
        uid = r.get("_unit_id", r.get("_overlap_unit_id"))
        d = tx_local_date(r.get("transaction_date"))
        if uid is not None and d and is_monthly_summary(r):
            summary_months[int(uid)].add(d[:7])
    for r in rows:
        uid = r.get("_unit_id", r.get("_overlap_unit_id"))
        if uid is None or is_monthly_summary(r) or r.get("_exclusion_reason") == REASON_DATED_SUPERSEDED:
            continue
        try:
            period = confirmed_coverage_period(import_meta.get(int(r.get("import_id"))))
        except (TypeError, ValueError):
            period = None
        if period is None:
            continue
        day, end = max(period[0], first), min(period[1], last)
        while day <= end:
            if day.strftime("%Y-%m") not in summary_months[int(uid)]:
                covered[int(uid)].add(day)
            day += timedelta(days=1)
    return covered


def calculate_daily_fuel(
    kpi_rows: List[Dict[str, Any]],
    coverage_rows: List[Dict[str, Any]],
    import_meta: Dict[int, Dict[str, Any]],
    date_from: str,
    date_to: str,
    today: date,
) -> DailyFuelResponse:
    """
    Every calendar day of [date_from, date_to] with dated litres, cost, purchase and refund counts (kpi_rows: the KPI
    scope, fuel-type filter applied) and its coverage. coverage_rows are the scope's rows of every fuel type (matched
    plus rows excluded as summary/dated overlaps): their units are the vehicles the day must cover. A day is covered
    only when every such unit's statement is confirmed for it (unit_coverage_days); a later day than today is never
    covered. A day without rows is a zero only when statement-covered; otherwise its values are null (unknown), never 0.
    Statement coverage does not prove supplier completeness (see COVERAGE_NOTE).
    Monthly-summary rows are never placed on a day: they are reported in monthly_summary_excluded.
    """
    first = datetime.strptime(date_from[:10], "%Y-%m-%d").date()
    last = datetime.strptime(date_to[:10], "%Y-%m-%d").date()
    units = {int(r.get("_unit_id", r.get("_overlap_unit_id"))) for r in coverage_rows
             if r.get("_unit_id", r.get("_overlap_unit_id")) is not None}
    covered_by_unit = unit_coverage_days(coverage_rows, import_meta, first, last)

    per_day: Dict[date, Dict[str, Any]] = defaultdict(
        lambda: {"litres": Decimal(0), "cost": Decimal(0), "purchases": 0, "refunds": 0, "rows": 0})
    summaries: List[Dict[str, Any]] = []
    for r in kpi_rows:
        if is_monthly_summary(r):
            summaries.append(r)
            continue
        d = tx_local_date(r.get("transaction_date"))
        if not d:
            continue
        day = datetime.strptime(d, "%Y-%m-%d").date()
        if not first <= day <= last:
            continue
        litres = Decimal(str(r.get("litres") or 0))
        p = per_day[day]
        p["litres"] += litres
        p["cost"] += Decimal(str(r.get("total_cost") or 0))
        p["rows"] += 1
        if litres < 0:
            p["refunds"] += 1
        elif litres > 0:
            p["purchases"] += 1

    items: List[DailyFuelItem] = []
    day = first
    while day <= last:
        if day > today:
            status = COVERAGE_FUTURE
            n_covered = 0
        else:
            n_covered = sum(1 for u in units if day in covered_by_unit.get(u, ()))
            status = COVERAGE_COVERED if units and n_covered == len(units) else COVERAGE_NOT_COVERED
        p = per_day.get(day)
        known = p is not None or status == COVERAGE_COVERED
        items.append(DailyFuelItem(
            date=day.isoformat(),
            coverage=status,
            litres=_q2(p["litres"]) if p else (0.0 if known else None),
            cost=_q2(p["cost"]) if p else (0.0 if known else None),
            dated_purchase_count=p["purchases"] if p else (0 if known else None),
            refund_count=p["refunds"] if p else (0 if known else None),
            transactions=p["rows"] if p else (0 if known else None),
            units_covered=n_covered,
            units_in_scope=len(units),
        ))
        day += timedelta(days=1)

    covered_days = sum(1 for i in items if i.coverage == COVERAGE_COVERED)
    if covered_days == len(items):
        overall = "statement_verified"
    elif covered_days:
        overall = "partial"
    else:
        overall = "unverified"
    return DailyFuelResponse(
        date_from=first.isoformat(),
        date_to=last.isoformat(),
        coverage_status=overall,
        covered_days=covered_days,
        total_days=len(items),
        dated_row_count=sum(p["rows"] for p in per_day.values()),
        units_in_scope=len(units),
        data=items,
        monthly_summary_excluded=monthly_summary_excluded(summaries),
        supplier_completeness=SUPPLIER_COMPLETENESS_NOT_ESTABLISHED,
        coverage_note=COVERAGE_NOTE,
    )
