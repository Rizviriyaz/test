"""
Offline tests for the fuel import's mandatory-field and pricing rules, for dated statements (one row per refuel)
and undated monthly summaries (reporting month assigned), and for ambiguous date/price column mappings.

Every outbound HTTP call is blocked by OfflineGuard; the database functions the importer calls are patched.
These tests prove the rules on fixtures, not the accuracy of any production data.

Run from Backend/:  python scripts/test_fuel_import_pricing_rules.py
"""

import io
import os
import sys
import traceback
from unittest.mock import MagicMock, patch

import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from offline_guard import OfflineGuard  # noqa: E402
from test_fuel_import_replacement import FakeReplaceDB, replace_env  # noqa: E402

from app.fuel.importer import (  # noqa: E402
    FuelImportError,
    classify_statement_row,
    execute_fuel_import,
    generate_fuel_preview,
)
from app.fuel.mapper import ambiguous_mapping_fields, map_headers  # noqa: E402

USER = "00000000-0000-0000-0000-000000000001"
MONTH_END = "2026-08-31"


def xlsx(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def classify(cells, assigned_date=None):
    """cells: {canonical field: value}; columns in the order given."""
    fields = list(cells)
    idx = {f: i for i, f in enumerate(fields)}
    return classify_statement_row([cells[f] for f in fields], idx, 2, len(fields), None, assigned_date)


def run_import(headers, rows, mapping, **kwargs):
    """Import with replacement off; returns (result, rows sent to the insert)."""
    sent = []

    def insert(transactions, batch_size=100, bearer_token=None, **_):
        sent.extend(transactions)
        return len(transactions), []

    with patch.dict(os.environ, {"FUEL_SCHEMA_V2": "", "FUEL_IMPORT_REPLACE": ""}), \
         patch("app.fuel.importer.fetch_running_imports", return_value=[]), \
         patch("app.fuel.importer.create_fuel_import_record", return_value=11), \
         patch("app.fuel.importer.update_fuel_import_record", return_value=True), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=insert), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", return_value=set()):
        res = execute_fuel_import(xlsx(headers, rows), "s.xlsx", mapping, USER, **kwargs)
    return res, sent


# ─── Case A: dated statements ──────────────────────────────────────────────────

def test_dated_unit_price_without_total_cost():
    r = classify({"transaction_date": "2026-03-05 09:00:00", "vehicle_identifier": "AUH 44120", "litres": 50,
                  "unit_price": 3.1})
    assert r["kind"] == "fuel", r
    f = r["candidate"]["fields"]
    assert f["total_cost"] == 155.0 and f["unit_price"] == 3.1 and r["candidate"]["cost_derived"], f
    assert r["candidate"]["date_basis"] == "transaction" and f["transaction_date"] == "2026-03-05T09:00:00"
    res, sent = run_import(["Date", "Vehicle", "Litres", "Unit Price"], [["2026-03-05 09:00:00", "AUH 44120", 50, 3.1]],
                           {"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres",
                            "unit_price": "Unit Price"})
    assert res.rows_imported == 1 and res.rows_cost_derived == 1 and sent[0]["total_cost"] == 155.0, res
    print("[PASS] Dated + unit price, no total: total cost derived as litres x unit price (flagged derived).")


def test_dated_total_cost_only_derives_unit_price():
    # Decision 1: dated files with only a total cost are accepted; unit price = total / litres
    r = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "AUH 44120", "litres": 40, "total_cost": 124})
    assert r["kind"] == "fuel" and r["candidate"]["fields"]["unit_price"] == 3.1 and r["candidate"]["unit_price_derived"]
    print("[PASS] Dated + total cost only: unit price derived as total / litres.")


