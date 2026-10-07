"""
Phase 6 — Audit Wialon Vehicle Matching
Audits Wialon units vs Supabase fuel_transactions.vehicle_identifier.
"""
import os
import sys
import re
import json
from collections import defaultdict
from dotenv import load_dotenv

# Ensure Backend is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from app.wialon import login, wialon_request
from app.fuel.db import fetch_fuel_transactions_for_analytics

def normalize_key(s: str) -> str:
    """Strip all non-alphanumeric chars and lowercase."""
    if not s:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(s)).lower()

def extract_digits(s: str) -> str:
    """Extract only digits and strip leading zeros."""
    digits = re.sub(r"[^0-9]", "", str(s))
    return digits.lstrip("0") if digits else ""

def run_audit():
    print("=" * 80)
    print("PHASE 6: AUDIT WIALON VEHICLE MATCHING")
    print("=" * 80)

    # 1. Login to Wialon
    print("\n[Step 1 & 2] Connecting to Wialon and retrieving unit information...")
    sid = login()
    print("Wialon session established.")

    # Search with comprehensive flags (4194303 = all avl_unit flags or 4611686018427387903)
    # Flag bits:
    # 1 (0x1) - Base
    # 2 (0x2) - Custom fields (flds)
    # 4 (0x4) - Admin fields (aflds)
    # 8 (0x8) - Advanced properties
    # 16 (0x10) - Available sensors (sens)
    # 1024 (0x400) - Unit profile (prp)
    # 4096 (0x1000) - Advanced unit properties / counters
    # 8192 (0x2000) - Commands
    # 0x100000 (1048576) - Unit profile fields
    # Let's request flags = 4194303 (0x3FFFFF)
    flags = 4194303
    search_res = wialon_request(
        "core/search_items",
        {
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name"
            },
            "force": 1,
            "flags": flags,
            "from": 0,
            "to": 0
        },
        sid
    )

    if isinstance(search_res, dict) and "error" in search_res:
        print(f"Error searching units with flags {flags}: {search_res}. Retrying with flags 5121...")
        search_res = wialon_request(
            "core/search_items",
            {
                "spec": {
                    "itemsType": "avl_unit",
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name"
                },
                "force": 1,
                "flags": 5121,
                "from": 0,
                "to": 0
            },
            sid
        )

    wialon_units = search_res.get("items", [])
    print(f"Retrieved {len(wialon_units)} Wialon units.")

    # Inspect structure of Wialon units
    parsed_wialon = []
    for u in wialon_units:
        u_id = u.get("id")
        u_nm = u.get("nm", "")
        u_cls = u.get("cls")
        flds = u.get("flds", {})  # custom fields
        aflds = u.get("aflds", {})  # admin fields
        prp = u.get("prp", {})  # profile properties
        
        # Check if custom fields contain anything
        custom_fields_list = []
        if isinstance(flds, dict):
            for k, f in flds.items():
                if isinstance(f, dict):
                    custom_fields_list.append(f"{f.get('n')}: {f.get('v')}")
        elif isinstance(flds, list):
            for f in flds:
                if isinstance(f, dict):
                    custom_fields_list.append(f"{f.get('n')}: {f.get('v')}")

        # Check admin fields
        admin_fields_list = []
        if isinstance(aflds, dict):
            for k, f in aflds.items():
                if isinstance(f, dict):
                    admin_fields_list.append(f"{f.get('n')}: {f.get('v')}")
        elif isinstance(aflds, list):
            for f in aflds:
                if isinstance(f, dict):
                    admin_fields_list.append(f"{f.get('n')}: {f.get('v')}")

        # Check profile properties (registration_plate, brand, etc.)
        reg_plate = prp.get("registration_plate") or prp.get("reg_plate") or prp.get("reg_number") or ""
        vin = prp.get("vin") or ""
        brand = prp.get("brand") or ""
        model = prp.get("model") or ""
        year = prp.get("year") or ""

        parsed_wialon.append({
            "id": u_id,
            "name": str(u_nm).strip(),
            "cls": u_cls,
            "reg_plate": str(reg_plate).strip(),
            "vin": str(vin).strip(),
            "brand": str(brand).strip(),
            "model": str(model).strip(),
            "year": str(year).strip(),
            "custom_fields": custom_fields_list,
            "admin_fields": admin_fields_list,
            "all_prp_keys": list(prp.keys()) if isinstance(prp, dict) else [],
            "raw_prp": prp if isinstance(prp, dict) else {},
        })

    print("\n--- SAMPLE WIALON UNITS INSPECTION ---")
    for pu in parsed_wialon[:5]:
        print(f"Unit ID: {pu['id']} | Name: '{pu['name']}' | Reg Plate: '{pu['reg_plate']}' | Brand: '{pu['brand']}' | Custom Fields: {pu['custom_fields']} | PRP Keys: {pu['all_prp_keys']}")

    # Check across ALL 30 units if any reg_plate or custom field exists
    units_with_reg_plate = [pu for pu in parsed_wialon if pu['reg_plate']]
    units_with_custom_flds = [pu for pu in parsed_wialon if pu['custom_fields']]
    units_with_admin_flds = [pu for pu in parsed_wialon if pu['admin_fields']]
    print(f"\nUnits with non-empty 'reg_plate' profile property: {len(units_with_reg_plate)} / {len(parsed_wialon)}")
    print(f"Units with non-empty custom fields: {len(units_with_custom_flds)} / {len(parsed_wialon)}")
    print(f"Units with non-empty admin fields: {len(units_with_admin_flds)} / {len(parsed_wialon)}")

    # 2. Retrieve Supabase Fuel Identifiers
    print("\n[Step 3] Fetching unique fuel vehicle identifiers from Supabase...")
    # Fetch all fuel transactions
    txs = fetch_fuel_transactions_for_analytics(filters={})
    fuel_identifiers = sorted(list(set(tx["vehicle_identifier"] for tx in txs if tx.get("vehicle_identifier"))))
    print(f"Total fuel transactions: {len(txs)}")
    print(f"Unique fuel vehicle identifiers: {len(fuel_identifiers)}")

    print("\nSample Fuel Identifiers:")
    print(", ".join(fuel_identifiers[:15]))

    # 3. Build Comparison Indexes
    # What does Wialon unit name look like? E.g. "15770", "01234", etc.
    # What does Fuel identifier look like? E.g. "15770", "DXB-15770", "15770 D", "SHJ 1234", etc.
    print("\n[Step 4] Matching Analysis...")
    
    # Let's inspect all Wialon unit names
    wialon_names = [pu["name"] for pu in parsed_wialon]
    print(f"\nAll {len(wialon_names)} Wialon Unit Names:")
    for i, name in enumerate(sorted(wialon_names), 1):
        print(f"  {i}. '{name}'")

    # Matching logic tests:
    # 1. Exact match (case-insensitive)
    # 2. Normalized alphanumeric match (strip spaces, hyphens, etc.)
    # 3. Numeric / digit match (digits only, e.g. "15770" matches "15770" or "DXB 15770")
    # 4. Substring / token match (e.g. word token in fuel identifier matches unit name)

    matched = []
    unmatched_fuel = []
    matched_fuel_set = set()
    matched_wialon_set = set()

    # Pre-index Wialon units
    # Map exact lowercase name -> unit
    wialon_by_exact = {pu["name"].strip().lower(): pu for pu in parsed_wialon}
    # Map alphanumeric name -> unit
    wialon_by_norm = {normalize_key(pu["name"]): pu for pu in parsed_wialon}
    # Map digit-only -> unit
    wialon_by_digits = {}
    for pu in parsed_wialon:
        d = extract_digits(pu["name"])
        if d:
            wialon_by_digits[d] = pu
        # Also check reg_plate if present
        if pu["reg_plate"]:
            wialon_by_exact[pu["reg_plate"].strip().lower()] = pu
            wialon_by_norm[normalize_key(pu["reg_plate"])] = pu
            d_rp = extract_digits(pu["reg_plate"])
            if d_rp:
                wialon_by_digits[d_rp] = pu

    for f_id in fuel_identifiers:
        f_norm = normalize_key(f_id)
        f_digits = extract_digits(f_id)
        f_lower = f_id.strip().lower()

        match_found = None
        match_type = None

        # 1. Exact match
        if f_lower in wialon_by_exact:
            match_found = wialon_by_exact[f_lower]
            match_type = "Exact Match (unit name)"
        # 2. Normalized match
        elif f_norm in wialon_by_norm:
            match_found = wialon_by_norm[f_norm]
            match_type = "Normalized Alphanumeric Match"
        # 3. Digits match (if length >= 3 to avoid single digit false positives)
        elif f_digits and len(f_digits) >= 3 and f_digits in wialon_by_digits:
            match_found = wialon_by_digits[f_digits]
            match_type = f"Numeric Match (digits: {f_digits})"
        else:
            # 4. Check if Wialon unit name appears as a distinct word / token in fuel identifier
            # e.g., if f_id is "D 15770" or "DXB 15770" and Wialon unit is "15770"
            for pu in parsed_wialon:
                u_name = pu["name"]
                # Split fuel identifier by spaces/hyphens
                tokens = re.split(r"[\s\-_/]+", f_id)
                if u_name in tokens or normalize_key(u_name) in [normalize_key(t) for t in tokens]:
                    match_found = pu
                    match_type = f"Token Match ('{u_name}' in '{f_id}')"
                    break

        if match_found:
            matched.append({
                "fuel_identifier": f_id,
                "wialon_unit_id": match_found["id"],
                "wialon_unit_name": match_found["name"],
                "match_type": match_type
            })
            matched_fuel_set.add(f_id)
            matched_wialon_set.add(match_found["id"])
        else:
            unmatched_fuel.append(f_id)

    unmatched_wialon = [pu for pu in parsed_wialon if pu["id"] not in matched_wialon_set]

    print("\n" + "=" * 80)
    print("AUDIT RESULTS SUMMARY")
    print("=" * 80)
    print(f"Total Unique Fuel Identifiers: {len(fuel_identifiers)}")
    print(f"Total Wialon Units:            {len(parsed_wialon)}")
    print(f"Matched Fuel Identifiers:      {len(matched)}")
    print(f"Matched Wialon Units:          {len(matched_wialon_set)} / {len(parsed_wialon)}")
    print(f"Unmatched Fuel Identifiers:    {len(unmatched_fuel)}")
    print(f"Unmatched Wialon Units:        {len(unmatched_wialon)}")

    print("\n" + "-" * 80)
    print(f"MATCHED VEHICLES ({len(matched)} matches):")
    print("-" * 80)
    print(f"{'Fuel identifier':<25} | {'Wialon unit ID':<15} | {'Wialon unit name':<20} | {'Matching field / Rule'}")
    print("-" * 80)
    for m in matched:
        print(f"{m['fuel_identifier']:<25} | {str(m['wialon_unit_id']):<15} | {m['wialon_unit_name']:<20} | {m['match_type']}")

    print("\n" + "-" * 80)
    print(f"UNMATCHED WIALON UNITS ({len(unmatched_wialon)} units):")
    print("-" * 80)
    for pu in unmatched_wialon:
        print(f"Unit ID: {pu['id']} | Name: '{pu['name']}' | Reg Plate: '{pu['reg_plate']}' | Brand: '{pu['brand']}'")

    print("\n" + "-" * 80)
    print(f"UNMATCHED FUEL IDENTIFIERS ({len(unmatched_fuel)} vehicles):")
    print("-" * 80)
    # Print in columns or lines
    for i in range(0, len(unmatched_fuel), 5):
        chunk = unmatched_fuel[i:i+5]
        print("  " + ", ".join(f"'{x}'" for x in chunk))

    # Save detailed audit output as JSON
    out_path = os.path.join(BASE_DIR, "scripts", "output", "wialon_fuel_matching_audit.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_fuel_identifiers": len(fuel_identifiers),
            "total_wialon_units": len(parsed_wialon),
            "matched_count": len(matched),
            "matched_wialon_count": len(matched_wialon_set),
            "unmatched_fuel_count": len(unmatched_fuel),
            "unmatched_wialon_count": len(unmatched_wialon),
            "matched": matched,
            "unmatched_wialon": [
                {"id": pu["id"], "name": pu["name"], "reg_plate": pu["reg_plate"], "brand": pu["brand"]}
                for pu in unmatched_wialon
            ],
            "unmatched_fuel": unmatched_fuel,
            "wialon_units_all": [
                {"id": pu["id"], "name": pu["name"], "cls": pu["cls"], "reg_plate": pu["reg_plate"], "brand": pu["brand"], "custom_fields": pu["custom_fields"]}
                for pu in parsed_wialon
            ]
        }, f, indent=2)
    print(f"\nDetailed audit saved to: {out_path}")

if __name__ == "__main__":
    run_audit()
