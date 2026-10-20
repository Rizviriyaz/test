"""
Offline regression tests for the production-readiness fixes:

  - numeric cells: decimal commas and nan/inf are rejected (never misparsed into stored litres/cost)
  - workbook loading: generic client errors (no library text), expanded-size cap (413), upload cap (413)
  - import totals reconcile when a database batch fails (rows counted as failed, never lost)
  - a failed import-history status update is surfaced as a warning
  - impossible calendar dates are 400 (not 500/503); import-history outage is 503 (not an empty 200)
  - Wialon 502 bodies and AI tool errors do not carry raw upstream text

Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_fuel_production_hardening.py
"""
import asyncio
import io
import json
import os
import sys
import zipfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient  # noqa: E402

from test_fuel_import_integrity import AUTH, FAKE_USER, MAPPING, FakePostgrest, FakeResponse, make_xlsx, run_import  # noqa: E402
from test_fuel_scope import run  # noqa: E402

import app.fuel.importer as importer  # noqa: E402
import app.fuel.routes as fuel_routes  # noqa: E402
from app.fuel import db as fuel_db  # noqa: E402
from app.fuel.importer import FuelImportError, execute_fuel_import, load_fuel_workbook, parse_numeric_value  # noqa: E402
from app.main import app, wialon_upstream_error_handler  # noqa: E402
from app.wialon import WialonAPIError  # noqa: E402

ROW = ["2026-03-05 09:00:00", "AUH 44120", 80, 3.0, 240, "INV-1", None]


def test_numeric_parsing_rejects_ambiguous_and_non_finite():
    ok = {"1,234.56": 1234.56, "AED 1,200": 1200.0, "12.5": 12.5, "-15": -15.0, "1,234,567": 1234567.0, 80: 80.0, 2.5: 2.5}
    for raw, expected in ok.items():
        value, warning = parse_numeric_value(raw)
        assert value == expected and warning is None, (raw, value, warning)
    for raw in ("12,5", "1.234,56", "1,23", "12,50", "nan", "inf", "-Infinity", "1e999", float("nan"), float("inf"), True):
        value, warning = parse_numeric_value(raw)
        assert value is None and warning, (raw, value, warning)
    assert "ambiguous comma" in parse_numeric_value("12,5")[1]
    assert parse_numeric_value(None) == (None, None) and parse_numeric_value("n/a") == (None, None)
    print("[PASS] Numeric cells: thousands commas parsed; decimal commas, nan/inf and booleans rejected with a warning.")


def test_import_rejects_ambiguous_litres_row():
    fake = FakePostgrest("timestamp")
    res, _ = run_import(fake, [ROW, ["2026-03-05 10:00:00", "AUH 44120", "12,5", 3.0, None, "INV-2", None]])
    assert res.rows_imported == 1 and res.rows_failed == 1, res
    assert [r["litres"] for r in fake.rows] == [80], "the '12,5' row must never be stored as 125 L"
    print("[PASS] Import: a decimal-comma litres cell fails its row instead of storing 10x the volume.")


def test_workbook_errors_generic_and_size_capped():
    for bad in (b"not a zip at all", _zip_with({"junk.txt": b"hello"})):
        try:
            load_fuel_workbook(bad, "test")
            raise AssertionError("expected FuelImportError")
        except FuelImportError as exc:
            assert exc.status_code == 422 and exc.message == importer.UNREADABLE_WORKBOOK_MESSAGE, exc.message
            assert "Error:" not in exc.message and "zip" not in exc.message.lower()
    good = make_xlsx([ROW])
    assert load_fuel_workbook(good, "test").sheetnames
    with patch.object(importer, "MAX_UNCOMPRESSED_BYTES", 100):
        try:
            load_fuel_workbook(good, "test")
            raise AssertionError("expected 413")
        except FuelImportError as exc:
            assert exc.status_code == 413 and exc.message == importer.WORKBOOK_TOO_LARGE_MESSAGE
    print("[PASS] Workbook: unreadable files get a generic 422 (no library text); oversized expansion is 413 before loading.")


