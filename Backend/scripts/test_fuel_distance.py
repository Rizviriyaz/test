"""
Test Suite for Phase 7: Dynamic Wialon Distance and Vehicle Matching.
Validates:
1. Dynamic vehicle matching without hardcoded values.
2. Date range boundary resolution (UAE Time, 00:00:00 to 23:59:59.999).
3. Parameter validations (invalid dates, inverted ranges, missing vehicle identifier).
4. Unmatched vehicle handling (returns clean 404).
5. Unit distance calculation and 'has_data' differentiation.
6. GET /api/fuel/analytics/distance with Wialon mocked (offline).

Offline by default: Wialon is mocked and outbound HTTP is blocked. test_09 calls live Wialon and runs only
with ATS_LIVE_TESTS=1 (e.g. `set ATS_LIVE_TESTS=1 && python scripts/test_fuel_distance.py`).
"""
import os
import sys
import unittest
from datetime import datetime
from fastapi.testclient import TestClient
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

from app.main import app
from app.fuel.wialon_distance import (
    match_fuel_vehicle_to_wialon,
    get_vehicle_distance,
    extract_plate_tokens,
    normalize_alphanumeric,
)
from app.utils.datetime_helpers import parse_date_to_uae_bounds, UAE_TZ


from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from offline_guard import OfflineGuard  # noqa: E402

LIVE = os.getenv("ATS_LIVE_TESTS") == "1"
MOCK_UNITS = [{"id": 1001, "nm": "17428"}, {"id": 1002, "nm": "30561"}]


