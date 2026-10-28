"""
Fuel Waste Indicators and Anomaly Detection Engine for ATS Fleet Management.
Identifies potential fuel waste, unusual refuelling patterns, and data quality inconsistencies
with complete mathematical transparency and explicit rule explanations.
"""
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import logging
import os
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from app.fuel.db import build_fingerprint
from app.fuel.schemas import FuelAnomalyItem, FuelWasteResponse, VehicleEfficiencyItem
from app.fuel.reporting_period import is_monthly_summary
from app.fuel.scope import (
    OVERLAP_UNCONFIRMED,
    REASON_DUPLICATE,
    REASON_OVERLAP,
    REASON_PARTIAL_SUMMARY,
    REASON_SUMMARY_SUPERSEDED,
    unit_key,
)
from app.utils.datetime_helpers import parse_tx_wallclock

logger = logging.getLogger(__name__)

# Rule thresholds (documented in detect_fuel_waste_and_anomalies)
SPIKE_RATIO = 1.6               # refuel > 1.6 x median of the vehicle's OTHER purchases
SPIKE_MIN_BASELINE_PURCHASES = 3
SPIKE_MIN_BASELINE_LITRES = 15.0
RAPID_REFUEL_HOURS = 24.0
IDLE_CRITICAL_HOURS = 15.0      # idle hours per day
IDLE_CRITICAL_RATIO_PCT = 40.0
IDLE_THRESHOLD_MODES = ("per_day", "total")


def _spike_abs_litres() -> Optional[float]:
    """Optional absolute spike cap (FUEL_SPIKE_ABS_LITRES); unset by default."""
    raw = os.getenv("FUEL_SPIKE_ABS_LITRES", "").strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def parse_tx_datetime(date_str: Any) -> Optional[datetime]:
    """Parse transaction date string to datetime."""
    if not date_str:
        return None
    if isinstance(date_str, datetime):
        return date_str.replace(tzinfo=None) if date_str.tzinfo is not None else date_str
    if hasattr(date_str, "year") and hasattr(date_str, "month") and hasattr(date_str, "day"):
        from datetime import date
        if isinstance(date_str, date) and not isinstance(date_str, datetime):
            return datetime.combine(date_str, datetime.min.time())
    s = str(date_str).strip()
    if not s:
        return None

    # Handle standard ISO formats (including 2026-08-03T10:00:00 and offsets)
    iso_clean = s.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_clean)
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
    except (ValueError, TypeError):
        pass

    # Normalized formats with T replaced by space
    norm_s = s[:19].replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y"):
        try:
            return datetime.strptime(norm_s, fmt)
        except (ValueError, TypeError):
            pass

    return None



