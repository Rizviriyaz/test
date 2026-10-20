"""
Backfill fuel_transactions.fingerprint / transaction_kind for rows imported before FUEL_SCHEMA_V2.

DRY RUN BY DEFAULT: it only reads and prints what it would write. Pass --apply to write, and only after
sections 1 (columns) of Backend/sql/proposed_fuel_dedup.sql have been reviewed and applied. This script is
run by the database owner, never by the application. It never deletes rows.

Fingerprints are computed with app.fuel.db.build_fingerprint, the same function the importer uses, so
SQL §3/§5 and the app agree. Identical rows: the keeper gets the plain fingerprint, the others
'<fingerprint>|dup:<id>' (see plan_backfill). cost_derived/unit_price_derived stay NULL (unknown).
A write is counted only when PostgREST returns exactly one updated row.

Requires SUPABASE_SERVICE_ROLE_KEY (dry run included): with the anon key row-level security can hide rows,
and a dry run reporting "0 rows needing a fingerprint" would falsely satisfy the SQL §5 precondition.
Exit status: 0 on success, 1 if any write failed (re-run to retry; written rows are never rewritten).

Usage (from Backend/):
    python scripts/backfill_fuel_fingerprints.py            # dry run: counts + sample + duplicate groups
    python scripts/backfill_fuel_fingerprints.py --apply    # writes fingerprint + transaction_kind
"""
import argparse
import os
import sys
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


DUP_SUFFIX = "|dup:"


def base_fingerprint(stored: str) -> str:
    """Stored fingerprint without the '|dup:<id>' suffix given to non-keeper duplicates."""
    return str(stored).split(DUP_SUFFIX, 1)[0]


def plan_backfill(rows):
    """
    Pure: [(id, {fingerprint, transaction_kind})] for rows missing a fingerprint, plus duplicate groups
    ({base fingerprint: [ids]}).

    Within each group of identical rows exactly one row (the keeper) holds the plain fingerprint, so the
    unique index in SQL §5 can be built without deleting anything: the row that already stores the plain
    fingerprint (written by a FUEL_SCHEMA_V2 import), else the lowest id. Every other row gets
    '<fingerprint>|dup:<id>'. New imports still compute the plain fingerprint and so collide with the keeper.
    The dashboard does not read this column (it computes fingerprints itself), so KPIs are unaffected.
    """
    from app.fuel.db import build_fingerprint

    groups = defaultdict(list)
    for r in rows:
        fp = build_fingerprint(r.get("vehicle_identifier"), r.get("transaction_date"), r.get("litres"),
                               r.get("total_cost"), r.get("invoice_number"), r.get("odometer_km"))
        groups[fp].append(r)

    updates = []
    for fp, members in groups.items():
        holders = [r for r in members if r.get("fingerprint") == fp]
        keeper = min(holders or members, key=lambda r: int(r["id"]))
        for r in sorted(members, key=lambda r: int(r["id"])):
            if r.get("fingerprint"):
                continue  # already written (by an import or an earlier backfill run); never rewritten
            kind = "refund_adjustment" if float(r.get("litres") or 0) < 0 else "purchase"
            value = fp if r is keeper else f"{fp}{DUP_SUFFIX}{r['id']}"
            updates.append((r["id"], {"fingerprint": value, "transaction_kind": kind}))
    duplicates = {fp: sorted(int(r["id"]) for r in members) for fp, members in groups.items() if len(members) > 1}
    return updates, duplicates


def patch_succeeded(response) -> bool:
    """A PATCH counts only when it succeeded AND updated exactly one row (RLS or a missing id give 200 with [])."""
    if not getattr(response, "ok", False):
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, list) and len(body) == 1


def _fetch_all(url, headers):
    from app.fuel.db import _fetch_all_pages
    params = {"select": "id,import_id,transaction_date,vehicle_identifier,litres,total_cost,invoice_number,odometer_km,fingerprint",
              "order": "id.asc"}
    return _fetch_all_pages(url, params, headers, "fuel transactions (backfill)")


def require_service_role_key() -> None:
    """Refuse to touch Supabase without the service-role key (before any request is made)."""
    if not os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip():
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY is not set: refusing to run. Without it row-level security "
                         "can hide rows and the backfill (dry run included) would under-report.")


def run(apply: bool, fetch=None, patch=None, out=print) -> dict:
    """fetch() -> rows, patch(id, values) -> bool are injectable for offline tests."""
    if fetch is None or patch is None:
        require_service_role_key()
        import requests
        from app.fuel.db import _read_headers, get_headers, get_supabase_rest_url
        url = get_supabase_rest_url("fuel_transactions")
        fetch = fetch or (lambda: _fetch_all(url, _read_headers(None)))

        def _patch(row_id, values):
            # Only rows still without a fingerprint are touched, so a re-run never overwrites earlier writes.
            r = requests.patch(url, params={"id": f"eq.{row_id}", "fingerprint": "is.null"}, json=values,
                               headers=get_headers(None), timeout=30)
            return patch_succeeded(r)
        patch = patch or _patch

    rows = fetch()
    updates, duplicates = plan_backfill(rows)
    out(f"Rows read: {len(rows)}; rows needing a fingerprint: {len(updates)}; duplicate fingerprint groups: {len(duplicates)}")
    # Fingerprints embed vehicle plates and invoice numbers: print ids and roles only, never the values.
    for row_id, values in updates[:10]:
        role = "duplicate (suffixed)" if DUP_SUFFIX in values["fingerprint"] else "keeper (plain)"
        out(f"  would set id={row_id}: transaction_kind={values['transaction_kind']}, fingerprint={role}")
    for fp, ids in list(duplicates.items())[:10]:
        out(f"  duplicate group ids={ids}")
    written = failed = 0
    if apply:
        for row_id, values in updates:
            if patch(row_id, values):
                written += 1
            else:
                failed += 1
        out(f"Applied: {written} updated, {failed} failed.")
    else:
        out("Dry run only; nothing written. Re-run with --apply after reviewing.")
    return {"rows": len(rows), "planned": len(updates), "written": written, "failed": failed,
            "duplicate_groups": len(duplicates)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the planned values (default: dry run)")
    args = ap.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    result = run(apply=args.apply)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