class TestFuelDistance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.auth_patcher = patch(
            "app.main.authenticate_access_token",
            return_value={"id": "admin-uuid", "email": "admin@ats.ae", "role": "admin"},
        )
        cls.auth_patcher.start()
        cls.guard = None if LIVE else OfflineGuard().start()

    @classmethod
    def tearDownClass(cls):
        cls.auth_patcher.stop()
        if cls.guard:
            cls.guard.stop()
            cls.guard.assert_no_live_calls()

    def test_01_dynamic_vehicle_matching_generic(self):
        """Test dynamic matching against diverse vehicle registration formats without hardcoding."""
        mock_wialon_units = [
            {"id": 1001, "nm": "17428"},
            {"id": 1002, "nm": "30561"},
            {"id": 1003, "nm": "15770"},
            {"id": 1004, "nm": "TRUCK-99"},
        ]

        # Dubai format with letter prefix
        m1 = match_fuel_vehicle_to_wialon("CC-DXB-17428", mock_wialon_units)
        self.assertIsNotNone(m1)
        self.assertEqual(m1["id"], 1001)

        # Abu Dhabi format with numeric category prefix
        m2 = match_fuel_vehicle_to_wialon("13-AUH-30561", mock_wialon_units)
        self.assertIsNotNone(m2)
        self.assertEqual(m2["id"], 1002)

        # Single letter prefix
        m3 = match_fuel_vehicle_to_wialon("T-DXB-15770", mock_wialon_units)
        self.assertIsNotNone(m3)
        self.assertEqual(m3["id"], 1003)

        # Exact string match
        m4 = match_fuel_vehicle_to_wialon("TRUCK-99", mock_wialon_units)
        self.assertIsNotNone(m4)
        self.assertEqual(m4["id"], 1004)

        # Non-existent vehicle returns None
        m5 = match_fuel_vehicle_to_wialon("UNKNOWN-PLATE-9999", mock_wialon_units)
        self.assertIsNone(m5)

    def test_02_plate_token_extraction(self):
        """Verify plate tokenization extracts prefixes, categories, and trailing registration numbers."""
        tokens_dxb = extract_plate_tokens("CC-DXB-17428")
        self.assertIn("17428", tokens_dxb)

        tokens_auh = extract_plate_tokens("13-AUH-30561")
        self.assertIn("30561", tokens_auh)

        tokens_zero = extract_plate_tokens("DXB-01234")
        self.assertIn("1234", tokens_zero)

    def test_03_date_boundary_resolution_uae(self):
        """Test date bounds start at 00:00:00 and end at 23:59:59.999 in UAE timezone (+04:00)."""
        from_ts, to_ts = parse_date_to_uae_bounds("2026-08-01", "2026-08-15")

        from_dt = datetime.fromtimestamp(from_ts, tz=UAE_TZ)
        to_dt = datetime.fromtimestamp(to_ts, tz=UAE_TZ)

        self.assertEqual(from_dt.hour, 0)
        self.assertEqual(from_dt.minute, 0)
        self.assertEqual(from_dt.second, 0)
        self.assertEqual(from_dt.day, 1)
        self.assertEqual(from_dt.month, 8)

        self.assertEqual(to_dt.hour, 23)
        self.assertEqual(to_dt.minute, 59)
        self.assertEqual(to_dt.second, 59)
        self.assertEqual(to_dt.day, 15)
        self.assertEqual(to_dt.month, 8)

    def test_04_date_range_validation_errors(self):
        """Verify invalid date formats and reversed intervals raise ValueErrors."""
        with self.assertRaises(ValueError):
            parse_date_to_uae_bounds("2026-08-15", "2026-08-01")

        with self.assertRaises(ValueError):
            parse_date_to_uae_bounds("invalid-date", "2026-08-15")

    def test_05_api_validation_missing_vehicle(self):
        """GET /api/fuel/analytics/distance returns 422 when vehicle_identifier is missing."""
        resp = self.client.get(
            "/api/fuel/analytics/distance",
            params={"date_from": "2026-08-03", "date_to": "2026-08-31"},
            headers={"Authorization": "Bearer mock-dev-token"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_06_api_validation_invalid_date_range(self):
        """GET /api/fuel/analytics/distance returns 400 when date_from > date_to."""
        resp = self.client.get(
            "/api/fuel/analytics/distance",
            params={
                "vehicle_identifier": "CC-DXB-17428",
                "date_from": "2026-08-31",
                "date_to": "2026-08-03",
            },
            headers={"Authorization": "Bearer mock-dev-token"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("date_from must be on or before date_to", resp.json()["detail"])

    def test_07_api_unmatched_vehicle_returns_404(self):
        """GET /api/fuel/analytics/distance returns 404 for vehicle without active Wialon GPS (Wialon mocked)."""
        with patch("app.fuel.routes.login", return_value="sid"), \
                patch("app.fuel.routes.get_all_vehicles", return_value=MOCK_UNITS):
            resp = self.client.get(
                "/api/fuel/analytics/distance",
                params={
                    "vehicle_identifier": "NON-EXISTENT-PLATE-99999",
                    "date_from": "2026-08-03",
                    "date_to": "2026-08-31",
                },
                headers={"Authorization": "Bearer mock-dev-token"},
            )
        self.assertEqual(resp.status_code, 404)
        self.assertIn("does not have an active matching Wialon GPS unit", resp.json()["detail"])

    def test_08_api_distance_retrieval_mocked(self):
        """GET /api/fuel/analytics/distance resolves the plate to a unit and returns its report distance (Wialon mocked)."""
        dist = {"distance": 3210.5, "unit": "km", "source": "wialon_native_trips_report", "trip_count": 42, "has_data": True}
        with patch("app.fuel.routes.login", return_value="sid"), \
                patch("app.fuel.routes.get_all_vehicles", return_value=MOCK_UNITS), \
                patch("app.fuel.routes.get_vehicle_distance", return_value=dist) as get_distance:
            resp = self.client.get(
                "/api/fuel/analytics/distance",
                params={"vehicle_identifier": "CC-DXB-17428", "date_from": "2026-08-03", "date_to": "2026-08-31"},
                headers={"Authorization": "Bearer mock-dev-token"},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        get_distance.assert_called_once_with(1001, "2026-08-03", "2026-08-31", "sid")
        self.assertEqual((data["wialon_unit_id"], data["wialon_unit_name"]), (1001, "17428"))
        self.assertEqual((data["distance"], data["trip_count"], data["has_data"]), (3210.5, 42, True))
        self.assertEqual(data["source"], "wialon_native_trips_report")

    @unittest.skipUnless(LIVE, "live Wialon; set ATS_LIVE_TESTS=1 to run")
    def test_09_api_live_distance_retrieval(self):
        """LIVE: GET /api/fuel/analytics/distance resolves a real vehicle and fetches its Wialon distance."""
        resp = self.client.get(
            "/api/fuel/analytics/distance",
            params={
                "vehicle_identifier": "CC-DXB-17428",
                "date_from": "2026-08-03",
                "date_to": "2026-08-31",
            },
            headers={"Authorization": "Bearer mock-dev-token"},
        )
        self.assertEqual(resp.status_code, 200, f"Expected 200, got: {resp.text}")
        data = resp.json()

        self.assertEqual(data["vehicle_identifier"], "CC-DXB-17428")
        self.assertEqual(data["wialon_unit_name"], "17428")
        self.assertEqual(data["wialon_unit_id"], 600691973)
        self.assertEqual(data["date_from"], "2026-08-03")
        self.assertEqual(data["date_to"], "2026-08-31")
        self.assertEqual(data["unit"], "km")
        self.assertEqual(data["source"], "wialon_native_trips_report")
        self.assertGreater(data["distance"], 3000.0)
        self.assertGreater(data["trip_count"], 10)
        self.assertTrue(data["has_data"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
