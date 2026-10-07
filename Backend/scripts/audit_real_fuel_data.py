"""Full detailed audit of real Excel file and Wialon vehicle resolution."""

import os
import sys
import json
import re
from datetime import datetime
from collections import Counter
import openpyxl

# Add backend directory
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, backend_dir)

from dotenv import load_dotenv
load_dotenv(os.path.join(backend_dir, ".env"))

REAL_FILE_PATH = r"C:\Users\HP\Downloads\Transactions_09-02-26_09-09 (1).xlsx"

def analyze_dataset():
    print("==================================================")
    print("DETAILED DATASET AUDIT")
    print("==================================================")
    wb = openpyxl.load_workbook(REAL_FILE_PATH, data_only=True)
    sheet = wb["Transactions"]
    
    rows = list(sheet.iter_rows(values_only=True))
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    print(f"Total rows in sheet (including header): {len(rows)}")
    print(f"Headers ({len(headers)}): {headers}")
    
    col_map = {h: idx for idx, h in enumerate(headers)}
    
    total_excel_data_rows = len(rows) - 1
    
    valid_txns = []
    empty_plate_rows = []
    zero_or_bad_litres_rows = []
    bad_date_rows = []
    unique_plates = set()
    all_dates = []
    total_litres = 0.0
    total_cost_with_vat = 0.0
    total_cost_without_vat = 0.0
    total_vat = 0.0
    
    qty_idx = col_map.get("Quantity")
    plate_idx = col_map.get("Plate Number")
    date_idx = col_map.get("Transaction Date")
    total_idx = col_map.get("Total Amount")
    vat_idx = col_map.get("Vat Amount")
    net_idx = col_map.get("Amount Without VAT")
    txn_num_idx = col_map.get("Transaction Number")
    
    for r_idx, r in enumerate(rows[1:], start=2):
        raw_txn = r[txn_num_idx] if txn_num_idx is not None else None
        raw_date = r[date_idx] if date_idx is not None else None
        raw_plate = r[plate_idx] if plate_idx is not None else None
        raw_qty = r[qty_idx] if qty_idx is not None else None
        raw_total = r[total_idx] if total_idx is not None else None
        raw_vat = r[vat_idx] if vat_idx is not None else None
        raw_net = r[net_idx] if net_idx is not None else None
        
        # Check plate
        plate_str = str(raw_plate).strip() if raw_plate is not None else ""
        if not plate_str or plate_str == "#REF!":
            empty_plate_rows.append((r_idx, raw_txn, raw_date, plate_str, raw_qty, raw_total))
            continue
            
        # Check quantity
        try:
            qty_val = float(str(raw_qty).replace(",", "").strip())
        except (ValueError, TypeError):
            zero_or_bad_litres_rows.append((r_idx, raw_txn, raw_qty))
            continue
            
        # Check total
        try:
            total_val = float(str(raw_total).replace(",", "").strip())
        except (ValueError, TypeError):
            total_val = 0.0
            
        try:
            vat_val = float(str(raw_vat).replace(",", "").strip())
        except (ValueError, TypeError):
            vat_val = 0.0
            
        try:
            net_val = float(str(raw_net).replace(",", "").strip())
        except (ValueError, TypeError):
            net_val = 0.0
            
        unique_plates.add(plate_str)
        total_litres += qty_val
        total_cost_with_vat += total_val
        total_vat += vat_val
        total_cost_without_vat += net_val
        all_dates.append(str(raw_date)[:10])
        valid_txns.append((r_idx, plate_str, raw_date, qty_val, total_val))

    print(f"\nTotal Excel data rows: {total_excel_data_rows}")
    print(f"Empty/Missing Plate rows: {len(empty_plate_rows)}")
    for item in empty_plate_rows:
        print(f"  Row {item[0]}: Txn={item[1]}, Date={item[2]}, Plate='{item[3]}', Qty={item[4]}, Total={item[5]}")
        
    print(f"Bad/Zero Litres rows: {len(zero_or_bad_litres_rows)}")
    print(f"Valid transactions identified: {len(valid_txns)}")
    print(f"Unique vehicle identifiers (plates): {len(unique_plates)}")
    print(f"Total Litres: {total_litres:,.2f}")
    print(f"Total Cost (with VAT): {total_cost_with_vat:,.2f}")
    print(f"Total Cost (without VAT): {total_cost_without_vat:,.2f}")
    print(f"Total VAT: {total_vat:,.2f}")
    
    if all_dates:
        print(f"Date range: min={min(all_dates)}, max={max(all_dates)}")
        
    # Check Wialon vehicle matching
    print("\n==================================================")
    print("WIALON VEHICLE RESOLUTION AUDIT")
    print("==================================================")
    from app.wialon import login
    from app.vehicles import get_all_vehicles
    
    try:
        sid = login()
        wialon_units = get_all_vehicles(sid)
        print(f"Wialon units retrieved: {len(wialon_units)}")
        
        # Build lookup strategies
        wialon_names = {}
        for u in wialon_units:
            u_id = u.get("id")
            u_nm = u.get("nm", "")
            wialon_names[u_nm.strip().lower()] = (u_id, u_nm)
            # Normalized alphanumeric
            alnum = re.sub(r"[^a-zA-Z0-9]", "", u_nm.lower())
            if alnum:
                wialon_names[alnum] = (u_id, u_nm)
                
        # Test matching against Excel plates
        matched = []
        unmatched = []
        
        for plate in sorted(unique_plates):
            norm_p = plate.strip().lower()
            alnum_p = re.sub(r"[^a-zA-Z0-9]", "", norm_p)
            
            # Let's check direct match or alnum match
            match = None
            if norm_p in wialon_names:
                match = wialon_names[norm_p]
            elif alnum_p in wialon_names:
                match = wialon_names[alnum_p]
            else:
                # Check plate variations e.g. "7-AUH-55943" vs "55943" or "AUH 55943" or "T-DXB-54290" vs "DXB 54290" or "54290"
                # Extract digits
                digits = re.findall(r"\d+", plate)
                digit_match = None
                for d in digits:
                    if len(d) >= 4 and d in wialon_names:
                        digit_match = wialon_names[d]
                        break
                if digit_match:
                    match = digit_match
                    
            if match:
                matched.append((plate, match[0], match[1]))
            else:
                unmatched.append(plate)
                
        print(f"\nMatched Excel plates to Wialon units: {len(matched)} / {len(unique_plates)} ({len(matched)/len(unique_plates)*100:.1f}%)")
        print(f"Unmatched Excel plates: {len(unmatched)}")
        print("\n--- Top 10 Matched Examples ---")
        for m in matched[:10]:
            print(f"  Excel '{m[0]}' -> Wialon ID {m[1]} ('{m[2]}')")
            
        print("\n--- Top 10 Unmatched Examples ---")
        for u in unmatched[:10]:
            print(f"  Excel '{u}'")
            
    except Exception as exc:
        print(f"Wialon connection error: {exc}")


if __name__ == "__main__":
    analyze_dataset()