def _zip_with(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_upload_size_limit_returns_413():
    client = TestClient(app)
    preview = MagicMock()
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch.object(fuel_routes, "MAX_IMPORT_FILE_BYTES", 1000), \
         patch("app.fuel.routes.generate_fuel_preview", preview), \
         patch("app.fuel.routes.execute_fuel_import", preview):
        r1 = client.post("/api/fuel/import/preview", headers=AUTH, files={"file": ("big.xlsx", b"x" * 1001)})
        r2 = client.post("/api/fuel/import", headers=AUTH, data={"mapping": json.dumps(MAPPING)},
                         files={"file": ("big.xlsx", b"x" * 1001)})
        r3 = client.post("/api/fuel/import/preview", headers=AUTH, files={"file": ("empty.xlsx", b"")})
    assert r1.status_code == 413 and r2.status_code == 413, (r1.text, r2.text)
    assert r3.status_code == 400 and not preview.called
    print("[PASS] Upload cap: preview and import return 413 above the limit (400 when empty) before parsing.")


def _run_with_failing_batches(v2):
    create = MagicMock(return_value=900)
    update = MagicMock(return_value=True)
    batch = MagicMock(return_value=(0, ["Batch 1 failed: HTTP 400"]))
    batch_v2 = MagicMock(return_value=(0, 0, ["Batch 1 failed: HTTP 400"]))
    rows = [ROW, ["2026-03-05 11:00:00", "AUH 44120", 60, 3.0, 180, "INV-3", None]]
    with patch.dict(os.environ, {"FUEL_SCHEMA_V2": "1" if v2 else ""}), \
         patch("app.fuel.importer.create_fuel_import_record", create), \
         patch("app.fuel.importer.update_fuel_import_record", update), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", batch), \
         patch("app.fuel.importer.insert_fuel_transactions_ignore_duplicates", batch_v2), \
         patch("app.fuel.importer.find_import_by_sha256", return_value=None), \
         patch("app.fuel.importer.fetch_existing_transaction_fingerprints", return_value=set()):
        res = execute_fuel_import(make_xlsx(rows), "s.xlsx", MAPPING, FAKE_USER["id"])
    return res, update


def test_failed_batch_rows_counted_as_failed():
    for v2 in (False, True):
        res, update = _run_with_failing_batches(v2)
        assert res.rows_imported == 0 and res.rows_failed == 2, (v2, res.rows_failed)
        assert res.rows_found == res.rows_imported + res.rows_duplicate + res.rows_failed, res
        assert res.status == "failed" and "Batch 1 failed" in (res.error_message or "")
        assert update.call_args.kwargs["updates"]["rows_failed"] == 2
    print("[PASS] Rejected database batches count as failed rows (both insert paths); totals reconcile with rows_found.")


def test_history_update_failure_is_warned():
    fake = FakePostgrest("timestamp")
    with patch("app.fuel.importer.create_fuel_import_record", return_value=77), \
         patch("app.fuel.importer.update_fuel_import_record", return_value=False), \
         patch("app.fuel.importer.insert_fuel_transactions_batch", side_effect=fake.insert), \
         patch("app.fuel.db.requests.get", side_effect=fake.get):
        res = execute_fuel_import(make_xlsx([ROW]), "s.xlsx", MAPPING, FAKE_USER["id"])
    assert res.rows_imported == 1
    assert any("#77" in w and "processing" in w for w in res.warnings or []), res.warnings
    print("[PASS] A failed import-history status update is reported as a warning, not hidden.")


def test_impossible_dates_are_400():
    client = TestClient(app)
    fetch = MagicMock(return_value=[])
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.routes.fetch_fuel_transactions_for_analytics", fetch):
        for q in ("date_from=2026-02-31", "date_to=2026-13-45", "date_from=2026-02-29T10:00:00"):
            r = client.get(f"/api/fuel/analytics/summary?{q}", headers=AUTH)
            assert r.status_code == 400 and "Invalid calendar date" in r.json()["detail"], (q, r.status_code, r.text)
        r = client.get("/api/fuel/analytics/distance?vehicle_identifier=AUH%2044120&date_from=2026-02-30&date_to=2026-03-01",
                       headers=AUTH)
        assert r.status_code == 400, r.text
        ok = client.get("/api/fuel/analytics/summary?date_from=2028-02-29&date_to=2028-03-01", headers=AUTH)
    assert ok.status_code == 200, ok.text
    assert fetch.call_count == 1, "invalid dates must be rejected before any data read"
    print("[PASS] Impossible calendar dates return 400 before any read (leap day 2028-02-29 still valid).")


def test_import_history_outage_is_503():
    with patch("app.fuel.db.requests.get", return_value=FakeResponse(status=500)):
        try:
            fuel_db.get_fuel_imports_list(10, 0)
            raise AssertionError("expected FuelDataFetchError")
        except fuel_db.FuelDataFetchError:
            pass
    client = TestClient(app)
    with patch("app.main.authenticate_access_token", return_value=FAKE_USER), \
         patch("app.fuel.db.requests.get", side_effect=fuel_db.requests.RequestException("down")):
        r = client.get("/api/fuel/imports?limit=10", headers=AUTH)
    assert r.status_code == 503 and "unavailable" in r.json()["detail"], r.text
    with patch("app.fuel.db.requests.get",
               return_value=FakeResponse([{"id": 1}], headers={"content-range": "0-0/1"})):
        records, total = fuel_db.get_fuel_imports_list(10, 0)
    assert records == [{"id": 1}] and total == 1
    print("[PASS] Import history: a Supabase outage is 503, never an empty 'no imports' list.")


def test_upstream_error_text_not_sent_to_clients():
    raw = "Wialon error 4: {'items': [{'nm': 'secret-unit', 'id': 123}]}"
    response = asyncio.run(wialon_upstream_error_handler(MagicMock(url=MagicMock(path="/api/x")), WialonAPIError(raw)))
    body = json.loads(response.body)
    assert response.status_code == 502 and body == {"detail": "Wialon upstream request failed"}, body

    from app.ai import tools
    with patch.object(tools, "tool_get_fleet_summary", side_effect=RuntimeError(raw)):
        out = json.loads(tools.dispatch_tool("get_fleet_summary", {}))
    assert "secret-unit" not in out["error"] and "unavailable" in out["error"], out
    with patch.object(tools, "tool_get_fleet_summary", side_effect=ValueError("date_from must be YYYY-MM-DD")):
        out = json.loads(tools.dispatch_tool("get_fleet_summary", {}))
    assert out["error"] == "date_from must be YYYY-MM-DD", "validation messages still reach the model"
    print("[PASS] Wialon 502 bodies and AI tool errors carry no raw upstream payloads; validation messages kept.")


TESTS = [
    test_numeric_parsing_rejects_ambiguous_and_non_finite,
    test_import_rejects_ambiguous_litres_row,
    test_workbook_errors_generic_and_size_capped,
    test_upload_size_limit_returns_413,
    test_failed_batch_rows_counted_as_failed,
    test_history_update_failure_is_warned,
    test_impossible_dates_are_400,
    test_import_history_outage_is_503,
    test_upstream_error_text_not_sent_to_clients,
]

if __name__ == "__main__":
    sys.exit(1 if run(TESTS, "FUEL PRODUCTION HARDENING (OFFLINE)") else 0)
