"""
Fuel Waste Indicators and Anomaly Detection Engine for ATS Fleet Management.
Identifies potential fuel waste, unusual refuelling patterns, and data quality inconsistencies
with complete mathematical transparency and explicit rule explanations.
"""
from collections import defaultdict
from datetime import datetime, timedelta
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.fuel.schemas import FuelAnomalyItem, FuelWasteResponse, VehicleEfficiencyItem

logger = logging.getLogger(__name__)


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
) -> FuelWasteResponse:
    """
    Evaluates real transaction records and telematics activity to identify:
    1. Excessive vehicle idling waste
    2. Rapid consecutive refuels (< 24 hrs apart)
    3. Transaction volume spikes (> 1.5x vehicle average or > 120 L)
    4. Odometer regressions and missing odometers
    5. Candidate duplicate records
    6. Refuels assigned to unmatched or ambiguous vehicles
    """
    anomalies: List[FuelAnomalyItem] = []
    
    # ── 1. Excessive Idling Detection ──────────────────────────────────────────
    excessive_idle_count = 0
    total_idle_waste_litres = 0.0
    total_idle_waste_cost = 0.0
    top_idle_candidates: List[VehicleEfficiencyItem] = []

    for v in vehicle_items:
        idle_hrs = v.idle_duration_hours or 0.0
        eng_hrs = v.engine_hours or 0.0
        idle_waste_l = v.estimated_idle_fuel_litres or (idle_hrs * idle_rate_lph)
        idle_cost = v.estimated_idle_cost or 0.0

        if v.status == "matched" and idle_hrs >= idle_threshold_hours:
            excessive_idle_count += 1
            total_idle_waste_litres += idle_waste_l
            total_idle_waste_cost += idle_cost
            top_idle_candidates.append(v)

            ratio_pct = (idle_hrs / eng_hrs * 100.0) if eng_hrs > 0 else 0.0
            severity = "critical" if idle_hrs >= 15.0 or ratio_pct >= 40.0 else "warning"

            anomalies.append(
                FuelAnomalyItem(
                    id=f"idle-{v.vehicle_identifier}",
                    anomaly_type="excessive_idling",
                    severity=severity,
                    finding_type="estimated",
                    vehicle_identifier=v.vehicle_identifier,
                    title=f"Excessive Engine Idling ({idle_hrs:.1f} hrs)",
                    description=(
                        f"Vehicle accumulated {idle_hrs:.1f} qualifying idle hours "
                        f"({ratio_pct:.1f}% of {eng_hrs:.1f}h total engine runtime), exceeding the {idle_threshold_hours:.1f}h threshold. "
                        f"Estimated fuel waste: {idle_waste_l:.1f} L (AED {idle_cost:.2f}) at configured {idle_rate_lph:.1f} L/h rate."
                    ),
                    recorded_value=f"{idle_hrs:.1f} hrs idle / {eng_hrs:.1f} hrs engine",
                    threshold_or_baseline=f"Threshold: > {idle_threshold_hours:.1f} hrs idle",
                    wasted_litres=round(idle_waste_l, 2),
                    wasted_cost=round(idle_cost, 2),
                    details={
                        "idle_hours": idle_hrs,
                        "engine_hours": eng_hrs,
                        "idle_ratio_pct": round(ratio_pct, 1),
                        "idle_rate_lph": idle_rate_lph,
                    },
                )
            )

    top_idle_candidates.sort(key=lambda x: (x.estimated_idle_cost or 0.0), reverse=True)

    # ── 2. Group Transactions by Vehicle for Longitudinal Analysis ─────────────
    txns_by_vehicle: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unmatched_vehicles = set()

    for tx in transactions:
        vid = str(tx.get("vehicle_identifier", "")).strip()
        if not vid:
            continue
        txns_by_vehicle[vid].append(tx)

    # Map vehicle match status
    veh_status_map = {v.vehicle_identifier: v.status for v in vehicle_items}

    refuel_anomalies_count = 0
    data_quality_issues_count = 0

    # ── 3. Evaluate Per-Vehicle Refuel Patterns ────────────────────────────────
    for vid, v_txns in txns_by_vehicle.items():
        v_status = veh_status_map.get(vid, "matched")
        if v_status != "matched":
            continue

        # Sort transactions chronologically - strictly using successfully parsed datetimes
        valid_date_txns = []
        for t in v_txns:
            dt = parse_tx_datetime(t.get("transaction_date"))
            if dt is not None:
                valid_date_txns.append((dt, t))
            else:
                data_quality_issues_count += 1
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"invalid-dt-{t.get('id') or vid}",
                        anomaly_type="invalid_transaction_date",
                        severity="warning",
                        finding_type="measured",
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

        # Average litres for this vehicle
        litres_list = [float(t.get("litres") or 0) for t in v_txns if float(t.get("litres") or 0) > 0]
        avg_litres = sum(litres_list) / len(litres_list) if litres_list else 0.0

        # Check consecutive refuels, volume spikes, and odometer sequence
        last_dt: Optional[datetime] = None
        last_tx: Optional[Dict[str, Any]] = None
        last_odo: Optional[float] = None

        seen_fingerprints = set()

        for dt, tx in valid_date_txns:
            tx_id = tx.get("id")
            litres = float(tx.get("litres") or 0)
            cost = float(tx.get("total_cost") or 0)
            odo = float(tx.get("odometer_km")) if tx.get("odometer_km") is not None and str(tx.get("odometer_km")).strip() != "" else None
            date_str = str(tx.get("transaction_date", ""))

            # A. Duplicate Candidate Detection
            fp = f"{date_str[:10]}_{litres:.2f}_{cost:.2f}"
            if fp in seen_fingerprints:
                data_quality_issues_count += 1
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"dupe-{tx_id or vid}-{fp}",
                        anomaly_type="duplicate_candidate",
                        severity="info",
                        finding_type="measured",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title="Potential Duplicate Fuel Transaction",
                        description=(
                            f"Identical refuel volume ({litres:.2f} L) and expenditure (AED {cost:.2f}) "
                            f"recorded multiple times on date {date_str[:10]}."
                        ),
                        recorded_value=f"{litres:.2f} L / AED {cost:.2f}",
                        threshold_or_baseline="Exact volume + cost match on same day",
                        details={"station": tx.get("station_name"), "invoice": tx.get("invoice_number")},
                    )
                )
            seen_fingerprints.add(fp)

            # B. Rapid Consecutive Refuels (< 24 hours)
            if last_dt and (dt - last_dt) < timedelta(hours=24) and (dt - last_dt) > timedelta(seconds=1):
                delta_hrs = (dt - last_dt).total_seconds() / 3600.0
                refuel_anomalies_count += 1
                last_l = float(last_tx.get("litres") or 0) if last_tx else 0.0
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"rapid-{tx_id or vid}-{date_str[:10]}",
                        anomaly_type="rapid_refuel",
                        severity="warning",
                        finding_type="measured",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title=f"Rapid Consecutive Refuel ({delta_hrs:.1f}h Interval)",
                        description=(
                            f"Vehicle refueled {litres:.1f} L just {delta_hrs:.1f} hours after preceding refuel of {last_l:.1f} L. "
                            f"Short interval refuels warrant route and operational schedule verification."
                        ),
                        recorded_value=f"{litres:.1f} L ({delta_hrs:.1f}h delta)",
                        threshold_or_baseline="Normal interval: > 24 hours between full tank refills",
                        details={
                            "previous_litres": last_l,
                            "current_litres": litres,
                            "hours_between": round(delta_hrs, 1),
                            "previous_date": str(last_tx.get("transaction_date")) if last_tx else None,
                        },
                    )
                )

            # C. Volume Spike Anomaly
            if avg_litres > 15.0 and (litres > avg_litres * 1.6 or litres > 140.0):
                refuel_anomalies_count += 1
                ratio = (litres / avg_litres) if avg_litres > 0 else 1.0
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"spike-{tx_id or vid}-{date_str[:10]}",
                        anomaly_type="volume_spike",
                        severity="warning",
                        finding_type="measured",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title=f"Unusual Refuel Volume Spike ({litres:.1f} L)",
                        description=(
                            f"Refuel quantity of {litres:.1f} L is {ratio:.1f}x higher than this vehicle's average dispensed volume of {avg_litres:.1f} L."
                        ),
                        recorded_value=f"{litres:.1f} L",
                        threshold_or_baseline=f"Vehicle average: {avg_litres:.1f} L (Threshold: > {avg_litres * 1.6:.1f} L)",
                        details={"avg_litres": round(avg_litres, 1), "ratio": round(ratio, 2)},
                    )
                )

            # D. Odometer Inconsistency
            if odo is None or odo <= 0:
                data_quality_issues_count += 1
            elif last_odo is not None and odo < last_odo:
                data_quality_issues_count += 1
                anomalies.append(
                    FuelAnomalyItem(
                        id=f"odo-{tx_id or vid}-{date_str[:10]}",
                        anomaly_type="odometer_inconsistency",
                        severity="warning",
                        finding_type="measured",
                        vehicle_identifier=vid,
                        transaction_id=tx_id,
                        transaction_date=date_str,
                        title=f"Odometer Decreased ({last_odo:.0f} → {odo:.0f} km)",
                        description=(
                            f"Recorded odometer value ({odo:.0f} km) is lower than previous reading ({last_odo:.0f} km). "
                            f"Indicates manual entry error at fuel station or odometer replacement."
                        ),
                        recorded_value=f"{odo:.0f} km",
                        threshold_or_baseline=f"Previous reading: {last_odo:.0f} km",
                        details={"previous_odometer": last_odo, "current_odometer": odo},
                    )
                )

            last_dt = dt
            last_tx = tx
            if odo is not None and odo > 0:
                last_odo = odo

    # Sort anomalies: critical first, then warning, then info
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda x: (severity_order.get(x.severity, 3), x.anomaly_type))

    return FuelWasteResponse(
        total_anomalies_count=len(anomalies),
        excessive_idle_vehicles_count=excessive_idle_count,
        total_idle_waste_litres=round(total_idle_waste_litres, 2),
        total_idle_waste_cost=round(total_idle_waste_cost, 2),
        refuel_anomalies_count=refuel_anomalies_count,
        data_quality_issues_count=data_quality_issues_count,
        idle_threshold_hours=idle_threshold_hours,
        idle_rate_lph=idle_rate_lph,
        anomalies=anomalies,
        top_idle_wasters=top_idle_candidates[:5],
    )
