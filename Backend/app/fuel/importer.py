"""Dynamic Excel fuel import processor, preview generator, and Supabase execution engine."""

import io
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl
from openpyxl.utils.datetime import from_excel

from app.fuel.db import (
    build_fingerprint,
    create_fuel_import_record,
    fetch_existing_transaction_fingerprints,
    insert_fuel_transactions_batch,
    update_fuel_import_record,
)
from app.fuel.mapper import CANONICAL_FIELDS, REQUIRED_FIELDS, map_headers, normalize_header
from app.fuel.schemas import FuelImportPreviewResponse, FuelImportResultResponse, HeaderMappingItem

logger = logging.getLogger(__name__)


class FuelImportError(Exception):
    """Custom domain exception for fuel import validation errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# Summary keywords that identify subtotal/total/summary rows
SUMMARY_ROW_KEYWORDS: Set[str] = {
    "total", "grand total", "subtotal", "sub total", "summary",
    "average", "avg", "count", "page", "report total", "closing balance",
    "opening balance", "net total", "gross total"
}


def parse_numeric_value(val: Any) -> Tuple[Optional[float], Optional[str]]:
    """
    Safely parse numeric values from cells containing numbers, strings, or currency indicators.
    Returns: (parsed_float_or_None, warning_if_any)
    """
    if val is None:
        return None, None
    if isinstance(val, (int, float)):
        return float(val), None

    str_val = str(val).strip()
    if not str_val or str_val.lower() in {"-", "n/a", "na", "null", "none", "/"}:
        return None, None

    # Remove currency strings, units, and commas
    cleaned = re.sub(r"(?i)\b(aed|sar|usd|eur|gbp|dh|dhs|ltr|litres|liters|km)\b", "", str_val)
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "")
    cleaned = cleaned.replace(",", "").strip()

    try:
        return float(cleaned), None
    except ValueError:
        return None, f"Could not parse numeric value '{str_val}'"


def parse_date_value(val: Any) -> Tuple[Optional[str], Optional[str]]:
    """
    Safely parse date/datetime values from Excel cells into ISO formatted strings.
    Returns: (iso_date_string_or_None, warning_if_any)
    """
    if val is None:
        return None, None

    # Native openpyxl datetime or date
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%dT%H:%M:%S") if (val.hour or val.minute or val.second) else val.strftime("%Y-%m-%d"), None
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d"), None

    # Excel numeric date (serial number)
    if isinstance(val, (int, float)) and val > 20000:
        try:
            converted = from_excel(val)
            if isinstance(converted, datetime):
                return converted.strftime("%Y-%m-%dT%H:%M:%S") if (converted.hour or converted.minute or converted.second) else converted.strftime("%Y-%m-%d"), None
            if isinstance(converted, date):
                return converted.strftime("%Y-%m-%d"), None
        except Exception:
            pass

    str_val = str(val).strip()
    if not str_val or str_val.lower() in {"-", "n/a", "na", "null", "none"}:
        return None, None

    # ISO formats with optional time and fractional seconds (e.g. '2026-08-31 23:39:39.7478736')
    iso_match = re.match(
        r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?$",
        str_val
    )
    if iso_match:
        y = int(iso_match.group(1))
        m = int(iso_match.group(2))
        d = int(iso_match.group(3))
        hh = int(iso_match.group(4) or 0)
        mm = int(iso_match.group(5) or 0)
        ss = int(iso_match.group(6) or 0)
        try:
            dt = datetime(y, m, d, hh, mm, ss)
            if hh or mm or ss:
                return dt.strftime("%Y-%m-%dT%H:%M:%S"), None
            return dt.strftime("%Y-%m-%d"), None
        except ValueError:
            return None, f"Invalid ISO date values in '{str_val}'"

    # Common UAE / European format: DD/MM/YYYY or DD-MM-YYYY or DD.MM.YYYY
    dmy_match = re.match(
        r"^(\d{1,2})[\/\-\.](\d{1,2})[\/\-\.](\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?$",
        str_val
    )
    if dmy_match:
        part1 = int(dmy_match.group(1))
        part2 = int(dmy_match.group(2))
        year = int(dmy_match.group(3))
        hh = int(dmy_match.group(4) or 0)
        mm = int(dmy_match.group(5) or 0)
        ss = int(dmy_match.group(6) or 0)

        # Disambiguation logic: if first part > 12, it must be DD/MM/YYYY
        if part1 > 12 and part2 <= 12:
            try:
                dt = datetime(year, part2, part1, hh, mm, ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d"), None
            except ValueError:
                return None, f"Invalid date values in '{str_val}'"
        # If second part > 12, it must be MM/DD/YYYY
        elif part2 > 12 and part1 <= 12:
            try:
                dt = datetime(year, part1, part2, hh, mm, ss)
                return dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d"), None
            except ValueError:
                return None, f"Invalid date values in '{str_val}'"
        else:
            # Both <= 12: default to DD/MM/YYYY (standard in UAE/Middle East) with a warning note
            try:
                dt = datetime(year, part2, part1, hh, mm, ss)
                res_str = dt.strftime("%Y-%m-%dT%H:%M:%S") if (hh or mm or ss) else dt.strftime("%Y-%m-%d")
                warning = f"Ambiguous date '{str_val}' interpreted as DD/MM/YYYY ({res_str})"
                return res_str, warning
            except ValueError:
                return None, f"Could not parse date '{str_val}'"

    return None, f"Unrecognized date format '{str_val}'"


def is_summary_or_blank_row(row_values: List[Any], header_count: int) -> Tuple[bool, str]:
    """
    Detect whether a row is blank, a header repetition, or a summary/total row.
    Returns: (is_ignored, reason)
    """
    non_empty = [v for v in row_values if v is not None and str(v).strip() != ""]
    if not non_empty:
        return True, "blank row"

    # Check if first 3 populated values match any summary row keyword
    first_few_strs = [str(v).strip().lower() for v in non_empty[:3]]
    for s in first_few_strs:
        norm_s = normalize_header(s)
        if norm_s in SUMMARY_ROW_KEYWORDS or any(kw in norm_s for kw in ["total", "grand total", "sub total", "summary", "closing bal"]):
            return True, f"summary row: '{s}'"

    # If row only contains 1 populated value across a table with 4+ columns
    if header_count >= 4 and len(non_empty) == 1:
        return True, "lone single-value row (likely title/note/separator)"

    return False, ""


def detect_header_row(sheet: openpyxl.worksheet.worksheet.Worksheet, max_scan_rows: int = 25) -> Tuple[int, List[str]]:
    """
    Scan the first max_scan_rows of a worksheet to identify the most probable header row.
    Returns: (header_row_1_indexed, detected_headers)
    """
    best_row_idx = 1
    best_score = -1
    best_headers: List[str] = []

    all_known_aliases: Set[str] = set()
    for field_info in CANONICAL_FIELDS.values():
        for alias in field_info["aliases"]:
            all_known_aliases.add(normalize_header(alias))

    for row_idx, row in enumerate(sheet.iter_rows(min_row=1, max_row=max_scan_rows, values_only=True), start=1):
        if not row:
            continue

        row_headers = [str(cell).strip() if cell is not None else "" for cell in row]
        while row_headers and row_headers[-1] == "":
            row_headers.pop()

        if not row_headers or len([h for h in row_headers if h]) < 2:
            continue

        score = 0
        populated_count = 0
        for h in row_headers:
            if not h:
                continue
            populated_count += 1
            norm = normalize_header(h)
            if norm in all_known_aliases:
                score += 10
            elif len(norm) > 2 and not norm.replace(".", "").isdigit():
                score += 1

        score += populated_count

        if score > best_score:
            best_score = score
            best_row_idx = row_idx
            best_headers = row_headers

    if best_score <= 0 or not best_headers:
        raise FuelImportError("Could not detect a valid header row with column titles in the Excel sheet.", status_code=422)

    return best_row_idx, best_headers


def validate_confirmed_mapping(raw_headers: List[str], confirmed_mapping: Dict[str, str]) -> Dict[str, str]:
    """
    Validate that:
    1. Required fields are present.
    2. Mapped Excel headers actually exist in raw_headers.
    3. No Excel header is assigned to multiple incompatible canonical fields.
    """
    if not isinstance(confirmed_mapping, dict) or not confirmed_mapping:
        raise FuelImportError("A valid mapping object must be provided.", status_code=400)

    # Clean mapping
    clean_mapping: Dict[str, str] = {}
    for canonical, excel_hdr in confirmed_mapping.items():
        if excel_hdr and str(excel_hdr).strip():
            clean_mapping[canonical] = str(excel_hdr).strip()

    # 1. Required fields check
    for req in REQUIRED_FIELDS:
        if req not in clean_mapping or not clean_mapping[req]:
            raise FuelImportError(
                f"Missing required mapping for '{req}'. Required fields: {', '.join(REQUIRED_FIELDS)}",
                status_code=422,
            )

    # 2. Check header existence in Excel
    raw_header_set = {h.strip().lower(): h for h in raw_headers if h and str(h).strip()}
    for canonical, excel_hdr in clean_mapping.items():
        if excel_hdr.strip().lower() not in raw_header_set:
            raise FuelImportError(
                f"Mapped column '{excel_hdr}' for field '{canonical}' does not exist in the uploaded Excel file.",
                status_code=422,
            )

    # 3. Duplicate header assignment check
    seen_excel_headers: Dict[str, str] = {}
    for canonical, excel_hdr in clean_mapping.items():
        hdr_key = excel_hdr.strip().lower()
        if hdr_key in seen_excel_headers:
            prev_canonical = seen_excel_headers[hdr_key]
            raise FuelImportError(
                f"Excel header '{excel_hdr}' cannot be mapped to multiple canonical fields ('{prev_canonical}' and '{canonical}').",
                status_code=422,
            )
        seen_excel_headers[hdr_key] = canonical

    return clean_mapping


def resolve_fleet_vehicles() -> Dict[str, int]:
    """
    Attempt to fetch fleet vehicle units to match vehicle_identifier with Wialon unit IDs.
    First indexes verified Wialon profile registration plates.
    Keeps unit name matching as an explicit backward-compatibility fallback.
    Returns normalized plate/name -> vehicle_id mapping.
    """
    try:
        from app.wialon import login
        from app.vehicles import get_all_vehicles, extract_registration_plate

        sid = login()
        vehicles = get_all_vehicles(sid)
        lookup: Dict[str, int] = {}
        fallback_lookup: Dict[str, int] = {}

        for v in vehicles:
            v_id = v.get("id")
            if not v_id:
                continue
            v_id_int = int(v_id)
            lookup[str(v_id)] = v_id_int

            # 1. Primary: Verified Profile Registration Plate
            v_plate = extract_registration_plate(v)
            if v_plate:
                plate_clean = str(v_plate).strip().lower()
                lookup[plate_clean] = v_id_int
                plate_alpha = re.sub(r"[^a-zA-Z0-9]", "", plate_clean)
                if plate_alpha:
                    lookup[plate_alpha] = v_id_int

            # 2. Explicit Fallback: Unit Name (General -> Name) for backward compatibility
            v_name = v.get("nm")
            if v_name:
                name_clean = str(v_name).strip().lower()
                fallback_lookup[name_clean] = v_id_int
                name_alpha = re.sub(r"[^a-zA-Z0-9]", "", name_clean)
                if name_alpha:
                    fallback_lookup[name_alpha] = v_id_int

        # Add fallback mappings only if not already claimed by a verified plate
        for k, vid in fallback_lookup.items():
            if k not in lookup:
                lookup[k] = vid

        return lookup
    except Exception as exc:
        logger.warning("Could not pre-fetch Wialon vehicles for ID resolution: %s", exc)
        return {}


def generate_fuel_preview(file_bytes: bytes, file_name: str) -> FuelImportPreviewResponse:
    """Parse an uploaded Excel workbook, detect structure, map headers, and generate a safe preview."""
    lower_name = file_name.lower()
    if not (lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm") or lower_name.endswith(".xltx")):
        raise FuelImportError("Unsupported file type. Please upload a modern Excel file (.xlsx).", status_code=400)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=False)
    except Exception as exc:
        raise FuelImportError(f"Unable to read Excel file. It may be corrupt or encrypted. (Error: {str(exc)})", status_code=422)

    if not wb.sheetnames:
        raise FuelImportError("Excel workbook contains no worksheets.", status_code=422)

    sheet = wb.active or wb[wb.sheetnames[0]]
    worksheet_name = sheet.title

    header_row_idx, raw_headers = detect_header_row(sheet)
    suggested_mapping, mapping_details, unmapped_columns, missing_required_fields = map_headers(raw_headers)

    col_idx_to_canonical: Dict[int, str] = {}
    for col_idx, header in enumerate(raw_headers):
        for canonical, mapped_hdr in suggested_mapping.items():
            if mapped_hdr == header:
                col_idx_to_canonical[col_idx] = canonical
                break

    rows_found = 0
    rows_candidate = 0
    rows_ignored = 0
    validation_warnings: List[str] = []
    sample_rows: List[Dict[str, Any]] = []

    all_rows = list(sheet.iter_rows(min_row=header_row_idx + 1, values_only=True))
    while all_rows:
        last_r = all_rows[-1]
        if not last_r or all(v is None or str(v).strip() == "" for v in last_r):
            all_rows.pop()
        else:
            break

    total_cols = len(raw_headers)
    for offset, row in enumerate(all_rows, start=1):
        row_idx = header_row_idx + offset
        rows_found += 1
        row_slice = list(row[:total_cols]) if row else []
        while len(row_slice) < total_cols:
            row_slice.append(None)

        is_ignored, reason = is_summary_or_blank_row(row_slice, total_cols)
        if is_ignored:
            rows_ignored += 1
            continue

        rows_candidate += 1

        if len(sample_rows) < 5:
            sample_record: Dict[str, Any] = {
                "_source_row": row_idx,
            }
            for canonical in CANONICAL_FIELDS:
                sample_record[canonical] = None

            for col_idx, cell_value in enumerate(row_slice):
                canonical = col_idx_to_canonical.get(col_idx)
                if not canonical:
                    continue

                if canonical == "transaction_date":
                    parsed_dt, warn = parse_date_value(cell_value)
                    sample_record[canonical] = parsed_dt
                    if warn and len(validation_warnings) < 10:
                        validation_warnings.append(f"Row {row_idx}: {warn}")
                elif canonical in {"litres", "unit_price", "total_cost", "odometer_km"}:
                    parsed_num, warn = parse_numeric_value(cell_value)
                    sample_record[canonical] = parsed_num
                    if warn and len(validation_warnings) < 10:
                        validation_warnings.append(f"Row {row_idx} ({canonical}): {warn}")
                else:
                    sample_record[canonical] = str(cell_value).strip() if cell_value is not None else None

            sample_rows.append(sample_record)

    if missing_required_fields:
        validation_warnings.insert(
            0,
            f"Missing required columns: {', '.join(missing_required_fields)}. Please map these before final import."
        )

    if rows_candidate == 0:
        validation_warnings.append("No transaction data rows were found below the header row.")

    return FuelImportPreviewResponse(
        file_name=file_name,
        worksheet_name=worksheet_name,
        detected_header_row=header_row_idx,
        rows_found=rows_found,
        rows_candidate=rows_candidate,
        rows_ignored=rows_ignored,
        detected_columns=raw_headers,
        suggested_mapping=suggested_mapping,
        mapping_details=mapping_details,
        unmapped_columns=unmapped_columns,
        missing_required_fields=missing_required_fields,
        validation_warnings=validation_warnings,
        sample_rows=sample_rows,
    )


def execute_fuel_import(
    file_bytes: bytes,
    file_name: str,
    confirmed_mapping: Dict[str, str],
    imported_by: str,
    bearer_token: Optional[str] = None,
) -> FuelImportResultResponse:
    """
    Execute full fuel import:
    1. Parse workbook & detect headers.
    2. Validate confirmed mapping against actual headers.
    3. Create initial 'processing' record in fuel_imports.
    4. Validate candidate rows and parse values.
    5. Deduplicate against in-file records and existing database transactions.
    6. Batch insert valid records into fuel_transactions.
    7. Update fuel_imports with final statistics and status.
    """
    # 1. Load Excel
    lower_name = file_name.lower()
    if not (lower_name.endswith(".xlsx") or lower_name.endswith(".xlsm") or lower_name.endswith(".xltx")):
        raise FuelImportError("Unsupported file type. Please upload a modern Excel file (.xlsx).", status_code=400)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=False)
    except Exception as exc:
        raise FuelImportError(f"Unable to read Excel file: {str(exc)}", status_code=422)

    if not wb.sheetnames:
        raise FuelImportError("Excel workbook contains no worksheets.", status_code=422)

    sheet = wb.active or wb[wb.sheetnames[0]]
    header_row_idx, raw_headers = detect_header_row(sheet)

    # 2. Validate confirmed mapping
    valid_mapping = validate_confirmed_mapping(raw_headers, confirmed_mapping)

    # Map column index -> canonical field
    header_to_col_idx: Dict[str, int] = {
        h.strip().lower(): idx for idx, h in enumerate(raw_headers) if h and str(h).strip()
    }
    canonical_to_col_idx: Dict[str, int] = {}
    for canonical, mapped_hdr in valid_mapping.items():
        idx = header_to_col_idx.get(mapped_hdr.strip().lower())
        if idx is not None:
            canonical_to_col_idx[canonical] = idx

    # 3. Read table rows
    all_rows = list(sheet.iter_rows(min_row=header_row_idx + 1, values_only=True))
    while all_rows:
        last_r = all_rows[-1]
        if not last_r or all(v is None or str(v).strip() == "" for v in last_r):
            all_rows.pop()
        else:
            break

    total_rows_found = len(all_rows)

    # 4. Create initial fuel_imports record
    import_id = create_fuel_import_record(
        file_name=file_name,
        imported_by=imported_by,
        rows_found=total_rows_found,
        bearer_token=bearer_token,
    )

    # 5. Fetch vehicle lookup & existing transaction fingerprints
    vehicle_lookup = resolve_fleet_vehicles()
    existing_fingerprints = fetch_existing_transaction_fingerprints(bearer_token=bearer_token)
    in_memory_fingerprints: Set[str] = set()

    # 6. Process rows
    total_cols = len(raw_headers)
    rows_candidate = 0
    rows_ignored = 0
    rows_failed = 0
    rows_duplicate = 0
    valid_transactions: List[Dict[str, Any]] = []
    failed_row_details: List[Dict[str, Any]] = []
    parsed_dates: List[str] = []

    for offset, row in enumerate(all_rows, start=1):
        row_idx = header_row_idx + offset
        row_slice = list(row[:total_cols]) if row else []
        while len(row_slice) < total_cols:
            row_slice.append(None)

        is_ignored, reason = is_summary_or_blank_row(row_slice, total_cols)
        if is_ignored:
            rows_ignored += 1
            continue

        rows_candidate += 1

        # Extract values
        raw_date = row_slice[canonical_to_col_idx["transaction_date"]] if "transaction_date" in canonical_to_col_idx else None
        raw_vehicle = row_slice[canonical_to_col_idx["vehicle_identifier"]] if "vehicle_identifier" in canonical_to_col_idx else None
        raw_litres = row_slice[canonical_to_col_idx["litres"]] if "litres" in canonical_to_col_idx else None

        # Validate required fields
        parsed_dt, date_err = parse_date_value(raw_date)
        if not parsed_dt:
            rows_failed += 1
            failed_row_details.append({
                "source_row": row_idx,
                "reason": f"Invalid or missing transaction date ('{raw_date}')"
            })
            continue

        if not raw_vehicle or not str(raw_vehicle).strip():
            rows_failed += 1
            failed_row_details.append({
                "source_row": row_idx,
                "reason": "Missing vehicle identifier"
            })
            continue
        clean_vehicle = str(raw_vehicle).strip()

        parsed_litres, litres_err = parse_numeric_value(raw_litres)
        if parsed_litres is None or parsed_litres <= 0:
            rows_failed += 1
            failed_row_details.append({
                "source_row": row_idx,
                "reason": f"Invalid or zero fuel litres ('{raw_litres}')"
            })
            continue

        # Parse optional fields
        def get_opt_numeric(canonical_key: str) -> Optional[float]:
            if canonical_key in canonical_to_col_idx:
                val, _ = parse_numeric_value(row_slice[canonical_to_col_idx[canonical_key]])
                return val
            return None

        def get_opt_str(canonical_key: str) -> Optional[str]:
            if canonical_key in canonical_to_col_idx:
                raw_s = row_slice[canonical_to_col_idx[canonical_key]]
                return str(raw_s).strip() if raw_s is not None and str(raw_s).strip() else None
            return None

        unit_price = get_opt_numeric("unit_price")
        total_cost = get_opt_numeric("total_cost")
        odometer_km = get_opt_numeric("odometer_km")

        # Auto-compute total_cost or unit_price if one is missing but the other is present
        if total_cost is None and unit_price is not None and parsed_litres:
            total_cost = round(unit_price * parsed_litres, 2)
        elif unit_price is None and total_cost is not None and parsed_litres:
            unit_price = round(total_cost / parsed_litres, 4)

        fuel_type = get_opt_str("fuel_type")
        station_name = get_opt_str("station_name")
        invoice_number = get_opt_str("invoice_number")
        card_number = get_opt_str("card_number")
        driver_name = get_opt_str("driver_name")
        reference_number = get_opt_str("reference_number")
        notes = get_opt_str("notes")

        # Deduplication check
        fp = build_fingerprint(
            vehicle_identifier=clean_vehicle,
            transaction_date=parsed_dt,
            litres=parsed_litres,
            total_cost=total_cost,
            invoice_number=invoice_number,
            odometer_km=odometer_km,
        )

        if fp in in_memory_fingerprints or fp in existing_fingerprints:
            rows_duplicate += 1
            continue

        in_memory_fingerprints.add(fp)

        # Vehicle ID: public.vehicles is empty in Supabase and Wialon IDs do not belong in that FK.
        # vehicle_id is kept NULL while vehicle_identifier stores the full registration plate.
        vehicle_id: Optional[int] = None

        parsed_dates.append(parsed_dt[:10])

        valid_transactions.append({
            "import_id": import_id,
            "vehicle_id": vehicle_id,
            "transaction_date": parsed_dt,
            "vehicle_identifier": clean_vehicle,
            "fuel_type": fuel_type,
            "litres": parsed_litres,
            "unit_price": unit_price,
            "total_cost": total_cost,
            "odometer_km": odometer_km,
            "station_name": station_name,
            "invoice_number": invoice_number,
            "card_number": card_number,
            "driver_name": driver_name,
            "reference_number": reference_number,
            "notes": notes,
            "source_row_number": row_idx,
        })

    # 7. Insert batches into Supabase
    rows_imported = 0
    batch_errors: List[str] = []
    if valid_transactions:
        rows_imported, batch_errors = insert_fuel_transactions_batch(
            transactions=valid_transactions,
            batch_size=100,
            bearer_token=bearer_token,
        )

    # 8. Date ranges and final status
    date_from: Optional[str] = min(parsed_dates) if parsed_dates else None
    date_to: Optional[str] = max(parsed_dates) if parsed_dates else None

    error_message: Optional[str] = None
    if batch_errors:
        error_message = "; ".join(batch_errors)

    if rows_imported == 0 and total_rows_found > 0:
        final_status = "failed"
        if not error_message:
            error_message = "No valid transactions were imported."
    elif rows_failed > 0 or rows_duplicate > 0 or batch_errors:
        final_status = "completed_with_errors"
    else:
        final_status = "completed"

    completed_at = datetime.now(timezone.utc).isoformat()

    # 9. Update fuel_imports record
    update_fuel_import_record(
        import_id=import_id,
        updates={
            "status": final_status,
            "rows_imported": rows_imported,
            "rows_duplicate": rows_duplicate,
            "rows_failed": rows_failed,
            "date_from": date_from,
            "date_to": date_to,
            "error_message": error_message,
            "completed_at": completed_at,
        },
        bearer_token=bearer_token,
    )

    return FuelImportResultResponse(
        import_id=import_id,
        file_name=file_name,
        status=final_status,
        rows_found=total_rows_found,
        rows_imported=rows_imported,
        rows_duplicate=rows_duplicate,
        rows_failed=rows_failed,
        date_from=date_from,
        date_to=date_to,
        error_message=error_message,
        failed_row_details=failed_row_details[:20] if failed_row_details else None,
    )