def test_dated_both_prices_consistent_and_mismatched():
    ok = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 50, "unit_price": 3.1,
                   "total_cost": 155.0})
    assert ok["kind"] == "fuel" and not ok["price_mismatch"] and not ok["candidate"]["cost_derived"]
    # Within max(0.05 AED, 1%): rounding on the statement is not a mismatch
    near = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 33.33, "unit_price": 3.1,
                     "total_cost": 103.33})
    assert near["kind"] == "fuel" and not near["price_mismatch"], near
    # VAT-inclusive total: accepted with a warning, the statement's total is kept (KPIs use total_cost)
    vat = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 50, "unit_price": 3.1,
                    "total_cost": 162.75})
    assert vat["kind"] == "fuel" and vat["price_mismatch"] and vat["candidate"]["fields"]["total_cost"] == 162.75
    assert any("differs from litres x unit price" in w for w in vat["warnings"])
    print("[PASS] Dated + both prices: consistent rows plain; a mismatch is kept with a warning, never rejected or rewritten.")


def test_dated_missing_or_invalid_unit_price():
    base = {"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 50}
    cases = [
        ({"unit_price": None}, "Missing unit price per litre"),
        ({"unit_price": "-"}, "Missing unit price per litre"),
        ({"unit_price": "abc"}, "Invalid unit price ('abc')"),
        ({"unit_price": -3.1}, "Negative unit price"),
        ({"unit_price": "abc", "total_cost": 155}, "Invalid unit price ('abc')"),  # invalid price never ignored
        ({"unit_price": "3,1"}, "Invalid unit price"),  # decimal comma is ambiguous
    ]
    for extra, reason in cases:
        r = classify({**base, **extra})
        assert r["kind"] == "failed" and r["reason"].startswith(reason), (extra, r["reason"])
    # A zero unit price is a genuine price: kept as 0 and the derived total is 0, never missing
    zero = classify({**base, "unit_price": 0})
    assert zero["kind"] == "fuel", zero
    assert zero["candidate"]["fields"]["unit_price"] == 0.0 and zero["candidate"]["fields"]["total_cost"] == 0.0, zero
    # No price column at all: the preview lists it as a missing required column and the import refuses it
    headers = ["Date", "Vehicle", "Litres"]
    p = generate_fuel_preview(xlsx(headers, [["2026-03-05", "V1", 50]]), "s.xlsx")
    assert "unit_price" in p.missing_required_fields and not p.ready_to_import and p.rows_fuel is None, p
    try:
        run_import(headers, [["2026-03-05", "V1", 50]],
                   {"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres"})
        raise AssertionError("expected 422")
    except FuelImportError as exc:
        assert exc.status_code == 422 and "unit_price" in exc.message
    print("[PASS] Dated: missing, text, negative or comma-decimal unit price rejected with a named reason; zero kept as 0.")


# ─── Case B: undated monthly summaries ─────────────────────────────────────────

def test_undated_unit_price_without_total_rejected():
    r = classify({"vehicle_identifier": "V1", "litres": 400, "unit_price": 3.1}, assigned_date=MONTH_END)
    assert r["kind"] == "failed" and "Missing total cost (AED)" in r["reason"] and "unit price alone" in r["reason"], r
    print("[PASS] Monthly summary with unit price but no total cost: rejected.")


def test_undated_total_without_unit_price():
    r = classify({"vehicle_identifier": "V1", "litres": 400, "total_cost": 1240}, assigned_date=MONTH_END)
    assert r["kind"] == "fuel", r
    c = r["candidate"]
    assert c["fields"]["transaction_date"] == MONTH_END and c["date_basis"] == "reporting_month_end"
    assert c["fields"]["unit_price"] == 3.1 and c["unit_price_derived"] and not c["cost_derived"]
    print("[PASS] Monthly summary with total cost only: accepted, unit price derived, month-end reporting date.")


# ─── Mandatory fields and malformed values (both formats) ──────────────────────

def test_mandatory_fields_and_malformed_values():
    for assigned in (None, MONTH_END):
        base = {"vehicle_identifier": "V1", "litres": 50, "total_cost": 155}
        if assigned is None:
            base = {"transaction_date": "2026-03-05", **base}
        cases = [
            ({"vehicle_identifier": "  "}, "Missing vehicle identifier"),
            ({"litres": None}, "Missing or invalid fuel litres"),
            ({"litres": "abc"}, "Missing or invalid fuel litres"),
            ({"litres": "12,5"}, "Missing or invalid fuel litres"),
            ({"litres": -20}, "Fuel litres cannot be negative"),
            ({"litres": "-0.5"}, "Fuel litres cannot be negative"),
            ({"total_cost": "AED12.5"}, "Invalid total cost"),
            ({"total_cost": -155}, "Negative total cost"),
        ]
        for extra, reason in cases:
            r = classify({**base, **extra}, assigned_date=assigned)
            assert r["kind"] == "failed" and r["reason"].startswith(reason), (assigned, extra, r["reason"])
    # A genuine zero is never confused with a missing value: blank is rejected, zero is kept as 0
    blank = classify({"vehicle_identifier": "V1", "litres": 50, "total_cost": None}, assigned_date=MONTH_END)
    zero = classify({"vehicle_identifier": "V1", "litres": 50, "total_cost": 0}, assigned_date=MONTH_END)
    assert blank["kind"] == "failed" and "Missing total cost" in blank["reason"], blank
    assert zero["kind"] == "fuel" and zero["candidate"]["fields"]["total_cost"] == 0.0, zero
    assert zero["candidate"]["fields"]["unit_price"] == 0.0, "0 AED / 50 L derives a 0 unit price"
    # A derived total that rounds to zero is a genuine 0, not missing
    tiny = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 0.001, "unit_price": 0.5})
    assert tiny["kind"] == "fuel" and tiny["candidate"]["fields"]["total_cost"] == 0.0, tiny
    print("[PASS] Vehicle, litres (>= 0) and prices (>= 0) validated in both formats; negative, blank and text rejected.")


