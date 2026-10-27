"""
Read-only reconciliation: Fleet Overview "today" mileage vs Wialon's own Trips report over the SAME window.

LIVE: logs into Wialon with WIALON_TOKEN from Backend/.env and runs one Trips report. Reads only: it never writes to
Wialon or Supabase, and it changes no application code or cache (it runs in its own process and Wialon session).

The dashboard response comes from one of:
  --dashboard-json FILE   the /api/dashboard/mileage?timeframe=today response saved from the browser (DevTools >
                          Network > Response). This is exactly what the dashboard showed, including its cache state.
  --api-url URL           fetched from a running backend with the bearer token in RECON_BEARER_TOKEN.
  --in-process            computed now by the backend's own route function in this process (fresh, uncached).

The Wialon report is re-run with the backend's template, resource and "All Unit" group (as configured in the LOCAL
Backend/.env; they must match the server's) over the response's exact effective_from..effective_to. Every row keeps
Wialon's raw value at full precision, so rounding (at most 0.005 km per unit) is separated from real differences.

Run from Backend/:
    python scripts/reconcile_dashboard_mileage.py --dashboard-json mileage_today.json
    python scripts/reconcile_dashboard_mileage.py --in-process --all
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(BASE_DIR, ".env"))

from app.utils.datetime_helpers import UAE_TZ
from app.wialon import get_vehicle_groups, login, wialon_request
from app.wialon_reports import (
    TEMPLATE_ID_GROUP_TRIPS,
    WIALON_RESOURCE_ID,
    _column_index,
    _fetch_all_rows,
    _holding_report_lock,
    extract_cell_text,
    extract_cell_value,
    parse_mileage_km,
    resolve_all_units_group_id,
)

EPS = 1e-9
ROUNDING_PER_UNIT_KM = 0.005


def fmt_ts(ts):
    if ts is None:
        return "None"
    utc = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return f"{int(ts)}  UTC {utc:%Y-%m-%d %H:%M:%S}  UAE {utc.astimezone(UAE_TZ):%Y-%m-%d %H:%M:%S}"


def km(value):
    return "None" if value is None else f"{value:,.6f}"


# ─── Dashboard response ───────────────────────────────────────────────────────

def load_dashboard(args):
    if args.dashboard_json:
        with open(args.dashboard_json, encoding="utf-8") as fh:
            return json.load(fh), f"file {args.dashboard_json}"
    if args.api_url:
        import requests
        token = os.getenv("RECON_BEARER_TOKEN", "").strip()
        if not token:
            sys.exit("Set RECON_BEARER_TOKEN to a valid Supabase access token for --api-url")
        url = f"{args.api_url.rstrip('/')}/api/dashboard/mileage"
        res = requests.get(url, params={"timeframe": "today"}, headers={"Authorization": f"Bearer {token}"}, timeout=180)
        res.raise_for_status()
        return res.json(), f"GET {url}?timeframe=today"
    # Imported only here: the backend app module is not needed for the other modes
    from app.main import get_dashboard_mileage
    return get_dashboard_mileage(timeframe="today"), "in-process app.main.get_dashboard_mileage (fresh, uncached)"


# ─── Wialon report (same parameters as execute_wialon_group_trips_report) ─────

def template_name(sid, resource_id, template_id):
    """Name of the report template on the resource (read-only lookup); None when it cannot be read."""
    try:
        res = wialon_request("core/search_item", {"id": int(resource_id), "flags": 0x2001}, sid=sid)
        rep = (res.get("item") or {}).get("rep") or {}
        entry = rep.get(str(template_id)) or rep.get(template_id) or {}
        return entry.get("n")
    except Exception as exc:  # diagnostic only
        return f"<lookup failed: {exc}>"


def run_wialon_report(sid, group_id, from_ts, to_ts):
    exec_params = {
        "reportResourceId": WIALON_RESOURCE_ID,
        "reportTemplateId": TEMPLATE_ID_GROUP_TRIPS,
        "reportObjectId": int(group_id),
        "reportObjectSecId": 0,
        "interval": {"flags": 0, "from": int(from_ts), "to": int(to_ts)},
    }
    with _holding_report_lock("group_trips", sid):
        executed = False
        try:
            res = wialon_request("report/exec_report", exec_params, sid=sid, timeout=120)
            if not isinstance(res, dict) or "error" in res or "reportResult" not in res:
                sys.exit(f"Wialon report/exec_report failed: {res}")
            executed = True
            tables = res["reportResult"].get("tables") or []
            if not tables:
                return {"params": exec_params, "tables": [], "header": None, "total": None, "rows": []}
            t0 = tables[0]
            rows = _fetch_all_rows(sid, t0.get("rows", 0), "reconciliation trips report") if t0.get("rows", 0) else []
            return {"params": exec_params, "tables": tables, "header": t0.get("header"), "total": t0.get("total"),
                    "rows": rows}
        finally:
            if executed:
                try:
                    wialon_request("report/cleanup_result", {}, sid=sid)
                except Exception as exc:
                    print(f"  (report cleanup failed: {exc})")


# ─── Reconciliation ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dashboard-json", help="saved /api/dashboard/mileage?timeframe=today response")
    source.add_argument("--api-url", help="backend base URL (token in RECON_BEARER_TOKEN)")
    source.add_argument("--in-process", action="store_true", help="compute the dashboard result in this process")
    parser.add_argument("--all", action="store_true", help="also list every vehicle that matches")
    args = parser.parse_args()

    dash, dash_source = load_dashboard(args)

    print("=" * 100)
    print("1. DASHBOARD RESPONSE")
    print("=" * 100)
    print(f"source           : {dash_source}")
    print(f"timeframe / tz   : {dash.get('timeframe')} / {dash.get('timezone')}")
    print(f"effective_from   : {fmt_ts(dash.get('effective_from'))}")
    print(f"effective_to     : {fmt_ts(dash.get('effective_to'))}")
    print(f"retrieved_at     : {fmt_ts(dash.get('retrieved_at'))}")
    print(f"stale            : {dash.get('stale')}")
    print(f"total_km         : {dash.get('total_km')}")
    print(f"incomplete_units : {dash.get('incomplete_units')}")
    print(f"unmatched rows   : {dash.get('unmatched_report_rows')}")
    print(f"ambiguous names  : {dash.get('ambiguous_vehicle_names')}")
    if dash.get("timeframe") != "today":
        print("WARNING: this is not a timeframe=today response")
    from_ts, to_ts = dash.get("effective_from"), dash.get("effective_to")
    if from_ts is None or to_ts is None:
        sys.exit("The dashboard response has no effective_from/effective_to; cannot re-run the same window")

    units = dash.get("all_units") or []
    dash_by_name = defaultdict(list)
    for u in units:
        dash_by_name[str(u.get("vehicle_name", "")).strip()].append(u)
    dash_known = [u["mileage"] for u in units if isinstance(u.get("mileage"), (int, float))]
    dash_sum = math.fsum(dash_known)
    print(f"all_units        : {len(units)} entries, {len(dash_known)} with known mileage")
    print(f"sum(all_units)   : {km(dash_sum)}  (the Fleet Overview Distance for 'All Groups')")

    sid = login()
    groups = get_vehicle_groups(sid)
    group_id = resolve_all_units_group_id(sid, groups)
    group = next((g for g in groups if g.get("id") == group_id), {}) or {}

    print()
    print("=" * 100)
    print("2. WIALON REPORT CONFIGURATION (from the LOCAL Backend/.env; must match the server's)")
    print("=" * 100)
    print(f"resource id      : {WIALON_RESOURCE_ID}")
    print(f"template id      : {TEMPLATE_ID_GROUP_TRIPS}  name: {template_name(sid, WIALON_RESOURCE_ID, TEMPLATE_ID_GROUP_TRIPS)}")
    print(f"object (group)   : {group_id}  name: {group.get('nm')}  members: {len(group.get('u') or [])}")
    print(f"interval         : flags 0, from {fmt_ts(from_ts)}")
    print(f"                   {' ' * 14}to   {fmt_ts(to_ts)}")

    report = run_wialon_report(sid, group_id, from_ts, to_ts)
    print(f"tables           : {[t.get('label') or t.get('name') for t in report['tables']]}")
    print(f"table 0 header   : {report['header']}")
    warnings, unlocated = [], []
    mileage_idx = _column_index(report["header"], ("Mileage",), 5, warnings, unlocated)
    print(f"Mileage column   : index {mileage_idx}{'  (NOT located by name: ' + '; '.join(warnings) + ')' if unlocated else ''}")
    print(f"table 0 rows     : {len(report['rows'])}")

    # Wialon Total row (what the Wialon UI prints at the bottom)
    total_cells = report["total"] if isinstance(report["total"], list) else []
    total_cell = total_cells[mileage_idx] if len(total_cells) > mileage_idx else None
    wialon_total = parse_mileage_km(extract_cell_value(total_cell)) if total_cell is not None else None

    rows = []
    for r in report["rows"]:
        cols = r.get("c", [])
        if len(cols) < 6:
            continue
        name = extract_cell_text(cols[0]).strip()
        cell = cols[mileage_idx] if len(cols) > mileage_idx else None
        rows.append({"name": name, "cell": cell, "raw": parse_mileage_km(extract_cell_value(cell)),
                     "text": extract_cell_text(cell) if cell is not None else None})
    name_counts = Counter(row["name"] for row in rows)
    raw_known = [row["raw"] for row in rows if row["raw"] is not None]
    wialon_rows_sum = math.fsum(raw_known)
    extra_precision = sum(1 for v in raw_known if abs(v - round(v, 2)) > EPS)

    # Classify every Wialon row against the dashboard
    results = []
    for row in rows:
        name, raw = row["name"], row["raw"]
        entries = dash_by_name.get(name, [])
        dash_val = entries[0].get("mileage") if len(entries) == 1 else None
        if raw is None:
            status = "WIALON_UNREADABLE"
        elif name_counts[name] > 1:
            status = "DUPLICATE_WIALON_ROW"
        elif not entries:
            status = "MISSING_FROM_DASHBOARD"
        elif len(entries) > 1:
            status = "AMBIGUOUS_NAME"
        elif not isinstance(dash_val, (int, float)):
            status = f"DASHBOARD_UNKNOWN ({entries[0].get('source')})"
        elif abs(dash_val - round(raw, 2)) < EPS:
            status = "OK"
        else:
            status = "DIFFERENT"
        results.append({**row, "dash": dash_val if isinstance(dash_val, (int, float)) else None, "status": status})

    wialon_names = set(name_counts)
    dashboard_only = [u for u in units if isinstance(u.get("mileage"), (int, float))
                      and str(u.get("vehicle_name", "")).strip() not in wialon_names]

    ok = [x for x in results if x["status"] == "OK"]
    different = [x for x in results if x["status"] == "DIFFERENT"]
    excluded = [x for x in results if x["status"] not in ("OK", "DIFFERENT") and x["raw"] is not None]

    print()
    print("=" * 100)
    print("3. PER-VEHICLE COMPARISON  (delta = Wialon raw - dashboard)")
    print("=" * 100)
    print(f"{'vehicle':<34} {'Wialon text':>16} {'Wialon raw':>16} {'dashboard':>14} {'delta':>12}  status")
    for x in sorted(results, key=lambda x: (x["status"] == "OK", x["name"])):
        if x["status"] == "OK" and not args.all:
            continue
        delta = (x["raw"] - x["dash"]) if x["raw"] is not None and x["dash"] is not None else x["raw"]
        print(f"{x['name'][:34]:<34} {str(x['text'])[:16]:>16} {km(x['raw']):>16} {km(x['dash']):>14} "
              f"{km(delta):>12}  {x['status']}")
    for u in dashboard_only:
        print(f"{str(u.get('vehicle_name'))[:34]:<34} {'-':>16} {'-':>16} {km(u['mileage']):>14} "
              f"{km(-u['mileage']):>12}  DASHBOARD_ONLY ({u.get('source')})")
    if not args.all:
        print(f"... {len(ok)} matching vehicles not listed (use --all)")

    rounding = math.fsum(x["raw"] - x["dash"] for x in ok)
    genuine = math.fsum(x["raw"] - x["dash"] for x in different)
    excluded_km = math.fsum(x["raw"] for x in excluded)
    dashboard_only_km = -math.fsum(u["mileage"] for u in dashboard_only)
    total_vs_rows = (wialon_total - wialon_rows_sum) if wialon_total is not None else None

    print()
    print("=" * 100)
    print("4. TOTALS AND GAP BREAKDOWN")
    print("=" * 100)
    print(f"Wialon Total row               : {km(wialon_total)}  text: {extract_cell_text(total_cell) if total_cell is not None else 'not provided'}")
    print(f"Sum of Wialon rows (raw)       : {km(wialon_rows_sum)}  ({len(raw_known)} readable rows, "
          f"{extra_precision} with more than 2 decimals)")
    print(f"Dashboard total_km             : {dash.get('total_km')}")
    print(f"Dashboard sum(all_units)       : {km(dash_sum)}")
    if wialon_total is not None:
        print(f"GAP  Wialon Total - dashboard  : {km(wialon_total - dash_sum)}")
    print(f"GAP  Wialon rows  - dashboard  : {km(wialon_rows_sum - dash_sum)}")
    print("  made up of:")
    print(f"  a. Wialon Total vs its rows  : {km(total_vs_rows)}  (Wialon's own total display/rounding)")
    print(f"  b. per-unit rounding         : {km(rounding)}  over {len(ok)} units; bound "
          f"±{ROUNDING_PER_UNIT_KM * len(ok):.3f}{'  EXCEEDS BOUND' if abs(rounding) > ROUNDING_PER_UNIT_KM * len(ok) + EPS else ''}")
    print(f"  c. genuinely different units : {km(genuine)}  ({len(different)} units)")
    print(f"  d. units not in dashboard sum: {km(excluded_km)}  ({len(excluded)} rows: missing/ambiguous/unknown/duplicate)")
    print(f"  e. dashboard-only units      : {km(dashboard_only_km)}  ({len(dashboard_only)} units)")
    residual = (wialon_rows_sum - dash_sum) - (rounding + genuine + excluded_km + dashboard_only_km)
    print(f"  residual (b+c+d+e vs gap)    : {km(residual)}  (should be ~0)")
    print()
    print("Notes: a 'DIFFERENT' unit in --dashboard-json/--api-url mode can also be data Wialon received after the")
    print("dashboard read (late or buffered messages); --in-process compares both reads from the same moment.")


if __name__ == "__main__":
    main()
