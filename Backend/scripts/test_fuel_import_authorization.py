"""
Offline tests for fuel import role authorization (audit item H1).

  - admin, manager and fleet_manager may call /api/fuel/import/preview and /api/fuel/import
  - viewer (and a profile with no or an unknown role) gets 403 before the file is parsed or any
    database write is attempted
  - no bearer token is still 401 (middleware unchanged)

Authentication is mocked (app.main.authenticate_access_token); the importer is mocked, so nothing is
written. Every outbound HTTP call is blocked by OfflineGuard. Run from Backend/:
    python scripts/test_fuel_import_authorization.py
"""
import io
import json
import os
import sys
import traceback
from unittest.mock import patch

import openpyxl
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# These tests cover the import with FUEL_IMPORT_REPLACE off (the replacement path has its own suites); a local
# .env that enables it must not change them. load_dotenv never overrides a variable that is already set.
os.environ["FUEL_IMPORT_REPLACE"] = ""

from offline_guard import OfflineGuard  # noqa: E402

from app.main import app  # noqa: E402
from app.fuel.schemas import FuelImportResultResponse  # noqa: E402

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
AUTH_HEADERS = {"Authorization": "Bearer test-token"}
MAPPING = json.dumps({"transaction_date": "Date", "vehicle_identifier": "Vehicle", "litres": "Litres"})


def _excel_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Date", "Vehicle", "Litres", "Amount"])
    ws.append(["2026-08-01", "7-AUH-55943", 50.0, 120.0])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _user(role):
    return {"id": "00000000-0000-0000-0000-000000000001", "email": "user@example.com", "role": role}


def _import_result() -> FuelImportResultResponse:
    return FuelImportResultResponse(
        import_id=1, file_name="fuel.xlsx", status="completed",
        rows_found=1, rows_imported=1, rows_duplicate=0, rows_failed=0,
    )


def _post(client, path, role, with_mapping=False):
    files = {"file": ("fuel.xlsx", _excel_bytes(), XLSX_TYPE)}
    data = {"mapping": MAPPING} if with_mapping else None
    with patch("app.main.authenticate_access_token", return_value=_user(role)):
        return client.post(path, headers=AUTH_HEADERS, files=files, data=data)


def test_allowed_roles_can_preview(client):
    for role in ("admin", "manager", "fleet_manager"):
        res = _post(client, "/api/fuel/import/preview", role)
        assert res.status_code == 200, (role, res.status_code, res.text)
        assert res.json()["rows_candidate"] == 1, res.json()
    print("[PASS] admin, manager and fleet_manager can preview an import (200).")


def test_allowed_roles_can_import(client):
    for role in ("admin", "manager", "fleet_manager"):
        with patch("app.fuel.routes.execute_fuel_import", return_value=_import_result()) as importer:
            res = _post(client, "/api/fuel/import", role, with_mapping=True)
        assert res.status_code == 200, (role, res.status_code, res.text)
        assert importer.call_count == 1, role
        # imported_by is the authenticated user's id
        assert importer.call_args.args[3] == _user(role)["id"], importer.call_args
    print("[PASS] admin, manager and fleet_manager reach the importer (200, one call each).")


def test_viewer_denied(client):
    with patch("app.fuel.routes.generate_fuel_preview") as preview, \
            patch("app.fuel.routes.execute_fuel_import") as importer:
        res_preview = _post(client, "/api/fuel/import/preview", "viewer")
        res_import = _post(client, "/api/fuel/import", "viewer", with_mapping=True)
    assert res_preview.status_code == 403, (res_preview.status_code, res_preview.text)
    assert res_import.status_code == 403, (res_import.status_code, res_import.text)
    assert "role" in res_import.json()["detail"].lower()
    assert preview.call_count == 0 and importer.call_count == 0
    print("[PASS] viewer gets 403 on preview and import; file never parsed, importer never called.")


def test_missing_or_unknown_role_denied(client):
    with patch("app.fuel.routes.execute_fuel_import") as importer:
        for role in (None, "", "ADMIN", "guest"):
            res = _post(client, "/api/fuel/import", role, with_mapping=True)
            assert res.status_code == 403, (role, res.status_code, res.text)
    assert importer.call_count == 0
    print("[PASS] missing, empty, wrong-case and unknown roles get 403.")


def test_unauthenticated_still_401(client):
    res = client.post("/api/fuel/import", files={"file": ("fuel.xlsx", _excel_bytes(), XLSX_TYPE)},
                      data={"mapping": MAPPING})
    assert res.status_code == 401, (res.status_code, res.text)
    print("[PASS] no bearer token is still 401.")


def main() -> int:
    guard = OfflineGuard().start()
    client = TestClient(app)
    tests = [
        test_allowed_roles_can_preview,
        test_allowed_roles_can_import,
        test_viewer_denied,
        test_missing_or_unknown_role_denied,
        test_unauthenticated_still_401,
    ]
    passed = 0
    try:
        for test in tests:
            try:
                test(client)
                passed += 1
            except Exception:
                print(f"[FAIL] {test.__name__}")
                traceback.print_exc()
    finally:
        guard.stop()
    if guard.attempts:
        print(f"[FAIL] Network guard: {len(guard.attempts)} live call(s) attempted: {guard.attempts}")
        return 1
    print("[PASS] Network guard: zero live network calls attempted.")
    print("=" * 60)
    print(f"{passed}/{len(tests)} test functions passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