def test_zero_litres_and_zero_prices_are_kept_as_zero():
    # Dated: 0 L at a price -> total 0; positive values unchanged
    dated = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 0, "unit_price": 3.1})
    assert dated["kind"] == "fuel", dated
    f = dated["candidate"]["fields"]
    assert (f["litres"], f["unit_price"], f["total_cost"]) == (0.0, 3.1, 0.0) and dated["candidate"]["cost_derived"], f
    normal = classify({"transaction_date": "2026-03-05", "vehicle_identifier": "V1", "litres": 50, "unit_price": 3.1})
    assert (normal["candidate"]["fields"]["litres"], normal["candidate"]["fields"]["total_cost"]) == (50.0, 155.0)
    # Monthly summary: 0 L and 0 AED (a vehicle that did not refuel) -> stored as zeros; no unit price is invented
    summ = classify({"vehicle_identifier": "V1", "litres": 0, "total_cost": 0}, assigned_date=MONTH_END)
    assert summ["kind"] == "fuel", summ
    f = summ["candidate"]["fields"]
    assert (f["litres"], f["total_cost"], f["unit_price"]) == (0.0, 0.0, None), f
    assert not summ["candidate"]["unit_price_derived"]
    # 0 L with a non-zero total: kept, unit price unknown (cannot divide by 0 L), never a made-up value
    odd = classify({"vehicle_identifier": "V1", "litres": "0", "total_cost": "12.50"}, assigned_date=MONTH_END)
    assert odd["kind"] == "fuel" and odd["candidate"]["fields"]["unit_price"] is None, odd
    # Text zero forms are numbers too; '-' and blanks stay missing
    assert classify({"vehicle_identifier": "V1", "litres": "0.00", "total_cost": "AED 0"}, assigned_date=MONTH_END)["kind"] == "fuel"
    assert classify({"vehicle_identifier": "V1", "litres": "-", "total_cost": 0}, assigned_date=MONTH_END)["kind"] == "failed"
    # Import end to end (in-memory store): zeros reach the stored rows as 0; a negative row is rejected
    headers = ["Vehicle", "Litres", "Amount"]
    rows = [["AUH 44120", 0, 0], ["DXB 10293", 120.5, 373.55], ["SHJ 88211", -5, 10]]
    db = FakeReplaceDB()
    with replace_env(db):
        res = execute_fuel_import(xlsx(headers, rows), "s.xlsx", {"vehicle_identifier": "Vehicle", "litres": "Litres",
                                  "total_cost": "Amount"}, "00000000-0000-0000-0000-000000000001",
                                  reporting_month="2026-02")
    assert (res.rows_imported, res.rows_failed) == (2, 1), res
    stored = {r["vehicle_identifier"]: (r["litres"], r["total_cost"], r["unit_price"]) for r in db.rows}
    assert stored["AUH 44120"] == (0.0, 0.0, None) and stored["DXB 10293"][:2] == (120.5, 373.55), stored
    assert "cannot be negative" in res.failed_row_details[0]["reason"], res.failed_row_details
    print("[PASS] Zero litres and zero prices are stored as 0 (dated and monthly); negative rejected; positive unchanged.")