def detect_fuel_waste_and_anomalies(
    transactions: List[Dict[str, Any]],
    vehicle_items: List[VehicleEfficiencyItem],
    idle_threshold_hours: float = 5.0,
    idle_rate_lph: float = 2.0,
    excluded_groups: Optional[List[Dict[str, Any]]] = None,
    idle_threshold_mode: str = "per_day",
    period_days: Optional[int] = None,
    overlap_conflicts: Optional[List[Dict[str, Any]]] = None,
) -> FuelWasteResponse:
    """
    Rules (evaluated per Wialon unit; refund adjustments are never refuel events):
    1. Excessive idling: Wialon idle hours >= effective threshold. per_day mode scales the threshold by the
       period length in days (period_days, default 1); total mode applies it to the whole period.
       Unknown idle hours are never treated as 0. Idle litres and cost are the vehicle records' own estimates
       (efficiency.estimate_unit_idle), so fleet_estimated_idle_fuel_litres equals the efficiency KPI and the
       rankings; a vehicle without an estimate (e.g. mixed-fuel under a fuel-type filter) has no idle litres. [estimated]
    2. Rapid refuel: two purchases < 24 h apart, only when both carry a time of day. Date-only rows on the
       same day give one 'same_day_multiple_refuels' info item instead. [recorded]
    3. Volume spike: purchase > SPIKE_RATIO x median of the vehicle's OTHER purchases (needs >= 3 others and a
       median > 15 L); optional absolute cap via FUEL_SPIKE_ABS_LITRES. [recorded]
    4. Odometer: regressions per transaction; missing odometers aggregated into one item per vehicle. [recorded]
    5. Duplicates: same-import identical rows (full fingerprint) here; cross-import suspected duplicates come
       from the scope partition (already excluded from KPIs). [recorded]
    6. Unmatched / ambiguous identifiers: one info item each, from the scope partition. [recorded]
    7. Monthly-summary / dated overlaps (excluded_scope.overlap_conflicts): one 'summary_dated_overlap' info item per
       unresolved unit-month. Those vehicles ARE matched to a Wialon unit, so they are never reported as unmatched;
       a summary superseded by confirmed full-month dated rows is resolved and reported nowhere here. [recorded]
    refuel_anomalies_count counts distinct transactions; data_quality_issues_count equals the number of
    data-quality items emitted.
    """
    anomalies: List[FuelAnomalyItem] = []
    mode = idle_threshold_mode if idle_threshold_mode in IDLE_THRESHOLD_MODES else "per_day"
    days = max(1, int(period_days or 1))
    effective_threshold = idle_threshold_hours * days if mode == "per_day" else idle_threshold_hours

    # ── 1. Excessive Idling Detection ──────────────────────────────────────────
    excessive_idle_count = 0
    total_idle_waste_litres = 0.0
    total_idle_waste_cost = 0.0
    fleet_idle_litres = Decimal(0)
    fleet_idle_known = 0
    top_idle_candidates: List[VehicleEfficiencyItem] = []

    for v in vehicle_items:
        if v.status not in ("matched", "no_distance_data") or v.idle_duration_hours is None:
            continue
        idle_hrs = float(v.idle_duration_hours)
        eng_hrs = v.engine_hours
        idle_waste_l = v.estimated_idle_fuel_litres
        idle_cost = v.estimated_idle_cost
        if idle_waste_l is not None:
            fleet_idle_litres += Decimal(str(idle_waste_l))
            fleet_idle_known += 1

        if idle_hrs >= effective_threshold and idle_hrs > 0:
            excessive_idle_count += 1
            if idle_waste_l is not None:
                total_idle_waste_litres += idle_waste_l
            if idle_cost is not None:
                total_idle_waste_cost += idle_cost  # an unknown cost is left out, not added as 0
            top_idle_candidates.append(v)

            ratio_pct = (idle_hrs / eng_hrs * 100.0) if eng_hrs else None
            per_day = idle_hrs / days
            severity = "critical" if per_day >= IDLE_CRITICAL_HOURS or (ratio_pct or 0) >= IDLE_CRITICAL_RATIO_PCT else "warning"
            ratio_txt = f"{ratio_pct:.1f}% of {eng_hrs:.1f}h engine runtime" if ratio_pct is not None else "engine runtime unavailable"
            cost_txt = f"AED {idle_cost:.2f}" if idle_cost is not None else "cost unavailable"
            basis = (f"{idle_threshold_hours:.1f}h/day x {days} day(s) = {effective_threshold:.1f}h"
                     if mode == "per_day" else f"{effective_threshold:.1f}h for the period")

            anomalies.append(
                FuelAnomalyItem(
                    id=f"idle-{v.wialon_unit_id or v.vehicle_identifier}",
                    anomaly_type="excessive_idling",
                    severity=severity,
                    finding_type="estimated",
                    classification="estimated",
                    vehicle_identifier=v.vehicle_identifier,
                    title=f"Excessive Engine Idling ({idle_hrs:.1f} hrs)",
                    description=(
                        f"Wialon recorded {idle_hrs:.1f} idle hours ({ratio_txt}), exceeding the threshold of {basis}. "
                        + (f"Estimated idle fuel: {idle_waste_l:.1f} L ({cost_txt}) at the configured {idle_rate_lph:.1f} L/h rate."
                           if idle_waste_l is not None else
                           "Idle fuel not estimated: " + next((w for w in v.quality_warnings if "idle" in w.lower()),
                                                              "no idle estimate for this vehicle."))
                    ),
                    recorded_value=f"{idle_hrs:.1f} hrs idle" + (f" / {eng_hrs:.1f} hrs engine" if eng_hrs is not None else ""),
                    threshold_or_baseline=f"Threshold: {basis}",
                    wasted_litres=round(idle_waste_l, 2) if idle_waste_l is not None else None,
                    wasted_cost=round(idle_cost, 2) if idle_cost is not None else None,
                    details={
                        "idle_hours": idle_hrs,
                        "engine_hours": eng_hrs,
                        "idle_ratio_pct": round(ratio_pct, 1) if ratio_pct is not None else None,
                        "idle_rate_lph": idle_rate_lph,
                        "idle_threshold_mode": mode,
                        "effective_threshold_hours": effective_threshold,
                    },
                )
            )

    top_idle_candidates.sort(key=lambda x: (x.estimated_idle_cost or 0.0), reverse=True)

    # ── 2. Group Transactions per Wialon unit for Longitudinal Analysis ────────
    items_by_unit = {v.wialon_unit_id: v for v in vehicle_items if v.wialon_unit_id is not None}
    status_by_ident = {v.vehicle_identifier: v.status for v in vehicle_items}
    groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
    for tx in transactions:
        vid = str(tx.get("vehicle_identifier", "") or "").strip()
        if not vid or is_monthly_summary(tx):
            continue  # a monthly summary is a month's total, not a refuel: no refuel-pattern checks apply
        groups[unit_key(tx)].append(tx)

    refuel_flagged_tx = set()
    data_quality_issues_count = 0

    def flag_refuel(tx: Dict[str, Any], vid: str) -> None:
        refuel_flagged_tx.add(tx.get("id") if tx.get("id") is not None else (vid, id(tx)))

    # ── 3. Evaluate Per-Unit Refuel Patterns ───────────────────────────────────
    for key, v_txns in groups.items():
        item = items_by_unit.get(key[1]) if key[0] == "unit" else None
        vid = item.vehicle_identifier if item else str(v_txns[0].get("vehicle_identifier")).strip()
        status = item.status if item else status_by_ident.get(vid, "matched")
        if status not in ("matched", "no_distance_data"):
            continue

        valid_date_txns: List[Tuple[datetime, bool, Dict[str, Any]]] = []
        for t in v_txns:
            parsed = parse_tx_wallclock(t.get("transaction_date"))
            if parsed is not None:
                valid_date_txns.append((parsed[0], parsed[1] != "date", t))
            else:
                data_quality_issues_count += 1
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"invalid-dt-{t.get('id') or vid}",
                        anomaly_type="invalid_transaction_date",
                        severity="warning",
                        finding_type="measured",
                        classification="recorded",
                        vehicle_identifier=vid,
                        transaction_id=t.get("id"),
                        transaction_date=str(t.get("transaction_date", "")),
                        title="Unparseable Transaction Timestamp",
                        description=f"Transaction timestamp '{t.get('transaction_date')}' could not be parsed.",
                        recorded_value=str(t.get("transaction_date", "")),
                        threshold_or_baseline="Valid ISO-8601 or YYYY-MM-DD format",
                        details={"raw_value": str(t.get("transaction_date", ""))},
                    )
                )

        valid_date_txns.sort(key=lambda x: x[0])
        purchases = [(dt, has_time, t) for dt, has_time, t in valid_date_txns if float(t.get("litres") or 0) > 0]
        purchase_litres = [float(t.get("litres")) for _, _, t in purchases]
        abs_cap = _spike_abs_litres()

        last_dt: Optional[datetime] = None
        last_has_time = False
        last_tx: Optional[Dict[str, Any]] = None
        last_odo: Optional[float] = None
        seen_fingerprints = set()
        same_day_reported = set()
        missing_odo: List[Dict[str, Any]] = []

        for idx, (dt, has_time, tx) in enumerate(purchases):
            tx_id = tx.get("id")
            litres = float(tx.get("litres") or 0)
            cost = float(tx.get("total_cost") or 0)
            odo = float(tx.get("odometer_km")) if tx.get("odometer_km") is not None and str(tx.get("odometer_km")).strip() != "" else None
            date_str = str(tx.get("transaction_date", ""))
            day = dt.strftime("%Y-%m-%d")

            # A. Same-import duplicate candidate (identical full fingerprint, incl. time)
            fp = build_fingerprint(tx.get("vehicle_identifier"), tx.get("transaction_date"), tx.get("litres"),
                                   tx.get("total_cost"), tx.get("invoice_number"), tx.get("odometer_km"))
            if fp in seen_fingerprints:
                data_quality_issues_count += 1
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"dupe-{tx_id or vid}-{day}",
                        anomaly_type="duplicate_candidate",
                        severity="info",
                        finding_type="measured",
                        classification="recorded",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title="Potential Duplicate Fuel Transaction",
                        description=(
                            f"Identical refuel ({litres:.2f} L, AED {cost:.2f}, same timestamp, invoice and odometer) "
                            f"appears more than once in the same import on {day}. It is included in KPIs; review it."
                        ),
                        recorded_value=f"{litres:.2f} L / AED {cost:.2f}",
                        threshold_or_baseline="Identical fingerprint within one import",
                        details={"station": tx.get("station_name"), "invoice": tx.get("invoice_number")},
                    )
                )
            seen_fingerprints.add(fp)

            # B. Rapid consecutive refuels (time of day required on both rows)
            if last_dt is not None and has_time and last_has_time:
                delta = dt - last_dt
                if timedelta(seconds=1) < delta < timedelta(hours=RAPID_REFUEL_HOURS):
                    delta_hrs = delta.total_seconds() / 3600.0
                    flag_refuel(tx, vid)
                    last_l = float(last_tx.get("litres") or 0) if last_tx else 0.0
                    anomalies.append(
                        FuelAnomalyItem(
                            id=f"rapid-{tx_id or vid}-{day}",
                            anomaly_type="rapid_refuel",
                            severity="warning",
                            finding_type="measured",
                            classification="recorded",
                            vehicle_identifier=vid,
                            transaction_id=tx_id,
                            transaction_date=date_str,
                            title=f"Rapid Consecutive Refuel ({delta_hrs:.1f}h Interval)",
                            description=(
                                f"Vehicle refuelled {litres:.1f} L just {delta_hrs:.1f} hours after a preceding refuel of {last_l:.1f} L. "
                                f"Short-interval refuels warrant route and schedule verification."
                            ),
                            recorded_value=f"{litres:.1f} L ({delta_hrs:.1f}h delta)",
                            threshold_or_baseline=f"Normal interval: > {RAPID_REFUEL_HOURS:.0f} hours between refills",
                            details={
                                "previous_litres": last_l,
                                "current_litres": litres,
                                "hours_between": round(delta_hrs, 1),
                                "previous_date": str(last_tx.get("transaction_date")) if last_tx else None,
                            },
                        )
                    )
            elif (last_dt is not None and not (has_time and last_has_time)
                  and last_dt.date() == dt.date() and day not in same_day_reported):
                same_day_reported.add(day)
                n_same = sum(1 for d, _, _ in purchases if d.date() == dt.date())
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"sameday-{key[1]}-{day}",
                        anomaly_type="same_day_multiple_refuels",
                        severity="info",
                        finding_type="measured",
                        classification="recorded",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=day,
                        title=f"{n_same} Refuels on {day} (no time of day)",
                        description=("Several refuels are recorded on the same day without a time of day on both, so the "
                                     "interval between them cannot be measured."),
                        recorded_value=f"{n_same} refuels",
                        threshold_or_baseline="Interval needs a time of day on both rows",
                        details={"date": day, "count": n_same},
                    )
                )

            # C. Volume spike against the median of the vehicle's OTHER purchases
            others = purchase_litres[:idx] + purchase_litres[idx + 1:]
            baseline = median(others) if len(others) >= SPIKE_MIN_BASELINE_PURCHASES else None
            ratio_hit = baseline is not None and baseline > SPIKE_MIN_BASELINE_LITRES and litres > baseline * SPIKE_RATIO
            abs_hit = abs_cap is not None and litres > abs_cap
            if ratio_hit or abs_hit:
                flag_refuel(tx, vid)
                ratio = (litres / baseline) if baseline else None
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"spike-{tx_id or vid}-{day}",
                        anomaly_type="volume_spike",
                        severity="warning",
                        finding_type="measured",
                        classification="recorded",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title=f"Unusual Refuel Volume Spike ({litres:.1f} L)",
                        description=(
                            f"Refuel of {litres:.1f} L is {ratio:.1f}x the median ({baseline:.1f} L) of this vehicle's other purchases."
                            if ratio_hit else f"Refuel of {litres:.1f} L exceeds the configured absolute cap of {abs_cap:.1f} L."
                        ),
                        recorded_value=f"{litres:.1f} L",
                        threshold_or_baseline=(
                            f"Median of other purchases: {baseline:.1f} L (threshold > {baseline * SPIKE_RATIO:.1f} L)"
                            if ratio_hit else f"Absolute cap: {abs_cap:.1f} L"
                        ),
                        details={"baseline_median_litres": round(baseline, 1) if baseline is not None else None,
                                 "ratio": round(ratio, 2) if ratio is not None else None,
                                 "baseline_purchases": len(others)},
                    )
                )

            # D. Odometer
            if odo is None or odo <= 0:
                missing_odo.append(tx)
            elif last_odo is not None and odo < last_odo:
                data_quality_issues_count += 1
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"odo-{tx_id or vid}-{day}",
                        anomaly_type="odometer_inconsistency",
                        severity="warning",
                        finding_type="measured",
                        classification="recorded",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title=f"Odometer Decreased ({last_odo:.0f} → {odo:.0f} km)",
                        description=(
                            f"Recorded odometer value ({odo:.0f} km) is lower than the previous reading ({last_odo:.0f} km). "
                            f"Indicates a manual entry error at the station or an odometer replacement."
                        ),
                        recorded_value=f"{odo:.0f} km",
                        threshold_or_baseline=f"Previous reading: {last_odo:.0f} km",
                        details={"previous_odometer": last_odo, "current_odometer": odo},
                    )
                )

            last_dt = dt
            last_has_time = has_time
            last_tx = tx
            if odo is not None and odo > 0:
                last_odo = odo

        if missing_odo:
            data_quality_issues_count += 1
            anomalies.append(
                FuelAnomalyItem(
                    id=f"odo-missing-{key[1]}",
                    anomaly_type="missing_odometer",
                    severity="info",
                    finding_type="measured",
                    classification="recorded",
                    vehicle_identifier=vid,
                    title=f"Odometer Missing on {len(missing_odo)} of {len(purchases)} Refuels",
                    description="Refuels without an odometer reading cannot be checked for odometer consistency.",
                    recorded_value=f"{len(missing_odo)} refuels",
                    threshold_or_baseline="Odometer expected on every refuel",
                    details={"transaction_ids": [t.get("id") for t in missing_odo[:50]]},
                )
            )

    # ── 4. Excluded identifiers and cross-import duplicates (from the scope partition) ──
    for g in excluded_groups or []:
        rsn = g.get("reason")
        if rsn == REASON_PARTIAL_SUMMARY:
            continue  # excluded only because the selected range splits its month; not a data-quality finding
        if rsn in (REASON_OVERLAP, REASON_SUMMARY_SUPERSEDED):
            continue  # matched vehicles: unresolved overlaps are reported per unit-month below, superseded ones are resolved
        is_dup = rsn == REASON_DUPLICATE
        if is_dup:
            data_quality_issues_count += 1
        cand = g.get("candidate_unit_names") or []
        anomalies.append(
            FuelAnomalyItem(
                id=f"{'dupx' if is_dup else 'unmatched'}-{rsn}-{g.get('vehicle_identifier')}",
                anomaly_type="duplicate_candidate" if is_dup else "unmatched_vehicle",
                severity="info",
                finding_type="measured",
                classification="recorded",
                vehicle_identifier=g.get("vehicle_identifier") or "(blank)",
                title=(f"{g['transaction_count']} Suspected Re-imported Transaction(s)" if is_dup else
                       f"Fuel Not Attributed to a Wialon Unit ({rsn})"),
                description=(
                    "Identical to transactions from an earlier import; excluded from KPIs. Nothing was deleted."
                    if is_dup else
                    (f"Identifier matches several Wialon units ({', '.join(cand)}); excluded from KPIs rather than guessed."
                     if rsn == "ambiguous" else "Identifier matches no Wialon unit; excluded from KPIs.")
                ),
                recorded_value=f"{g['transaction_count']} tx / {g['litres']:.1f} L / AED {g['cost']:.2f}",
                threshold_or_baseline="KPI scope: transactions resolving to exactly one Wialon unit, without cross-import duplicates",
                details={k: g.get(k) for k in ("reason", "candidate_unit_names", "first_date", "last_date", "transaction_ids")},
            )
        )

    # ── 5. Monthly-summary / dated overlaps of matched units (excluded from KPIs, never "unmatched") ──
    for c in overlap_conflicts or []:
        if c.get("resolution") != OVERLAP_UNCONFIRMED:
            continue
        summ, dated = c.get("excluded_summary") or {}, c.get("excluded_dated") or {}
        unit = c.get("wialon_unit_name") or c.get("wialon_unit_id")
        anomalies.append(
            FuelAnomalyItem(
                id=f"overlap-{c.get('wialon_unit_id')}-{c.get('month')}",
                anomaly_type="summary_dated_overlap",
                severity="info",
                finding_type="measured",
                classification="recorded",
                vehicle_identifier=(c.get("identifiers") or [str(unit)])[0],
                title=f"Monthly Summary and Dated Transactions Overlap ({unit}, {c.get('month')})",
                description=(
                    f"Wialon unit {unit} has both a monthly-summary total and dated transactions for {c.get('month')}, "
                    "and no import confirms that the dated statement covers the whole month. Both are excluded from "
                    "KPIs to avoid double counting; nothing was deleted. Re-import a complete statement to resolve it."
                ),
                recorded_value=(f"summary {summ.get('transaction_count', 0)} row(s) / {summ.get('litres', 0.0):.1f} L; "
                                f"dated {dated.get('transaction_count', 0)} row(s) / {dated.get('litres', 0.0):.1f} L"),
                threshold_or_baseline="One source per unit-month: dated rows only with confirmed full-month coverage",
                details={"wialon_unit_id": c.get("wialon_unit_id"), "wialon_unit_name": c.get("wialon_unit_name"),
                         "month": c.get("month"), "identifiers": c.get("identifiers"),
                         "excluded_summary": summ, "excluded_dated": dated, "resolution": c.get("resolution")},
            )
        )

    severity_order = {"critical": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda x: (severity_order.get(x.severity, 3), x.anomaly_type))

    return FuelWasteResponse(
        total_anomalies_count=len(anomalies),
        excessive_idle_vehicles_count=excessive_idle_count,
        total_idle_waste_litres=round(total_idle_waste_litres, 2),
        total_idle_waste_cost=round(total_idle_waste_cost, 2),
        refuel_anomalies_count=len(refuel_flagged_tx),
        data_quality_issues_count=data_quality_issues_count,
        idle_threshold_hours=idle_threshold_hours,
        idle_rate_lph=idle_rate_lph,
        anomalies=anomalies,
        top_idle_wasters=top_idle_candidates[:5],
        fleet_estimated_idle_fuel_litres=(float(fleet_idle_litres.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
                                          if fleet_idle_known else None),
        idle_waste_basis="vehicles_at_or_above_threshold",
        idle_threshold_mode=mode,
        effective_idle_threshold_hours=round(effective_threshold, 2),
        period_days=days,
    )
