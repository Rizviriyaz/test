"""
Read-only check of Wialon's driving-time fields before the Dashboard "Driving time" estimate is replaced (audit H5).

LIVE: logs into Wialon and runs two report templates for one closed UAE day. Reports are executed and cleaned up
(report/exec_report, report/cleanup_result); nothing in Wialon or Supabase is modified. Run only when approved.

For every unit it prints, side by side:
  - Engine Hours group report (template 2): Total time, Engine hours, In motion, Idling, Mileage
  - Trips group report (template 5): Mileage, Total time, Avg. speed
  - the Dashboard's current estimate: template-5 mileage / avg speed (minutes)
and flags rows where "In motion" cannot be used as driving time:
  - MOTION_ZERO_WITH_KM   mileage > 0 but In motion = 0 (no engine/ignition sensor?)
  - MOTION_GT_ENGINE      In motion > Engine hours
  - PARTS_NE_ENGINE       In motion + Idling differs from Engine hours by more than 60 s
  - UNREADABLE            a duration cell could not be parsed
  - NOT_IN_ENGINE_REPORT  unit present in the trips report only

USAGE (from Backend/):
    python scripts/inspect_driving_time_fields.py [--date YYYY-MM-DD] [--out PATH.json]
Default date: yesterday (UAE). The JSON is written only when --out is given.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.utils.datetime_helpers import UAE_TZ, parse_date_to_uae_bounds  # noqa: E402
from app.wialon import login  # noqa: E402
from app.wialon_reports import (  # noqa: E402
    execute_wialon_group_engine_idle_report,
    execute_wialon_group_trips_report,
    parse_duration_strict,
)


def _speed(text):
    """'45 km/h' -> 45.0; None when unreadable."""
    try:
        return float(str(text or "").lower().replace("km/h", "").strip())
    except ValueError:
        return None


def _mins(seconds):
    return None if seconds is None else round(seconds / 60.0, 1)


def main():
    yesterday = (datetime.now(UAE_TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--date", default=yesterday, help="closed UAE day, YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--out", default=None, help="optional JSON output path")
    args = ap.parse_args()

    from_ts, to_ts = parse_date_to_uae_bounds(args.date, args.date)
    sid = login()
    engine = execute_wialon_group_engine_idle_report(sid, from_ts, to_ts, include_events=False) or {}
    trips = execute_wialon_group_trips_report(sid, from_ts, to_ts) or []

    eng_by_name = {r["vehicle_name"]: r for r in engine.get("vehicles") or []}
    trips_by_name = {r["vehicle_name"]: r for r in trips}
    rows, flag_counts = [], {}
    for name in sorted(set(eng_by_name) | set(trips_by_name)):
        e, t = eng_by_name.get(name), trips_by_name.get(name)
        flags = []
        if e is None:
            flags.append("NOT_IN_ENGINE_REPORT")
        unreadable = set((e or {}).get("incomplete_fields") or []) & {
            "total_time_seconds", "engine_hours_seconds", "in_motion_seconds", "total_idle_seconds"}
        if unreadable:
            flags.append("UNREADABLE")
        motion = None if e is None or "in_motion_seconds" in unreadable else e.get("in_motion_seconds")
        idling = None if e is None or "total_idle_seconds" in unreadable else e.get("total_idle_seconds")
        eng_s = None if e is None or "engine_hours_seconds" in unreadable else e.get("engine_hours_seconds")
        km_t2 = (e or {}).get("mileage_km")
        km_t5 = None if t is None or "mileage_km" in (t.get("incomplete_fields") or []) else t.get("mileage_km")
        km = km_t5 if km_t5 is not None else km_t2
        if motion == 0 and (km or 0) > 0:
            flags.append("MOTION_ZERO_WITH_KM")
        if motion is not None and eng_s is not None and motion > eng_s:
            flags.append("MOTION_GT_ENGINE")
        if None not in (motion, idling, eng_s) and abs(motion + idling - eng_s) > 60:
            flags.append("PARTS_NE_ENGINE")
        avg = _speed((t or {}).get("avg_speed"))
        estimate_min = round(km_t5 / avg * 60, 1) if km_t5 and avg else (0.0 if km_t5 == 0 else None)
        t5_total = parse_duration_strict((t or {}).get("total_time")) if t else None
        for f in flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
        rows.append({
            "unit": name, "flags": flags,
            "t2_total_min": _mins(None if e is None else e.get("total_time_seconds")), "t2_engine_min": _mins(eng_s),
            "t2_in_motion_min": _mins(motion), "t2_idling_min": _mins(idling), "t2_km": km_t2,
            "t5_km": km_t5, "t5_total_time_min": _mins(t5_total), "t5_avg_speed": (t or {}).get("avg_speed"),
            "current_estimate_min": estimate_min,
        })

    cols = ["unit", "t5_km", "t2_km", "t2_engine_min", "t2_in_motion_min", "t2_idling_min", "t5_total_time_min",
            "current_estimate_min", "t5_avg_speed", "flags"]
    print(f"Driving-time fields for UAE day {args.date} ({len(rows)} units)")
    print("\t".join(cols))
    for r in rows:
        print("\t".join("" if r[c] is None else (",".join(r[c]) if isinstance(r[c], list) else str(r[c])) for c in cols))

    usable = [r for r in rows if r["t2_in_motion_min"] is not None and not r["flags"]]
    moving = [r for r in usable if (r["t5_km"] or 0) > 0 and r["current_estimate_min"]]
    print("\nSummary")
    print(f"  units: {len(rows)}; In motion usable (no flags): {len(usable)}")
    print(f"  flags: {json.dumps(flag_counts) if flag_counts else 'none'}")
    if moving:
        ratio = sum(r["t2_in_motion_min"] for r in moving) / sum(r["current_estimate_min"] for r in moving)
        print(f"  fleet In motion / current estimate (units with km): {ratio:.3f}")
    print(f"  engine report column warnings: {engine.get('column_warnings') or 'none'}; "
          f"unlocated: {engine.get('unlocated_fields') or 'none'}")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"date": args.date, "rows": rows, "flags": flag_counts,
                       "column_warnings": engine.get("column_warnings")}, fh, indent=2)
        print(f"  JSON written to {args.out}")


if __name__ == "__main__":
    main()