def test_blank_cells_in_mapped_date_column_are_not_filled():
    r = classify({"transaction_date": None, "vehicle_identifier": "V1", "litres": 50, "total_cost": 155})
    assert r["kind"] == "failed" and "transaction date" in r["reason"], r
    headers = ["Date", "Vehicle", "Litres", "Amount"]
    rows = [["2026-03-05", "V1", 50, 155], [None, "V1", 40, 124], ["not a date", "V1", 30, 93]]
    mapping = {"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres", "total_cost": "Amount"}
    res, sent = run_import(headers, rows, mapping)
    assert res.rows_imported == 1 and res.rows_failed == 2 and len(sent) == 1, res
    # A reporting month is refused while a date column is mapped, so it can never fill blank cells
    with patch.dict(os.environ, {"FUEL_IMPORT_REPLACE": "1"}):
        try:
            run_import(headers, rows, mapping, reporting_month="2026-03")
            raise AssertionError("expected 422")
        except FuelImportError as exc:
            assert exc.status_code == 422 and "date column" in exc.message
    print("[PASS] Blank/invalid cells of a mapped date column fail their rows; a reporting month is never used for them.")


# ─── Ambiguous date and pricing headers ────────────────────────────────────────

def test_distinct_price_headers_are_never_swapped():
    mapping, details, _, _ = map_headers(["Date", "Vehicle", "Litres", "Price per Litre", "Total Cost"])
    assert mapping["unit_price"] == "Price per Litre" and mapping["total_cost"] == "Total Cost", mapping
    assert ambiguous_mapping_fields(["Date", "Vehicle", "Litres", "Price per Litre", "Total Cost"], mapping, details) == []
    mapping, details, _, _ = map_headers(["Date", "Vehicle", "Litres", "Unit Price", "Total Amount"])
    assert mapping["unit_price"] == "Unit Price" and mapping["total_cost"] == "Total Amount", mapping
    mapping, _, _, _ = map_headers(["Date", "Vehicle", "Litres", "Cost per Litre", "Amount"])
    assert mapping["unit_price"] == "Cost per Litre" and mapping["total_cost"] == "Amount", mapping
    print("[PASS] 'Unit Price', 'Price per Litre', 'Cost per Litre', 'Total Cost' and 'Amount' map to the right fields.")


def test_generic_price_headers_need_confirmation_unless_values_prove_them():
    headers = ["Date", "Vehicle", "Litres", "Price", "Total"]
    consistent = [["2026-03-05", "V1", 50, 3.1, 155], ["2026-03-06", "V1", 40, 3.1, 124]]
    p = generate_fuel_preview(xlsx(headers, consistent), "s.xlsx")
    assert p.mapping_confirmation_required == [] and p.ready_to_import, (p.mapping_confirmation_required, p.blocking_reasons)
    swapped = [["2026-03-05", "V1", 50, 155, 3.1], ["2026-03-06", "V1", 40, 124, 3.1]]  # 'Price' holds the totals
    p = generate_fuel_preview(xlsx(headers, swapped), "s.xlsx")
    assert set(p.mapping_confirmation_required) == {"unit_price", "total_cost"} and not p.ready_to_import, p
    assert any("Confirm the column mapping" in b for b in p.blocking_reasons)
    mapping = {"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres", "unit_price": "Price",
               "total_cost": "Total"}
    try:
        run_import(headers, swapped, mapping)
        raise AssertionError("expected 422")
    except FuelImportError as exc:
        assert exc.status_code == 422 and "must be confirmed" in exc.message
    # The user remaps explicitly (Price is the total, Total is the per-litre price) and confirms
    fixed = {**mapping, "unit_price": "Total", "total_cost": "Price"}
    p = generate_fuel_preview(xlsx(headers, swapped), "s.xlsx", mapping=fixed, mapping_confirmed=True)
    assert p.ready_to_import and p.active_mapping["total_cost"] == "Price", p.blocking_reasons
    res, sent = run_import(headers, swapped, fixed, mapping_confirmed=True)
    assert res.rows_imported == 2 and [t["total_cost"] for t in sent] == [155, 124], sent
    print("[PASS] Generic 'Price'/'Total' headers need confirmation unless every row proves total = litres x price.")


def test_ambiguous_date_headers_need_confirmation():
    headers = ["Transaction Date", "Invoice Date", "Vehicle", "Litres", "Amount"]
    rows = [["2026-03-05", "2026-03-31", "V1", 50, 155]]
    p = generate_fuel_preview(xlsx(headers, rows), "s.xlsx")
    assert "transaction_date" in p.mapping_confirmation_required and not p.ready_to_import, p
    assert set(p.date_column_candidates) == {"Transaction Date", "Invoice Date"}
    p = generate_fuel_preview(xlsx(headers, rows), "s.xlsx", mapping=p.active_mapping, mapping_confirmed=True)
    assert p.ready_to_import and p.import_mode == "dated", p.blocking_reasons
    # A header that only mentions a date ('Card Expiry Date') is never accepted as the transaction date silently
    headers = ["Vehicle", "Card Expiry Date", "Litres", "Amount"]
    p = generate_fuel_preview(xlsx(headers, [["V1", "2027-01-31", 50, 155]]), "s.xlsx")
    assert "transaction_date" in p.mapping_confirmation_required and not p.ready_to_import, p
    print("[PASS] Several date-like headers, or a loosely matched one, require an explicit date mapping.")


def test_preview_counts_match_import():
    headers = ["Date", "Vehicle", "Litres", "Rate", "Amount"]
    rows = [["2026-03-05", "V1", 50, 3.1, None], ["2026-03-06", "V1", 40, None, 124], ["2026-03-07", "", 10, 3.1, 31],
            ["2026-03-08", "V1", 10, 0, None], ["2026-03-09", "V1", 10, 3.1, 31], ["2026-03-09", "V1", 10, 3.1, 31]]
    p = generate_fuel_preview(xlsx(headers, rows), "s.xlsx")
    # Row 4 (unit price 0) is a valid fuel row with a derived total of 0; only the row without a vehicle is invalid
    assert (p.rows_fuel, p.rows_invalid, p.rows_identical_in_file) == (5, 1, 1), p
    assert (p.rows_cost_derived, p.rows_unit_price_derived) == (2, 1), p
    res, _ = run_import(headers, rows, p.active_mapping)
    assert res.rows_imported == p.rows_fuel and res.rows_failed == p.rows_invalid, res
    print("[PASS] Preview and import count fuel, invalid, derived and identical rows the same way.")


TESTS = [
    test_dated_unit_price_without_total_cost,
    test_dated_total_cost_only_derives_unit_price,
    test_dated_both_prices_consistent_and_mismatched,
    test_dated_missing_or_invalid_unit_price,
    test_undated_unit_price_without_total_rejected,
    test_undated_total_without_unit_price,
    test_mandatory_fields_and_malformed_values,
    test_zero_litres_and_zero_prices_are_kept_as_zero,
    test_blank_cells_in_mapped_date_column_are_not_filled,
    test_distinct_price_headers_are_never_swapped,
    test_generic_price_headers_need_confirmation_unless_values_prove_them,
    test_ambiguous_date_headers_need_confirmation,
    test_preview_counts_match_import,
]


def main():
    print("=" * 60)
    print("FUEL IMPORT - PRICING AND MANDATORY FIELDS (OFFLINE)")
    print("=" * 60)
    guard = OfflineGuard().start()
    failures = 0
    try:
        for t in TESTS:
            try:
                t()
            except Exception:
                failures += 1
                print(f"[FAIL] {t.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    guard.assert_no_live_calls()
    print("=" * 60)
    print(f"{len(TESTS) - failures}/{len(TESTS)} test functions passed")
    return failures


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
